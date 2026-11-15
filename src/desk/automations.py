"""Automation rules (DESK-0292): 'when X then Y' rules in `automations.yaml` at the store root, run by `drew auto run`.

Each rule names its source (the decision, standards rule or event pattern it comes from), a `when` (conditions on one open ticket, or
`every:` for a schedule) and a `then` (a list of actions). The sweep is the guaranteed path; nothing runs on a clock by itself.

Who writes: every change is made by Desk's own actor `automation` with the timeline kind `rule`, set here and nowhere else. A rule file
cannot name an actor (`as`, `actor` and `by` are refused when the file is read), and `--as automation` is refused on the command line, so
a rule can never write as a person and a person can never write as a rule (decision 110).

How it is undone: one sweep is one `activity.batch`, so DESK-0284's undo reverts it as one; the sweep also keeps the text of every ticket
it touched in `.desk/automations/<batch>.json`, which `drew auto undo <batch>` restores. Every what-line in the event log ends with
`(rule <name>)`.

Hand-set values: a rule's `set`, `lane` and `priority` never change a value a person set (the last change of that field in the event log
was not a rule, or the field has a value and no change on record) unless the rule says `override: true`; a tag a person added is never
removed. Status moves always go through rules.check_transition, so every lifecycle rule still applies (a rule never approves).
Docs: docs/AUTOMATIONS.md."""

from __future__ import annotations

import copy
import datetime as dt
import json
import re
import secrets
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import activity, deadlines, identity, lanes, notify_queue, ops, people as peoplemod, repeats, review, subtickets, yamlio
from .rules import OWNER_ROLES, RuleError, check_transition
from .store import Store
from .ticket import CLOSED, PRIORITIES, STATUSES, Ticket

FILE = "automations.yaml"
ACTOR = "automation"
JOURNAL = Path(".desk") / "automations"
GH_TIMEOUT = 20
RULE_KEYS = {"name", "source", "does", "enabled", "override", "when", "then", "every", "day", "repos", "create"}
FORBIDDEN = {"as", "actor", "by", "as_", "author"}  # a rule never names who writes: it is always `automation`
WHEN_KEYS = {"status", "not_status", "type", "project", "priority", "tags_any", "tags_none", "field_set", "field_empty", "field_changed",
             "untouched_days", "deadline_within_days", "no_hard_deadline", "subtickets_all_closed", "blocked_on_closed", "repeated_ask",
             "pr_merged", "pr_open", "owner_not"}
THEN_KEYS = {"set", "lane", "priority", "add_tags", "remove_tags", "move", "reason", "note", "comment", "notify", "urgent", "ci",
             "create", "weekly_review", "unblock", "link_pr"}
EVERY = ("weekly", "monthly", "yearly")
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_PR_ID = re.compile(r"^\W*([A-Za-z][A-Za-z0-9]*-\d{3,})\b")


class AutomationError(Exception):
    """A rules file that cannot be used, in plain words."""


@dataclass
class Rule:
    name: str
    source: str
    does: str
    when: dict = field(default_factory=dict)
    then: list = field(default_factory=list)
    enabled: bool = True
    override: bool = False
    every: str = ""
    on: str = ""
    repos: list = field(default_factory=list)
    create: dict = field(default_factory=dict)

    @property
    def scheduled(self) -> bool:
        return bool(self.every)


@dataclass
class Item:
    """One thing one rule did, or would do, today."""
    rule: str
    ticket: str
    title: str
    why: str
    changes: list = field(default_factory=list)
    skipped: str = ""


@dataclass
class Sweep:
    items: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    batch: str = ""
    applied: bool = False

    @property
    def changed(self) -> list:
        return [i for i in self.items if i.changes and not i.skipped]

    def summary(self) -> str:
        ch = self.changed
        n = sum(len(i.changes) for i in ch)
        tickets = len({i.ticket for i in ch})
        rules = len({i.rule for i in ch})
        skipped = len([i for i in self.items if i.skipped])
        if not self.applied:
            head = f"Dry run: {n} change(s) on {tickets} ticket(s) by {rules} rule(s) would be made today; nothing was changed."
        elif n:
            head = f"Done: {n} change(s) on {tickets} ticket(s) by {rules} rule(s), all by automation, batch {self.batch}. Undo: drew auto undo {self.batch}"
        else:
            head = "Done: no rule had anything to change today."
        return head + (f" {skipped} held back by a rule check (listed above)." if skipped else "")


# ---- the rules file -----------------------------------------------------------------------------

def path(home: Path) -> Path:
    return Path(home) / FILE


def _list(v) -> list:
    if v is None or v == "":
        return []
    return [x for x in v] if isinstance(v, (list, tuple)) else [v]


def parse(data) -> tuple[list[Rule], list]:
    """(rules, top-level repos) from the loaded YAML. Raises AutomationError naming the rule and the key for anything unknown."""
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise AutomationError(f"{FILE} needs a top-level `rules:` list.")
    out, seen = [], set()
    for i, r in enumerate(data["rules"], start=1):
        if not isinstance(r, dict) or not str(r.get("name") or "").strip():
            raise AutomationError(f"Rule {i} in {FILE} needs a name.")
        name = str(r["name"]).strip()
        if name in seen:
            raise AutomationError(f"Two rules are called {name}; each name is used once.")
        seen.add(name)
        bad = set(r) & FORBIDDEN
        if bad:
            raise AutomationError(f"Rule {name} names who writes ({', '.join(sorted(bad))}); rules always write as automation, never as a person.")
        unknown = set(r) - RULE_KEYS
        if unknown:
            raise AutomationError(f"Rule {name}: unknown key(s) {', '.join(sorted(map(str, unknown)))}.")
        when = r.get("when") or {}
        if not isinstance(when, dict) or set(when) - WHEN_KEYS:
            raise AutomationError(f"Rule {name}: unknown condition(s) {', '.join(sorted(set(when) - WHEN_KEYS)) if isinstance(when, dict) else when}.")
        then = r.get("then") or []
        then = [then] if isinstance(then, dict) else then
        for a in then:
            if not isinstance(a, dict) or set(a) - THEN_KEYS:
                raise AutomationError(f"Rule {name}: unknown action {a if not isinstance(a, dict) else ', '.join(sorted(set(a) - THEN_KEYS))}.")
            if set(a) & FORBIDDEN:
                raise AutomationError(f"Rule {name} names who writes; rules always write as automation.")
            if a.get("move") and a["move"] not in STATUSES:
                raise AutomationError(f"Rule {name}: `{a['move']}` is not a status.")
            if a.get("priority") and a["priority"] not in PRIORITIES:
                raise AutomationError(f"Rule {name}: `{a['priority']}` is not a priority.")
            if a.get("lane") and a["lane"] not in lanes.LANES:
                raise AutomationError(f"Rule {name}: `{a['lane']}` is not a lane ({', '.join(lanes.LANES)}).")
        every = str(r.get("every") or "").strip().lower()
        if every and every not in EVERY:
            raise AutomationError(f"Rule {name}: every is one of {', '.join(EVERY)}.")
        if not str(r.get("source") or "").strip():
            raise AutomationError(f"Rule {name} needs a source: the decision, rule or event pattern it comes from.")
        out.append(Rule(name, str(r["source"]).strip(), str(r.get("does") or "").strip(), when, then, bool(r.get("enabled", True)),
                        bool(r.get("override", False)), every, str(r.get("day") or "").strip().lower(), _list(r.get("repos")), r.get("create") or {}))
    return out, _list(data.get("repos"))


def load(home: Path) -> tuple[list[Rule], list]:
    p = path(home)
    if not p.exists():
        raise AutomationError(f"No {FILE} in {home}. `drew auto init` writes the default rules there.")
    try:
        data = yamlio.load(p.read_text(encoding="utf-8"))
    except yamlio.YAMLError as e:
        raise AutomationError(f"{FILE} is not readable YAML: {e}")
    return parse(data)


def find(rules: list[Rule], name: str) -> Rule:
    for r in rules:
        if r.name == name:
            return r
    raise AutomationError(f"No rule called {name}. `drew auto list` names them.")


# ---- who writes ---------------------------------------------------------------------------------

def actor(rule: str = "") -> identity.Actor:
    """Desk's own identity for rule changes: never a person, never claimed, kind rule (the timeline's kind)."""
    return identity.Actor(ACTOR, "rule", rule or "sweep", "cli", "automation", True)


def hand_set(store: Store, t: Ticket, name: str) -> bool:
    """True when a person (not a rule) set this field: the last logged change of it was not by a rule, or it has a value and no
    change on record (set when the ticket was written)."""
    v = t.meta.get(name)
    if v in (None, "", []):
        return False
    last = [e for e in activity.read(store.home, t.id) if e.change == "field" and e.field == name]
    return not last or last[-1].kind != "rule"


def tag_by_hand(store: Store, t: Ticket, tag: str) -> bool:
    for e in reversed(activity.read(store.home, t.id)):
        if e.change == "field" and e.field == "tags" and f"+{tag}" in e.what.split():
            return e.kind != "rule"
    return tag in t.tags


# ---- pull requests (gh) -------------------------------------------------------------------------

def gh_run(args: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    """One gh call with a timeout. (returncode, stdout, stderr); 127 when gh is not installed. Tests replace this function."""
    try:
        r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=GH_TIMEOUT, cwd=cwd)
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return 127, "", "gh is not installed"
    except (OSError, subprocess.SubprocessError) as e:
        return 1, "", str(e)


class PullRequests:
    """Merged and open pull requests by the ticket id their title starts with (decision 62), read once per sweep."""

    def __init__(self, home: Path, repos: list):
        self.home, self.repos = Path(home), repos or ["."]
        self._merged: dict | None = None
        self._open: dict | None = None
        self.note = ""

    def _fetch(self, state: str, fields: str) -> dict:
        out: dict = {}
        for repo in self.repos:
            local = (self.home / str(repo))
            args = ["pr", "list", "--state", state, "--limit", "100", "--json", fields]
            cwd = local if local.is_dir() else self.home
            if not local.is_dir():
                args += ["--repo", str(repo)]
            rc, so, se = gh_run(args, cwd)
            if rc == 127:
                self.note = "gh is not installed, so the pull request rules were skipped. Install the GitHub CLI to use them."
                return {}
            if rc != 0:
                self.note = f"gh could not read pull requests ({(se or 'offline or not signed in').strip()[:120]}), so the pull request rules were skipped."
                return {}
            try:
                rows = json.loads(so or "[]")
            except ValueError:
                rows = []
            for pr in rows:
                m = _PR_ID.match(str(pr.get("title") or ""))
                if m:
                    out.setdefault(m.group(1).upper(), pr)
        return out

    def merged(self) -> dict:
        if self._merged is None:
            self._merged = self._fetch("merged", "number,title,url,mergedAt")
        return self._merged

    def open(self) -> dict:
        if self._open is None:
            self._open = self._fetch("open", "number,title,url,statusCheckRollup")
        return self._open


def ci_state(pr: dict) -> str:
    """green, red, pending or not run, from gh's statusCheckRollup."""
    checks = pr.get("statusCheckRollup") or []
    if not checks:
        return "not run"
    bad = {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "ERROR", "STARTUP_FAILURE"}
    states = [str(c.get("conclusion") or c.get("state") or "").upper() for c in checks]
    if any(s in bad for s in states):
        return "red"
    if any(s in ("", "PENDING", "QUEUED", "IN_PROGRESS", "EXPECTED") for s in states):
        return "pending"
    return "green"


# ---- conditions ---------------------------------------------------------------------------------

@dataclass
class Ctx:
    store: Store
    today: dt.date
    tickets: list
    family: subtickets.Family
    prs: PullRequests
    by_id: dict
    touched: set = field(default_factory=set)  # tickets a rule changed earlier in this sweep: touched today, whatever the clock says


def _updated(t: Ticket) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(t.meta.get("updated"))[:10])
    except ValueError:
        return None


def _days(v, store: Store) -> int:
    return store.stale_days if str(v).strip().lower() in ("store", "stale") else int(v)


def match(rule: Rule, t: Ticket, c: Ctx) -> tuple[bool, list[str], dict]:
    """(matches, the plain reasons, values the actions may use: pr, days)."""
    w, why, vals = rule.when, [], {}
    if "status" in w:
        if t.status not in _list(w["status"]):
            return False, [], {}
        why.append(f"status is {t.status}")
    if "not_status" in w and t.status in _list(w["not_status"]):
        return False, [], {}
    for k in ("type", "project", "priority"):
        if k in w and str(t.meta.get(k) or "") not in [str(x) for x in _list(w[k])]:
            return False, [], {}
    if "tags_any" in w:
        hit = [x for x in _list(w["tags_any"]) if x in t.tags]
        if not hit:
            return False, [], {}
        why.append(f"tagged {', '.join(hit)}")
    if "tags_none" in w and any(x in t.tags for x in _list(w["tags_none"])):
        return False, [], {}
    for f in _list(w.get("field_set")):
        if t.meta.get(f) in (None, "", []):
            return False, [], {}
    for f in _list(w.get("field_empty")):
        if t.meta.get(f) not in (None, "", []):
            return False, [], {}
    if "field_changed" in w:
        fc = w["field_changed"] or {}
        since = c.today - dt.timedelta(days=int(fc.get("within_days", 1)))
        evs = [e for e in activity.read(c.store.home, t.id) if e.change == "field" and e.field == fc.get("field") and e.ts[:10] >= since.isoformat()]
        if not evs:
            return False, [], {}
        why.append(f"{fc.get('field')} changed on {evs[-1].ts[:10]}")
    if "owner_not" in w:
        names = [c.store.owner if str(x) == "owner" else str(x) for x in _list(w["owner_not"])]
        if str(t.meta.get("owner") or "") in names:
            return False, [], {}
    if "untouched_days" in w:
        n, u = _days(w["untouched_days"], c.store), _updated(t)
        if u is None or (c.today - u).days <= n or t.id in c.touched:
            return False, [], {}
        vals["days"] = (c.today - u).days
        why.append(f"untouched for {vals['days']} days (more than {n})")
    if "deadline_within_days" in w:
        d, kind = deadlines.of(t)
        if not d or kind != "hard" or (d - c.today).days > int(w["deadline_within_days"]):
            return False, [], {}
        why.append(f"hard deadline {d.isoformat()}, {deadlines.words((d - c.today).days)}")
    if w.get("no_hard_deadline") and deadlines.of(t)[1] == "hard":
        return False, [], {}
    if w.get("subtickets_all_closed"):
        kids = c.family.subs(t)
        if not kids or any(k.status not in CLOSED for k in kids) or not any(k.status == "done" for k in kids):
            return False, [], {}
        vals["n"] = len(kids)
        why.append(f"all {len(kids)} subtickets are closed")
    if w.get("blocked_on_closed"):
        on = str(t.meta.get("blocked_on") or "").strip().upper()
        dep = c.by_id.get(on)
        if dep is None or dep.status not in CLOSED:
            return False, [], {}
        why.append(f"it was blocked on {dep.id}, which is now {dep.status}")
    if w.get("repeated_ask"):
        r = repeats.repeated_ask(t, c.store.owner)
        if not r:
            return False, [], {}
        why.append(f"{c.store.owner} asked for the same thing twice ({r.first:%Y-%m-%d} and {r.second:%Y-%m-%d})")
    if w.get("pr_merged"):
        pr = c.prs.merged().get(t.id.upper())
        if not pr:
            return False, [], {}
        vals["pr"] = pr
        why.append(f"pull request #{pr.get('number')} merged ({pr.get('url')})")
    if w.get("pr_open"):
        pr = c.prs.open().get(t.id.upper())
        if not pr:
            return False, [], {}
        vals["pr"] = pr
        why.append(f"pull request #{pr.get('number')} is open")
    return True, why, vals


# ---- schedules ----------------------------------------------------------------------------------

def period(rule: Rule, today: dt.date) -> tuple[str, dt.date]:
    """(the period key, the day in this period the rule is due)."""
    if rule.every == "weekly":
        monday = today - dt.timedelta(days=today.weekday())
        day = DAYS.index(rule.on) if rule.on in DAYS else 0
        return monday.isoformat(), monday + dt.timedelta(days=day)
    if rule.every == "monthly":
        day = min(max(int(rule.on or 1), 1), 28)
        return f"{today:%Y-%m}", today.replace(day=day)
    mm, dd = (rule.on or "01-01").split("-")
    return f"{today:%Y}", dt.date(today.year, int(mm), int(dd))


def _fill(text, vals: dict) -> str:
    s = str(text or "")
    for k, v in vals.items():
        s = s.replace("{" + k + "}", str(v))
    return s


# ---- one sweep ----------------------------------------------------------------------------------

class _Journal:
    """Text of every ticket a run touched, before and after, so `drew auto undo` restores exactly."""

    def __init__(self, store: Store, batch: str):
        self.store, self.batch, self.rows = store, batch, {}

    def before(self, t: Ticket | None, created: Ticket | None = None) -> None:
        if created is not None:
            self.rows.setdefault(created.id, {"path": str(created.path.relative_to(self.store.home)), "before": None})
            return
        if t is not None and t.id not in self.rows and t.path and t.path.is_file():
            self.rows[t.id] = {"path": str(t.path.relative_to(self.store.home)), "before": t.path.read_bytes().decode("utf-8")}

    def close(self, who: str) -> None:
        if not self.rows:
            return
        for tid, row in list(self.rows.items()):
            p = self.store.home / row["path"]
            row["after"] = p.read_bytes().decode("utf-8") if p.is_file() else None
            if row["after"] == row["before"]:
                del self.rows[tid]  # matched but nothing changed: nothing to undo
        if not self.rows:
            return
        d = self.store.home / JOURNAL
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{self.batch}.json").write_bytes(json.dumps({"batch": self.batch, "started_by": who, "tickets": self.rows}, ensure_ascii=False, indent=1).encode("utf-8"))


def _act(rule: Rule, t: Ticket, vals: dict, c: Ctx, write: bool, journal: _Journal | None) -> tuple[list[str], str]:
    """Do (or, with write False, describe) a rule's actions on one ticket. (the changes in plain words, why it was held back)."""
    store, changes, dirty = c.store, [], False
    pr = vals.get("pr") or {}
    fill = {"id": t.id, "title": t.meta.get("title", ""), "rule": rule.name, "days": vals.get("days", ""), "n": vals.get("n", ""),
            "pr": pr.get("url", ""), "date": c.today.isoformat(), "owner": store.owner,
            "due": deadlines.words((deadlines.of(t)[0] - c.today).days) if deadlines.of(t)[0] else ""}
    for a in rule.then:  # all or nothing: a move the lifecycle rules refuse holds back the whole rule before anything is written
        if a.get("unblock") and t.status != "blocked":
            return [], f"{t.id} is not blocked"
        if a.get("move"):
            try:
                check_transition(t, a["move"], store, actor=ACTOR, reason=_fill(a.get("reason") or a.get("note") or f"Moved by automation rule {rule.name}", fill))
            except RuleError as e:
                return [], f"not moved to {a['move']}: {e}"
    if journal is not None:
        journal.before(t)

    def set_field(name, value):
        nonlocal dirty
        if t.meta.get(name) == value:
            return
        if not rule.override and hand_set(store, t, name):
            changes.append(f"kept {name} {t.meta.get(name)} (set by hand)")
            return
        changes.append(f"{name}: {t.meta.get(name) or 'empty'} → {value}")
        t.meta[name] = value
        dirty = True

    for a in rule.then:
        if "set" in a:
            for k, v in (a["set"] or {}).items():
                set_field(str(k), _fill(v, fill) if isinstance(v, str) else v)
        if a.get("lane"):
            cur = lanes.explicit_lane(t)  # the lane field, or a lane:<name> tag a person put on
            if cur == a["lane"]:
                pass
            elif cur and not rule.override and (hand_set(store, t, "lane") or not t.meta.get("lane")):
                changes.append(f"kept lane {cur} (set by hand)")
            else:
                set_field("lane", a["lane"])
        if a.get("priority"):
            set_field("priority", a["priority"])
        for tag in _list(a.get("add_tags")):
            if tag not in t.tags:
                t.meta["tags"] = list(t.tags) + [tag]
                changes.append(f"tag +{tag}")
                dirty = True
        for tag in _list(a.get("remove_tags")):
            if tag in t.tags:
                if not rule.override and tag_by_hand(store, t, tag):
                    changes.append(f"kept tag {tag} (added by hand)")
                    continue
                t.meta["tags"] = [x for x in t.tags if x != tag]
                changes.append(f"tag −{tag}")
                dirty = True
        if a.get("ci") and pr:
            set_field("ci", ci_state(pr))
            set_field("ci_pr", pr.get("url", ""))
        if a.get("comment"):
            text = _fill(a["comment"], fill)
            changes.append(f"comment: {text}")
            if write:
                ops.comment(store, t, f"{text} (rule {rule.name})", actor=ACTOR, kind="FYI")
                dirty = False
        if a.get("notify"):
            text = _fill(a["notify"], fill)
            changes.append(f"notify{' (urgent)' if a.get('urgent') else ''}: {text}")
            if write:
                notify_queue.enqueue(store.home, "alert", text, [t.id], bool(a.get("urgent")), source=f"automation:{rule.name}")
        if a.get("unblock"):
            target = str(t.meta.get("blocked_from") or "ready")
            changes.append(f"status: blocked → {target}")
            if write:
                if dirty:
                    store.save(t)
                    dirty = False
                ops.unblock(store, t, actor=ACTOR)
        if a.get("move"):
            new = a["move"]
            reason = _fill(a.get("reason") or a.get("note") or f"Moved by automation rule {rule.name}", fill)
            changes.append(f"status: {t.status} → {new}" + (f" (with {pr.get('url')})" if new == "done" and pr else ""))
            if write:
                if dirty:
                    store.save(t)
                    dirty = False
                if new == "done":
                    ops.done(store, t, commit=pr.get("url") if (a.get("link_pr") and pr) else None)
                elif new == "rejected":
                    ops.reject(store, t, reason, actor=ACTOR)
                else:
                    ops.move(store, t, new, actor=ACTOR, reason=reason, note=_fill(a.get("note") or "", fill) or None)
    if write and dirty:
        store.save(t)
    return changes, ""


def _create(rule: Rule, c: Ctx, key: str, write: bool, journal: _Journal | None) -> Item | None:
    tpl = dict(rule.create or {})
    tag = f"{rule.name}@{key}"
    if any(str(t.meta.get("recurring") or "") == tag for t in c.tickets):
        return None
    fill = {"period": key, "date": c.today.isoformat(), "rule": rule.name}
    title = _fill(tpl.get("title") or rule.name, fill)
    project = str(tpl.get("project") or "")
    if project not in c.store.projects():
        return Item(rule.name, "", title, f"due for {key}", [], f"no project `{project}` in this store")
    owner = str(tpl.get("owner") or c.store.owner)
    status = "ready" if tpl.get("status") == "ready" and owner in OWNER_ROLES | {c.store.owner} else "inbox"
    item = Item(rule.name, "(new)", title, f"{rule.every} on {rule.on or 'the first day'}; nothing created for {key} yet", [f"create ticket in {project} ({status})"])
    if not write:
        return item
    t = c.store.create(title, project, str(tpl.get("type") or "task"), priority=str(tpl.get("priority") or "p2"), size=str(tpl.get("size") or "s"),
                       owner=owner, assignee=str(tpl.get("assignee") or "") or None, source=f"automation rule {rule.name} ({rule.source})")
    t.meta.update(status=status, tags=_list(tpl.get("tags")), recurring=tag)
    for sec, text in (tpl.get("sections") or {}).items():
        t.set_section(str(sec), _fill(text, fill).rstrip() + "\n")
    c.store.save(t)
    if journal is not None:
        journal.before(None, created=t)
    item.ticket = t.id
    return item


def _weekly_review(rule: Rule, c: Ctx, write: bool, journal: _Journal | None) -> Item | None:
    monday = review.monday_of(c.today)
    friday = (monday + dt.timedelta(days=4)).isoformat()  # review.find's test, over the tickets already read
    if any(review.is_review_ticket(t) and str(t.meta.get("review_by", ""))[:10] == friday for t in c.tickets):
        return None
    project = str((rule.create or {}).get("project") or "management")
    title = review.title_for(monday)
    if project not in c.store.projects():
        return Item(rule.name, "", title, "it is the week's first sweep", [], f"no project `{project}` in this store")
    item = Item(rule.name, "(new)", title, f"no ticket review exists for the week of {monday.isoformat()}", ["create and sync the weekly ticket review"])
    if write:
        t, _ = review.create(c.store, c.today, project=project, owner=c.store.owner)
        if journal is not None:
            journal.before(None, created=t)
        item.ticket = t.id
    return item


def sweep(store: Store, rules: list[Rule], repos: list | None = None, today: dt.date | None = None, apply: bool = False,
          only: str | None = None, progress=None, started_by: str = "") -> Sweep:
    """Run every enabled rule (or the one named) over the open tickets. apply=False changes nothing and says what would change."""
    today = today or dt.date.today()
    out = Sweep(applied=apply)
    use = [r for r in rules if r.enabled and (only is None or r.name == only)]
    prs = PullRequests(store.home, repos or [])
    out.batch = "b-" + secrets.token_hex(6) if apply else ""
    journal = _Journal(store, out.batch) if apply else None
    touched: set = set()
    token = identity.CURRENT.set(actor()) if apply else None
    try:
        with activity.batch(out.batch) if apply else _nothing():
            tickets, wrote = None, True
            for i, rule in enumerate(use, start=1):
                if tickets is None or wrote:  # read the store again only after a rule wrote to it
                    tickets = store.tickets()
                c = Ctx(store, today, tickets, subtickets.Family(tickets), prs, {t.id: t for t in tickets}, touched)
                identity.CURRENT.set(actor(rule.name)) if apply else None
                with activity.because(f"rule {rule.name}"):
                    found = _run_rule(rule, c, apply, journal)
                out.items += found
                wrote = apply and any(x.changes and not x.skipped for x in found)
                if progress:
                    progress(i, len(use), rule.name, len([x for x in found if x.changes and not x.skipped]))
    finally:
        if token is not None:
            identity.CURRENT.reset(token)
        if journal is not None:
            journal.close(started_by)
    if prs.note:
        out.notes.append(prs.note)
    return out


class _nothing:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


def _run_rule(rule: Rule, c: Ctx, write: bool, journal) -> list[Item]:
    if rule.scheduled:
        key, due = period(rule, c.today)
        if c.today < due:
            return []
        if any(a.get("weekly_review") for a in rule.then):
            it = _weekly_review(rule, c, write, journal)
        else:
            it = _create(rule, c, key, write, journal)
        return [it] if it else []
    found = []
    for t in c.tickets:
        if t.status in CLOSED:
            continue
        ok, why, vals = match(rule, t, c)
        if not ok:
            continue
        work = t if write else copy.deepcopy(t)
        try:
            changes, skipped = _act(rule, work, vals, c, write, journal)
        except (RuleError, ValueError, OSError) as e:  # one ticket that cannot be written never stops the sweep
            changes, skipped = [], f"not changed: {e}"
        if changes and not skipped:
            c.touched.add(t.id)
        if changes or skipped:
            found.append(Item(rule.name, t.id, str(t.meta.get("title") or ""), "; ".join(why) or "it matches", changes, skipped))
    return found


# ---- undo ---------------------------------------------------------------------------------------

def undo(store: Store, batch: str) -> tuple[list[str], list[str]]:
    """Restore what one run changed. (restored ticket ids, the ones left alone with why). A ticket changed after the run is left alone,
    named, so nobody's later work is lost; a ticket the run created is closed as rejected with the reason (its record stays)."""
    if not re.fullmatch(r"b-[0-9a-f]{12}", batch or ""):
        raise AutomationError(f"`{batch}` is not a batch id; `drew auto run` prints it (b- and 12 letters or digits).")
    p = store.home / JOURNAL / f"{batch}.json"
    if not p.exists():
        gone = p.with_name(f"{batch}.undone.json").exists()
        raise AutomationError(f"Automation run {batch} was already undone." if gone else f"No automation run {batch} in this store.")
    data = json.loads(p.read_text(encoding="utf-8"))
    me = identity.CURRENT.get()
    who = me.name if me is not None else None
    done, kept = [], []
    for tid, row in data["tickets"].items():
        f = store.home / row["path"]
        now = f.read_bytes().decode("utf-8") if f.is_file() else None
        if now != row.get("after"):
            kept.append(f"{tid}: changed after the run, so it was left as it is; undo it by hand")
            continue
        if row["before"] is None:
            t = store.get(tid)
            if t.status not in CLOSED:
                ops.reject(store, t, f"Undone: created by automation run {batch}", actor=who)
            done.append(tid)
            continue
        old = Ticket.parse(row["before"], f)
        store.save(old, touch=False)
        done.append(tid)
    p.rename(p.with_name(f"{batch}.undone.json"))  # undone once; a second undo finds nothing to do
    return done, kept


# ---- the default rules file ---------------------------------------------------------------------

DEFAULT = """\
# Automation rules (DESK-0292). `drew auto list` shows them, `drew auto dry-run` says what each would change today,
# `drew auto run` runs them as Desk's own actor `automation` (never a person). Docs: docs/AUTOMATIONS.md in the Desk repo.
# A rule never changes a value a person set by hand unless it says `override: true`. Status moves follow the lifecycle rules.
# Pull request rules read GitHub with the gh command; list repositories as folders in this store or owner/name.
repos: ["."]
rules:
  - name: merged-pr-closes-ticket
    source: "standards rule 50 (merge after approval, then done with the commit); decision 62 (a pull request title starts with its id); idea 47"
    does: An approved ticket whose pull request is merged moves to done with the pull request link.
    when: {status: approved, pr_merged: true}
    then:
      - {move: done, link_pr: true}

  - name: merged-pr-waits-for-approval
    source: "DESK-0195 (the ticket follows the work); standards rule 50 (approval comes before done)"
    does: A pull request merged before approval. The ticket gets the tag pr-merged and a note, and the owner is told.
    when: {status: [claimed, in_progress, review, changes_requested], pr_merged: true, tags_none: [pr-merged]}
    then:
      - {add_tags: [pr-merged], comment: "Pull request merged ({pr}); it closes when approved (rule 50)."}
      - {notify: "{id} was merged before approval: {title}"}

  - name: ci-state-on-review
    source: "idea 48; standards rule 80 item 9 (CI must pass before merging); skill release"
    does: A ticket in review with an open pull request shows its CI state (green, red, pending or not run) in the ci field.
    when: {status: review, pr_open: true}
    then:
      - {ci: true}

  - name: subtickets-done-parent-to-review
    source: "idea 49; DESK-0208 (a main ticket waits on its subtickets); event log: 86 hand-written 'moved to review' notes"
    does: A main ticket in progress whose subtickets are all closed (at least one done) moves to review.
    when: {status: in_progress, subtickets_all_closed: true}
    then:
      - {move: review, note: "All {n} subtickets are closed, so this moved to review (rule {rule})."}

  - name: hard-deadline-two-days
    source: "idea 57; DESK-0193 (hard deadlines); DESK-0236 (the two-day window; the priority change waits on that choice); skill management (the Now lane)"
    does: A hard deadline two days away or missed. The ticket gets the tag deadline-near and goes to the Now lane (urgent work). The owner is told at once.
    when: {deadline_within_days: 2, not_status: [review, approved], tags_none: [deadline-near]}
    then:
      - {add_tags: [deadline-near], lane: now}
      - {notify: "{id}: hard deadline {due}. {title}", urgent: true}

  - name: repeated-ask-emergency
    source: "decision 60 and standards rule 10 (repetition is an emergency); DESK-0158"
    does: The owner asked for the same thing twice in a week. The ticket becomes p0 with the tag emergency, in the Now lane (urgent work).
    override: true
    when: {repeated_ask: true, tags_none: [emergency]}
    then:
      - {priority: p0, add_tags: [emergency], lane: now}
      - {notify: "Second ask on {id}: {title}", urgent: true}

  - name: review-waiting-a-week
    source: "skill management (the review queue's oldest item over seven days); rules.py REVIEW_MAX_DAYS"
    does: A ticket waits in review for more than a week. It gets the tag review-overdue and the owner is told once.
    when: {status: review, untouched_days: 7, tags_none: [review-overdue]}
    then:
      - {add_tags: [review-overdue], notify: "{id} has waited {days} days in review: {title}"}

  - name: approved-waits-to-ship
    source: "skill release (built is not delivered: shipped within one day of approval)"
    does: Approved over a day ago but not done (features, upgrades, bugs and releases). It gets the tag ship-pending and the owner is told once.
    when: {status: approved, type: [feature, upgrade, bug, release], untouched_days: 1, tags_none: [ship-pending]}
    then:
      - {add_tags: [ship-pending], notify: "{id} was approved {days} days ago and is not shipped: {title}"}

  - name: unblock-when-blocker-closes
    source: "standards rule 10 (blocked_on names what it waits on); event log: unblocked by hand 11 times"
    does: A ticket blocked on another ticket returns to where it was once that ticket is closed.
    when: {status: blocked, blocked_on_closed: true}
    then:
      - {unblock: true}

  - name: stale-label
    source: "DESK-0199 and idea 52 (label, then close); desk.toml [stale] days"
    does: An open ticket untouched for more than the store's stale days is tagged stale with a note.
    when: {untouched_days: store, not_status: [review, approved], tags_none: [stale]}
    then:
      - {add_tags: [stale], comment: "Untouched for {days} days, so it is labelled stale; it closes after as many days again unless someone touches it (not while it is blocked, claimed or in progress)."}

  - name: stale-close
    source: "DESK-0199 (close with a reason; hard deadlines, review and the owner's tickets are never auto-closed); standards rule 10 (blocked_on says what a ticket waits on)"
    does: A ticket marked stale stays untouched as long again. It closes as rejected, with the reason. The owner's tickets, reviews, hard deadlines and tickets someone is working on or waiting on stay open.
    when: {tags_any: [stale], untouched_days: store, not_status: [review, approved, blocked, claimed, in_progress], no_hard_deadline: true, owner_not: [owner]}
    then:
      - {move: rejected, reason: "Untouched for {days} days after it was labelled stale (rule {rule}); reopen it with a comment if it still matters."}

  - name: weekly-review-monday
    source: "decision 24 (one ticket review per ISO week); idea 56; skill management (the weekly review carries the dashboard)"
    does: On Monday (or the first sweep after it) the week's ticket review is created and synced if it does not exist.
    every: weekly
    day: monday
    create: {project: management}
    then:
      - {weekly_review: true}

  - name: monthly-recurring-example
    source: "idea 55 and DESK-0047 (recurring tickets from a template); decision 94 (repeatable processes)"
    does: An example of a recurring ticket. Turn it on and change the template to create one ticket each month.
    enabled: false
    every: monthly
    day: 1
    create:
      project: management
      title: "Do: monthly housekeeping {period}"
      type: task
      priority: p2
      assignee: main
      tags: [repeatable-process]
      sections:
        Outcome: "The store is tidy for {period}: no duplicates, nothing left behind (skill housekeeping)."
    then: []
"""


def init(home: Path) -> Path:
    p = path(home)
    if p.exists():
        raise AutomationError(f"{p} already exists; edit it instead. Nothing was written.")
    p.write_bytes(DEFAULT.encode("utf-8"))
    return p


def describe_when(rule: Rule) -> list[str]:
    if rule.scheduled:
        return [f"every {rule.every.removesuffix('ly') if rule.every != 'monthly' else 'month'} on {rule.on or 'the first day'}"]
    out = []
    for k, v in rule.when.items():
        v = ", ".join(map(str, v)) if isinstance(v, list) else v
        out.append(f"{k.replace('_', ' ')}: {v}")
    return out


def describe_then(rule: Rule) -> list[str]:
    out = []
    for a in rule.then:
        out.append(", ".join(f"{k.replace('_', ' ')}: {', '.join(map(str, v)) if isinstance(v, list) else v}" for k, v in a.items()))
    return out
