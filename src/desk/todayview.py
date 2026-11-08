"""Today as a short page of rows (DESK-0040): what needs you, what you are holding, a three-row preview of the review pile.

Nothing else renders as a list of tickets. The rest of the open work is counted, each count a link to the search that lists it, so a
store with a thousand tickets still draws about fifteen rows. Queue order: priority, then the review-by date, then the id."""

from __future__ import annotations

import datetime as dt

from . import asks, ops
from .store import Store
from .ticket import PRIORITIES, Ticket

PREVIEW = 3  # rows of the review pile shown on Today
HAND_ROWS = 5
FOLDED_ROWS = 8
ASK_OPEN = ("inbox", "triage", "ready", "review")
HAND = ("claimed", "in_progress", "changes_requested")
OTHER = ("inbox", "triage", "ready", "blocked", "approved")


def queue_key(t: Ticket) -> tuple:
    p = str(t.meta.get("priority") or "")
    return (PRIORITIES.index(p) if p in PRIORITIES else len(PRIORITIES), str(t.meta.get("review_by") or "9999")[:10], t.id)


def due_text(t: Ticket) -> str:
    """`due Nov 15` for a ticket with a review_by date, else ''."""
    try:
        d = dt.date.fromisoformat(str(t.meta.get("review_by") or "")[:10])
    except ValueError:
        return ""
    return f"due {d.strftime('%b')} {d.day}"


def ask_label(t: Ticket, owner: str) -> str:
    """The word on the badge of a Needs-you row: Approve, Choose, Answer, Provide for a decision, Do for a task assigned to the owner."""
    a = asks.ask_of(t)
    if a:
        return a["kind"].capitalize()
    if t.meta.get("type") == "task" and ops.assignee_of(t).lower() == owner.lower() and t.status not in ("done", "rejected", "approved"):
        return "Do"
    return ""


def build(store: Store, tickets: list[Ticket]) -> dict:
    owner = store.owner
    needs = sorted([t for t in tickets if t.status in ASK_OPEN and ask_label(t, owner)], key=lambda t: (due_text(t) == "", str(t.meta.get("review_by") or "9999")[:10], queue_key(t)))
    need_ids = {t.id for t in needs}
    hand = sorted([t for t in tickets if t.status in HAND and t.id not in need_ids], key=queue_key)
    pile = sorted([t for t in tickets if t.status == "review" and t.id not in need_ids], key=queue_key)
    counts = {st: sum(1 for t in tickets if t.status == st) for st in OTHER}
    return {"needs": needs, "hand": hand, "hand_shown": hand[:HAND_ROWS], "hand_more": hand[HAND_ROWS:], "pile": pile, "pile_shown": pile[:PREVIEW],
            "other_counts": counts, "ask_label": lambda t: ask_label(t, owner), "due_text": due_text}
