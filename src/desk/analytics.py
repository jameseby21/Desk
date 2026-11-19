"""The numbers behind /analytics (DESK-0336): what moved, who is active, what is waiting, how fast, what is happening now, and how well
the checkers say the system is doing. Every number is counted from the store's own files and nothing is written:

* tickets/*.md: status, created, assignee, tags and the dates in the front matter;
* events/<ID>.jsonl: the append-only activity log (status and assignee changes, comments, who did them and when);
* intake/: the checker and audit reports, read for the verdict or false-claim line they state;
* the store's git history, for the latest commit.

Commits that `drew migrate events` copied out of git history (`change: git`) are not counted as ticket activity: they record that a
file was saved, not that a person or an agent worked on the ticket.

Docs: docs/COMMANDS.md (/analytics). One owner per number: a test recomputes each from the files without calling this module."""

from __future__ import annotations

import datetime as dt
import json
import re
import statistics
import subprocess
import threading
import time
from pathlib import Path

from . import activity
from .ticket import CLOSED, STATUSES

DAYS = 30
STUCK_DAYS = 7
IDLE_DAYS = 7
WEEKS = 4
TOP_PROJECTS = 10
OLDEST = 10
LIVE_HOURS = 1
LIVE_MAX = 40
META_GLOBS = ("qc", "claims-audit", "triage")
PATCH_ACTORS = {"claude-code", "main", "desk", "patch"}
AGENT_ACTORS = {"qc-checker"}
COWORKERS = (("chaz", "Chaz"), ("em", "Em"), ("joe", "Joe"), ("lily", "Lily"), ("bri", "Bri"), ("bigd", "Big D"))
OPEN_STATUSES = [s for s in STATUSES if s not in CLOSED]
_ID = re.compile(r"\b([A-Za-z][A-Za-z0-9]*-\d+)\b")


# -- reading ------------------------------------------------------------------------------

def parse_ts(value) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(value)[:19])
    except ValueError:
        return None


def parse_date(value) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def load_events(home: Path) -> dict[str, list]:
    """{ticket id: [Event, ...]} for every log file in the store, in the order written."""
    out: dict[str, list] = {}
    d = Path(home) / activity.DIR
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.jsonl")):
        evs = activity.read(Path(home), p.stem)
        if evs:
            out[p.stem.upper()] = evs
    return out


def counted(e) -> bool:
    """An event that shows someone worked on a ticket (a commit copied out of git history does not)."""
    return e.change != "git" and parse_ts(e.ts) is not None


def day_list(today: dt.date, n: int = DAYS) -> list[dt.date]:
    return [today - dt.timedelta(days=n - 1 - i) for i in range(n)]


def _status_events(evs: list) -> list:
    return sorted((e for e in evs if e.change == "field" and e.field == "status" and parse_ts(e.ts)), key=lambda e: (parse_ts(e.ts), e.seq))


def closed_when(t, evs: list) -> tuple[dt.datetime | None, str]:
    """When a closed ticket was closed, and where that came from: the log (`log`), its done_at date (`done_at`), or its last update
    (`updated`, the least exact). An open ticket has no close time."""
    if t.status not in CLOSED:
        return None, ""
    closes = [e for e in _status_events(evs) if e.new in CLOSED]
    if closes:
        return parse_ts(closes[-1].ts), "log"
    for key, src in (("done_at", "done_at"), ("updated", "updated")):
        d = parse_date(t.meta.get(key))
        if d:
            return dt.datetime.combine(d, dt.time(12)), src
    return None, ""


def created_date(t, evs: list) -> dt.date | None:
    d = parse_date(t.meta.get("created"))
    if d:
        return d
    for e in evs:
        if e.change == "created" and parse_ts(e.ts):
            return parse_ts(e.ts).date()
    return None


# -- 1. flow ------------------------------------------------------------------------------

def flow(tickets: list, events: dict, today: dt.date) -> dict:
    days = day_list(today)
    created = {d: 0 for d in days}
    closed = {d: 0 for d in days}
    approx = 0
    for t in tickets:
        evs = events.get(t.id.upper(), [])
        cd = created_date(t, evs)
        if cd in created:
            created[cd] += 1
        when, src = closed_when(t, evs)
        if when and when.date() in closed:
            closed[when.date()] += 1
            approx += src != "log"
    open_t = [t for t in tickets if t.is_open]
    by_status = {s: 0 for s in OPEN_STATUSES}
    for t in open_t:
        by_status[t.status] = by_status.get(t.status, 0) + 1
    by_status = {k: v for k, v in by_status.items() if v}
    proj: dict[str, int] = {}
    for t in open_t:
        proj[t.project or "(none)"] = proj.get(t.project or "(none)", 0) + 1
    ranked = sorted(proj.items(), key=lambda kv: (-kv[1], kv[0]))
    top, rest = ranked[:TOP_PROJECTS], ranked[TOP_PROJECTS:]
    return {"days": [{"date": d.isoformat(), "created": created[d], "closed": closed[d]} for d in days],
            "created_total": sum(created.values()), "closed_total": sum(closed.values()), "closed_from_file_dates": approx,
            "open_total": len(open_t), "by_status": by_status,
            "by_project": [{"project": k, "n": v} for k, v in top], "other_projects": len(rest), "other_n": sum(v for _, v in rest)}


# -- 2. people ----------------------------------------------------------------------------

def person_of(e, owner: str) -> tuple[str, str]:
    """(group key, label) for an event's actor: the owner, Patch, a coworker, the agents, or an other name."""
    a = str(e.actor or "").strip().lower()
    if a == owner.lower():
        return "owner", owner.capitalize()
    if a in PATCH_ACTORS:
        return "patch", "Patch"
    for k, label in COWORKERS:
        if a == k:
            return k, label
    if a in AGENT_ACTORS or a.startswith("subagent:") or a.startswith("subagent-") or e.kind == "subagent":
        return "agents", "Agents (subagents and the checker)"
    return "other", "Other names"


def people(events: dict, owner: str, now: dt.datetime) -> dict:
    today = now.date()
    order = [("owner", owner.capitalize()), ("patch", "Patch")] + list(COWORKERS) + [("agents", "Agents (subagents and the checker)")]
    rows = {k: {"key": k, "label": label, "last": None, "last_ticket": "", "weeks": [0] * WEEKS, "total": 0} for k, label in order}
    other = {"key": "other", "label": "Other names", "last": None, "last_ticket": "", "weeks": [0] * WEEKS, "total": 0}
    for tid, evs in events.items():
        for e in evs:
            if not counted(e):
                continue
            ts = parse_ts(e.ts)
            if ts > now:
                continue
            key, _ = person_of(e, owner)
            row = rows.get(key, other)
            if row["last"] is None or ts > row["last"]:
                row["last"], row["last_ticket"] = ts, tid
            w = (today - ts.date()).days // 7
            if w < WEEKS:
                row["weeks"][WEEKS - 1 - w] += 1  # oldest week first, this week last
            row["total"] += 1
    out = [rows[k] for k, _ in order]
    if other["total"]:
        out.append(other)
    cutoff = now - dt.timedelta(days=IDLE_DAYS)
    for r in out:
        r["idle"] = r["last"] is None or r["last"] < cutoff
        r["last_text"] = r["last"].strftime("%Y-%m-%d %H:%M") if r["last"] else ""
    idle = [r["label"] for r in out if r["idle"] and r["key"] != "other"]
    return {"rows": out, "idle": idle, "idle_days": IDLE_DAYS}


def idle_sentence(idle: list[str], days: int = IDLE_DAYS) -> str:
    if not idle:
        return f"Everyone listed touched a ticket in the last {days} days."
    names = ", ".join(idle[:-1]) + (" and " if len(idle) > 1 else "") + idle[-1]
    return f"No ticket activity in the last {days} days from {names}."


# -- 3. waiting ---------------------------------------------------------------------------

def _assignee_events(evs: list) -> list:
    return sorted((e for e in evs if e.change == "field" and e.field == "assignee" and parse_ts(e.ts)), key=lambda e: (parse_ts(e.ts), e.seq))


def _norm(v) -> str:
    return str(v or "").strip().lower()


def assignee_at(t, ae: list, when: dt.datetime) -> str:
    """Who held the ticket at `when`: the logged changes replayed; before the first logged change, the old value of that change;
    with no logged change at all, today's assignee."""
    if not ae:
        return _norm(t.meta.get("assignee"))
    cur = _norm(ae[0].old)
    for e in ae:
        if parse_ts(e.ts) <= when:
            cur = _norm(e.new)
        else:
            break
    return cur


def open_at(t, evs: list, when: dt.datetime) -> bool:
    cd = created_date(t, evs)
    if cd and dt.datetime.combine(cd, dt.time()) > when:
        return False
    se = _status_events(evs)
    if se:
        cur = se[0].old
        for e in se:
            if parse_ts(e.ts) <= when:
                cur = e.new
            else:
                break
        return cur not in CLOSED
    if t.status in CLOSED:
        c, _ = closed_when(t, evs)
        return not (c and c <= when)
    return True


def waiting(tickets: list, events: dict, owner: str, now: dt.datetime) -> dict:
    owner = owner.lower()
    today = now.date()
    mine = []
    for t in tickets:
        evs = events.get(t.id.upper(), [])
        ae = _assignee_events(evs)
        if _norm(t.meta.get("assignee")) == owner or any(owner in (_norm(e.old), _norm(e.new)) for e in ae):
            mine.append((t, evs, ae))
    series = []
    for d in day_list(today):
        end = dt.datetime.combine(d, dt.time(23, 59, 59))
        end = min(end, now)
        series.append({"date": d.isoformat(), "n": sum(1 for t, evs, ae in mine if assignee_at(t, ae, end) == owner and open_at(t, evs, end))})
    logged = [parse_ts(e.ts) for evs in events.values() for e in evs if e.change == "field" and e.field == "assignee" and parse_ts(e.ts)]
    first_logged = min(logged).date().isoformat() if logged else ""
    queue = []
    for t, evs, ae in mine:
        if not t.is_open or _norm(t.meta.get("assignee")) != owner:
            continue
        since = None
        to_owner = [parse_ts(e.ts) for e in ae if _norm(e.new) == owner]
        if to_owner:
            since = max(to_owner)
        else:
            since = parse_ts(t.meta.get("assigned_at")) or (dt.datetime.combine(created_date(t, evs), dt.time()) if created_date(t, evs) else None)
        queue.append({"id": t.id, "title": str(t.meta.get("title") or ""), "status": t.status, "since": since.isoformat() if since else "",
                      "days": (now - since).days if since else None})
    queue.sort(key=lambda r: (r["since"] or "9999", r["id"]))
    stuck: dict[str, list] = {}
    cutoff = now - dt.timedelta(days=STUCK_DAYS)
    for t in tickets:
        if not t.is_open:
            continue
        evs = events.get(t.id.upper(), [])
        se = _status_events(evs)
        if se:
            since, src = parse_ts(se[-1].ts), "log"
        else:
            d = parse_date(t.meta.get("updated")) or created_date(t, evs)
            since, src = (dt.datetime.combine(d, dt.time()) if d else None), "updated"
        if since and since <= cutoff:
            stuck.setdefault(t.status, []).append({"id": t.id, "days": (now - since).days, "src": src})
    stuck_rows = []
    for st in OPEN_STATUSES:
        items = sorted(stuck.get(st, []), key=lambda r: (-r["days"], r["id"]))
        if items:
            stuck_rows.append({"status": st, "n": len(items), "oldest": items[:3]})
    return {"queue_series": series, "queue_now": sum(1 for t, evs, ae in mine if t.is_open and _norm(t.meta.get("assignee")) == owner),
            "first_logged": first_logged, "oldest": queue[:OLDEST], "stuck": stuck_rows, "stuck_total": sum(r["n"] for r in stuck_rows),
            "stuck_days": STUCK_DAYS}


# -- 4. speed -----------------------------------------------------------------------------

def speed(tickets: list, events: dict, now: dt.datetime) -> dict:
    """Days from the latest entry to `ready` to the next entry to `review`, and from the latest entry to `review` to the entry to
    `done`, for moves that happened in the last 30 days. Only what the log shows is used, so a ticket that moved before the log began
    is not in either median."""
    since = now - dt.timedelta(days=DAYS)
    r2r, r2d = [], []
    for t in tickets:
        se = _status_events(events.get(t.id.upper(), []))
        ready_at = review_at = None
        for e in se:
            ts = parse_ts(e.ts)
            if e.new == "ready":
                ready_at = ts
            elif e.new == "review":
                if ready_at is not None and ts >= since:
                    r2r.append((ts - ready_at).total_seconds() / 86400)
                review_at, ready_at = ts, None
            elif e.new == "done":
                if review_at is not None and ts >= since:
                    r2d.append((ts - review_at).total_seconds() / 86400)
                review_at = None

    def med(xs):
        return round(statistics.median(xs), 2) if xs else None

    return {"ready_to_review": med(r2r), "ready_to_review_n": len(r2r), "review_to_done": med(r2d), "review_to_done_n": len(r2d)}


# -- 5. live monitoring -------------------------------------------------------------------

def recent_events(home: Path, now: dt.datetime, hours: int = LIVE_HOURS) -> list[tuple[str, object]]:
    """Events in the last `hours` hours, newest first, as (ticket id, Event). A log file is only opened when it was written to in the
    window (its modified time), so this stays cheap on a store with thousands of logs."""
    d = Path(home) / activity.DIR
    if not d.is_dir():
        return []
    start = now - dt.timedelta(hours=hours)
    floor = (min(now, dt.datetime.now()) - dt.timedelta(hours=hours, minutes=5)).timestamp()
    out = []
    for p in d.glob("*.jsonl"):
        try:
            if p.stat().st_mtime < floor:
                continue
        except OSError:
            continue
        for e in activity.read(Path(home), p.stem):
            ts = parse_ts(e.ts)
            if counted(e) and ts and start <= ts <= now:
                out.append((p.stem.upper(), e))
    out.sort(key=lambda x: (parse_ts(x[1].ts), x[1].seq), reverse=True)
    return out


def last_event(home: Path) -> tuple[str, object] | None:
    """The newest counted event anywhere, from the five most recently written logs."""
    d = Path(home) / activity.DIR
    if not d.is_dir():
        return None
    files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]
    best = None
    for p in files:
        for e in activity.read(Path(home), p.stem):
            if counted(e) and (best is None or parse_ts(e.ts) >= parse_ts(best[1].ts)):
                best = (p.stem.upper(), e)
    return best


def _run_git(home: Path, *args: str) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=home, capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


_GIT_CACHE: dict[str, tuple[float, dict]] = {}
_GIT_TTL = 30.0


def store_git(home: Path, n: int = 5) -> dict:
    """The store's newest commits: {'latest': {...} or None, 'commits': [...]}, cached for 30 seconds."""
    key = str(Path(home).resolve())
    hit = _GIT_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < _GIT_TTL:
        return hit[1]
    text = _run_git(Path(home), "log", f"-{n}", "--format=%h%x1f%cI%x1f%s")
    commits = []
    for line in text.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 3:
            commits.append({"sha": parts[0], "when": parts[1][:16].replace("T", " "), "subject": parts[2][:120]})
    res = {"latest": commits[0] if commits else None, "commits": commits}
    _GIT_CACHE[key] = (time.monotonic(), res)
    return res


class Validation:
    """`drew validate` takes seconds on a big store, so a page never waits for it: the count is computed on a background thread, kept for
    five minutes, and the page shows it as soon as it exists (the live poll picks it up)."""

    TTL = 300.0

    def __init__(self, runner=None):
        self._runner = runner
        self._lock = threading.Lock()
        self._state: dict[str, dict] = {}

    def _run(self, store):
        runner = self._runner
        if runner is None:
            from .rules import validate_store as runner  # imported late: the rules module is large
        key = str(store.home)
        try:
            errors, warnings = runner(store)
            res = {"errors": len(errors), "warnings": len(warnings), "at": dt.datetime.now().isoformat(timespec="seconds"), "running": False, "failed": ""}
        except Exception as exc:  # a broken store must not break the page; say what happened
            res = {"errors": None, "warnings": None, "at": dt.datetime.now().isoformat(timespec="seconds"), "running": False, "failed": str(exc)[:120]}
        with self._lock:
            self._state[key] = {**res, "mono": time.monotonic()}

    def get(self, store, wait: bool = False) -> dict:
        key = str(store.home)
        with self._lock:
            cur = self._state.get(key)
            fresh = cur is not None and not cur.get("running") and time.monotonic() - cur["mono"] < self.TTL
            if cur is not None and cur.get("running"):
                return {**cur}
            if not fresh:
                self._state[key] = {**(cur or {"errors": None, "warnings": None, "at": "", "failed": ""}), "running": True, "mono": time.monotonic()}
                start = True
            else:
                start = False
        if start:
            if wait:
                self._run(store)
            else:
                threading.Thread(target=self._run, args=(store,), daemon=True, name="desk-analytics-validate").start()
        with self._lock:
            return {**self._state[key]}


VALIDATION = Validation()


def known_ids(home: Path, texts: list[str]) -> set[str]:
    """Which ticket ids named in `texts` have a ticket file (a glob per id, so a poll never reads every ticket)."""
    found = {m.group(1).upper() for t in texts for m in _ID.finditer(t or "")}
    d = Path(home) / "tickets"
    return {i for i in found if any(d.glob(f"{i}-*.md"))}


def parts(text: str, ids: set) -> list[dict]:
    return [{"t": piece, "id": is_id} for piece, is_id in link_ids(text, ids)]


def live(store, now: dt.datetime | None = None, validation: Validation | None = None, wait: bool = False) -> dict:
    now = now or dt.datetime.now()
    evs = recent_events(store.home, now)
    rows = [{"time": parse_ts(e.ts).strftime("%H:%M:%S"), "ts": e.ts, "actor": e.actor, "who": person_of(e, store.owner)[1] if person_of(e, store.owner)[0] != "other" else str(e.actor),
             "ticket": tid, "what": (e.what or f"{e.change} {e.field}")[:140]} for tid, e in evs[:LIVE_MAX]]
    last = last_event(store.home)
    last_row = None
    if last:
        tid, e = last
        last_row = {"ts": e.ts, "actor": e.actor, "ticket": tid}
    g = store_git(store.home)
    ids = known_ids(store.home, [r["what"] for r in rows] + [c["subject"] for c in g["commits"]])
    for r in rows:
        r["parts"] = parts(r["what"], ids)
    commits = [{**c, "parts": parts(c["subject"], ids)} for c in g["commits"]]
    v = (validation or VALIDATION).get(store, wait=wait)
    from . import version as ver

    return {"now": now.isoformat(timespec="seconds"), "hours": LIVE_HOURS, "count": len(evs), "events": rows, "more": max(0, len(evs) - LIVE_MAX),
            "last": last_row, "quiet": quiet_sentence(len(evs), last_row, now),
            "desk": ver.describe(), "store_commit": g["latest"], "commits": commits, "validate": v, "validate_text": validate_text(v)}


def validate_text(v: dict) -> str:
    if v.get("running") and v.get("errors") is None:
        return "Checking now; this takes several seconds on a large store and shows here when it finishes."
    if v.get("failed"):
        return f"The check could not run: {v['failed']}"
    if v.get("errors") is None:
        return "Not checked yet."
    when = f" (checked {v['at'][11:16]})" if v.get("at") else ""
    return f"{v['errors']} error{'s' if v['errors'] != 1 else ''}, {v['warnings']} warning{'s' if v['warnings'] != 1 else ''}{when}"


def quiet_sentence(n: int, last: dict | None, now: dt.datetime) -> str:
    if n:
        return f"{n} event{'s' if n != 1 else ''} in the last hour."
    if not last:
        return "Nothing happened in the last hour, and the store has no recorded events yet."
    ts = parse_ts(last["ts"])
    return f"Nothing happened in the last hour. The last event was {last['actor']} on {last['ticket']} at {ts:%Y-%m-%d %H:%M}."


# -- 6. meta-analysis ---------------------------------------------------------------------

_VERDICT = re.compile(r"^\W*(?:verdict|result)\b[^:\n]*:\W*(.+)$", re.I)
_RATE = re.compile(r"^\W*(?:false[- ]?(?:claim )?rate|false)\b[^:\n]*:\W*(.+)$", re.I)


def _plain(s: str) -> str:
    s = re.sub(r"[*_`]+", "", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)
    return re.sub(r"\s+", " ", s).strip(" .")


def report_lines(text: str) -> tuple[str, str]:
    """(verdict line, false-claim rate line) as the report states them, each '' when the report has none."""
    verdict = rate = ""
    for line in text.splitlines():
        s = line.strip().lstrip("-*#>• ").strip()
        if not verdict:
            m = _VERDICT.match(s.replace("**", ""))
            if m:
                verdict = _plain(m.group(1))[:160]
        if not rate:
            m = _RATE.match(s.replace("**", ""))
            if m:
                rate = _plain(m.group(1))[:160]
        if verdict and rate:
            break
    return verdict, rate


def meta(store, tickets: list) -> dict:
    d = store.intake_dir
    files = []
    if d.is_dir():
        for p in d.rglob("*.md"):
            low = p.name.lower()
            if any(g in low for g in META_GLOBS):
                files.append(p)
    rows = []
    for p in files:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", p.relative_to(store.home).as_posix())  # the date in the file name, or in its folder's name
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            when = m.group(1) if m else dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")
        except OSError:
            continue
        title = next((_plain(ln.lstrip("# ")) for ln in text.splitlines() if ln.startswith("#")), p.stem)
        verdict, rate = report_lines(text)
        rows.append({"file": p.name, "rel": p.relative_to(store.home).as_posix(), "date": when, "title": title[:120], "verdict": verdict, "rate": rate})
    rows.sort(key=lambda r: (r["date"], r["file"]), reverse=True)
    rep = [t for t in tickets if t.is_open and "repetition" in [x.strip().lower() for x in t.tags]]
    return {"reports": rows, "repetition_open": len(rep), "repetition_ids": sorted(t.id for t in rep)[:12]}


# -- the whole page -----------------------------------------------------------------------

def build(store, now: dt.datetime | None = None, tickets: list | None = None, events: dict | None = None) -> dict:
    now = now or dt.datetime.now()
    tickets = store.tickets() if tickets is None else tickets
    events = load_events(store.home) if events is None else events
    return {"now": now.isoformat(timespec="seconds"), "n_tickets": len(tickets), "n_events": sum(1 for evs in events.values() for e in evs if counted(e)),
            "flow": flow(tickets, events, now.date()), "people": people(events, store.owner, now),
            "waiting": waiting(tickets, events, store.owner, now), "speed": speed(tickets, events, now), "meta": meta(store, tickets),
            "ids": {t.id.upper() for t in tickets}}


def link_ids(text: str, ids: set) -> list[tuple[str, bool]]:
    """Split `text` into (piece, is_a_known_ticket_id) so a template can link every id that exists."""
    out, at = [], 0
    for m in _ID.finditer(text or ""):
        if m.group(1).upper() in ids:
            if m.start() > at:
                out.append((text[at:m.start()], False))
            out.append((m.group(1).upper(), True))
            at = m.end()
    if at < len(text or ""):
        out.append(((text or "")[at:], False))
    return out


def to_json(obj) -> str:
    return json.dumps(obj, default=str)
