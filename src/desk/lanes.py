"""Lanes, snooze and park (DESK-0282 contract; DESK-0283 owns and extends this module).

The management skill's four lanes: now (emergencies, overdue asks, live failures), planned (this sprint's slices), later (ideas,
recorded but not built) and parked (decided not now; the record stays). A ticket's lane is its `lane:` field when set; otherwise a
`lane:<name>` tag; otherwise derived: p0 or an `emergency` tag is now, inbox and triage are later, everything else planned.
`snooze_until: YYYY-MM-DD` keeps a ticket off Today and Needs me until that date; for three days after it the ticket carries a
'Back from snooze' marker. Parking writes `lane: parked` and a one-line `park_reason`; nothing is deleted.

Writes go through `Store.save`, so each change is an event in the ticket's activity log (and undoable by its batch id)."""

from __future__ import annotations

import datetime as dt
import re

LANES = ("now", "planned", "later", "parked")
LABELS = {"now": "Now", "planned": "Planned", "later": "Later", "parked": "Parked"}
BACK_DAYS = 3  # a ticket whose snooze ended this many days ago or less is marked 'Back from snooze'
WORKING = ("claimed", "in_progress")  # a worker holds the ticket
QUIET = ("later", "parked")  # lanes that must not carry a worker or a branch (validate warns)
AGENT_KINDS = ("agent", "code")  # people.yaml kinds that are not a person
_REL = re.compile(r"^\+?(\d{1,3})\s*([dw])$", re.I)
_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


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


def back_from_snooze(t, today: dt.date | None = None) -> bool:
    """True when the ticket's snooze date passed within the last BACK_DAYS days (it just came back to Today)."""
    d = snooze_until(t)
    if d is None:
        return False
    gone = ((today or dt.date.today()) - d).days
    return 0 <= gone <= BACK_DAYS


def parse_when(text: str, today: dt.date | None = None) -> dt.date:
    """A snooze date from what a person types: 2026-11-22, 1d, 3d, 1w, 2w, tomorrow, next-monday (or any weekday).
    Refuses a date that is not after today, since that would snooze nothing."""
    today = today or dt.date.today()
    v = " ".join(str(text or "").split()).lower().replace(" ", "-")
    m = _REL.match(v)
    if m:
        n = int(m.group(1)) * (7 if m.group(2).lower() == "w" else 1)
        d = today + dt.timedelta(days=n)
    elif v == "tomorrow":
        d = today + dt.timedelta(days=1)
    elif v.removeprefix("next-") in _DAYS:
        ahead = (_DAYS.index(v.removeprefix("next-")) - today.weekday()) % 7
        d = today + dt.timedelta(days=ahead or 7)
    else:
        try:
            d = dt.date.fromisoformat(v)
        except ValueError:
            raise ValueError(f"'{text}' is not a date Desk knows. Use a date like 2026-11-22, or 1d, 1w, 2w, next-monday.") from None
    if d <= today:
        raise ValueError(f"{d.isoformat()} is not after today, so it would snooze nothing. Pick a later date.")
    return d


# -- writes ------------------------------------------------------------------------------

def set_lane(store, t, lane: str, reason: str = ""):
    """Put a ticket in a lane ('' clears the field, so the derived lane shows). Parked needs a one-line reason; leaving parked
    clears the reason from the file (the activity log keeps it)."""
    lane = str(lane or "").strip().lower()
    if lane and lane not in LANES:
        raise ValueError(f"'{lane}' is not a lane. Pick one of: {', '.join(LANES)}.")
    reason = " ".join(str(reason or "").split())
    if lane == "parked":
        if not reason:
            raise ValueError(f'Parking needs a one-line reason, for example: drew park {t.id} "not this quarter".')
        t.meta["park_reason"] = reason[:200]
    else:
        t.meta.pop("park_reason", None)
    if lane:
        t.meta["lane"] = lane
    else:
        t.meta.pop("lane", None)
    store.save(t)
    return t


def park(store, t, reason: str):
    """Park a ticket with a reason. Its file and history stay; it leaves the lanes a person works from."""
    return set_lane(store, t, "parked", reason)


def set_snooze(store, t, until: dt.date | None):
    """Snooze a ticket until a date (None wakes it now)."""
    if until is None:
        t.meta.pop("snooze_until", None)
    else:
        t.meta["snooze_until"] = until.isoformat()
    store.save(t)
    return t


# -- who it is for ----------------------------------------------------------------------

def is_agent_only(t, people) -> bool:
    """Owner and assignee are both agents or code sessions (no person and no coworker in it). A blank assignee is the owner's
    'not scanned yet' marker, so it never counts as agent-only."""
    def agent(v) -> bool:
        p = people.get(str(v or ""))
        return p is not None and p.kind in AGENT_KINDS

    assignee = " ".join(str(t.meta.get("assignee") or "").split())
    return bool(assignee) and agent(assignee) and agent(t.meta.get("owner"))


def is_sub(t) -> bool:
    v = t.meta.get("parent")
    return isinstance(v, str) and bool(v.strip())


# -- validate ----------------------------------------------------------------------------

def warnings(t) -> list[str]:
    """`drew validate` warnings: an unknown lane or snooze date, and a Later or Parked ticket that has a branch or a worker."""
    out = []
    raw = str(t.meta.get("lane") or "").strip()
    if raw and raw.lower() not in LANES:
        out.append(f"{t.id}: lane `{raw}` is not one of {', '.join(LANES)}")
    if t.meta.get("snooze_until") not in (None, "") and snooze_until(t) is None:
        out.append(f"{t.id}: snooze_until must be a YYYY-MM-DD date")
    lane = lane_of(t)
    if lane in QUIET and t.is_open:
        branch = " ".join(str(t.meta.get("branch") or "").split())
        bits = ([f"a branch ({branch})"] if branch else []) + ([f"a worker ({t.status})"] if t.status in WORKING else [])
        if bits:
            out.append(f"{t.id}: in the {LABELS[lane]} lane but has {' and '.join(bits)}; "
                       f"move it with `drew lane {t.id} planned`, or take the work off it")
    return out
