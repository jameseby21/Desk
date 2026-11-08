"""The board's filters (DESK-0034): Project, Assignee, Track and Sprint, each a set of values held in the URL.

A value list is written once as `project=a,b` and also read from repeated keys (`project=a&project=b`), which is what a plain HTML
form posts, so the dropdowns work without script. Counts beside each choice follow the other filters, so a choice that would show
nothing says 0 (or is left out unless it is picked)."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from . import ops, sprints as sprintmod, tracks as trk
from .store import Store
from .ticket import Ticket

FACETS = ("project", "assignee", "track", "sprint")
OTHER_AREA = "Other"


def split(vals) -> list[str]:
    """Values from a repeated query key, each possibly a comma list; order kept, duplicates dropped."""
    out: list[str] = []
    for v in ([vals] if isinstance(vals, str) else vals or []):
        for piece in str(v).split(","):
            piece = piece.strip()
            if piece and piece not in out:
                out.append(piece)
    return out


def _sprint_slug(t: Ticket) -> str:
    n = sprintmod.sprint_of(t)
    return sprintmod.slugify(n, 60) if n else ""


def _track_slug(t: Ticket) -> str:
    n = trk.track_of(t)
    return trk.track_slug(n) if n else ""


def keep(t: Ticket, sel: dict, skip: str = "") -> bool:
    if skip != "project" and sel["project"] and t.project not in sel["project"]:
        return False
    if skip != "assignee" and sel["assignee"] and ops.assignee_of(t).lower() not in {a.lower() for a in sel["assignee"]}:
        return False
    if skip != "track" and sel["track"] and _track_slug(t) not in sel["track"]:
        return False
    if skip != "sprint" and sel["sprint"] and _sprint_slug(t) not in sel["sprint"]:
        return False
    return True


def apply(tickets: list[Ticket], sel: dict) -> list[Ticket]:
    return [t for t in tickets if keep(t, sel)]


@dataclass
class Opt:
    value: str
    label: str
    n: int
    on: bool


def options(store: Store, tickets: list[Ticket], sel: dict, people) -> dict:
    """{facet: [(group heading, [Opt])]}: every value any ticket carries, counted over the tickets the other filters leave."""
    out: dict = {}
    # projects, grouped by the area in the project profile
    mine = [t for t in tickets if keep(t, sel, "project")]
    counts: dict = {}
    for t in mine:
        counts[t.project] = counts.get(t.project, 0) + 1
    by_area: dict[str, list[Opt]] = {}
    for key in store.projects():
        n = counts.get(key, 0)
        if not n and key not in sel["project"]:
            continue
        area = str(store.project_profile(key)[0].get("area") or "") or OTHER_AREA
        by_area.setdefault(area, []).append(Opt(key, key, n, key in sel["project"]))
    out["project"] = [(a, sorted(o, key=lambda x: x.label.lower())) for a, o in sorted(by_area.items(), key=lambda kv: (kv[0] == OTHER_AREA, kv[0].lower()))]
    # assignees
    mine = [t for t in tickets if keep(t, sel, "assignee")]
    names: dict = {}
    for t in mine:
        n = ops.assignee_of(t)
        if n:
            names[n] = names.get(n, 0) + 1
    for a in sel["assignee"]:
        names.setdefault(a, 0)
    picked = {a.lower() for a in sel["assignee"]}
    out["assignee"] = [("", [Opt(n, people.display(n) if people.get(n) else n, k, n.lower() in picked)
                              for n, k in sorted(names.items(), key=lambda kv: kv[0].lower())])]
    # tracks and sprints
    for facet, slug_of, name_of in (("track", _track_slug, trk.track_of), ("sprint", _sprint_slug, sprintmod.sprint_of)):
        mine = [t for t in tickets if keep(t, sel, facet)]
        seen: dict = {}
        for t in mine:
            s = slug_of(t)
            if s:
                seen[s] = (seen[s][0], seen[s][1] + 1) if s in seen else (name_of(t), 1)
        for s in sel[facet]:
            seen.setdefault(s, (s, 0))
        out[facet] = [("", [Opt(s, nm, k, s in sel[facet]) for s, (nm, k) in sorted(seen.items(), key=lambda kv: kv[1][0].lower())])]
    return out


def query(sel: dict, **over) -> str:
    """The query string for a selection, with `over` replacing facets (a list) or adding other keys (`by`, ...)."""
    merged = {**sel, **over}
    parts = [f"{k}={quote(','.join(v) if isinstance(v, list) else str(v), safe=',:*')}" for k, v in merged.items() if v]
    return "&".join(parts)


def tokens(sel: dict, opts: dict, extra: dict) -> list[dict]:
    """One removable token per picked value: its label and the address without it."""
    out = []
    for facet in FACETS:
        names = {o.value: o.label for _h, group in opts[facet] for o in group}
        for v in sel[facet]:
            q = query({**sel, facet: [x for x in sel[facet] if x != v]}, **extra)
            out.append({"facet": facet, "label": names.get(v, v), "href": "/board" + ("?" + q if q else "")})
    return out
