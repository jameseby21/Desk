"""The broad-context board (DESK-0084): projects first, then tracks or sprints, then the tickets in status columns.

The page opens with one row per project and nothing else: counts and the top priority. A person drills in (project, then group)
to see the tickets, and collapses to hide the ones that do not matter now. What is open lives in the URL (`open=` for projects,
`g=` for groups as `project:slug`; `*` means all), so a view can be shared.

Calm-board rules held at every level (Chaz, DESK-0034 and DESK-0035): a row never wraps (long names truncate), a card shows at most
two chips plus +N, a title clamps to three lines, columns scroll inside their own container with sticky headers, a column can
collapse, and a long column shows its first eight cards with "Show N more"."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from . import nav as navmod
from .store import Store
from .ticket import CLOSED, PRIORITIES, STATUSES, Ticket
from .tracks import UNTRACKED, track_of, track_slug

NO_SPRINT = "No sprint"
BY = ("track", "sprint")
FIRST_CARDS = 8
WIDE = "*"


@dataclass
class Group:
    name: str
    slug: str
    total: int
    open: int
    top: str  # best priority among the open tickets, or ""
    top_ticket: Ticket | None
    counts: dict
    tickets: list[Ticket] = field(default_factory=list)
    is_open: bool = False


@dataclass
class ProjectRow:
    key: str
    title: str
    area: str
    total: int
    open: int
    top: str
    top_ticket: Ticket | None
    counts: dict
    groups: list[Group]
    is_open: bool = False


def _best(ts: list[Ticket]) -> Ticket | None:
    live = [t for t in ts if t.status not in CLOSED]
    if not live:
        return None
    return sorted(live, key=lambda t: (PRIORITIES.index(t.meta["priority"]) if t.meta.get("priority") in PRIORITIES else len(PRIORITIES),
                                       str(t.meta.get("review_by") or "9999")[:10], t.id))[0]


def _stats(ts: list[Ticket]) -> tuple[int, str, Ticket | None, dict]:
    top = _best(ts)
    return (len([t for t in ts if t.status not in CLOSED]), str(top.meta.get("priority") or "") if top else "", top,
            {st: len([t for t in ts if t.status == st]) for st in STATUSES})


def group_name(t: Ticket, by: str) -> str:
    if by == "sprint":
        return navmod.sprint_of(t) or NO_SPRINT
    return track_of(t) or UNTRACKED


def _wanted(spec: str) -> set[str]:
    return {x.strip() for x in (spec or "").split(",") if x.strip() and x.strip() != "-"}  # "-" is the explicit "nothing open"


def build(store: Store, tickets: list[Ticket], by: str = "track", open_spec: str = "", group_spec: str = "", expand_all: bool = False) -> list[ProjectRow]:
    """One row per project that has tickets (or all projects when `tickets` is every ticket): groups with their counts and top
    priority, and for the ones the URL opens, their tickets. `expand_all` opens everything unless the URL names what to open."""
    if by not in BY:
        by = "track"
    projects, groups = _wanted(open_spec), _wanted(group_spec)
    if expand_all and not projects and not groups and "-" not in (open_spec or ""):
        projects, groups = {WIDE}, {WIDE}
    rows = []
    for key in store.projects():
        mine = [t for t in tickets if t.project == key]
        meta, _ = store.project_profile(key)
        by_group: dict[str, list[Ticket]] = {}
        for t in mine:
            by_group.setdefault(group_name(t, by), []).append(t)
        gs = []
        for name, ts in sorted(by_group.items(), key=lambda kv: (kv[0] in (UNTRACKED, NO_SPRINT), kv[0].lower())):
            n_open, top, top_t, counts = _stats(ts)
            slug = track_slug(name if name not in (UNTRACKED, NO_SPRINT) else "")
            on = WIDE in groups or f"{key}:{slug}" in groups
            gs.append(Group(name, slug, len(ts), n_open, top, top_t, counts, sorted(ts, key=lambda t: t.id) if on else [], on))
        n_open, top, top_t, counts = _stats(mine)
        on = WIDE in projects or key in projects or any(g.is_open for g in gs)  # an open group keeps its project open
        rows.append(ProjectRow(key, store.project_title(key), str(meta.get("area") or ""), len(mine), n_open, top, top_t, counts, gs, on))
    rows = [r for r in rows if r.total]
    rows.sort(key=lambda r: (PRIORITIES.index(r.top) if r.top in PRIORITIES else len(PRIORITIES), -r.open, r.key))  # the most urgent project first
    return rows


def by_status(tickets: list[Ticket]) -> list[tuple[str, list[Ticket]]]:
    """The status columns of one group, in the order of the lifecycle; empty columns that do not matter are left out by the template."""
    return [(st, [t for t in tickets if t.status == st]) for st in STATUSES]


def age_days(t: Ticket, today: dt.date | None = None) -> int | None:
    try:
        return ((today or dt.date.today()) - dt.date.fromisoformat(str(t.meta.get("created"))[:10])).days
    except ValueError:
        return None


def review_by_text(t: Ticket, today: dt.date | None = None) -> str:
    """`in 3 d`, `today` or `2 d late` for a ticket with a review_by date; '' without one."""
    if not t.meta.get("review_by") or t.status in CLOSED:
        return ""
    try:
        d = (dt.date.fromisoformat(str(t.meta["review_by"])[:10]) - (today or dt.date.today())).days
    except ValueError:
        return ""
    return "today" if d == 0 else f"in {d} d" if d > 0 else f"{-d} d late"


def proof_dot(t: Ticket) -> str:
    """none (no Report yet), notrun (a failed check is on the ticket) or ran (a Report is written)."""
    if t.failed_checks():
        return "notrun"
    return "ran" if t.section("Report") else "none"
