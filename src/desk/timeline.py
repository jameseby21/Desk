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
SOURCE_LABELS = {"ui": "on the page", "cli": "command line", "mcp": "MCP", "migration": "from old comments", "git": "from git history",
                 "file": "from the ticket file"}
KIND_LABELS = {"owner": "Owner", "main": "Main session", "coworker": "Coworker", "code": "Code session", "subagent": "Subagent", "person": "Person",
               "rule": "Rule"}
FIELD_LABELS = {"comment": "Comment", "created": "Created", "git": "Saved in git"}
# A fixed palette for every dimension but people (people.yaml colours) and kinds (fixed below). Tableau 10: readable on light and dark.
PALETTE = ["#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F", "#EDC948", "#B07AA1", "#FF9DA7", "#9C755F", "#BAB0AC"]
# DESK-0329: one colour per kind of actor, the same on the activity chart, the rail's dots, the filters and the Undo page. The three
# hues (owner amber, Patch teal, coworker violet) pass every dataviz palette check, all pairs, on the card colour of both themes with
# one value each (lightness band, chroma floor, colour-blind and normal-vision separation, 3:1 contrast); a fourth hue fails the
# all-pairs floors, so agents, rules and other people share a warm sand and the chart tells them apart by shape. None is a status
# colour (DESK-0306): each is at least 10 OKLab units from every status. The main session and code sessions are both Patch's.
# Before: owner #C8F03C (the lime of Sent for review), main #4E79A7 (the blue of In progress), coworker #F28E2B, code #76B7B2,
# subagent #B07AA1, person #9C755F, rule #E15759.
KIND_COLOURS = {"owner": "#C76A00", "main": "#12968C", "coworker": "#8A5FD9", "code": "#12968C", "subagent": "#A08C70", "person": "#A08C70",
                "rule": "#A08C70"}
NONE = "(none)"
# The key a Claude Code session or Desk itself is recorded under when no person in people.yaml owns it. A person reads "Code session", not the key.
RAW_SESSION_KEYS = identity.RAW_SESSION_KEYS
def clock() -> dt.datetime:
    """The timeline's only reading of the computer clock: the page, the strip and the dates all ask here, so a test can pin one
    moment for the whole render (DESK-0282: a route test drew today's strip against dates fixed on 15 Nov)."""
    return dt.datetime.now()


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
    index: int = 0  # position newest first, from 0
    unit: int = 0  # position on the rail, newest first: a fold of git saves is one; the rail shows the first FIRST
    run: str = ""  # the fold this git save sits in (consecutive saves by one actor on one day), or ""

    @property
    def group(self) -> str:
        return GROUP_OF.get(self.ekind, "changes")

    @property
    def ekind_label(self) -> str:
        return EVENT_KINDS.get(self.ekind, "Change")

    @property
    def who(self) -> str:
        """`Chaz (Coworker)`; a name that already says its kind (`Code session`) is written once, never `Code session (Code session)`."""
        return self.actor_name if self.actor_name == self.kind_label else f"{self.actor_name} ({self.kind_label})"

    @property
    def who_html(self) -> str:
        """The rail's name and kind tag, escaped; the tag is left out when the name already says it."""
        tag = "" if self.actor_name == self.kind_label else f" <small>{_esc(self.kind_label)}</small>"
        return f"{_esc(self.actor_name)}{tag}"

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
    today: dt.date | None = None  # the strip's own "now", so its words name the year by that date, never by the machine clock
    since_unknown: bool = False  # the status is on record at `start`, but nothing says when it began (it may be earlier)
    note: str = ""  # for a span with no record: what the next record says about it
    on_owner: bool = False  # the ticket was waiting on the owner: in review, or blocked with blocked_on naming them (set by view)

    @property
    def unknown(self) -> bool:
        """No record covers this span: drawn hatched, named Not recorded, never given a status."""
        return not self.status

    @property
    def seconds(self) -> float:
        return max(0.0, (self.end - self.start).total_seconds())

    @property
    def known(self) -> bool:
        return self.status in STATUSES

    @property
    def css(self) -> str:
        return "unknown" if self.unknown else (self.status if self.known else "other")

    @property
    def label(self) -> str:
        return words.screen(self.status) if self.status else "Not recorded"

    @property
    def text(self) -> str:
        """What a person reads for this period: status, from when, for how long. A status whose start is not on record says
        `or earlier` and `at least`; a span no record covers says so."""
        start = day_text(self.start, self.today, with_time=True)
        if self.since_unknown and self.seconds < 60:
            return f"{self.label} (now); when it started is not recorded"
        if self.since_unknown:
            out = f"{self.label}, from {start} or earlier, for at least {duration_text(self.seconds)}"
        else:
            out = f"{self.label}, from {start}, for {duration_text(self.seconds)}"
        if self.unknown:
            out += ": no status record covers this time" + (f"; {self.note}" if self.note else "")
        return out + (" (now)" if self.current else "")

    @property
    def key(self) -> dict:
        """The key's words after the status name, in parts, so a phone can drop the weekday, the time and the note and keep each
        period to about one line: {pre, wd, dm, tm, post, note}; joined they read as `text` without the name."""
        now = " (now)" if self.current else ""
        if self.since_unknown and self.seconds < 60:
            return {"pre": "now; when it started is not recorded", "wd": "", "dm": "", "tm": "", "post": "", "note": ""}
        d, today = self.start, self.today or clock().date()
        tm = f" {d:%H:%M}" if (d.hour, d.minute, d.second) != (0, 0, 0) else ""
        dm = f"{d.day} {d:%b}" + ("" if d.year == today.year else f" {d.year}")
        how = f"for at least {duration_text(self.seconds)}" if self.since_unknown else f"for {duration_text(self.seconds)}"
        post = (" or earlier, " if self.since_unknown else ", ") + how + ("" if self.unknown else now)
        note = (": no status record covers this time" + (f"; {self.note}" if self.note else "") + now) if self.unknown else ""
        return {"pre": "from ", "wd": f"{d:%a}", "dm": dm, "tm": tm, "post": post, "note": note}


@dataclass
class Day:
    label: str  # Today, Yesterday, Fri 13 Nov
    iso: str
    rows: list
    hidden: bool = False  # every event of the day is past the first FIRST


@dataclass
class Fold:
    """Consecutive git saves by one actor on one day, drawn as one row that opens in place (DESK-0305 review: 88% of a real
    store's events are git saves, and unfolded they pushed the people's events and the status changes off the first screen)."""
    key: str
    rows: list  # newest first

    @property
    def text(self) -> str:
        a, b = self.rows[-1].time, self.rows[0].time
        return f"{len(self.rows)} git saves" + (f", {a}–{b}" if a and b and a != b else (f", {b}" if b else ""))


@dataclass
class View:
    ticket_id: str
    title: str
    rows: list  # every event, newest first
    shown: list  # after the filters
    latest: Row | None  # the newest change that is not a git save (a git save only when there is nothing else)
    facets: list
    filters: dict
    git_after: int = 0  # git saves newer than `latest`
    units: int = 0  # rows on the rail once git saves are folded
    first_events: int = 0  # events the rail shows before Show all (a fold counts all its saves)
    axis: list = field(default_factory=list)  # date marks between Created and Now: (percent from the left, label, css class)
    on_you: str = ""  # how long the ticket waited on the owner, in words, or ""
    key_first: int = 12  # the newest periods listed in the key before "Show the earlier periods"
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
    today = today or clock().date()
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


@dataclass
class _Anchor:
    """One record of the ticket's status at a moment: `before` and `after` are what it says the status was just before and just
    after (None when it does not say); `creation` marks where the ticket's life starts (its created event, or the first commit)."""
    at: dt.datetime
    order: tuple
    before: str | None
    after: str | None
    creation: bool = False


SAME_SAVE = 5  # seconds: a file written this close to a record was written by that record's save


def periods(created, events, status: str, now: dt.datetime | None = None, snapshots=(), done_at=None, saved_at=None) -> list[Period]:
    """The ticket's life as status periods, oldest first, from its created date to now. Widths (`share`) are percent of the time.

    Built only from records, never assumed (DESK-0305 review: a strip that put the current status across the whole life of a ticket
    whose status changes were made in git). The records: the log's status changes (old -> new), its created event, the status the
    ticket file said at each commit (`snapshots`: (time, status, first commit?)), `done_at` for a done ticket, and the file as it
    is now (written at `saved_at`). Between two records the earlier one holds; when the later one says the status was something
    else just before it, the change happened at a time nobody recorded, and that span is Not recorded. Before the first record
    is Not recorded too, unless that record is the ticket's creation on its created day. A status the file has that no record
    reached starts when the file was last written (`saved_at`), or now."""
    now = (now or clock()).replace(microsecond=0)
    anchors: list[_Anchor] = []
    for e in events:
        d = _ts(e.ts)
        if d is None:
            continue
        if e.change == "field" and e.field == "status" and e.new:
            anchors.append(_Anchor(d, (0, e.seq), e.old or None, e.new))
        elif e.change == "created" and e.source != "file":
            anchors.append(_Anchor(d, (0, e.seq), None, None, creation=True))
    for at, st, first in snapshots:
        d = _ts(at)
        if d is not None and (st or first):
            anchors.append(_Anchor(d, (1, 0), None, st or None, creation=bool(first)))
    done = _ts(done_at) if done_at else None
    if status == "done" and done is not None and not any(a.after == "done" for a in anchors):
        anchors.append(_Anchor(done, (2, 0), None, "done"))
    anchors.sort(key=lambda a: (a.at, a.order))
    for a in anchors:
        a.at = min(a.at, now)
    start = _ts(created)
    stamps = [d for e in events if (d := _ts(e.ts)) is not None] + [a.at for a in anchors]
    if start is None:
        start = min(stamps) if stamps else now
    if stamps and min(stamps) < start:  # the records are older than the created date the file says: the records win
        start = min(stamps)
    start = min(start, now)
    saved = min(max(_ts(saved_at), start), now) if saved_at is not None and _ts(saved_at) is not None else None
    out: list[Period] = []

    def add(st, a, b, **kw):
        out.append(Period(st or "", a, max(a, b), **kw))

    if not anchors:
        cut = saved if saved is not None and saved > start else now
        add("", start, cut)
        add(status, cut, now, current=True, since_unknown=True)
        return _finish(out, now)
    first = anchors[0]
    if first.creation and first.at.date() == start.date():
        start = first.at  # created that day: the strip starts when it was made
    elif first.at > start:
        add("", start, first.at, note=f"it was {words.screen(first.before)} just before {day_text(first.at, now.date(), with_time=True)}" if first.before else "")
    carry: str | None = None
    for i, a in enumerate(anchors):
        nxt = anchors[i + 1] if i + 1 < len(anchors) else None
        st = a.after if a.after is not None else carry
        if nxt is not None:
            if st is None:
                st = nxt.before
            elif nxt.before is not None and nxt.before != st:
                add("", a.at, nxt.at, note=f"it was {words.screen(st)} at {day_text(a.at, now.date(), with_time=True)} and "
                                           f"{words.screen(nxt.before)} just before {day_text(nxt.at, now.date(), with_time=True)}")
                carry = nxt.before
                continue
            add(st, a.at, nxt.at)
            carry = st
            continue
        # the last record, then the file as it is now
        cur = status or st or ""
        quiet = saved is None or saved <= a.at + dt.timedelta(seconds=SAME_SAVE)  # the file not written since this record
        if st is None:
            if quiet and saved is not None:
                add(cur, a.at, now, current=True)
            else:
                cut = saved if saved is not None else now
                add("", a.at, cut)
                add(cur, cut, now, current=True, since_unknown=True)
        elif st == cur:
            add(cur, a.at, now, current=True)
        else:
            cut = saved if saved is not None and saved > a.at else now
            add(st, a.at, cut)
            add(cur, cut, now, current=True, since_unknown=True)
    return _finish(out, now)


def _finish(out: list[Period], now: dt.datetime) -> list[Period]:
    """Join neighbours in the same status (a record that says nothing new is not a period), then widths by time."""
    joined: list[Period] = []
    for p in out:
        if joined and joined[-1].status == p.status:
            last = joined[-1]
            last.end, last.current = max(last.end, p.end), p.current
            last.note = last.note or p.note
            continue
        joined.append(p)
    if joined and joined[0].unknown and joined[0].seconds == 0 and len(joined) > 1:
        joined.pop(0)  # an empty span before a record at the very start says nothing
    total = sum(p.seconds for p in joined)
    for p in joined:
        p.today = now.date()
        p.share = round(100 * p.seconds / total, 3) if total else round(100 / len(joined), 3)
    return joined


def axis_marks(start: dt.datetime, now: dt.datetime) -> list[tuple[float, str, str]]:
    """Date marks for a strip longer than two days: the midnight nearest the middle (always drawn) and the ones nearest a quarter and
    three quarters (wide screens only), each as (percent from the left, `Wed 11 Nov`, class). None within 12% of either end, where
    they would crowd Created and Now."""
    total = (now - start).total_seconds()
    if total <= 2 * 86400:
        return []
    out, used = [], set()
    for frac, cls in ((0.5, "mid"), (0.25, "q"), (0.75, "q")):
        at = start + dt.timedelta(seconds=total * frac)
        day = dt.datetime(at.year, at.month, at.day) + (dt.timedelta(days=1) if at.hour >= 12 else dt.timedelta())
        pct = round(100 * (day - start).total_seconds() / total, 2)
        if day.date() in used or not 12 <= pct <= 88:
            continue
        used.add(day.date())
        out.append((pct, day_text(day, now.date()), cls))
    return sorted(out)


def waiting_on_owner(ps: list[Period], t, owner: str) -> str:
    """Mark the periods the ticket waited on the owner (review; blocked when blocked_on names the owner, known only for the current
    blocked period) and say how long in words: `now, for 9 hours; 6 days in all`, or `6 days in all`, or ""."""
    who = str(t.meta.get("blocked_on") or "").lower()
    for p in ps:
        p.on_owner = p.status == "review" or (p.status == "blocked" and p.current and bool(owner) and owner.lower() in who)
    mine = [p for p in ps if p.on_owner]
    if not mine:
        return ""
    total = duration_text(sum(p.seconds for p in mine))
    cur = ps[-1] if ps and ps[-1].on_owner else None
    if cur is not None:
        return f"now, for {'at least ' if cur.since_unknown else ''}{duration_text(cur.seconds)}" + ("" if len(mine) == 1 else f"; {total} in all")
    return f"{total} in all"


def fold_git(rs: list) -> int:
    """Give every row its rail position (`unit`), folding each run of two or more consecutive git saves by one actor on one day into
    one position (`run`). Returns the number of positions."""
    i = unit = 0
    while i < len(rs):
        r = rs[i]
        j = i
        if r.ekind == "git":
            while (j + 1 < len(rs) and rs[j + 1].ekind == "git" and rs[j + 1].dims.get("actor") == r.dims.get("actor")
                   and rs[j + 1].event.ts[:10] == r.event.ts[:10]):
                j += 1
        for m in rs[i:j + 1]:
            m.unit, m.run = unit, (f"f{unit}" if j > i else "")
        unit += 1
        i = j + 1
    return unit


def latest_of(rs: list) -> tuple[Row | None, int]:
    """(the newest change that is not a git save, the git saves newer than it). Git saves are 88% of a real store's events: the
    line names what a person or a status did, and counts the saves after it."""
    for r in rs:
        if r.ekind != "git":
            return r, r.index
    return (rs[0], 0) if rs else (None, 0)


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
        day.hidden = all(r.unit >= first for r in day.rows)
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


def rows(store, t, pe=None, events=None) -> list[Row]:
    """Every event of the ticket (its log, or `events` when given), newest first, with its dimensions as they were when it happened."""
    from . import companies

    pe = pe or peoplemod.load(store.home)
    evs = sorted(activity.read(store.home, t.id) if events is None else events, key=lambda e: (e.ts, e.seq), reverse=True)
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
        klabel = kind_label(kind)
        name = p.name if p else (klabel if kind == "code" and e.actor.lower() in RAW_SESSION_KEYS else e.actor)
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


def events_of(store, t, pe) -> tuple[list, list, dt.datetime | None]:
    """(every event, the ticket file's git snapshots, when the file was last written). The events are the activity log plus what
    the ticket's git history proves and the log lacks (commits and status changes made outside Desk, DESK-0305 review), plus a
    Created event from the file's created date when nothing recorded the creation: so a ticket changed only in git, or never through
    Desk, still has its history on the page and in `drew history`, without anything being written."""
    logged = activity.read(store.home, t.id)
    path = getattr(t, "path", None)
    snaps = activity.snapshots(store.home, t.id, path, pe, store.owner)
    extra = activity.events_from_git(t.id, snaps, logged)
    created = _ts(t.meta.get("created"))
    if created is not None and not any(e.change == "created" for e in logged):
        extra.append(activity.Event(t.id.upper(), created.isoformat(), "not recorded", "person", "file", "created", "created", "",
                                    str(t.meta.get("title") or ""), "Created (the date in the ticket file)"))
    saved = None
    try:
        saved = dt.datetime.fromtimestamp(path.stat().st_mtime).replace(microsecond=0) if path else None
    except OSError:
        saved = None
    return logged + extra, snaps, saved


def view(store, t, filters: dict[str, set[str]] | None = None, now: dt.datetime | None = None) -> View:
    # to the minute: the strip says minutes at the finest, and two renders a moment apart draw the same page (test_flags compares three)
    now = now or clock().replace(second=0, microsecond=0)
    pe = peoplemod.load(store.home)
    evs, snaps, saved = events_of(store, t, pe)
    rs = rows(store, t, pe, evs)
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
    units = fold_git(rs)
    latest, git_after = latest_of(rs)
    shown = [r for r in rs if matches(r, filters)]
    counts = {k: (len(rs) if k == "all" else sum(1 for r in rs if r.group == k)) for k, _lab in CHIPS}
    created = _ts(t.meta.get("created"))
    ps = periods(t.meta.get("created"), evs, str(t.meta.get("status") or ""), now, snapshots=[(x.ts, x.status, x.first) for x in snaps],
                 done_at=t.meta.get("done_at"), saved_at=saved)
    return View(t.id, str(t.meta.get("title") or ""), rs, shown, latest, facets, filters, git_after=git_after, units=units,
                first_events=sum(1 for r in rs if r.unit < FIRST), axis=axis_marks(ps[0].start, now) if ps else [],
                on_you=waiting_on_owner(ps, t, store.owner), periods=ps,
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
        runs: dict[str, list] = {}
        for r in day.rows:
            if r.run:
                runs.setdefault(r.run, []).append(r)
        for r in day.rows:
            key = tuple(r.dims.values())  # most events share their dimensions: escape each set once
            dims = seen.get(key)
            if dims is None:
                dims = seen[key] = "".join(f' data-{d}="{_e(val)}"' for d, val in r.dims.items())
            run_attr = (' data-run="' + r.run + '"') if r.run else ""
            if r.run and runs[r.run][0] is r:  # the fold's own row, before its saves: one line, opens them in place
                f = Fold(r.run, runs[r.run])
                out.append(
                    f'<li class="tl-foldh ek-git" data-run="{r.run}" data-group="git"{" hidden" if r.unit >= v.first else ""}{dims}>'
                    f'<time class="tl-time" datetime="{_e(r.event.ts)}" title="{_e(r.when)}">{r.time}</time><i class="tl-mark" aria-hidden="true"></i>'
                    f'<div class="tl-body"><button type="button" class="tl-foldb" aria-expanded="false"><span class="tl-fn">{_e(f.text)}</span></button>'
                    f'<span class="tl-actor" title="{_e(r.source_label)}"><i class="tl-dot" style="background:{_e(r.colours.get("kind", ""))}"></i>{r.who_html}</span></div></li>')
            out.append(
                f'<li class="tl-ev ek-{r.ekind}{" tl-strong" if r.strong else ""}" data-group="{r.group}" data-i="{r.index}"'
                f'{run_attr}{" hidden" if r.run or r.unit >= v.first else ""}{dims}>'
                f'<time class="tl-time" datetime="{_e(r.event.ts)}" title="{_e(r.when)}">{r.time}</time><i class="tl-mark" aria-hidden="true"></i>'
                f'<div class="tl-body"><div class="tl-head"><span class="tl-ekind">{_e(r.ekind_label)}</span><span class="tl-actor" title="{_e(r.source_label)}">'
                f'<i class="tl-dot" style="background:{_e(r.colours.get("kind", ""))}"></i>{r.who_html}</span></div>'
                f'<div class="tl-what">{_e(r.shown_what)}</div></div></li>')
        out.append("</ol></li>")
    return "\n".join(out)


def latest_line(r: Row | None, git_after: int = 0) -> str:
    if r is None:
        return "No changes recorded yet."
    more = f" (+{git_after} git save{'s' if git_after != 1 else ''} since)" if git_after else ""
    return f"{r.who} · {r.what} · {r.when}{more}"


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
    return f"  {_dot(r.colours.get(by or 'kind', ''), colour)} {r.when:<16}  {r.who} · {r.source_label} · {r.what}"


def text(v: View, by: str | None = None, colour: bool = False) -> str:
    """What `drew history` prints: the latest change, then the events newest first, grouped by `by` when given, with counts."""
    out = [f"{v.ticket_id} · {v.title}", f"Latest change: {latest_line(v.latest, v.git_after)}"]
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
