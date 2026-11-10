"""Needs me (DESK-0149): only the tickets that carry an ask for the owner, or that someone else changed since the owner last acted on
them. Opening a ticket never counts as acting. Used by Today's first block, the board chip and `drew needs-me`.

An ask: a decision ticket the owner holds, a ticket in review that only the owner may approve, or a ticket the owner holds to do.
A change: after the owner's last action on a ticket (a comment, a decision or a checked line of his, or Mark seen) someone else posted
a comment, checked a line, or changed the status, the ask, Done means, the Report or the proof. A ticket the owner never acted on has
no baseline, so it shows only when it carries an ask. `.desk/needs-me.json` remembers, per ticket, when the owner last acted and
what the ticket looked like then."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import asks as asksmod
from .ops import _COMMENT_LINE
from .store import Store
from .ticket import Ticket, parse_checks

SHOWN = 5  # DESK-0149 (2026-11-10 review): Today draws this many asks and this many changes, then "N more" opens the board's Needs me view

STATE_FILE = "needs-me.json"
CHECK_SECTIONS = ("Acceptance", "How to validate", "Prompt", "Evidence")
_DECISION_LINE = re.compile(r"^- (\d{4}-\d{2}-\d{2}) · (.+?) · ")
SLACK = dt.timedelta(minutes=2)  # a write follows the minute stamped on its own line by up to a minute

CHIP = {"approve": "Approve this", "choose": "Choose one", "answer": "Answer this", "provide": "Provide this", "do": "Do a step"}
ACT = {"approve": "Review", "choose": "Choose", "answer": "Answer", "provide": "Provide", "do": "Start"}
EMPTY = {
    "plain": ("Nothing needs you", "Every open ticket has someone else on it. New asks and changes show up here."),
    "dogs": ("Nothing to fetch for you", "Drew has every ball covered. New asks and changes show up here."),
    "cat": ("Nothing on your doorstep", "Soot has it handled. New asks and changes show up here."),
}


@dataclass
class Item:
    ticket: Ticket
    group: str  # "ask" or "change"
    kind: str  # approve, choose, answer, provide, do; "change" for a change
    chip: str
    why: str  # the reason line, word for word
    act: str  # the main button: Review, Choose, Start, ...
    overdue: bool = False
    when: dt.datetime | None = None
    emergency: bool = False
    parts: list = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.ticket.id


# ---- who and what ------------------------------------------------------------------------------------------------------------

def person(store: Store, who: str | None = None) -> str:
    return (who or store.owner).strip().lower()


def display(name: str) -> str:
    """How a person is named on screen: `main` is Patch, the rest capitalised."""
    n = (name or "").strip()
    return "Patch" if n.lower() in ("main", "patch") else (n[:1].upper() + n[1:])


def clock(when: dt.datetime, now: dt.datetime | None = None) -> str:
    """`4:55 AM`; with the date when it is not today (`Nov 7, 4:55 AM`)."""
    now = now or dt.datetime.now()
    t = when.strftime("%I:%M %p").lstrip("0")
    return t if when.date() == now.date() else f"{when.strftime('%b')} {when.day}, {t}"


def _day(d: dt.date, now: dt.datetime) -> str:
    """`Mon` when the date is within the coming week, else `Nov 10`."""
    delta = (d - now.date()).days
    return d.strftime("%a") if 0 <= delta < 7 else f"{d.strftime('%b')} {d.day}"


def _hand_from(t: Ticket, who: str) -> str:
    for k in ("assignee", "owner"):
        v = str(t.meta.get(k) or "").strip()
        if v and v.lower() != who:
            return display(v)
    return "Patch"


# ---- state ------------------------------------------------------------------------------------------------------------------

def _path(store: Store) -> Path:
    return store.home / ".desk" / STATE_FILE


def _load(store: Store) -> dict:
    try:
        data = json.loads(_path(store).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(store: Store, data: dict) -> None:
    p = _path(store)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def _h(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:12]


def signature(t: Ticket) -> dict:
    """What a change of status, ask, Done means, checks, Report or proof looks like. Titles of comments are not in it (comments have times)."""
    m = t.meta
    return {
        "status": t.status,
        "ask": _h("|".join(str(m.get(k) or "") for k in ("owner", "type", "title", "review_by")) + "|" + ",".join(map(str, m.get("dod") or []))),
        "report": _h(t.section("Report")),
        "proof": _h(t.section("Evidence") + "|" + str(m.get("evidence") or "") + "|" + t.section("Attachments")),
        "checks": "".join(c.state for sec in ("Acceptance", "How to validate", "Prompt") for c in parse_checks(t.sections.get(sec) or "")),
        "done_means": _h(",".join(map(str, m.get("dod") or []))),
    }


def record_action(store: Store, t: Ticket, actor: str | None, now: dt.datetime | None = None) -> None:
    """The owner just acted on `t` (a comment, a decision, a checked line, a move): remember when and what it looked like."""
    if not actor or actor.strip().lower() != store.owner.lower():
        return
    mark(store, [t], now)


def mark(store: Store, tickets: list[Ticket], now: dt.datetime | None = None) -> int:
    now = (now or dt.datetime.now()).replace(microsecond=0)
    data = _load(store)
    seen = data.setdefault("seen", {})
    for t in tickets:
        seen[t.id] = {"at": now.isoformat(), "sig": signature(t)}
    _save(store, data)
    return len(tickets)


# ---- events in a ticket's file ------------------------------------------------------------------------------------------------

def _comment_rows(t: Ticket) -> list[tuple[dt.datetime, str]]:
    out = []
    for line in t.section("Comments").splitlines():
        m = _COMMENT_LINE.match(line)
        if not m:
            continue
        try:
            out.append((dt.datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}"), m.group(3).strip()))
        except ValueError:
            continue
    return out


def _check_rows(t: Ticket) -> list[tuple[dt.datetime, str]]:
    out = []
    for sec in CHECK_SECTIONS:
        for c in parse_checks(t.sections.get(sec) or ""):
            if c.who and c.when:
                try:
                    out.append((dt.datetime.fromisoformat(c.when), c.who))
                except ValueError:
                    pass
    return out


def _decision_rows(t: Ticket) -> list[tuple[dt.date, str]]:
    out = []
    for line in t.section("Decision").splitlines():
        m = _DECISION_LINE.match(line)
        if m:
            try:
                out.append((dt.date.fromisoformat(m.group(1)), m.group(2).strip()))
            except ValueError:
                pass
    return out


def same_person(a: str, b: str) -> bool:
    from .people import same_person as sp

    return sp(a, b)


def last_action(t: Ticket, who: str, state: dict) -> dt.datetime | None:
    """When `who` last acted on the ticket: his latest comment, checked line or decision, or the time he pressed Mark seen."""
    times = [w for w, a in _comment_rows(t) if same_person(a, who)]
    times += [w for w, a in _check_rows(t) if same_person(a, who)]
    times += [dt.datetime.combine(d, dt.time.max.replace(microsecond=0)) for d, a in _decision_rows(t) if same_person(a, who)]
    rec = (state.get("seen") or {}).get(t.id)
    if rec:
        try:
            times.append(dt.datetime.fromisoformat(rec["at"]))
        except (KeyError, ValueError):
            pass
    return max(times) if times else None


def _names(people: list[str]) -> str:
    shown = []
    for p in people:
        if display(p) not in shown:
            shown.append(display(p))
    return " and ".join(shown) if len(shown) <= 2 else ", ".join(shown[:-1]) + " and " + shown[-1]


def changes_of(store: Store, t: Ticket, who: str, state: dict, now: dt.datetime) -> Item | None:
    last = last_action(t, who, state)
    if last is None or not t.is_open:
        return None
    parts: list[tuple[int, str]] = []  # (order, words)
    latest = last
    comments = [(w, a) for w, a in _comment_rows(t) if w > last and not same_person(a, who)]
    if comments:
        n = len(comments)
        parts.append((0, f"{n} comment{'s' if n > 1 else ''} by {_names([a for _, a in comments])}"))
        latest = max(latest, max(w for w, _ in comments))
    checks = [(w, a) for w, a in _check_rows(t) if w > last and not same_person(a, who)]
    if checks:
        parts.append((1, f"checks changed by {_names([a for _, a in checks])}"))
        latest = max(latest, max(w for w, _ in checks))
    decisions = [(d, a) for d, a in _decision_rows(t) if d > last.date() and not same_person(a, who)]
    if decisions:
        parts.append((2, f"decision recorded by {_names([a for _, a in decisions])}"))
    snap = ((state.get("seen") or {}).get(t.id) or {}).get("sig")
    actor = _hand_from(t, who)
    mtime = None
    try:
        mtime = dt.datetime.fromtimestamp(t.path.stat().st_mtime).replace(microsecond=0) if t.path else None
    except OSError:
        pass
    if snap:
        now_sig = signature(t)
        words = {"status": f"status changed to {t.status}", "ask": "the ask changed", "report": "report updated", "proof": "proof added",
                 "checks": "a line checked or unchecked", "done_means": "Done means edited"}
        for order, key in enumerate(("status", "ask", "report", "proof", "checks", "done_means"), start=3):
            if key == "checks" and checks:
                continue  # already said, with a name
            if key == "done_means" and now_sig["ask"] != snap.get("ask"):
                continue  # the ask hash already covers dod
            if now_sig[key] != snap.get(key):
                parts.append((order, f"{words[key]} by {actor}"))
                if mtime and mtime > latest:
                    latest = mtime
    else:
        upd = str(t.meta.get("updated") or "")[:10]
        if not parts and upd > last.date().isoformat():
            parts.append((9, f"ticket updated by {actor}"))
            if mtime and mtime > latest:
                latest = mtime
    if not parts:
        return None
    parts.sort()
    words = ", ".join(w for _, w in parts)
    return Item(t, "change", "change", "Changed", f"changed: {words} · {clock(latest, now)}", "Open", when=latest, parts=[w for _, w in parts])


# ---- asks ----------------------------------------------------------------------------------------------------------------------

def ask_of(store: Store, t: Ticket, who: str, now: dt.datetime) -> Item | None:
    if not t.is_open or t.status == "blocked":
        return None
    mine = str(t.meta.get("owner") or "").strip().lower() == who
    kind = None
    options = 0
    a = asksmod.ask_of(t)
    if a and mine and t.status in ("inbox", "triage", "ready", "review"):
        kind, options = a["kind"], len(a["options"])
    elif t.status == "review" and who == store.owner.lower() and not store.delegated(t):
        kind = "approve"
    elif mine and t.status in ("inbox", "triage", "ready", "claimed", "in_progress", "changes_requested"):
        kind = "do"
    if kind is None:
        return None
    bits = [f"ask: {kind}"]
    if kind == "choose" and options:
        bits.append(f"{options} options")
    elif kind == "do":
        steps = len(parse_checks(t.section("Acceptance")))
        if steps:
            bits.append(f"{steps} step{'s' if steps > 1 else ''}")
        bits.append(f"from {_hand_from(t, who)}")
    else:
        bits.append(f"from {_hand_from(t, who)}")
    overdue = False
    due = str(t.meta.get("review_by") or "")[:10]
    if due:
        try:
            d = dt.date.fromisoformat(due)
            overdue = d < now.date()
            bits.append(f"due {_day(d, now)}" + (" overdue" if overdue else ""))
        except ValueError:
            pass
    return Item(t, "ask", kind, CHIP[kind], " · ".join(bits), ACT[kind], overdue=overdue)


# ---- the list ------------------------------------------------------------------------------------------------------------------

def build(store: Store, who: str | None = None, now: dt.datetime | None = None, tickets: list[Ticket] | None = None) -> list[Item]:
    """Asks first (emergencies, then overdue, then by due date and priority), then changes (newest first)."""
    now = now or dt.datetime.now()
    who = person(store, who)
    state = _load(store)
    asks, changes = [], []
    for t in (tickets if tickets is not None else store.tickets()):
        a = ask_of(store, t, who, now)
        if a:
            asks.append(a)
        else:
            c = changes_of(store, t, who, state, now)
            if c:
                changes.append(c)
    for it in asks + changes:
        it.emergency = "emergency" in it.ticket.tags
    asks.sort(key=lambda i: (not i.emergency, not i.overdue, str(i.ticket.meta.get("review_by") or "9999")[:10], str(i.ticket.meta.get("priority") or "p9"), i.id))
    changes.sort(key=lambda i: (not i.emergency, -(i.when.timestamp() if i.when else 0), i.id))
    return asks + changes


def summary(items: list[Item]) -> str:
    a = sum(1 for i in items if i.group == "ask")
    return f"{a} asks for you · {len(items) - a} changed by someone else"


def empty_words(style_name: str) -> tuple[str, str]:
    return EMPTY.get(style_name, EMPTY["plain"])
