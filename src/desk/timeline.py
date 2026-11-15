"""The ticket timeline (DESK-0171): the latest change first, then every event newest first, filtered and coloured by any dimension.

DESK-0305 makes it visual on the ticket page, open by default: a status strip (one coloured segment per status period, from the
created date to now, width by time) and an event rail (one dot per event, newest first, grouped by day, the first 12 shown, filter
chips by what happened). `periods` and `rail_days` build them; the page only draws.

Reads the ticket's activity log (DESK-0162). Each event is seen through eight dimensions: who, the kind of actor, where it came from,
and the project, company, track and sprint the ticket was in at that moment (worked out by walking back through the field changes),
plus what changed. The ticket page and `drew history` both use `view`, so the two always say the same thing. A plain default layout;
Chaz's design (DESK-0170) refines the look later."""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field

from markupsafe import escape as _escape

from . import activity, identity
from . import people as peoplemod
from . import words
from .ticket import STATUSES

DIMENSIONS = ("actor", "kind", "source", "project", "company", "track", "sprint", "field")
LABELS = {"actor": "Who", "kind": "Kind", "source": "Where from", "project": "Project", "company": "Company", "track": "Track",
          "sprint": "Sprint", "field": "What changed"}
SOURCE_LABELS = {"ui": "on the page", "cli": "command line", "mcp": "MCP", "migration": "from old comments", "git": "from git history"}
KIND_LABELS = {"owner": "Owner", "main": "Main session", "coworker": "Coworker", "code": "Code session", "subagent": "Subagent", "person": "Person",
               "rule": "Rule"}
FIELD_LABELS = {"comment": "Comment", "created": "Created", "git": "Saved in git"}
# A fixed palette for every dimension but people (people.yaml colours) and kinds (fixed below). Tableau 10: readable on light and dark.
PALETTE = ["#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F", "#EDC948", "#B07AA1", "#FF9DA7", "#9C755F", "#BAB0AC"]
KIND_COLOURS = {"owner": "#C8F03C", "main": "#4E79A7", "coworker": "#F28E2B", "code": "#76B7B2", "subagent": "#B07AA1", "person": "#9C755F",
                "rule": "#E15759"}
NONE = "(none)"
TRACKED = ("project", "track", "sprint")  # the ticket's own fields that a dimension follows over time

# DESK-0305: what kind of thing happened, for the rail's dot and the filter chips. Decisions and status changes are drawn stronger.
EVENT_KINDS = {"created": "Created", "status": "Status change", "decision": "Decision", "comment": "Comment", "field": "Field change",
               "section": "Section edit", "git": "Git commit", "attachment": "Attachment"}
GROUP_OF = {"created": "changes", "status": "status", "decision": "decisions", "comment": "comments", "field": "changes", "section": "changes",
            "git": "git", "attachment": "changes"}
CHIPS = (("all", "All"), ("decisions", "Decisions"), ("status", "Status"), ("comments", "Comments"), ("changes", "Changes"), ("git", "Git"))
STRONG = {"status", "decision"}
FIRST = 12  # events shown before "Show all N events"


def event_kind(e: activity.Event) -> str:
    """created, status, decision, comment, field, section, git or attachment: what the rail's dot says happened."""
    if e.change in ("created", "comment", "git"):
        return e.change
    if e.change == "field":
        return "status" if e.field == "status" else "field"
    if e.change == "section":
        return {"Decision": "decision", "Attachments": "attachment"}.get(e.field, "section")
    return "field"


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
    ekind: str = "field"  # event_kind: what the rail's dot says happened
    index: int = 0  # position newest first, from 0; the rail shows the first FIRST

    @property
    def group(self) -> str:
        return GROUP_OF.get(self.ekind, "changes")

    @property
    def ekind_label(self) -> str:
        return EVENT_KINDS.get(self.ekind, "Change")

    @property
    def strong(self) -> bool:
        return self.ekind in STRONG

    @property
    def shown_what(self) -> str:
        """The one line a person reads on the page: a status change in plain words (In progress → Sent for review), else `what`.
        The log keeps the keys as written."""
        if self.ekind == "status":
            return f"Status: {words.screen(self.event.old) if self.event.old else 'empty'} → {words.screen(self.event.new) if self.event.new else 'empty'}"
        return self.what

    @property
    def time(self) -> str:
        """`15:30`; empty for a migrated comment that only had a date."""
        ts = self.event.ts
        if len(ts) >= 16 and ts[10] == "T" and ts[13] == ":":  # the log's own form: no parsing needed (2,000 rows, DESK-0305)
            return "" if ts[11:19] == "00:00:00" else ts[11:16]
        d = _ts(ts)
        return "" if d is None or (d.hour, d.minute, d.second) == (0, 0, 0) else d.strftime("%H:%M")

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
class Period:
    """One stretch of the ticket's life in one status, for the status strip."""
    status: str
    start: dt.datetime
    end: dt.datetime
    current: bool = False
    share: float = 0.0  # percent of the strip's width, by time

    @property
    def seconds(self) -> float:
        return max(0.0, (self.end - self.start).total_seconds())

    @property
    def known(self) -> bool:
        return self.status in STATUSES

    @property
    def css(self) -> str:
        return self.status if self.known else "other"

    @property
    def label(self) -> str:
        return words.screen(self.status) if self.status else "Not recorded"

    @property
    def text(self) -> str:
        """What a person reads for this period: status, from when, for how long."""
        return f"{self.label}, from {day_text(self.start, with_time=True)}, for {duration_text(self.seconds)}" + (" (now)" if self.current else "")


@dataclass
class Day:
    label: str  # Today, Yesterday, Fri 13 Nov
    iso: str
    rows: list
    hidden: bool = False  # every event of the day is past the first FIRST


@dataclass
class View:
    ticket_id: str
    title: str
    rows: list  # every event, newest first
    shown: list  # after the filters
    latest: Row | None
    facets: list
    filters: dict
    periods: list = field(default_factory=list)  # status periods, oldest first
    days: list = field(default_factory=list)  # the rail: every event grouped by day, newest first
    counts: dict = field(default_factory=dict)  # chip key -> events
    created_text: str = ""
    first: int = FIRST
    chips: tuple = CHIPS

    def rail(self, mask=None):
        """The rail as safe HTML for the ticket page (see rail_html); `mask` is the page's profanity filter, or None when it is off."""
        from markupsafe import Markup

        return Markup(rail_html(self, mask))


def when_text(ts: str) -> str:
    """`2026-11-09 15:30`; a migrated comment that only had a date shows the date alone."""
    try:
        d = dt.datetime.fromisoformat(ts)
    except ValueError:
        return ts
    return d.strftime("%Y-%m-%d") if (d.hour, d.minute, d.second) == (0, 0, 0) else d.strftime("%Y-%m-%d %H:%M")


def _ts(ts) -> dt.datetime | None:
    """A timestamp or date from the log or the ticket (str, date or datetime) as a naive datetime; None when it cannot be read."""
    if isinstance(ts, dt.datetime):
        return ts.replace(tzinfo=None, microsecond=0)
    if isinstance(ts, dt.date):
        return dt.datetime(ts.year, ts.month, ts.day)
    try:
        return dt.datetime.fromisoformat(str(ts).strip()).replace(tzinfo=None, microsecond=0)
    except ValueError:
        return None


def day_text(d: dt.datetime, today: dt.date | None = None, with_time: bool = False) -> str:
    """`Fri 13 Nov`, with the year when it is not this year, and `10:00` after it when asked and known."""
    today = today or dt.date.today()
    out = f"{d:%a} {d.day} {d:%b}" + ("" if d.year == today.year else f" {d.year}")
    return out + (f" {d:%H:%M}" if with_time and (d.hour, d.minute, d.second) != (0, 0, 0) else "")


def day_label(d: dt.date, today: dt.date) -> str:
    if d == today:
        return "Today"
    if d == today - dt.timedelta(days=1):
        return "Yesterday"
    return day_text(dt.datetime(d.year, d.month, d.day), today)


def duration_text(seconds: float) -> str:
    """How long, in the one plain unit a person reads: under a minute, 5 minutes, 3 hours, 2 days, 3 weeks, 4 months, 2 years."""
    for unit, size in (("year", 365 * 86400), ("month", 30 * 86400), ("week", 7 * 86400), ("day", 86400), ("hour", 3600), ("minute", 60)):
        n = int(seconds // size)
        if n >= 1:
            return f"{n} {unit}" + ("s" if n != 1 else "")
    return "under a minute"


def periods(created, events, status: str, now: dt.datetime | None = None) -> list[Period]:
    """The ticket's life as status periods, oldest first, from its created date to now. Built from the status field events
    (old -> new) in time order (the sequence number breaks a tie); a move to the status it is already in is not a period; a log that
    starts after the ticket was created opens with the status before the first move. No status events: one period, created to now,
    in the current status. When the ticket's file says a status the log never reached (an edit outside Desk), that status closes
    the strip as the current one. Widths (`share`) are percent of the whole time."""
    now = (now or dt.datetime.now()).replace(microsecond=0)
    moves = sorted(((d, e.seq, e) for e in events if e.change == "field" and e.field == "status" and (d := _ts(e.ts)) is not None),
                   key=lambda x: (x[0], x[1]))
    start = _ts(created)
    stamps = [d for e in events if (d := _ts(e.ts)) is not None]
    if start is None:
        start = min(stamps) if stamps else now
    if stamps and min(stamps) < start:  # the log is older than the created date it says: the log wins
        start = min(stamps)
    start = min(start, now)
    cur = (moves[0][2].old or moves[0][2].new) if moves else (status or "")
    out: list[Period] = []
    for d, _seq, e in moves:
        new = e.new or ""
        if not new or new == cur:
            continue
        at = min(max(d, start), now)
        out.append(Period(cur, start, at))
        cur, start = new, at
    if status and cur != status:
        out.append(Period(cur, start, now))
        cur, start = status, now
    out.append(Period(cur, start, max(now, start), current=True))
    total = sum(p.seconds for p in out)
    for p in out:
        p.share = round(100 * p.seconds / total, 3) if total else round(100 / len(out), 3)
    return out


def rail_days(rs: list, today: dt.date, first: int = FIRST) -> list[Day]:
    """The rail's day groups, newest first; a day whose events are all past the first `first` starts hidden."""
    out: list[Day] = []
    for r in rs:
        d = _ts(r.event.ts)
        iso = d.date().isoformat() if d else ""
        if not out or out[-1].iso != iso:
            out.append(Day(day_label(d.date(), today) if d else "Date not recorded", iso, []))
        out[-1].rows.append(r)
    for day in out:
        day.hidden = all(r.index >= first for r in day.rows)
    return out


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
        out.append(Row(e, when_text(e.ts), name, klabel, labels["source"], e.what or e.change, dims, labels, ekind=event_kind(e), index=len(out)))
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


def view(store, t, filters: dict[str, set[str]] | None = None, now: dt.datetime | None = None) -> View:
    # to the minute: the strip says minutes at the finest, and two renders a moment apart draw the same page (test_flags compares three)
    now = now or dt.datetime.now().replace(second=0, microsecond=0)
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
    counts = {k: (len(rs) if k == "all" else sum(1 for r in rs if r.group == k)) for k, _lab in CHIPS}
    created = _ts(t.meta.get("created"))
    return View(t.id, str(t.meta.get("title") or ""), rs, shown, rs[0] if rs else None, facets, filters,
                periods=periods(t.meta.get("created"), [r.event for r in rs], str(t.meta.get("status") or ""), now),
                days=rail_days(rs, now.date()), counts=counts, created_text=day_text(created, now.date()) if created else "not recorded")


def _esc(v) -> str:
    return str(_escape(str(v)))  # markupsafe's C escape: quotes too, and about ten times faster than html.escape here


def rail_html(v: View, mask=None) -> str:
    """The rail's day groups and events as HTML, built here rather than in the template: a 2,000 event ticket renders in a few
    milliseconds this way, against about 200 in a Jinja loop (DESK-0305's 300 ms budget for the whole page). `mask` (str -> str, the
    profanity filter) is applied to every value before escaping, as the template's finalize does for the rest of the page."""
    _e = (lambda x: str(_escape(mask(str(x))))) if mask else _esc
    out, seen = [], {}
    for day in v.days:
        out.append(f'<li class="tl-day" data-day="{_e(day.iso)}"{" hidden" if day.hidden else ""}><h3 class="tl-dayh">{_e(day.label)}</h3>\n<ol class="tl-evs">')
        for r in day.rows:
            key = tuple(r.dims.values())  # most events share their dimensions: escape each set once
            dims = seen.get(key)
            if dims is None:
                dims = seen[key] = "".join(f' data-{d}="{_e(val)}"' for d, val in r.dims.items())
            out.append(
                f'<li class="tl-ev ek-{r.ekind}{" tl-strong" if r.strong else ""}" data-group="{r.group}" data-i="{r.index}"{" hidden" if r.index >= v.first else ""}{dims}>'
                f'<time class="tl-time" datetime="{_e(r.event.ts)}" title="{_e(r.when)}">{r.time}</time><i class="tl-mark" aria-hidden="true"></i>'
                f'<div class="tl-body"><div class="tl-head"><span class="tl-ekind">{_e(r.ekind_label)}</span><span class="tl-actor" title="{_e(r.source_label)}">'
                f'<i class="tl-dot" style="background:{_e(r.colours.get("kind", ""))}"></i>{_e(r.actor_name)} <small>{_e(r.kind_label)}</small></span></div>'
                f'<div class="tl-what">{_e(r.shown_what)}</div></div></li>')
        out.append("</ol></li>")
    return "\n".join(out)


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
