"""Hard deadlines (DESK-0193): a date a ticket cannot slip past, apart from `review_by` (the soft date to look again) and the sprint.

Frontmatter: `deadline: 2026-11-12` and `deadline_kind: hard | soft | sprint`. Only a hard deadline puts a ticket in Today's
"Immediate Action Required" row, and only while the ticket is still open: closed (done, rejected) and approved tickets are no longer
waiting on anyone. The window is 14 days ahead; a missed hard deadline stays in the row until the ticket closes.
Docs: docs/COMMANDS.md (Deadlines)."""

from __future__ import annotations

import datetime as dt

from .ticket import CLOSED, PRIORITIES, Ticket

KINDS = ("hard", "soft", "sprint")
WINDOW_DAYS = 14
SHOWN = 5
DONE_WITH = set(CLOSED) | {"approved"}  # nothing left to do on these


def parse(value) -> dt.date | None:
    """The deadline date of a frontmatter value, or None when it is empty or not a date."""
    try:
        return dt.date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def of(t: Ticket) -> tuple[dt.date | None, str]:
    """(date, kind) of a ticket; kind is '' when there is no deadline."""
    d = parse(t.meta.get("deadline"))
    return (d, str(t.meta.get("deadline_kind") or "")) if d else (None, "")


def days_left(t: Ticket, today: dt.date | None = None) -> int | None:
    d, _ = of(t)
    return (d - (today or dt.date.today())).days if d else None


def words(n: int) -> str:
    """Days left in plain words: 'due in 3 days', 'due today', 'missed by 2 days'."""
    if n < 0:
        return f"missed by {-n} day{'s' if n != -1 else ''}"
    if n == 0:
        return "due today"
    return f"due in {n} day{'s' if n != 1 else ''}"


def immediate(tickets: list[Ticket], today: dt.date | None = None, days: int = WINDOW_DAYS) -> list[Ticket]:
    """Open tickets with a hard deadline within `days` days or already missed, soonest first (a missed one is the soonest of all);
    ties by priority, then id."""
    now = today or dt.date.today()
    out = []
    for t in tickets:
        d, kind = of(t)
        if d and kind == "hard" and t.status not in DONE_WITH and (d - now).days <= days:
            out.append(t)
    return sorted(out, key=lambda t: (of(t)[0], PRIORITIES.index(t.meta["priority"]) if t.meta.get("priority") in PRIORITIES else len(PRIORITIES), t.id))


def parse_arg(text: str) -> dt.date:
    """A date typed on the command line or in the form; ValueError with a plain sentence otherwise."""
    d = parse(text) if len(str(text).strip()) >= 10 else None
    if d is None:
        raise ValueError(f"`{text}` is not a date. Write it as YYYY-MM-DD, for example 2026-11-12.")
    return d


def set_deadline(store, t: Ticket, date: dt.date | None, kind: str, actor: str) -> Ticket:
    """Set (or, with date None, clear) the deadline and note it in Comments so the change is on the record."""
    from . import ops

    if date is not None and kind not in KINDS:
        raise ValueError(f"Deadline kind is one of {', '.join(KINDS)}.")
    if date is None:
        t.meta.pop("deadline", None)
        t.meta.pop("deadline_kind", None)
        note = "deadline cleared"
    else:
        t.meta["deadline"] = date.isoformat()
        t.meta["deadline_kind"] = kind
        note = f"deadline set to {date.isoformat()} ({kind})"
    t.meta["updated"] = ops.today()
    t.append_line("Comments", ops._stamp(actor, note))
    store.save(t)
    return t
