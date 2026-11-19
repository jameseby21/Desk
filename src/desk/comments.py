"""Comments as work items (DESK-0146). A note or question James leaves on a ticket is a bug, a fix or a decision to execute; this lists
them across the store so the sweep can answer each one. `.desk/state` remembers when the last sweep looked."""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .ops import _COMMENT_LINE, comment_identity, split_comment_kind
from .store import Store

ANSWERERS = ("patch",)  # the main session; a later line by one of these answers every earlier comment on the ticket
LAST_SWEEP = "last-sweep"


@dataclass
class Entry:
    ticket_id: str
    title: str
    when: dt.datetime
    author: str
    kind: str
    text: str
    answered: bool
    ident: dict | None = None  # the parsed identity tag; {} for a comment written before identities were kept
    date_only: bool = False  # a Decision line is dated but not timed: `when` is that day's midnight and `stamp` shows the day alone
    source: str = "comment"  # where the words live: comment, decision, not accepted, park reason, relayed answer (DESK-0322)

    @property
    def stamp(self) -> str:
        return self.when.strftime("%Y-%m-%d" if self.date_only else "%Y-%m-%d %H:%M")

    def as_json(self) -> dict:
        d = asdict(self)
        return {"ticket": d["ticket_id"], "title": d["title"], "when": self.stamp, "author": d["author"],
                "kind": d["kind"], "text": d["text"], "answered": d["answered"], "identity": self.ident or {}, "source": self.source}


def _state_path(home: Path) -> Path:
    return Path(home) / ".desk" / "state"


def _state(home: Path) -> dict:
    try:
        data = json.loads(_state_path(home).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def last_sweep(home: Path) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(_state(home).get("last_sweep") or ""))
    except ValueError:
        return None


def mark_sweep(home: Path, when: dt.datetime | None = None) -> dt.datetime:
    """Record that a sweep looked at the comments now; `--since last-sweep` starts here next time."""
    when = (when or dt.datetime.now()).replace(microsecond=0)
    state = _state(home)
    state["last_sweep"] = when.isoformat()
    state["sweeps"] = [*(state.get("sweeps") or []), when.isoformat()][-8:]  # the times of the last sweeps: a hand-off unchanged across two is flagged (DESK-0158)
    p = _state_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return when


def parse_since(text: str | None, home: Path) -> dt.datetime | None:
    """A date (YYYY-MM-DD), a date and time (YYYY-MM-DD HH:MM), `last-sweep` (from .desk/state; everything when no sweep is marked), or none."""
    if not text:
        return None
    if text.strip().lower() == LAST_SWEEP:
        return last_sweep(home)
    try:
        return dt.datetime.fromisoformat(text.strip().replace(" ", "T"))
    except ValueError:
        raise ValueError(f"`{text}` is not a time. Use YYYY-MM-DD, YYYY-MM-DD HH:MM or last-sweep.") from None


def _updated(t) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(t.meta.get("updated"))[:10])
    except ValueError:
        return None


_UNDO_LINE = re.compile(r'^(Withdrawn|Restored): "(.*)" \(the comment of (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) by ([^)]+)\)$')


def _without_undo_lines(rows: list) -> tuple[list, set]:
    """DESK-0284: (rows, mute). An undo's Withdrawn or Restored line is not a comment and answers nothing; a comment that stands
    withdrawn answers nothing either. One its own author withdrew is left out; one withdrawn by someone else stays listed (`mute`)."""
    marks = []
    for i, (when, who, _kind, text, _ident) in enumerate(rows):
        m = _UNDO_LINE.match(text)
        if m:
            marks.append((i, m.group(1) == "Withdrawn", dt.datetime.fromisoformat(m.group(3).replace(" ", "T")), m.group(2).rstrip("…"), who.lower()))
    if not marks:
        return rows, set()
    state: dict[int, tuple[bool, str]] = {}
    skip = {i for i, *_ in marks}
    for i, withdrawn, at, words, withdrawer in marks:  # file order: the last line about a comment says whether it stands withdrawn
        for j, (when, _who, _kind, text, _ident) in enumerate(rows):  # the line it names: its time (the log's may be a minute later) and words
            if j not in skip and text.replace('"', "'").startswith(words) and dt.timedelta(0) <= at - when.replace(second=0) <= dt.timedelta(minutes=1):
                state[j] = (withdrawn, withdrawer)
    keep, mute = [], set()
    for j, row in enumerate(rows):
        if j in skip:
            continue
        withdrawn, withdrawer = state.get(j, (False, ""))
        if withdrawn and withdrawer == row[1].lower():
            continue
        if withdrawn:
            mute.add(len(keep))
        keep.append(row)
    return keep, mute


_DECISION_LINE = re.compile(r"^- (\d{4}-\d{2}-\d{2})(?: (\d{2}:\d{2}))? · (.+?) · (.*)$")
_VERDICT = re.compile(r'^(approved|changes_requested|rejected) · "(.*)"$', re.S)
_RELAY = re.compile(r"^Decided by ([^(:]+?) \(([^)]*)\):?\s*(.*)$", re.S)  # `Decided by James (2026-11-17, yes/no D5 re-asked): B, keep both`
CHECK_SECTIONS = ("Acceptance", "How to validate", "Prompt", "Evidence")
_END_OF_DAY = dt.time(23, 59, 59)
_LOOSE_BY = re.compile(r"<!--\s*by\s+([\w.@-]+)\s+(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})")


def _words(text: str) -> str:
    """One line, without hidden `<!-- ... -->` tags."""
    return re.sub(r"\s+", " ", re.sub(r"\s*<!--.*?-->", "", text or "")).strip()


def _decision_rows(t) -> list[tuple]:
    """(when, who, kind, text, ident, date_only, source) for every Decision line: the owner's Approve and Send back words, Decide mode and
    yes/no answers (all written by `ops.decide`), and his Move reasons to approved, changes requested or rejected."""
    out = []
    for line in t.section("Decision").splitlines():
        m = _DECISION_LINE.match(line)
        if not m:
            continue
        try:
            when = dt.datetime.fromisoformat(f"{m.group(1)}T{m.group(2) or '00:00'}")
        except ValueError:
            continue
        rest = m.group(4).strip()
        v = _VERDICT.match(rest)
        kind, text = (f"Decision: {v.group(1)}", v.group(2)) if v else ("Decision", rest)
        out.append((when, m.group(3).strip(), kind, _words(text), {}, m.group(2) is None, "decision"))
    return out


def _not_accepted_rows(t) -> list[tuple]:
    """(when, who, kind, text, ident, date_only, source) for every Not accepted line: the reason with the line it is about, by the
    person who pressed the box (the `by` tag), in any checkbox section."""
    from .ticket import parse_checks

    out = []
    for sec in CHECK_SECTIONS:
        for c in parse_checks(t.sections.get(sec) or ""):
            if c.state != "!":
                continue
            who, stamp = c.who, c.when
            if not who and (b := _LOOSE_BY.search(c.text)):  # a worker added a note inside the tag (`by james 2026-11-17T20:07; links added`): the first two words are still the presser's
                who, stamp = b.group(1), b.group(2)
            try:
                when, only = dt.datetime.fromisoformat(stamp), False
            except ValueError:  # an older line with no `by` tag: the day the ticket last changed, and nobody named
                when, only = dt.datetime.combine(_updated(t) or dt.date.min, dt.time()), True
            sev = f" ({c.severity})" if c.severity else ""
            out.append((when, who or "unknown", f"Not accepted{sev}", f"Not accepted{sev}: {_words(c.reason)}  [{sec}: {_words(c.text)}]", {}, only, "not accepted"))
    return out


def _park_rows(store: Store, t, taken: list) -> list[tuple]:
    """Park reasons written before DESK-0324 kept them as comments live only as a `park_reason` change in the activity log."""
    from . import activity

    out = []
    for e in activity.read(store.home, t.id):
        if e.change != "field" or e.field != "park_reason" or not e.new.strip():
            continue
        try:
            when = dt.datetime.fromisoformat(e.ts)
        except ValueError:
            continue
        words = _words(e.new).rstrip("…")[:60]
        if any(words in r[3] and r[1].lower() == e.actor.lower() for r in taken):  # the comment kept since DESK-0324 already says it
            continue
        out.append((when, e.actor, "Park reason", _words(e.new), {}, False, "park reason"))
    return out


def entries(store: Store, answerers: tuple[str, ...] = ANSWERERS) -> list[Entry]:
    """Every word a person wrote in the store, with whether it has been answered: a later line by an answerer in the same ticket, or the
    ticket having changed on a day after its last line (a move, a report, a decision). Dates are days, so a ticket touched on
    the same day stays open; that is deliberate: better listed twice than lost.

    DESK-0322: not only Comments lines but the Not accepted reasons (with their acceptance line), Decision lines (Approve, Send back,
    Decide mode, Move to approved or rejected), Park reasons from the activity log, and a yes/no answer a session recorded as
    `Decided by <name> (...)` in Comments (listed under that person, source "relayed answer", kind says who wrote it)."""
    out: list[Entry] = []
    names = {a.lower() for a in answerers}
    for t in store.tickets():
        rows = []
        for line in t.section("Comments").splitlines():
            m = _COMMENT_LINE.match(line)
            if not m:
                continue
            try:
                when = dt.datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}")
            except ValueError:
                continue
            kind, text = split_comment_kind(m.group(4))
            rows.append((when, m.group(3).strip(), kind or "", re.sub(r"\s+", " ", text).strip(), comment_identity(m.group(4))))
        rows, mute = _without_undo_lines(rows)
        relayed = []
        for when, who, _kind, text, ident in rows:
            r = _RELAY.match(text)
            if r:
                relayed.append((when, r.group(1).strip(), f"Answer (relayed by {who})", text, ident, False, "relayed answer"))
        extra = [*_decision_rows(t), *_not_accepted_rows(t), *relayed]
        extra += _park_rows(store, t, [*rows, *extra])
        if not rows and not extra:
            continue
        last_day = max(r[0].date() for r in [*rows, *extra])
        upd = _updated(t)
        moved = upd is not None and upd > last_day
        title = str(t.meta.get("title") or "")
        for i, (when, who, kind, text, ident) in enumerate(rows):
            later_answer = any(rows[j][1].lower() in names and j not in mute and (rows[j][0], j) > (when, i) for j in range(len(rows)))
            out.append(Entry(t.id, title, when, who, kind, text, later_answer or moved, ident))
        for when, who, kind, text, ident, only, source in extra:
            at = dt.datetime.combine(when.date(), _END_OF_DAY) if only else when  # a day-only line counts as the day's last minute
            later_answer = any(r[1].lower() in names and j not in mute and r[0] > at for j, r in enumerate(rows))
            out.append(Entry(t.id, title, when, who, kind, text, later_answer or moved, ident, only, source))
    return out


def query(store: Store, *, author: str | None = None, since: dt.datetime | None = None, unanswered: bool = False,
          answerers: tuple[str, ...] = ANSWERERS) -> list[Entry]:
    rows = entries(store, answerers)
    if author:
        rows = [r for r in rows if r.author.lower() == author.lower()]
    if since:  # a comment is timed to the minute and a mark to the second: a line from the mark's own minute is listed again, not lost
        cut = since.replace(second=0, microsecond=0)
        rows = [r for r in rows if (r.when.date() >= since.date() if r.date_only else r.when >= cut)]
    if unanswered:
        rows = [r for r in rows if not r.answered]
    return sorted(rows, key=lambda r: (r.when, r.ticket_id))
