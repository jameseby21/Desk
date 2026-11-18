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

    def as_json(self) -> dict:
        d = asdict(self)
        return {"ticket": d["ticket_id"], "title": d["title"], "when": self.when.strftime("%Y-%m-%d %H:%M"), "author": d["author"],
                "kind": d["kind"], "text": d["text"], "answered": d["answered"], "identity": self.ident or {}}


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


def entries(store: Store, answerers: tuple[str, ...] = ANSWERERS) -> list[Entry]:
    """Every comment line in the store, with whether it has been answered: a later line by an answerer in the same ticket, or the
    ticket having changed on a day after its last comment (a move, a report, a decision). Dates are days, so a ticket touched on
    the same day as the comment stays open; that is deliberate: better listed twice than lost."""
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
        if not rows:
            continue
        last_day = max(r[0].date() for r in rows)
        upd = _updated(t)
        moved = upd is not None and upd > last_day
        title = str(t.meta.get("title") or "")
        for i, (when, who, kind, text, ident) in enumerate(rows):
            later_answer = any(rows[j][1].lower() in names and j not in mute and (rows[j][0], j) > (when, i) for j in range(len(rows)))
            out.append(Entry(t.id, title, when, who, kind, text, later_answer or moved, ident))
    return out


def query(store: Store, *, author: str | None = None, since: dt.datetime | None = None, unanswered: bool = False,
          answerers: tuple[str, ...] = ANSWERERS) -> list[Entry]:
    rows = entries(store, answerers)
    if author:
        rows = [r for r in rows if r.author.lower() == author.lower()]
    if since:
        rows = [r for r in rows if r.when >= since]
    if unanswered:
        rows = [r for r in rows if not r.answered]
    return sorted(rows, key=lambda r: (r.when, r.ticket_id))
