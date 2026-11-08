"""Previous and Next between tickets (DESK-0148). One function over the tickets takes (scope, order, current id) and returns the
neighbours, the position and the total, so the ticket page and `drew next --after` read the same sequence.

Scope says which tickets are in the walk: this project, this track (the same track name anywhere, since a track such as
"First real use" runs across projects), the whole company, or this sprint. Order says how they are lined up: by date created
(newest first), by the hand-set priority p0..p3 (until the priority score of DESK-0135 exists), or by review date."""

from __future__ import annotations

from dataclasses import dataclass

from .store import id_parts
from .ticket import PRIORITIES, Ticket

SCOPES = {"project": "Within this project", "track": "Within this track", "company": "Across the company", "sprint": "Within this sprint"}
ORDERS = {"date": "By date created (newest first)", "priority": "By priority score", "review": "By review date"}
ORDER_SHORT = {"date": "date created", "priority": "priority", "review": "review date"}
DEFAULT_SCOPE, DEFAULT_ORDER = "project", "date"
_FAR = "9999-12-31"


@dataclass
class Nav:
    current: str
    prev: str | None
    next: str | None
    index: int
    total: int
    scope: str
    order: str
    scope_name: str  # the project key, the track name, "the company" or the sprint

    @property
    def text(self) -> str:
        return f"{self.index} of {self.total} in {self.scope_name}, by {ORDER_SHORT[self.order]}"

    @property
    def prev_note(self) -> str:
        return "" if self.prev else "This is the first ticket in this order."

    @property
    def next_note(self) -> str:
        return "" if self.next else "This is the last ticket in this order."


def track_of(t: Ticket) -> str:
    return str(t.meta.get("track") or "").strip()


def sprint_of(t: Ticket) -> str:
    """A ticket's sprint: its `sprint:` field (DESK-0147), else a track called `Sprint NNNN`."""
    s = str(t.meta.get("sprint") or "").strip()
    if s:
        return s
    tr = track_of(t)
    return tr if tr.lower().startswith("sprint ") else ""


def _idkey(t: Ticket) -> tuple:
    p = id_parts(t.id)
    return (p[0], p[1]) if p else (t.id, 0)


def in_scope(t: Ticket, cur: Ticket, scope: str) -> bool:
    if scope == "company":
        return True
    if scope == "project":
        return t.project == cur.project
    if scope == "track":
        if not track_of(cur):  # a ticket with no track walks with the other untracked tickets of its project
            return not track_of(t) and t.project == cur.project
        return track_of(t) == track_of(cur)
    if scope == "sprint":
        return t.id == cur.id or (bool(sprint_of(cur)) and sprint_of(t) == sprint_of(cur))  # no sprint: the walk is this ticket alone
    raise ValueError(f"Unknown scope {scope!r}; use one of {', '.join(SCOPES)}.")


def ordered(members: list[Ticket], order: str) -> list[Ticket]:
    if order not in ORDERS:
        raise ValueError(f"Unknown order {order!r}; use one of {', '.join(ORDERS)}.")
    by_date = sorted(members, key=lambda t: (str(t.meta.get("created") or ""), _idkey(t)), reverse=True)  # newest first; ties newest id first
    if order == "date":
        return by_date
    if order == "priority":
        return sorted(by_date, key=lambda t: PRIORITIES.index(t.meta["priority"]) if t.meta.get("priority") in PRIORITIES else len(PRIORITIES))
    return sorted(by_date, key=lambda t: str(t.meta.get("review_by") or _FAR)[:10])  # soonest first, none last


def neighbours(tickets: list[Ticket], current_id: str, scope: str = DEFAULT_SCOPE, order: str = DEFAULT_ORDER) -> Nav:
    """The previous and next ticket id (None at the ends; the walk never wraps), the 1-based position and the size of the walk."""
    cur = next((t for t in tickets if t.id.upper() == current_id.upper()), None)
    if cur is None:
        raise KeyError(current_id)
    seq = ordered([t for t in tickets if in_scope(t, cur, scope)], order)
    i = next(n for n, t in enumerate(seq) if t.id == cur.id)
    name = {"project": cur.project, "track": track_of(cur) or "no track", "company": "the company", "sprint": sprint_of(cur) or "no sprint"}[scope]
    return Nav(cur.id, seq[i - 1].id if i > 0 else None, seq[i + 1].id if i + 1 < len(seq) else None, i + 1, len(seq), scope, order, name)
