"""Flow time per ticket from the activity log (DESK-0288, idea 77; the four parts of DESK-0242).

The event log (`events/<ID>.jsonl`) records every status change and every change of `blocked_on` with its time. Replaying them gives
the time a ticket spent in each status, and four parts that add up to its whole time from creation to done (or to now when it is
still open):

* worked: claimed, in progress, or sent back for changes (someone is building);
* waiting on the owner: in review, or blocked with `blocked_on` naming the owner (the time his own queue adds);
* queued: inbox, triage, ready or approved (waiting for someone to pick it up or merge it);
* held up: blocked on anyone or anything else (DESK-0242 calls it stuck; that word is a mascot word on screen, DESK-0037).

A ticket with no status events (older than the log) counts its whole life in the status it has now. Docs: docs/COMMANDS.md (/flow)."""

from __future__ import annotations

import datetime as dt
import re

from . import activity
from .ticket import CLOSED, Ticket

PARTS = ("worked", "waiting", "queued", "held")
LABELS = {"worked": "Worked", "waiting": "Waiting on you", "queued": "Queued", "held": "Held up"}
WORKED = {"claimed", "in_progress", "changes_requested"}
QUEUED = {"inbox", "triage", "ready", "approved"}


def _ts(e) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(e.ts)[:19])
    except ValueError:
        return None


def names_owner(blocked_on: str, owner: str) -> bool:
    return bool(owner) and bool(re.search(rf"(?<![a-z0-9]){re.escape(owner.lower())}(?![a-z0-9])", (blocked_on or "").lower()))


def part_of(status: str, blocked_on: str, owner: str) -> str:
    if status == "review" or (status == "blocked" and names_owner(blocked_on, owner)):
        return "waiting"
    if status == "blocked":
        return "held"
    if status in WORKED:
        return "worked"
    return "queued"


def _start(t: Ticket, events: list) -> dt.datetime | None:
    for e in events:
        if e.change == "created" and _ts(e):
            return _ts(e)
    try:
        return dt.datetime.combine(dt.date.fromisoformat(str(t.meta.get("created"))[:10]), dt.time())
    except ValueError:
        stamps = [x for x in (_ts(e) for e in events) if x]
        return min(stamps) if stamps else None


def segments(t: Ticket, events: list, owner: str, now: dt.datetime | None = None) -> list[tuple[dt.datetime, dt.datetime, str, str]]:
    """[(from, to, status, blocked_on)] in time order, from the ticket's creation to its close (or `now`), closed spans left out."""
    now = now or dt.datetime.now()
    evs = sorted((e for e in events if e.change == "field" and e.field in ("status", "blocked_on") and _ts(e)), key=lambda e: (_ts(e), e.seq))
    first_status = next((e for e in evs if e.field == "status"), None)
    first_block = next((e for e in evs if e.field == "blocked_on"), None)
    status = first_status.old if first_status else t.status
    blocked = first_block.old if first_block else str(t.meta.get("blocked_on") or "")
    start = _start(t, events)
    if start is None:
        return []
    out, at = [], start
    for e in evs:
        when = max(_ts(e), at)
        if when > at and status not in CLOSED:  # time while closed is skipped; a reopen (a hand edit or a revert) counts again
            out.append((at, when, status or "inbox", blocked))
        at = when
        if e.field == "status":
            status = e.new
        else:
            blocked = e.new
    if status not in CLOSED and now > at:
        out.append((at, now, status or "inbox", blocked))
    return out


def of(store, t: Ticket, now: dt.datetime | None = None, events: list | None = None) -> dict:
    """{'by_status': {status: seconds}, 'parts': {part: seconds}, 'total': seconds, 'closed': bool, 'start', 'end'} for one ticket."""
    events = activity.read(store.home, t.id) if events is None else events
    segs = segments(t, events, store.owner, now)
    by_status: dict[str, float] = {}
    parts = {p: 0.0 for p in PARTS}
    for a, b, st, bl in segs:
        secs = (b - a).total_seconds()
        by_status[st] = by_status.get(st, 0.0) + secs
        parts[part_of(st, bl, store.owner)] += secs
    total = sum(parts.values())
    return {"by_status": by_status, "parts": parts, "total": total, "closed": t.status in CLOSED,
            "start": segs[0][0] if segs else _start(t, events), "end": segs[-1][1] if segs else None,
            "share": {p: (parts[p] / total if total else 0.0) for p in PARTS}}


def human(seconds: float) -> str:
    """`3 d 4 h`, `5 h 10 m`, `12 m`, `under a minute`."""
    s = int(seconds)
    d, h, m = s // 86400, (s % 86400) // 3600, (s % 3600) // 60
    if d:
        return f"{d} d {h} h" if h else f"{d} d"
    if h:
        return f"{h} h {m} m" if m else f"{h} h"
    return f"{m} m" if m else "under a minute"


def view(store, t: Ticket, now: dt.datetime | None = None) -> dict:
    """What the ticket page and /flow draw: the four parts in words and as shares of the whole, and the time in each status."""
    f = of(store, t, now)
    rows = [{"part": p, "label": LABELS[p], "time": human(f["parts"][p]), "pct": round(f["share"][p] * 100, 1)} for p in PARTS]
    return {**f, "rows": rows, "total_text": human(f["total"]),
            "statuses": sorted(((st, human(secs)) for st, secs in f["by_status"].items()), key=lambda x: -f["by_status"][x[0]])}
