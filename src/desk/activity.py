"""The append-only activity log (DESK-0162): nothing that happened to a ticket can be lost or rewritten.

Every save through `Store.save` compares the ticket on disk with the one being written and appends one event per change to
`events/<ID>.jsonl` at the store root: who (actor key and kind), where from (ui, cli, mcp, migration, git), when, and what (a field
with its old and new value, a section by name, a comment by its first line). The file is opened only for appending; each line carries
its sequence number and a hash of the line before it, so an edit anywhere in the file shows up in `verify`. `drew migrate events`
turns the Comments lines and the git history of each ticket file into events so old tickets have a timeline too; it writes each
migrated event once, keyed, so running it again writes nothing.

Why one file per ticket: the ticket page and `drew history` read exactly one small file (the id is the index), appends never
touch another ticket's history, and `git add -A` in the store's sync commits it like the ticket file itself (`.desk/` is ignored)."""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

DIR = "events"
SOURCES = ("ui", "cli", "mcp", "migration", "git")
# The kind of actor, as the timeline shows it. owner = the store owner (James); main = the main session (Patch).
KINDS = ("owner", "main", "coworker", "code", "subagent", "person")
CHANGES = ("created", "field", "section", "comment", "git")
SKIP_FIELDS = {"updated"}  # moves on every save; the save itself is the event
MAX_VALUE = 120
_AI_COAUTHOR = re.compile(r"\bclaude\b|anthropic\.com", re.I)  # a Co-Authored-By trailer that names an AI session
_ID = re.compile(r"^([A-Za-z][A-Za-z0-9]*-\d+)")
_ANY_COMMENT = re.compile(r"^- (\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}))? · (.+?) · (.*)$")
_TAGS = re.compile(r"\s*<!--.*?-->")
_BOX = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\[([ xX!])\]\s")


class AppendOnlyError(RuntimeError):
    """An attempt to change or replace an event that is already written."""


@dataclass(frozen=True)
class Event:
    ticket: str
    ts: str  # local time, YYYY-MM-DDTHH:MM:SS
    actor: str  # the person key from people.yaml when known, else the name as recorded
    kind: str  # one of KINDS
    source: str  # one of SOURCES
    change: str  # one of CHANGES
    field: str = ""  # the field or section name; `comment` for a comment; `git` for a commit
    old: str = ""
    new: str = ""
    what: str = ""  # one plain line: what changed
    instance: str = ""
    confirmed: bool = False
    key: str = ""  # a stable key for migrated events, so a second migration skips them
    seq: int = 0  # position in the file, from 1
    prev: str = ""  # hash of the line before this one

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


# -- where -------------------------------------------------------------------------------

def path(home: Path, ticket_id: str) -> Path:
    return Path(home) / DIR / f"{ticket_id.upper()}.jsonl"


def _hash(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()[:16]


def _lines(p: Path) -> list[str]:
    try:
        return [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        return []


def read(home: Path, ticket_id: str) -> list[Event]:
    """Every event of one ticket, in the order written. A line that cannot be read is skipped (verify names it)."""
    out = []
    names = {f.name for f in dataclasses.fields(Event)}
    for ln in _lines(path(home, ticket_id)):
        try:
            d = json.loads(ln)
            out.append(Event(**{k: v for k, v in d.items() if k in names}))
        except (ValueError, TypeError):
            continue
    return out


def append(home: Path, events: list[Event]) -> list[Event]:
    """Write events at the end of their ticket's file and return them as written (with seq and prev). The only write there is.
    An event that already has a sequence number is one that was written before: handing it back in is an edit, and refused."""
    written = []
    by_ticket: dict[str, list[Event]] = {}
    for e in events:
        if e.seq or e.prev:
            raise AppendOnlyError(f"Event {e.seq} of {e.ticket} is already written; events are never edited or replaced, only added.")
        by_ticket.setdefault(e.ticket.upper(), []).append(e)
    for tid, evs in by_ticket.items():
        p = path(home, tid)
        p.parent.mkdir(parents=True, exist_ok=True)
        lines = _lines(p)
        seq, prev = len(lines), (_hash(lines[-1]) if lines else "")
        chunk = []
        for e in evs:
            seq += 1
            e = dataclasses.replace(e, ticket=tid, seq=seq, prev=prev)
            line = json.dumps(e.as_dict(), ensure_ascii=False, sort_keys=True)
            prev = _hash(line)
            chunk.append(line)
            written.append(e)
        with p.open("a", encoding="utf-8", newline="\n") as fh:  # append mode only: nothing earlier in the file is ever rewritten
            fh.write("".join(x + "\n" for x in chunk))
    return written


def rewrite(*_args, **_kw):
    """There is no way to edit the log. This exists so a caller looking for one finds the reason instead."""
    raise AppendOnlyError("The activity log is append-only: events are never edited, reordered or deleted.")


def verify(home: Path, ticket_id: str) -> list[str]:
    """Problems with a ticket's log, in plain words; an empty list when every line is intact and in order."""
    problems, prev = [], ""
    for i, ln in enumerate(_lines(path(home, ticket_id)), start=1):
        try:
            d = json.loads(ln)
        except ValueError:
            problems.append(f"line {i} is not readable")
            prev = _hash(ln)
            continue
        if d.get("seq") != i:
            problems.append(f"line {i} says it is event {d.get('seq')}")
        if d.get("prev", "") != prev:
            problems.append(f"line {i} does not follow the line before it (the file was edited)")
        prev = _hash(ln)
    return problems


# -- who ---------------------------------------------------------------------------------

def kind_for(name: str, ident_kind: str, owner: str, pe) -> str:
    """The timeline's kind for an actor: owner, main (Patch), coworker, code (a code session), subagent, or person."""
    n = (name or "").strip().lower()
    if ident_kind == "subagent" or n.startswith("subagent:"):
        return "subagent"
    p = pe.get(name) if pe is not None else None
    key = p.key.lower() if p else n
    if key == (owner or "").lower():
        return "owner"
    pk = p.kind if p else ""
    if pk == "agent" or key in ("main", "patch"):
        return "main"
    if pk == "coworker" or ident_kind == "thread":
        return "coworker"
    if pk == "code" or ident_kind == "session" or n in ("claude-code", "desk"):
        return "code"
    return "person"


def _actor_now(store):
    from . import identity

    return identity.CURRENT.get() or identity.Actor("not recorded", "person", "", "cli", "not-recorded", False)


# -- what --------------------------------------------------------------------------------

def _val(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return ", ".join(_val(x) for x in v)
    s = " ".join(str(v).split())
    return s if len(s) <= MAX_VALUE else s[:MAX_VALUE - 1] + "…"


def comment_words(rest: str) -> str:
    """The first line of a comment's words, without its hidden identity and kind tags."""
    words = _TAGS.sub("", rest or "").strip()
    first = words.splitlines()[0] if words else ""
    return _val(first)


def _added_lines(old: str, new: str) -> list[str]:
    before = old.splitlines()
    seen: dict[str, int] = {}
    for ln in before:
        seen[ln] = seen.get(ln, 0) + 1
    out = []
    for ln in new.splitlines():
        if seen.get(ln):
            seen[ln] -= 1
        elif ln.strip():
            out.append(ln)
    return out


def diff(old, new) -> list[dict]:
    """What changed between two versions of a ticket, as event fields (change, field, old, new, what), in a stable order."""
    if old is None:
        return [{"change": "created", "field": "created", "old": "", "new": _val(new.meta.get("title")),
                 "what": f"Created: {_val(new.meta.get('title'))}"}]
    out = []
    keys = list(dict.fromkeys([*old.meta.keys(), *new.meta.keys()]))
    for k in keys:
        if k in SKIP_FIELDS:
            continue
        a, b = _val(old.meta.get(k)), _val(new.meta.get(k))
        if a != b:
            out.append({"change": "field", "field": k, "old": a, "new": b, "what": f"{k}: {a or 'empty'} → {b or 'empty'}"})
    names = list(dict.fromkeys([*old.sections.keys(), *new.sections.keys()]))
    for name in names:
        a, b = old.sections.get(name, "") or "", new.sections.get(name, "") or ""
        if a.strip() == b.strip():
            continue
        added = _added_lines(a, b)
        if name == "Comments":
            comments = [m for m in (_ANY_COMMENT.match(ln) for ln in added) if m]
            for m in comments:
                words = comment_words(m.group(4))
                out.append({"change": "comment", "field": "comment", "old": "", "new": words, "what": words or "(an empty comment)"})
            if comments and set(a.splitlines()) <= set(b.splitlines()):
                continue  # only new comments (and their continuation lines): the comment events say it all
        first = ""
        if added:
            box = _BOX.match(added[0])
            mark = {"x": "ticked: ", "X": "ticked: ", "!": "not accepted: "}.get(box.group(1), "") if box else ""
            first = _val(mark + re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX!]\]\s+)?", "", _TAGS.sub("", added[0])))
        out.append({"change": "section", "field": name, "old": "", "new": first,
                    "what": f"{name} changed" + (f": {first}" if first else " (lines removed)")})
    return out


def record_save(store, old, new, now: dt.datetime | None = None) -> list[Event]:
    """Append one event per change made by this save, named for the actor of the current command or request."""
    from . import people as peoplemod

    changes = diff(old, new)
    if not changes:
        return []
    a = _actor_now(store)
    pe = peoplemod.load(store.home)
    actor = pe.resolve(a.as_ or a.name) if a.name != "not recorded" else a.name
    kind = kind_for(actor, a.kind, store.owner, pe)
    ts = (now or dt.datetime.now()).replace(microsecond=0).isoformat()
    evs = [Event(new.id, ts, actor, kind, a.source if a.source in SOURCES else "cli", c["change"], c["field"], c["old"], c["new"], c["what"],
                 a.instance, bool(a.confirmed)) for c in changes]
    return append(store.home, evs)


# -- migration (DESK-0045 history into the log) -----------------------------------------

def comment_events(t, pe, owner: str) -> list[Event]:
    """Each dated Comments line as an event, keyed by its own text so it is migrated once."""
    from .ops import comment_identity

    out, seen = [], {}
    for ln in (t.section("Comments") or "").splitlines():
        m = _ANY_COMMENT.match(ln.strip())
        if not m:
            continue
        seen[ln.strip()] = n = seen.get(ln.strip(), 0) + 1  # two identical lines are two comments, each migrated once
        day, hm, who, rest = m.group(1), m.group(2) or "00:00", m.group(3).strip(), m.group(4)
        try:
            dt.date.fromisoformat(day)
        except ValueError:
            continue
        ident = comment_identity(rest)
        actor = pe.resolve(ident.get("as") or who) if pe is not None else who
        words = comment_words(rest)
        out.append(Event(t.id, f"{day}T{hm}:00", actor, kind_for(actor, ident.get("kind", ""), owner, pe), "migration", "comment", "comment", "", words,
                         words or "(an empty comment)", ident.get("inst", ""), ident.get("ok") == "1",
                         "comment:" + _hash(ln.strip()) + ("" if n == 1 else f":{n}")))
    return out


def git_history(home: Path, pe, owner: str) -> tuple[dict[str, list[Event]], str]:
    """({ticket id: [one event per commit that touched its file]}, a note when there is no git history to read). One `git log` call
    for the whole store; a renamed ticket file keeps its id prefix, so commits are found by id, not by file name. A commit with a
    Co-Authored-By trailer naming an AI is a code session's (claude-code), not its git author's; the raw author is kept in `old`, and
    no git event is confirmed, because a git user name is whatever the machine is set to (sessions commit under the owner's)."""
    from . import identity

    try:
        r = subprocess.run(["git", "-C", str(home), "log", "--format=%x1e%H%x1f%an%x1f%ae%x1f%aI%x1f%s%x1f%(trailers:key=Co-authored-by,valueonly,separator=%x1d)", "--name-only", "--", "tickets/"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        return {}, f"git history not read: {e}"
    if r.returncode != 0:
        return {}, "git history not read: the store is not a git repository" if "not a git repository" in r.stderr else f"git history not read: {r.stderr.strip()[:200]}"
    out: dict[str, list[Event]] = {}
    for block in r.stdout.split("\x1e"):
        if not block.strip():
            continue
        head, _, files = block.partition("\n")
        parts = head.split("\x1f")
        if len(parts) < 5:
            continue
        sha, name, email, when, subject = parts[:5]
        p = identity._person_for(pe, name) or identity._person_for(pe, email.split("@")[0]) if pe is not None else None
        actor = p.key if p else name
        kind = kind_for(actor, "", owner, pe)
        if _AI_COAUTHOR.search(parts[5] if len(parts) > 5 else ""):
            actor, kind = "claude-code", "code"
        try:
            ts = dt.datetime.fromisoformat(when).replace(tzinfo=None).isoformat()
        except ValueError:
            continue
        seen = set()
        for f in files.splitlines():
            f = f.strip()
            if not f.startswith("tickets/") or not f.endswith(".md") or f.count("/") != 1:
                continue
            m = _ID.match(f[len("tickets/"):])
            if not m or m.group(1).upper() in seen:
                continue
            tid = m.group(1).upper()
            seen.add(tid)
            what = f"Saved in git: {_val(subject)}"
            out.setdefault(tid, []).append(Event(tid, ts, actor, kind, "git", "git", "git", name, sha[:10], what, "", False, f"git:{sha}"))
    return out, ""


def plan(store, progress=None) -> tuple[list[tuple[str, int, int, list[Event]]], str]:
    """[(ticket id, comments to add, commits to add, the events)] for every ticket with something not yet in its log, plus the git note.
    Events already in a ticket's log (same key) are left out, so this is also what a second run would add: nothing."""
    from . import people as peoplemod

    pe = peoplemod.load(store.home)
    tickets = store.tickets()
    gits, note = git_history(store.home, pe, store.owner)
    rows = []
    for i, t in enumerate(tickets, start=1):
        have = {e.key for e in read(store.home, t.id) if e.key}
        cs = [e for e in comment_events(t, pe, store.owner) if e.key not in have]
        have |= {e.key for e in cs}
        gs = [e for e in gits.get(t.id.upper(), []) if e.key not in have]
        if cs or gs:
            rows.append((t.id, len(cs), len(gs), sorted(cs + gs, key=lambda e: (e.ts, e.key))))
        if progress and (i % 200 == 0 or i == len(tickets)):
            progress(i, len(tickets))
    return rows, note


def migrate(store, apply: bool, progress=None) -> tuple[list[tuple[str, int, int, list[Event]]], int, str]:
    """(the plan, events written, git note). Written once: each event carries its key, and a key already in the log is skipped."""
    rows, note = plan(store, progress)
    wrote = 0
    if apply:
        for _tid, _c, _g, evs in rows:
            wrote += len(append(store.home, evs))
    return rows, wrote, note
