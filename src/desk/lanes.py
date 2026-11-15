"""Lanes and snooze (DESK-0282 contract; DESK-0283 owns and extends this module).

The management skill's four lanes: now (emergencies, overdue asks, live failures), planned (this sprint's slices), later (ideas,
recorded but not built) and parked (decided not now; the record stays). A ticket's lane is its `lane:` field when set; otherwise a
`lane:<name>` tag; otherwise derived: p0 or an `emergency` tag is now, inbox and triage are later, everything else planned.
`snooze_until: YYYY-MM-DD` keeps a ticket off Today and Needs me until that date."""

from __future__ import annotations

import datetime as dt

LANES = ("now", "planned", "later", "parked")


def explicit_lane(t) -> str:
    v = str(t.meta.get("lane") or "").strip().lower()
    if v in LANES:
        return v
    for tag in t.tags:
        if tag.lower().startswith("lane:") and tag[5:].lower() in LANES:
            return tag[5:].lower()
    return ""


def lane_of(t) -> str:
    """The lane a ticket is in: explicit, else derived."""
    v = explicit_lane(t)
    if v:
        return v
    if str(t.meta.get("priority", "")) == "p0" or "emergency" in [x.lower() for x in t.tags]:
        return "now"
    if t.status in ("inbox", "triage"):
        return "later"
    return "planned"


def snooze_until(t) -> dt.date | None:
    v = t.meta.get("snooze_until")
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v).strip()) if v else None
    except ValueError:
        return None


def is_snoozed(t, today: dt.date | None = None) -> bool:
    d = snooze_until(t)
    return d is not None and d > (today or dt.date.today())
