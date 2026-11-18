"""`drew outbox` (DESK-0174): the entry that tells one coworker thread which tickets to look at, since the last entry.

Coworkers change tickets directly, but nothing tells them about the tickets Patch or James changed, the new ones, or the ones handed to
them. A thread cannot read every ticket, so this writes an entry for its team-folder (rule 25 header, one line per ticket) and
remembers where it stopped: the cut-off per actor lives in `.desk/state` under `outbox` and moves only when the entry is posted
(`--posted`), so the next entry starts where this one ended and the thread reads only what is new."""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field

from . import asks, comments as cm, people as peoplemod, subtickets
from .store import Store
from .ticket import CLOSED, Ticket

DEFAULT_LOOKBACK_DAYS = 7
EXPIRES_DAYS = 7
FROM_STAFF = ("patch", "main", "james")  # whose changes a thread needs to hear about, besides the store owner
_ASSIGNED = re.compile(r"^- (\d{4}-\d{2}-\d{2})(?: \d{2}:\d{2})? · (.+?) · assigned to (.+?)(?::.*)?$")
DOING = {"ready", "claimed", "in_progress", "changes_requested", "blocked", "inbox", "triage"}


@dataclass
class Line:
    ticket: Ticket
    changes: list[str] = field(default_factory=list)
    ask: str = ""
    by: str = ""
    part: str = ""  # DESK-0208: `part of DESK-0183: <title>` for a subticket

    def text(self) -> str:
        t = self.ticket
        parts = [t.id, str(t.meta.get("title") or "")] + ([self.part] if self.part else []) + [t.status, "; ".join(self.changes)]
        if self.ask:
            parts.append(f"ask: {self.ask}" + (f" by {self.by}" if self.by else ""))
        return "- " + " · ".join(parts)


# -- the cut-off per actor ------------------------------------------------------------------

def get_cutoff(home, actor: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str((cm._state(home).get("outbox") or {}).get(actor) or ""))
    except ValueError:
        return None


def set_cutoff(home, actor: str, when: dt.datetime | None = None) -> dt.datetime:
    when = (when or dt.datetime.now()).replace(microsecond=0)
    state = cm._state(home)
    box = state.get("outbox") if isinstance(state.get("outbox"), dict) else {}
    box[actor] = when.isoformat()
    state["outbox"] = box
    p = cm._state_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return when


def parse_since(text: str | None, home, actor: str) -> tuple[dt.datetime, str]:
    """(cut-off, how it was found): `last-entry` (the stored cut-off, else a week back), or a date or a date and time."""
    if not text or text.strip().lower() == "last-entry":
        got = get_cutoff(home, actor)
        if got:
            return got, "the last entry"
        return dt.datetime.now().replace(microsecond=0) - dt.timedelta(days=DEFAULT_LOOKBACK_DAYS), f"{DEFAULT_LOOKBACK_DAYS} days back (no earlier entry for {actor})"
    try:
        return dt.datetime.fromisoformat(text.strip().replace(" ", "T")), "the date you gave"
    except ValueError:
        raise ValueError(f"`{text}` is not a time. Use last-entry, YYYY-MM-DD or YYYY-MM-DD HH:MM.") from None


# -- what changed for one actor -------------------------------------------------------------------

def _date(v) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _named_in(t: Ticket, names: set[str]) -> bool:
    text = " ".join(t.section(s) for s in ("Context", "Prompt")).lower()
    return any(re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", text) for n in names if n)


def _assign_events(t: Ticket) -> list[tuple[dt.date, str, str]]:
    out = []
    for ln in t.section("Comments").splitlines():
        m = _ASSIGNED.match(ln.strip())
        if m:
            out.append((dt.date.fromisoformat(m.group(1)), m.group(2).strip(), m.group(3).strip()))
    return out


def _ask_for(t: Ticket, actor_key: str, pe: peoplemod.People) -> tuple[str, str]:
    """(ask, by-date) when the actor must do something on this ticket: answer a decision, do the work, review what was handed over."""
    if pe.resolve(str(t.meta.get("assignee") or "")) != actor_key or t.status in CLOSED:
        return "", ""
    by = str(t.meta.get("review_by") or "")[:10]
    a = asks.ask_of(t)
    if a and t.status in DOING | {"review"}:
        return a["kind"], by
    if t.status == "review":
        return "review", by
    if t.status in DOING:
        return "do", by
    return "", ""


def collect(store: Store, actor: str, since: dt.datetime, entries: list | None = None) -> list[Line]:
    """The tickets `actor` should look at: handed to them, changed by Patch or James since they last acted, or new and naming them."""
    pe = peoplemod.load(store.home)
    key = pe.resolve(actor)
    person = pe.get(actor)
    names = {key.lower(), (person.name.lower() if person else actor.lower())} | ({a.lower() for a in person.aliases} if person else set())
    staff = {a.lower() for a in (*FROM_STAFF, store.owner)}
    by_ticket: dict[str, list] = {}
    for e in entries if entries is not None else cm.entries(store):
        by_ticket.setdefault(e.ticket_id, []).append(e)
    lines = []
    every = store.tickets()
    fam = subtickets.Family(every)
    for t in every:
        mine = by_ticket.get(t.id, [])
        changes: list[str] = []
        is_mine = pe.resolve(str(t.meta.get("assignee") or "")) == key or str(t.meta.get("owner") or "").lower() == key.lower()
        touched = is_mine or any(pe.resolve(e.author) == key for e in mine)
        # 1. handed to them, or handed to them again
        try:
            at = dt.datetime.fromisoformat(str(t.meta.get("assigned_at") or ""))
        except ValueError:
            at = None
        if at and at > since and pe.resolve(str(t.meta.get("assignee") or "")) == key:
            events = _assign_events(t)
            who = str(t.meta.get("assigned_by") or "")
            changes.append(("reassigned" if len(events) >= 2 else "assigned") + (f" by {pe.display(who)}" if who and not who.startswith("rule:") else (f" by {who}" if who else "")))
        # 2. changed by Patch or James on something they touched
        if touched:
            for e in mine:
                if e.when >= since.replace(second=0) and e.author.lower() in staff:  # comments carry minutes, not seconds
                    changes.append(f"comment by {e.author.lower()}")
                    break
            for ln in t.section("Decision").splitlines():
                m = re.match(r"^- (\d{4}-\d{2}-\d{2}) · (\S+) · (approved|changes_requested)\b", ln.strip())
                if m and dt.date.fromisoformat(m.group(1)) >= since.date() and m.group(2).lower() in staff:
                    changes.append(f"decision: {m.group(3)}")
                    break
            theirs = [e.when.date() for e in mine if pe.resolve(e.author) == key] + ([at.date()] if at else [])
            upd = _date(t.meta.get("updated"))
            if not changes and upd and upd >= since.date() and (not theirs or upd > max(theirs)):
                changes.append("changed since you last acted")
        # 3. new, and naming them
        created = _date(t.meta.get("created"))
        if not changes and created and created >= since.date() and _named_in(t, names):
            changes.append("new, names " + (person.name if person else actor))
        if changes:
            ask, by = _ask_for(t, key, pe)
            lines.append(Line(t, list(dict.fromkeys(changes)), ask, by, fam.label(t)))
    lines.sort(key=lambda ln: ln.ticket.id)
    return lines


def render(store: Store, actor: str, lines: list[Line], since: dt.datetime, how: str, today: dt.date | None = None) -> str:
    pe = peoplemod.load(store.home)
    today = today or dt.date.today()
    to = pe.display(pe.resolve(actor))
    ids = ", ".join(ln.ticket.id for ln in lines)
    head = [f"Date: {today.isoformat()} · From: Patch · To: {to} · Expires: {(today + dt.timedelta(days=EXPIRES_DAYS)).isoformat()}",
            "Supersedes: none", f"Tickets: {ids}", "",
            f"Update for {to}: {len(lines)} ticket{'s' if len(lines) != 1 else ''} to look at since {since.strftime('%Y-%m-%d %H:%M')} ({how}). "
            "Work the list, then ignore this entry.", ""]
    return "\n".join(head + [ln.text() for ln in lines]) + "\n"


def actors(store: Store) -> list[str]:
    """Every coworker thread in people.yaml."""
    return [p.key for p in peoplemod.load(store.home).list if p.kind == "coworker"]


def as_json(store: Store, actor: str, lines: list[Line], since: dt.datetime, how: str) -> dict:
    return {"actor": actor, "since": since.isoformat(), "how": how, "tickets": [
        {"id": ln.ticket.id, "title": str(ln.ticket.meta.get("title") or ""), "status": ln.ticket.status, "changes": ln.changes, "ask": ln.ask, "by": ln.by,
         "part_of": ln.part} for ln in lines],
        "text": render(store, actor, lines, since, how) if lines else ""}
