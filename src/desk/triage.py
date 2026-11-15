"""Inbox triage (DESK-0285): inbox tickets one at a time, each sent to Now, Planned, Later or Park with one key, or merged into
another ticket. Placing a ticket sets its `lane` field (the DESK-0282 lanes contract in lanes.py) and moves it from inbox to triage,
so it leaves the inbox; Park also keeps `park_reason`. Merge records `duplicate_of`, links the two, closes the copy as rejected with
the reason, and leaves a line on the ticket it went into. Docs: docs/COMMANDS.md (`/triage`)."""

from __future__ import annotations

from . import lanes, ops
from .rules import RuleError
from .store import Store
from .ticket import Ticket

CHOICES = (("now", "Now", "1"), ("planned", "Planned", "2"), ("later", "Later", "3"), ("parked", "Park", "4"))
LABEL = {k: label for k, label, _ in CHOICES}
PARK_REASON = "Parked from inbox triage."
_PRIORITY = {"p0": 0, "p1": 1, "p2": 2, "p3": 3}


def queue(store: Store) -> list[Ticket]:
    """Every inbox ticket, most urgent first, then oldest first."""
    ts = [t for t in store.tickets() if t.status == "inbox"]
    return sorted(ts, key=lambda t: (_PRIORITY.get(str(t.meta.get("priority")), 9), str(t.meta.get("created") or ""), t.id))


def place(store: Store, t: Ticket, lane: str, actor: str, reason: str = "") -> Ticket:
    """Set the lane and move the ticket out of the inbox (to triage) in one save."""
    lane = (lane or "").strip().lower()
    if lane not in lanes.LANES:
        raise ValueError("Pick Now, Planned, Later or Park.")
    if t.status != "inbox":
        raise ValueError(f"{t.id} has already left the inbox (it is {t.status}).")
    t.meta["lane"] = lane
    if lane == "parked":
        t.meta["park_reason"] = " ".join((reason or "").split()) or PARK_REASON
    else:
        t.meta.pop("park_reason", None)
    return ops.move(store, t, "triage", actor=actor)


def merge(store: Store, t: Ticket, into: str, actor: str) -> Ticket:
    """Close `t` as a copy of `into`: duplicate_of and related on `t`, a rejected line with the reason, an FYI line on `into`."""
    into = (into or "").strip().upper()
    if not into:
        raise ValueError("Type the id of the ticket to merge into, such as DESK-0101.")
    if into == t.id:
        raise ValueError("A ticket can not be merged into itself.")
    try:
        target = store.get(into)
    except KeyError:
        raise ValueError(f"There is no ticket {into}.") from None
    if not target.is_open:
        raise ValueError(f"{into} is {target.status}; merge into an open ticket.")
    t.meta["duplicate_of"] = target.id
    rel = [str(x) for x in (t.meta.get("related") or [])]
    if target.id not in rel:
        t.meta["related"] = rel + [target.id]
    try:
        ops.reject(store, t, f"Merged into {target.id}: the same request.", actor=actor)
    except RuleError as e:
        raise ValueError(str(e)) from None
    ops.comment(store, store.get(target.id), f"{t.id} ({t.meta.get('title')}) was merged into this ticket from inbox triage.", actor=actor, kind="FYI")
    return t
