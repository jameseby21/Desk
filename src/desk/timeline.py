"""The ticket timeline (DESK-0171): the latest change first, then every event newest first, filtered and coloured by any dimension.

Reads the ticket's activity log (DESK-0162). Each event is seen through eight dimensions: who, the kind of actor, where it came from,
and the project, company, track and sprint the ticket was in at that moment (worked out by walking back through the field changes),
plus what changed. The ticket page and `drew history` both use `view`, so the two always say the same thing. A plain default layout;
Chaz's design (DESK-0170) refines the look later."""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field

from . import activity, identity
from . import people as peoplemod

DIMENSIONS = ("actor", "kind", "source", "project", "company", "track", "sprint", "field")
LABELS = {"actor": "Who", "kind": "Kind", "source": "Where from", "project": "Project", "company": "Company", "track": "Track",
          "sprint": "Sprint", "field": "What changed"}
SOURCE_LABELS = {"ui": "on the page", "cli": "command line", "mcp": "MCP", "migration": "from old comments", "git": "from git history"}
KIND_LABELS = {"owner": "Owner", "main": "Main session", "coworker": "Coworker", "code": "Code session", "subagent": "Subagent", "person": "Person"}
FIELD_LABELS = {"comment": "Comment", "created": "Created", "git": "Saved in git"}
# A fixed palette for every dimension but people (people.yaml colours) and kinds (fixed below). Tableau 10: readable on light and dark.
PALETTE = ["#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F", "#EDC948", "#B07AA1", "#FF9DA7", "#9C755F", "#BAB0AC"]
KIND_COLOURS = {"owner": "#C8F03C", "main": "#4E79A7", "coworker": "#F28E2B", "code": "#76B7B2", "subagent": "#B07AA1", "person": "#9C755F"}
NONE = "(none)"
TRACKED = ("project", "track", "sprint")  # the ticket's own fields that a dimension follows over time


@dataclass
class Row:
    event: activity.Event
    when: str
    actor_name: str
    kind_label: str
    source_label: str
    what: str
    dims: dict = field(default_factory=dict)  # dimension -> value
    labels: dict = field(default_factory=dict)  # dimension -> what a person reads for the value
    colours: dict = field(default_factory=dict)  # dimension -> colour

    def as_json(self) -> dict:
        return {"when": self.when, "actor": self.dims["actor"], "actor_name": self.actor_name, "kind": self.dims["kind"], "kind_label": self.kind_label,
                "source": self.dims["source"], "what": self.what, "field": self.dims["field"], "old": self.event.old, "new": self.event.new,
                "project": self.dims["project"], "company": self.dims["company"], "track": self.dims["track"], "sprint": self.dims["sprint"],
                "ts": self.event.ts, "seq": self.event.seq}


@dataclass
class Facet:
    dim: str
    label: str
    values: list  # [(value, label, count, colour)]


@dataclass
class View:
    ticket_id: str
    title: str
    rows: list  # every event, newest first
    shown: list  # after the filters
    latest: Row | None
    facets: list
    filters: dict


def when_text(ts: str) -> str:
    """`2026-11-09 15:30`; a migrated comment that only had a date shows the date alone."""
    try:
        d = dt.datetime.fromisoformat(ts)
    except ValueError:
        return ts
    return d.strftime("%Y-%m-%d") if (d.hour, d.minute, d.second) == (0, 0, 0) else d.strftime("%Y-%m-%d %H:%M")


def _stable(value: str) -> str:
    return PALETTE[int(hashlib.sha1(value.encode("utf-8")).hexdigest()[:6], 16) % len(PALETTE)]


def kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind.capitalize() or "Person")


def _colour(dim: str, value: str, pe, order: dict) -> str:
    if dim == "actor":
        p = pe.get(value)
        return p.colour if p and p.colour else _stable(value)
    if dim == "kind":
        return KIND_COLOURS.get(value, PALETTE[-1])
    idx = order.get((dim, value))
    return PALETTE[idx % len(PALETTE)] if idx is not None else PALETTE[-1]


def _field_label(name: str) -> str:
    return FIELD_LABELS.get(name, name)


def rows(store, t, pe=None) -> list[Row]:
    """Every event of the ticket, newest first, with its dimensions as they were when it happened."""
    from . import companies

    pe = pe or peoplemod.load(store.home)
    evs = sorted(activity.read(store.home, t.id), key=lambda e: (e.ts, e.seq), reverse=True)
    state = {"project": str(t.meta.get("project") or ""), "track": str(t.meta.get("track") or ""), "sprint": str(t.meta.get("sprint") or "")}
    company_of: dict[str, str] = {}
    out = []
    for e in evs:
        proj = state["project"]
        if proj not in company_of:
            company_of[proj] = companies.of(store, proj) if proj else ""
        # The log keeps the name as recorded; who that is gets worked out now, so a person added to people.yaml later (an OS or git
        # user listed under os_users) re-labels their old events without the log being touched.
        p = pe.get(e.actor) or identity._person_for(pe, e.actor)
        actor = p.key if p else e.actor
        kind = activity.kind_for(actor, "", store.owner, pe) if p and e.kind == "person" else e.kind
        dims = {"actor": actor, "kind": kind, "source": e.source, "project": proj or NONE, "company": company_of[proj] or NONE,
                "track": state["track"] or NONE, "sprint": state["sprint"] or NONE, "field": e.field or e.change}
        name = p.name if p else e.actor
        klabel = kind_label(kind)
        labels = {"actor": name, "kind": klabel, "source": SOURCE_LABELS.get(e.source, e.source), "field": _field_label(dims["field"])}
        for d in ("project", "company", "track", "sprint"):
            labels[d] = dims[d]
        out.append(Row(e, when_text(e.ts), name, klabel, labels["source"], e.what or e.change, dims, labels))
        if e.change == "field" and e.field in TRACKED:  # before this event the ticket was in the old one
            state[e.field] = e.old
    return out


def parse_filters(items: list[str]) -> dict[str, set[str]]:
    """`--filter actor=james --filter kind=main,coworker` -> {actor: {james}, kind: {main, coworker}}."""
    out: dict[str, set[str]] = {}
    for item in items:
        k, sep, v = item.partition("=")
        k = k.strip().lower()
        if not sep or k not in DIMENSIONS:
            raise ValueError(f"`{item}` is not a filter. Use KEY=VALUE with KEY one of: {', '.join(DIMENSIONS)}.")
        out.setdefault(k, set()).update(x.strip().lower() for x in v.split(",") if x.strip())
    return out


def matches(r: Row, filters: dict[str, set[str]]) -> bool:
    """Every filtered dimension must match one of its values (by value or by the label a person reads); dimensions are ANDed."""
    for k, vals in filters.items():
        if vals and str(r.dims.get(k, "")).lower() not in vals and str(r.labels.get(k, "")).lower() not in vals:
            return False
    return True


def view(store, t, filters: dict[str, set[str]] | None = None) -> View:
    pe = peoplemod.load(store.home)
    rs = rows(store, t, pe)
    order: dict = {}
    for d in DIMENSIONS:
        for i, v in enumerate(sorted({r.dims[d] for r in rs})):
            order[(d, v)] = i
    for r in rs:
        r.colours = {d: _colour(d, r.dims[d], pe, order) for d in DIMENSIONS}
    facets = []
    for d in DIMENSIONS:
        counts: dict[str, list] = {}
        for r in rs:
            c = counts.setdefault(r.dims[d], [r.labels[d], 0, r.colours[d]])
            c[1] += 1
        vals = sorted(((v, lab, n, col) for v, (lab, n, col) in counts.items()), key=lambda x: (-x[2], x[1].lower()))
        facets.append(Facet(d, LABELS[d], vals))
    filters = filters or {}
    shown = [r for r in rs if matches(r, filters)]
    return View(t.id, str(t.meta.get("title") or ""), rs, shown, rs[0] if rs else None, facets, filters)


def latest_line(r: Row | None) -> str:
    if r is None:
        return "No changes recorded yet."
    return f"{r.actor_name} ({r.kind_label}) · {r.what} · {r.when}"


def _dot(colour: str, on: bool) -> str:
    if not on:
        return "*"
    h = colour.lstrip("#")
    try:
        rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return "*"
    return f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m●\x1b[0m"


def _line(r: Row, by: str | None, colour: bool) -> str:
    return f"  {_dot(r.colours.get(by or 'kind', ''), colour)} {r.when:<16}  {r.actor_name} ({r.kind_label}) · {r.source_label} · {r.what}"


def text(v: View, by: str | None = None, colour: bool = False) -> str:
    """What `drew history` prints: the latest change, then the events newest first, grouped by `by` when given, with counts."""
    out = [f"{v.ticket_id} · {v.title}", f"Latest change: {latest_line(v.latest)}"]
    if not v.rows:
        out.append("Its activity log is empty. `drew migrate events --apply` brings in old comments and git history.")
        return "\n".join(out)
    filt = "; ".join(f"{k}={','.join(sorted(vals))}" for k, vals in v.filters.items())
    out.append(f"{len(v.shown)} of {len(v.rows)} events, newest first" + (f" (filter: {filt})" if filt else "") + ":")
    if not by:
        out += [_line(r, None, colour) for r in v.shown]
        return "\n".join(out)
    groups: dict[str, list[Row]] = {}
    for r in v.shown:
        groups.setdefault(r.dims[by], []).append(r)
    for val, rs in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[1][0].labels[by].lower())):
        out.append(f"{_dot(rs[0].colours[by], colour)} {LABELS[by]}: {rs[0].labels[by]} ({len(rs)})")
        out += [_line(r, by, colour) for r in rs]
    return "\n".join(out)
