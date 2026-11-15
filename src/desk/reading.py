"""Reading mode (DESK-0287): a ticket opens on what changed and what happens next, and the long parts wait folded.

Six small pieces, all read-only except the last-seen marker:

- **Last seen**, per viewer and ticket, in `.desk/state/last-seen.json`, with a copy of the ticket as it was then in
  `.desk/state/seen/<viewer>/<ID>.md`. Written when the viewer opens the ticket page (opening is not acting: Needs me keeps its own
  record). The copy is what the diff since the last visit compares with.
- **Next action**: the ask to the viewer if there is one, else the step the ticket's status calls for, with its smallest step.
- **Report reading mode**: a Report that opens with a summary (a paragraph of up to three lines, or up to three findings) shows the
  summary and the one ask on top and folds the rest; `drew validate` warns when a Report over 1,200 characters has no such summary.
- **Why am I seeing this**: one short line for a row on Today and Needs me (asked of you, changed by someone, deadline, Now, back
  from a snooze).
- **What changed since I last looked** (`/changes`): tickets with events since the viewer's last visit, grouped by company and project.
- **Agent comments fold** to one line each, unless they ask the owner something."""

from __future__ import annotations

import datetime as dt
import difflib
import html
import json
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from . import activity, asks, deadlines, identity, ladder, lanes
from . import companies as compmod
from . import people as peoplemod
from .ticket import Ticket

STATE_DIR = Path(".desk") / "state"
SEEN_FILE = "last-seen.json"
SNAP_DIR = "seen"
REPORT_LONG = 1200  # rule 15: from here a field needs a short summary on top
SUMMARY_LINES = 3
SUMMARY_CHARS = 400  # three short lines on the page (rule 15: under 300 characters is one or two sentences)
FINDING_CHARS = 300
FIRST_VISIT = 7  # days /changes looks back on a first visit
DIFF_LINES = 40  # lines of one section drawn in the diff before "and N more"
WHAT_LINES = 3  # what-lines per ticket on /changes
SNOOZE_BACK = 7  # days a ticket counts as "back from snooze"
QUIET_KINDS = ("owner", "person")  # comments by these never fold
_ASK_LEAD = r"(?:ask|your decision|your call|decision needed)"
_LIST = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_TAGS = re.compile(r"\s*<!--.*?-->")
_ASKY = re.compile(r"\?|\byour (?:decision|call|verdict|answer|approval|pick|go-ahead|step)\b|\bplease (?:approve|decide|choose|confirm|answer)\b", re.I)


def _now() -> dt.datetime:
    return dt.datetime.now().replace(microsecond=0)


# ---- who ---------------------------------------------------------------------------------------------------------------------

def viewer_key(actor) -> str:
    """The key the marker is kept under: the actor's person key (never an act-as name; decision 110)."""
    name = getattr(actor, "name", actor) or "someone"
    return re.sub(r"[^\w.@-]+", "-", str(name).strip().lower()).strip("-") or "someone"


class Names:
    """Actor keys from the event log as people read them, cached per request."""

    def __init__(self, home: Path):
        self.pe = peoplemod.load(home)
        self._cache: dict[str, tuple[str, str]] = {}

    def of(self, actor: str) -> tuple[str, str]:
        """(key, display name)."""
        if actor not in self._cache:
            p = self.pe.get(actor) or identity._person_for(self.pe, actor)
            self._cache[actor] = (p.key, p.name) if p else (actor, actor)
        return self._cache[actor]

    def same(self, actor: str, viewer: str) -> bool:
        return self.of(actor)[0].lower() == viewer.lower()


# ---- the last-seen marker ----------------------------------------------------------------------------------------------------

def is_visit(request) -> bool:
    """True when a person's browser opened the page: it carries the device cookie Desk gave it on an earlier visit, and it is a
    navigation, not a script's fetch or a prefetch. A curl, an agent's link check or a headless check has no cookie and resolves to the
    owner, so without this test it would spend the owner's marker and the diff since the last visit would be lost."""
    try:
        if not request.cookies.get("desk_device"):
            return False
        h = request.headers
        return h.get("sec-fetch-mode", "navigate") == "navigate" and "prefetch" not in (h.get("sec-purpose", "") + h.get("purpose", "")).lower()
    except AttributeError:
        return False


def _state(home: Path) -> Path:
    return Path(home) / STATE_DIR


def _load(home: Path) -> dict:
    try:
        data = json.loads((_state(home) / SEEN_FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


_LOCK = threading.Lock()  # the web server opens pages on many threads; the marker file is read, changed and written under it


def _write(home: Path, data: dict) -> None:
    p = _state(home) / SEEN_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")  # one temporary file per writer
    tmp.write_bytes((json.dumps(data, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    os.replace(tmp, p)


def _parse_ts(v) -> dt.datetime | None:
    """A time as the event log keeps it (local, no offset); one with an offset is turned into local time first."""
    try:
        d = dt.datetime.fromisoformat(str(v)) if v else None
    except ValueError:
        return None
    return d.astimezone().replace(tzinfo=None) if d is not None and d.tzinfo is not None else d


def seen_at(home: Path, viewer: str, ticket_id: str) -> dt.datetime | None:
    return _parse_ts(((_load(home).get(viewer) or {}).get("tickets") or {}).get(ticket_id.upper()))


def seen_seq(home: Path, viewer: str, ticket_id: str) -> int | None:
    """How many events the ticket's log held when the viewer last opened it (exact, unlike a time to the second)."""
    v = ((_load(home).get(viewer) or {}).get("seq") or {}).get(ticket_id.upper())
    return v if isinstance(v, int) else None


def changes_seen_at(home: Path, viewer: str) -> dt.datetime | None:
    return _parse_ts((_load(home).get(viewer) or {}).get("changes"))


def changes_offsets(home: Path, viewer: str, at: dt.datetime | None = None) -> dict | None:
    """{ticket id: bytes of its log} at the viewer's last visit to /changes; with `at`, the visit before the current one when that
    visit was at `at` (a filter on the page keeps the window the page opened with)."""
    me = _load(home).get(viewer) or {}
    if at is not None:
        prev = me.get("prev") or {}
        return prev.get("offsets") if _parse_ts(prev.get("changes")) == at and isinstance(prev.get("offsets"), dict) else None
    return me.get("offsets") if isinstance(me.get("offsets"), dict) else None


def log_sizes(home: Path) -> dict:
    """{ticket id: size in bytes of events/<ID>.jsonl}. The log is append-only, so a size is an exact place in it."""
    out = {}
    try:
        for f in (Path(home) / activity.DIR).glob("*.jsonl"):
            try:
                out[f.stem.upper()] = f.stat().st_size
            except OSError:
                continue
    except OSError:
        pass
    return out


def _snap_path(home: Path, viewer: str, ticket_id: str) -> Path:
    return _state(home) / SNAP_DIR / viewer / f"{ticket_id.upper()}.md"


def snapshot(home: Path, viewer: str, ticket_id: str) -> str | None:
    try:
        return _snap_path(home, viewer, ticket_id).read_text(encoding="utf-8")
    except OSError:
        return None


def mark_ticket(home: Path, viewer: str, t: Ticket, now: dt.datetime | None = None) -> None:
    """The viewer has just seen `t`: remember when, and what it said then."""
    seq = len(activity._lines(activity.path(home, t.id)))
    try:
        with _LOCK:
            data = _load(home)
            me = data.setdefault(viewer, {})
            me.setdefault("tickets", {})[t.id.upper()] = (now or _now()).isoformat()
            me.setdefault("seq", {})[t.id.upper()] = seq
            _write(home, data)
        p = _snap_path(home, viewer, t.id)
        text = t.render()
        if not p.exists() or p.read_text(encoding="utf-8") != text:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(text.encode("utf-8"))
    except OSError:
        pass  # the marker is a convenience; a page never fails for it


def mark_changes(home: Path, viewer: str, now: dt.datetime | None = None) -> None:
    offsets = log_sizes(home)
    try:
        with _LOCK:
            data = _load(home)
            me = data.setdefault(viewer, {})
            me["prev"] = {"changes": me.get("changes"), "offsets": me.get("offsets")}
            me["changes"] = (now or _now()).isoformat()
            me["offsets"] = offsets
            _write(home, data)
    except OSError:
        pass


# ---- the diff since the last visit -------------------------------------------------------------------------------------------

@dataclass
class SectionDiff:
    name: str
    lines: list  # [(sign, text)]: "+" added, "-" removed, in reading order
    more: int = 0  # lines not drawn

    @property
    def added(self) -> int:
        return sum(1 for s, _ in self.lines if s == "+")

    @property
    def removed(self) -> int:
        return sum(1 for s, _ in self.lines if s == "-")


@dataclass
class Diff:
    since: dt.datetime | None
    fields: list = field(default_factory=list)  # [(name, old, new)]
    sections: list = field(default_factory=list)  # [SectionDiff]

    def __bool__(self) -> bool:
        return bool(self.fields or self.sections)

    def summary(self) -> str:
        bits = []
        if self.sections:
            bits.append(f"{len(self.sections)} section{'s' if len(self.sections) != 1 else ''}")
        if self.fields:
            bits.append(f"{len(self.fields)} field{'s' if len(self.fields) != 1 else ''}")
        return " and ".join(bits) + " changed"


def _lines_of(text: str) -> list[str]:
    return [ln for ln in (text or "").splitlines() if ln.strip()]


def ticket_diff(old_text: str | None, new: Ticket, since: dt.datetime | None = None) -> Diff | None:
    """What changed between the copy kept at the last visit and the ticket now; None when there was no earlier visit."""
    if old_text is None:
        return None
    try:
        old = Ticket.parse(old_text)
    except ValueError:
        return None
    d = Diff(since)
    for k in dict.fromkeys([*old.meta.keys(), *new.meta.keys()]):
        if k in activity.SKIP_FIELDS:
            continue
        a, b = activity._val(old.meta.get(k)), activity._val(new.meta.get(k))
        if a != b:
            d.fields.append((k, a, b))
    for name in dict.fromkeys([*old.sections.keys(), *new.sections.keys()]):
        a, b = _lines_of(old.sections.get(name, "")), _lines_of(new.sections.get(name, ""))
        if a == b:
            continue
        out = []
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
            if op in ("replace", "delete"):
                out += [("-", _TAGS.sub("", x)) for x in a[i1:i2]]
            if op in ("replace", "insert"):
                out += [("+", _TAGS.sub("", x)) for x in b[j1:j2]]
        if out:
            d.sections.append(SectionDiff(name, out[:DIFF_LINES], max(0, len(out) - DIFF_LINES)))
    return d


# ---- the Report in reading mode ----------------------------------------------------------------------------------------------

def _ask_line(owner: str) -> re.Pattern:
    who = re.escape((owner or "").strip())
    lead = _ASK_LEAD + (f"|{who}" if who else "")
    return re.compile(rf"^\s*(?:[-*+]\s+|\d+[.)]\s+)?(?:\*\*|__)?(?:{lead})(?:\*\*|__)?\s*[:.]\s*(?:\*\*|__)?\s*(.+)$", re.I)


def _strip_item(ln: str) -> str:
    return _LIST.sub("", ln).strip()


@dataclass
class ReportView:
    findings: list  # up to three lines of summary (markdown)
    ask: str  # the one ask, or ""
    rest: str  # everything after the summary (markdown), folded
    rest_lines: int
    is_list: bool


_HEADING = re.compile(r"^\s*#{1,6}\s")
_BOLD_ONLY = re.compile(r"^(\*\*|__)[^*_]+?\1[.:]?$|^(\*\*|__)[^*_]+?[.:]\2$")
LEAD_WORDS = 3  # a block of fewer words is a label, not a summary


def _lead_only(block: list[str]) -> bool:
    """A block that only labels what follows (a heading, a bold lead alone, a line ending in ':', or a word or two) says nothing yet,
    so it is not the summary rule 15 asks for."""
    if len(block) != 1:
        return False
    s = _strip_item(block[0]).strip()
    words = re.sub(r"[*_#`]", " ", s).split()
    return bool(_HEADING.match(block[0]) or _BOLD_ONLY.match(s) or s.endswith(":") or len(words) < LEAD_WORDS)


def report_view(text: str, owner: str = "") -> ReportView | None:
    """The summary on top of a Report: its first block of content (not a heading or a bold lead alone) when that is a paragraph of up
    to three lines (and about 400 characters) or a list of up to three findings; the one ask is the first line, outside code, that
    starts Ask:, Your decision: or the owner's name. Only that one line leaves the body: every other line is in the summary or the
    folded rest. None without a summary."""
    t = (text or "").strip()
    if not t:
        return None
    rx = _ask_line(owner)
    ask = ""
    raw = ladder.blocks(t)
    pos = None
    for bi, b in enumerate(raw):
        if any(ladder._FENCE.match(x) for x in b):
            continue
        for li, x in enumerate(b):
            m = rx.match(x)
            if m:
                ask, pos = m.group(1).strip().strip("*_ ").strip(), (bi, li)
                break
        if pos:
            break
    bs = [b for b in ([x for li, x in enumerate(b) if (bi, li) != pos] for bi, b in enumerate(raw)) if any(x.strip() for x in b)]
    i = 0
    while i < len(bs) and (all(rx.match(x) or not x.strip() for x in bs[i]) or _lead_only(bs[i])):  # asks and labels above the summary
        i += 1
    if i >= len(bs):
        return None
    first = bs[i]
    if any(ladder._FENCE.match(x) for x in first):
        return None
    is_list = bool(_LIST.match(first[0]))
    if is_list:
        items = [x for x in first if _LIST.match(x)]
        if len(items) > SUMMARY_LINES or any(len(x) > FINDING_CHARS for x in items):
            return None
        findings = [_strip_item(x) for x in items]
        if len(first) != len(items):  # continuation lines: keep them with their item
            findings, cur = [], []
            for x in first:
                if _LIST.match(x) and cur:
                    findings.append(" ".join(cur))
                    cur = []
                cur.append(_strip_item(x) if _LIST.match(x) else x.strip())
            findings.append(" ".join(cur))
    else:
        para = " ".join(x.strip() for x in first)
        if len(first) > SUMMARY_LINES or len(para) > SUMMARY_CHARS:
            return None
        findings = [para]
    rest_bs = bs[:i] + bs[i + 1:]  # labels or other asks above the summary stay, at the top of the fold; only the one ask left the body
    rest = "\n\n".join("\n".join(b) for b in rest_bs)
    return ReportView(findings, ask, rest, sum(len(b) for b in rest_bs), is_list)


def report_warning(t: Ticket, owner: str = "") -> str | None:
    text = t.section("Report").strip()
    if len(text) <= REPORT_LONG or report_view(text, owner):
        return None
    return (f"{t.id}: Report is {len(text):,} characters with no summary on top; open it with up to three lines (or three findings) "
            "and the one ask, then the detail (rule 15)")


def warnings(t: Ticket, owner: str = "") -> list[str]:
    """`drew validate` lines (DESK-0287). Open tickets only: a closed ticket's Report is a record and is not rewritten for a summary."""
    if not t.is_open:
        return []
    w = report_warning(t, owner)
    return [w] if w else []


# ---- next action -------------------------------------------------------------------------------------------------------------

STATUS_NEXT = {
    "inbox": "Sort it: give it a size, an owner and a first step.",
    "triage": "Make it ready: write what must be true and how to check it.",
    "ready": "Start it: take it and do the smallest step.",
    "claimed": "{who} has it; the work itself comes next.",
    "in_progress": "{who} is working on it; the report with proof comes next.",
    "review": "Read the proof, then approve it or send it back.",
    "changes_requested": "{who} fixes what was sent back, then hands it in again.",
    "blocked": "Clear what it waits on: {blocked}.",
    "approved": "Merge it, then mark it done.",
    "done": "Nothing left to do: it is done.",
    "rejected": "Nothing left to do: it was let go.",
}
STATUS_NEXT_YOU = {
    "claimed": "You have it; the work itself comes next.",
    "in_progress": "You are working on it; the report with proof comes next.",
    "changes_requested": "Fix what was sent back, then hand it in again.",
}


@dataclass
class NextAction:
    for_whom: str  # "you", a display name, or "" when nobody holds it
    text: str
    step: str = ""  # the smallest step, when the ticket is open
    act: str = ""  # a button label
    href: str = ""
    mine: bool = False


def next_action(store, t: Ticket, viewer: str, report: ReportView | None = None, now: dt.datetime | None = None) -> NextAction:
    """The ask to the viewer when there is one (the same rules as Needs me), else the step the ticket's status calls for."""
    from . import needs_me as nmod

    now = now or _now()
    pe = peoplemod.load(store.home)
    step = str(t.meta.get("smallest_step") or "").strip() if t.is_open else ""
    item = nmod.ask_of(store, t, viewer.lower(), now)
    if item is not None:
        a = asks.ask_of(t)
        href = "#reply" if a and t.status in ("ready", "review") else ("#decision" if item.kind != "do" else "#acceptance")
        text = {"approve": "Read the proof, then approve it or send it back.", "choose": "Pick one of the options below.",
                "answer": "Answer the question below.", "provide": "Provide what is asked below.",
                "do": "This is yours to do."}[item.kind]
        if report and report.ask:
            text = f"The report asks: {report.ask}"
        return NextAction("you", f"{item.chip}. {text}", step, item.act, href, True)
    holder = str(t.meta.get("assignee") or t.meta.get("owner") or "").strip()
    who = "you" if holder and pe.resolve(holder).lower() == viewer.lower() else (pe.display(holder) if holder else "")
    text = (STATUS_NEXT_YOU.get(t.status) if who == "you" else None) or STATUS_NEXT.get(t.status, "Look at the status and decide the next step.")
    text = text.format(who=who or "Whoever takes it",
                       blocked=str(t.meta.get("blocked_on") or "").strip().rstrip(".") or "the tickets it waits on")
    if report and report.ask and t.is_open:
        text = f"The report asks: {report.ask}"
    return NextAction(who, text, step, "", "#report" if report and report.ask else "", who == "you")


# ---- agent comments fold -----------------------------------------------------------------------------------------------------

@dataclass
class CommentBlock:
    text: str  # the lines of the comment (or loose text before the first comment)
    who: str = ""  # display name
    when: str = ""
    kind: str = ""  # activity kind: owner, main, coworker, code, subagent, person, rule
    first: str = ""  # the first line of its words
    fold: bool = False


def comment_blocks(text: str, store) -> list[CommentBlock]:
    from .ops import comment_identity

    pe = peoplemod.load(store.home)
    out: list[CommentBlock] = []
    loose: list[str] = []
    cur: list[str] | None = None
    head = None

    def close():
        nonlocal cur, head
        if cur is None:
            return
        m = head
        who_raw, rest = m.group(3).strip(), m.group(4)
        ident = comment_identity(rest)
        actor = pe.resolve(ident.get("as") or who_raw)
        kind = activity.kind_for(actor, ident.get("kind", ""), store.owner, pe)
        body = "\n".join(cur)
        words = _TAGS.sub("", body)
        first = activity.comment_words(rest)
        asks_owner = bool(_ASKY.search(words) or _ask_line(store.owner).search(_TAGS.sub("", rest)))
        out.append(CommentBlock(body, pe.display(actor) if pe.get(actor) else who_raw, m.group(1), kind, first,
                                kind not in QUIET_KINDS and not asks_owner))
        cur, head = None, None

    for ln in (text or "").splitlines():
        m = activity._ANY_COMMENT.match(ln)
        if m:
            close()
            if loose:
                out.append(CommentBlock("\n".join(loose)))
                loose = []
            cur, head = [ln], m
        elif cur is not None and (ln.startswith((" ", "\t")) or not ln.strip()):
            cur.append(ln)
        else:
            close()
            loose.append(ln)
    close()
    if loose:
        out.append(CommentBlock("\n".join(loose)))
    return out


def fold_comments(text: str, store, render) -> str:
    """The Comments section as HTML: each comment rendered as before; one by an agent (main session, coworker, code session, subagent,
    rule) folds to a single line unless it asks the owner something."""
    parts = []
    for b in comment_blocks(text, store):
        if not b.text.strip():
            continue
        body = str(render(b.text))
        if b.fold:
            parts.append(f'<details class="rd-chat"><summary><b>{html.escape(b.who)}</b> <span class="rd-chat-line">{html.escape(b.first)}</span>'
                         f' <span class="rd-chat-when">{html.escape(b.when)}</span></summary>{body}</details>')
        else:
            parts.append(body)
    return "".join(parts)


# ---- why am I seeing this ----------------------------------------------------------------------------------------------------

def _short_day(d: dt.date) -> str:
    return f"{d.strftime('%b')} {d.day}"


def latest_by_other(home: Path, ticket_id: str, viewer: str, names: Names, after_seq: int | None = None):
    """(display name, event) of the newest event by someone other than the viewer, after event `after_seq` when given."""
    for e in reversed(activity.read(home, ticket_id)):
        if after_seq is not None and e.seq <= after_seq:
            return None
        if not names.same(e.actor, viewer):
            return names.of(e.actor)[1], e
    return None


def why_parts(store, t: Ticket, viewer: str, today: dt.date | None = None, names: Names | None = None) -> list[tuple[str, str]]:
    """[(kind, words)] in order of weight: back from snooze, asked of you, changed by someone, deadline, Now, held by."""
    from . import needs_me as nmod

    today = today or dt.date.today()
    names = names or Names(store.home)
    out: list[tuple[str, str]] = []
    sz = lanes.snooze_until(t)
    if sz and sz <= today and (today - sz).days <= SNOOZE_BACK:
        out.append(("snooze", f"back from snooze {_short_day(sz)}"))
    item = nmod.ask_of(store, t, viewer.lower(), dt.datetime.combine(today, dt.time(12)))
    if item is not None:
        out.append(("ask", f"asked of you: {item.kind}"))
    other = latest_by_other(store.home, t.id, viewer, names, seen_seq(store.home, viewer, t.id))
    if other:
        ts = _parse_ts(other[1].ts)
        out.append(("change", f"changed by {other[0]}" + (f" {_short_day(ts.date())}" if ts else "")))
    d, kind = deadlines.of(t)
    if d:
        out.append(("deadline", f"{kind or 'soft'} deadline {_short_day(d)}"))
    if lanes.lane_of(t) == "now":
        out.append(("now", "on the Now list"))
    holder = str(t.meta.get("assignee") or "").strip()
    if holder and t.status in ("claimed", "in_progress", "changes_requested"):
        out.append(("held", "held by you" if names.same(holder, viewer) else f"held by {names.of(holder)[1]}"))
    elif t.status == "review" and item is None:
        out.append(("held", "waiting for review"))
    return out


def why_line(store, t: Ticket, viewer: str, today: dt.date | None = None, names: Names | None = None, skip: tuple = (), limit: int = 2) -> str:
    """One short line: the two strongest reasons a row is on the list."""
    parts = [w for k, w in why_parts(store, t, viewer, today, names) if k not in skip]
    return " · ".join(parts[:limit])


# ---- what changed since I last looked ----------------------------------------------------------------------------------------

@dataclass
class ChangeRow:
    ticket: Ticket
    events: list  # [(display name, Event)] newest first
    latest: dt.datetime | None

    @property
    def lines(self) -> list[str]:
        return [f"{who}: {e.what or e.change}" for who, e in self.events[:WHAT_LINES]]

    @property
    def more(self) -> int:
        return max(0, len(self.events) - WHAT_LINES)


@dataclass
class ChangeGroup:
    company: str
    company_slug: str
    projects: list  # [(project key, project title, [ChangeRow])]

    @property
    def count(self) -> int:
        return sum(len(rows) for _, _, rows in self.projects)


def since_words(when: dt.datetime, last: bool = False) -> str:
    at = f"{_short_day(when.date())} at {when.strftime('%I:%M %p').lstrip('0')}"
    return f"since your last visit, {at}" if last else f"since {at}"


def since_for(home: Path, viewer: str, since: str, now: dt.datetime | None = None) -> tuple[dt.datetime, str]:
    """(the moment to look back to, the words for it). `since` is YYYY-MM-DD, or empty or `last` for the viewer's last visit."""
    now = now or _now()
    s = (since or "").strip().lower()
    if s and s != "last":
        try:
            d = dt.date.fromisoformat(s)
            return dt.datetime.combine(d, dt.time()), f"since {_short_day(d)}, {d.year}"
        except ValueError:
            raise ValueError("That date is not a day Desk can read. Use YYYY-MM-DD, such as 2026-11-14.") from None
    last = changes_seen_at(home, viewer)
    if last:
        return last, since_words(last, last=True)
    start = now - dt.timedelta(days=FIRST_VISIT)
    return start, f"in the last {FIRST_VISIT} days (your first visit)"


def _events_after(home: Path, ticket_id: str, offset: int) -> list:
    """The events written after byte `offset` of the ticket's log (the log only grows, so these are exactly the new ones)."""
    p = activity.path(home, ticket_id)
    try:
        with p.open("rb") as fh:
            fh.seek(offset)
            tail = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    names = set(activity.Event.__dataclass_fields__)
    out = []
    for ln in tail.splitlines():
        try:
            d = json.loads(ln)
            out.append(activity.Event(**{k: v for k, v in d.items() if k in names}))
        except (ValueError, TypeError):
            continue
    return out


def changes(store, viewer: str, since: dt.datetime, project: str = "", company: str = "", mine: bool = False,
            offsets: dict | None = None) -> list[ChangeGroup]:
    """Tickets with events after `since` (or, with `offsets` from the last visit, after that exact place in each log), grouped by
    company, then project; the viewer's own changes are left out unless `mine`."""
    names = Names(store.home)
    by_id = store.by_id()
    of = compmod.by_project(store)
    cut = since.timestamp()
    rows: dict[str, list[ChangeRow]] = {}
    evdir = Path(store.home) / activity.DIR
    try:
        files = list(evdir.glob("*.jsonl"))
    except OSError:
        files = []
    for f in files:
        try:
            st = f.stat()
        except OSError:
            continue
        if offsets is not None:
            old = offsets.get(f.stem.upper(), 0)
            if st.st_size == old:
                continue  # nothing appended since the last visit
        elif st.st_mtime < cut - 60:  # nothing was appended after the moment: skip without reading
            continue
        t = by_id.get(f.stem.upper())
        if t is None or (project and t.project != project):
            continue
        comp = of.get(t.project) or compmod.of(store, t.project)
        if company and compmod.slug(comp) != company:
            continue
        evs = []
        if offsets is not None:
            old = offsets.get(f.stem.upper(), 0)
            got = _events_after(store.home, t.id, old if st.st_size > old else 0)
        else:
            got = activity.read(store.home, t.id)
        for e in got:
            ts = _parse_ts(e.ts)
            if ts is None or (offsets is None and ts <= since) or (not mine and names.same(e.actor, viewer)):
                continue
            evs.append((names.of(e.actor)[1], e, ts))
        if not evs:
            continue
        evs.sort(key=lambda x: (x[2], x[1].seq), reverse=True)
        rows.setdefault(t.project, []).append(ChangeRow(t, [(w, e) for w, e, _ in evs], evs[0][2]))
    groups: dict[str, ChangeGroup] = {}
    for proj, rs in rows.items():
        rs.sort(key=lambda r: r.latest or dt.datetime.min, reverse=True)
        comp = of.get(proj) or compmod.of(store, proj)
        g = groups.setdefault(comp, ChangeGroup(compmod.title(comp), compmod.slug(comp), []))
        g.projects.append((proj, store.project_title(proj), rs))
    out = sorted(groups.values(), key=lambda g: (-g.count, g.company.lower()))
    for g in out:
        g.projects.sort(key=lambda p: (-len(p[2]), p[1].lower()))
    return out


# ---- the ticket page ---------------------------------------------------------------------------------------------------------

@dataclass
class TicketReading:
    next: NextAction
    report: ReportView | None
    report_flag: str
    diff: Diff | None
    first_visit: bool
    _store: object = None
    _t: Ticket | None = None

    def comments(self, render) -> str:
        return fold_comments(self._t.section("Comments") if self._t else "", self._store, render)

    def goal(self) -> str:
        """The first sentence of Outcome, for the line a folded Context shows."""
        text = " ".join(_TAGS.sub("", self._t.section("Outcome") if self._t else "").split()).replace("**", "")
        m = re.match(r"(.{20,180}?[.!?])(\s|$)", text)
        return m.group(1) if m else (text[:160] + ("…" if len(text) > 160 else ""))


def ticket_view(store, t: Ticket, actor, now: dt.datetime | None = None, write: bool = True) -> dict:
    """What the ticket page needs for reading mode, as template variables; marks the ticket seen for this viewer."""
    now = now or _now()
    viewer = viewer_key(actor)
    before = seen_at(store.home, viewer, t.id)
    old = snapshot(store.home, viewer, t.id)
    rep = report_view(t.section("Report"), store.owner)
    flag = "" if rep or len(t.section("Report").strip()) <= REPORT_LONG else "No summary on top: the first lines should say what was found and the one ask."
    d = ticket_diff(old, t, before)
    if write:
        mark_ticket(store.home, viewer, t, now)
    return {"rd": TicketReading(next_action(store, t, viewer, rep, now), rep, flag, d if d else None, old is None, store, t)}
