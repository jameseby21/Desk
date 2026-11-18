"""Sprints (DESK-0147): named, parallel, cutting across projects and tracks.

`sprints.yaml` in the store lists them (name, start, end, owner, project, note). A ticket joins one with `sprint: <name>`; the name is
free text checked against the file, so 'sprint 6 2026' means the sixth sprint of 2026 for whoever runs it and the same name can exist
in two projects without clashing: a sprint is identified by its name and its project (no project means it spans them all)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from . import yamlio
from .store import Store
from .ticket import CLOSED, PRIORITIES, Ticket, slugify

PATH = "sprints.yaml"
UNSPRINTED = ""


class SprintError(ValueError):
    pass


@dataclass
class Sprint:
    name: str
    project: str = ""
    start: str = ""
    end: str = ""
    owner: str = ""
    note: str = ""

    @property
    def slug(self) -> str:
        return slugify(self.name, 60)

    def as_dict(self) -> dict:
        d = {"name": self.name}
        for k in ("project", "start", "end", "owner", "note"):
            if getattr(self, k):
                d[k] = getattr(self, k)
        return d


def norm(name: str) -> str:
    return " ".join(str(name or "").split()).lower()


def _date(v) -> str:
    if v in (None, ""):
        return ""
    try:
        return dt.date.fromisoformat(str(v)[:10]).isoformat()
    except ValueError:
        raise SprintError(f"`{v}` is not a date (use YYYY-MM-DD)") from None


def load(home: Path) -> list[Sprint]:
    p = Path(home) / PATH
    if not p.is_file():
        return []
    try:
        data = yamlio.load(p.read_text(encoding="utf-8"))
    except yamlio.YAMLError as e:
        raise SprintError(f"{p}: not valid YAML ({e})") from e
    rows = data.get("sprints") if isinstance(data, dict) else data
    out = []
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict) and str(r.get("name") or "").strip():
            out.append(Sprint(" ".join(str(r["name"]).split()), str(r.get("project") or "").strip(), _date(r.get("start")), _date(r.get("end")),
                              str(r.get("owner") or "").strip(), str(r.get("note") or "").strip()))
    return out


def save(home: Path, sprints: list[Sprint]) -> Path:
    p = Path(home) / PATH
    text = yamlio.dump({"sprints": [s.as_dict() for s in sprints]}, sort_keys=False, allow_unicode=True, default_flow_style=False, width=1000)
    p.write_text("# Sprints (DESK-0147). A ticket joins one with `sprint: <name>`. The same name may exist in two projects.\n" + text, encoding="utf-8", newline="\n")
    return p


def create(store: Store, name: str, *, start: str = "", end: str = "", owner: str = "", project: str = "", note: str = "") -> Sprint:
    name = " ".join((name or "").split())
    if not name:
        raise SprintError("A sprint needs a name, like 'sprint 6 2026'.")
    if project and project not in store.projects():
        raise SprintError(f"Unknown project `{project}`. Add projects/{project}.md first, or leave --project out for a sprint that spans projects.")
    s = Sprint(name, project, _date(start), _date(end), owner, note)
    if s.start and s.end and s.end < s.start:
        raise SprintError("A sprint cannot end before it starts.")
    have = load(store.home)
    if any(norm(x.name) == norm(name) and x.project == project for x in have):
        raise SprintError(f"Sprint `{name}` already exists" + (f" in {project}." if project else "."))
    save(store.home, have + [s])
    return s


def names(store: Store) -> set[str]:
    return {norm(s.name) for s in load(store.home)}


def sprint_of(t: Ticket) -> str:
    return " ".join(str(t.meta.get("sprint") or "").split())


def matching(sprints: list[Sprint], name: str) -> list[Sprint]:
    return [s for s in sprints if norm(s.name) == norm(name)]


def unknown_warning(t: Ticket, known: set[str]) -> str | None:
    n = sprint_of(t)
    if n and norm(n) not in known:
        return f"{t.id}: sprint `{n}` is not in sprints.yaml (drew sprint new '{n}' --start --end --owner)"
    return None


def set_sprint(store: Store, t: Ticket, name: str, actor: str | None = None) -> Ticket:
    """Put a ticket in a sprint (or, with a blank name, out of it). The sprint must exist."""
    from .ops import _stamp

    name = " ".join((name or "").split())
    if name:
        have = matching(load(store.home), name)
        if not have:
            raise SprintError(f"No sprint `{name}`. Create it first: drew sprint new '{name}' --start YYYY-MM-DD --end YYYY-MM-DD")
        name = have[0].name  # the spelling in sprints.yaml
        t.meta["sprint"] = name
        t.append_line("Comments", _stamp(actor or "desk", f"sprint set to {name}"))
    else:
        t.meta.pop("sprint", None)
        t.append_line("Comments", _stamp(actor or "desk", "taken out of its sprint"))
    store.save(t)
    return t


# -- the sprint page -------------------------------------------------------------------------------

def score_key(t: Ticket) -> tuple:
    """Score order until the priority score of DESK-0135 exists: p0 first, then the sooner review_by, then the id."""
    return (PRIORITIES.index(t.meta["priority"]) if t.meta.get("priority") in PRIORITIES else len(PRIORITIES), str(t.meta.get("review_by") or "9999")[:10], t.id)


def _done_day(t: Ticket) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(t.meta.get("done_at") or t.meta.get("updated"))[:10])
    except ValueError:
        return None


@dataclass
class Group:
    sprint: Sprint | None  # None: tickets that name a sprint nobody defined
    name: str
    tickets: list[Ticket]
    total: int = 0
    done: int = 0
    remaining: int = 0
    by_status: dict = field(default_factory=dict)
    days_total: int = 0
    days_left: int | None = None
    ideal_now: float | None = None
    series: list[tuple[str, int, float]] = field(default_factory=list)  # (day, remaining, ideal)


def burn(g: Group, today: dt.date) -> Group:
    ts = g.tickets
    g.total = len(ts)
    g.done = len([t for t in ts if t.status == "done"])
    g.remaining = len([t for t in ts if t.status not in CLOSED])
    for t in ts:
        g.by_status[t.status] = g.by_status.get(t.status, 0) + 1
    s = g.sprint
    if s and s.start and s.end:
        a, b = dt.date.fromisoformat(s.start), dt.date.fromisoformat(s.end)
        g.days_total = (b - a).days + 1
        g.days_left = max(0, (b - today).days)
        last = min(b, max(today, a))
        for i in range((last - a).days + 1):
            day = a + dt.timedelta(days=i)
            gone = len([t for t in ts if t.status == "done" and (_done_day(t) or a) <= day])
            ideal = g.total * (1 - (i / max(1, g.days_total - 1))) if g.days_total > 1 else 0.0
            g.series.append((day.isoformat(), g.total - gone, round(ideal, 1)))
        g.ideal_now = g.series[-1][2] if g.series else None
    return g


def groups(store: Store, slug: str, tickets: list[Ticket] | None = None, today: dt.date | None = None) -> list[Group]:
    """One group per definition of the sprint named by `slug`: a sprint with a project holds that project's tickets, one without holds all
    of them; tickets that name the sprint under a project with no definition come last. Tickets are in score order."""
    today = today or dt.date.today()
    every = tickets if tickets is not None else store.tickets()
    defs = [s for s in load(store.home) if s.slug == slug]
    named = [t for t in every if sprint_of(t) and slugify(sprint_of(t), 60) == slug]
    out, claimed = [], set()
    for s in sorted(defs, key=lambda s: (s.project == "", s.project)):
        mine = [t for t in named if t.id not in claimed and (not s.project or t.project == s.project)]
        claimed |= {t.id for t in mine}
        out.append(burn(Group(s, s.name + (f" ({s.project})" if s.project else ""), sorted(mine, key=score_key)), today))
    rest = [t for t in named if t.id not in claimed]
    if rest:
        out.append(burn(Group(None, "not in sprints.yaml", sorted(rest, key=score_key)), today))
    return out


def all_sprints(store: Store, tickets: list[Ticket] | None = None) -> list[tuple[str, str, int, int]]:
    """(slug, name, tickets, open) for every sprint named in sprints.yaml or by a ticket, newest start first."""
    every = tickets if tickets is not None else store.tickets()
    seen: dict[str, dict] = {}
    for s in load(store.home):
        seen.setdefault(s.slug, {"name": s.name, "start": s.start})
    for t in every:
        n = sprint_of(t)
        if n:
            seen.setdefault(slugify(n, 60), {"name": n, "start": ""})
    out = []
    for slug, info in seen.items():
        ts = [t for t in every if sprint_of(t) and slugify(sprint_of(t), 60) == slug]
        out.append((slug, info["name"], len(ts), len([t for t in ts if t.status not in CLOSED]), info["start"]))
    out.sort(key=lambda r: (r[4] == "", r[4]), reverse=True)
    return [r[:4] for r in out]
