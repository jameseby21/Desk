"""Undo (DESK-0284): every write can be taken back, and taking it back is itself a write.

Nothing is ever removed from the activity log (DESK-0162). A revert writes NEW events through `Store.save`: the old value comes back,
and each event the revert writes carries its own batch id and a what-line that ends `· undoes #N`, so the timeline shows both the
change and its undo. Two things are never put back by deleting text:
- a comment is withdrawn by a new dated Comments line that names it (idea 60 of the 2026-11-15 ideas list);
- a Decision line is reverted by a new Decision line that names the old one.

The log keeps values cut to one line, so a section's full text cannot come back from it. `remember` keeps the full old and new value
of every field and section a save changed in `.desk/undo/<ID>.jsonl` (local, beside the search index; `.desk/` is never committed),
and `.desk/undo/batches.jsonl` says which tickets a batch touched. A change made before this existed can still be undone when its
logged value is complete (a field under 120 characters); otherwise the refusal says why.

A revert is refused when the field or section has changed since (the refusal names who changed it, when, and their kind), unless it
is forced. A batch is checked as a whole first: either every change in it is undone, newest first, or nothing is written.
Docs: docs/COMMANDS.md (`drew undo`, `/undo`, the undo toast)."""

from __future__ import annotations

import contextvars
import copy
import datetime as dt
import json
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from . import activity
from .ticket import _plain

DIR = Path(".desk") / "undo"
BATCHES = "batches.jsonl"
NOTE = re.compile(r"· undoes #(\d+)\b")
WEEK = dt.timedelta(days=7)
_COLLECT: contextvars.ContextVar[list | None] = contextvars.ContextVar("desk_undo_collect", default=None)


def new_batch() -> str:
    """A batch id as the DESK-0282 contract shapes it: `b-` and 12 hex characters."""
    return "b-" + secrets.token_hex(6)


# -- what a save changed, in full (called by activity.record_save after every append) ---------------------------------------

def _dir(home: Path) -> Path:
    return Path(home) / DIR


def remember(store, old, new, written: list) -> None:
    """Keep the full old and new value of each field and section this save changed, keyed by the event's seq."""
    collect = _COLLECT.get()
    if collect is not None:
        collect.extend(written)
    if not written:
        return
    rows = []
    for e in written:
        row = {"seq": e.seq, "save": written[0].seq, "change": e.change, "field": e.field}  # save: the first seq of this save
        if e.change == "field":
            row["had"] = old is not None and e.field in old.meta
            row["old"] = _plain(old.meta.get(e.field)) if old is not None else None
            row["new"] = _plain(new.meta.get(e.field))
            row["has"] = e.field in new.meta
        elif e.change == "section":
            row["old"] = (old.sections.get(e.field, "") if old is not None else "") or ""
            row["new"] = new.sections.get(e.field, "") or ""
        rows.append(row)
    d = _dir(store.home)
    d.mkdir(parents=True, exist_ok=True)
    tid = written[0].ticket.upper()
    if rows:
        with (d / f"{tid}.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
            fh.write("".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rows))
    batch = written[0].batch
    if batch:
        with (d / BATCHES).open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"batch": batch, "ticket": tid, "ts": written[0].ts}) + "\n")


def snapshots(home: Path, ticket_id: str) -> dict[int, dict]:
    out = {}
    try:
        text = (_dir(home) / f"{ticket_id.upper()}.jsonl").read_text(encoding="utf-8")
    except OSError:
        return out
    for ln in text.splitlines():
        try:
            r = json.loads(ln)
            out[int(r["seq"])] = r
        except (ValueError, KeyError, TypeError):
            continue
    return out


class collecting:
    """`with collecting() as got:` every event written inside the block is appended to `got` (the web middleware's way to know
    whether a request wrote anything, and so whether it gets an Undo)."""

    def __enter__(self) -> list:
        self.got: list = []
        self._token = _COLLECT.set(self.got)
        return self.got

    def __exit__(self, *exc):
        _COLLECT.reset(self._token)
        return False


# -- finding events ----------------------------------------------------------------------------------------------------------

def batch_events(store, batch_id: str) -> list:
    """Every event of one batch, across tickets, oldest first. The batch index names the tickets; without it every log is read."""
    ids: list[str] = []
    try:
        for ln in (_dir(store.home) / BATCHES).read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if r.get("batch") == batch_id and r.get("ticket") not in ids:
                ids.append(r["ticket"])
    except OSError:
        pass
    if not ids:
        ids = [p.stem for p in sorted((store.home / activity.DIR).glob("*.jsonl"))]
    evs = [e for tid in ids for e in activity.read(store.home, tid) if e.batch == batch_id]
    return sorted(evs, key=lambda e: (e.ts, e.ticket, e.seq))


def last_change(store, ticket_id: str) -> list:
    """The events of the ticket's last save, as `remember` recorded it; for older events, the trailing ones that share its time,
    actor and batch."""
    evs = activity.read(store.home, ticket_id)
    if not evs:
        return []
    last = evs[-1]
    save = snapshots(store.home, ticket_id).get(last.seq, {}).get("save")
    if save is not None:  # the save is known exactly
        return [e for e in evs if save <= e.seq <= last.seq]
    out = []
    for e in reversed(evs):
        if (e.ts, e.actor, e.batch) != (last.ts, last.actor, last.batch):
            break
        out.append(e)
    return list(reversed(out))


def undone_by(evs: list) -> dict[int, int]:
    """{seq of an event: seq of the later event that undid it} within one ticket's log."""
    out = {}
    for e in evs:
        m = NOTE.search(e.what or "")
        if m:
            out[int(m.group(1))] = e.seq
    return out


def is_undone(seq: int, done: dict[int, int]) -> bool:
    """True when the change is undone now: undone by a later event that was not itself undone (an undo of an undo is a redo)."""
    n, seen = 0, set()
    while seq in done and seq not in seen:
        seen.add(seq)
        seq, n = done[seq], n + 1
    return n % 2 == 1


# -- who, in words ---------------------------------------------------------------------------------------------------------

def who_words(store, e) -> str:
    from . import people as peoplemod, timeline

    pe = peoplemod.load(store.home)
    p = pe.get(e.actor)
    name = p.name if p else e.actor
    return f"{name} ({timeline.kind_label(e.kind)}) at {timeline.when_text(e.ts)}"


def saved_sentence(events: list) -> str:
    """The toast's sentence for what a write just saved, in plain words."""
    ids = sorted({e.ticket for e in events})
    if len(ids) > 1:
        return f"{len(events)} changes saved on {len(ids)} tickets."
    tid = ids[0]
    if any(e.field == "Decision" for e in events):
        return f"Decision recorded on {tid}."
    if len(events) == 1:
        e = events[0]
        if e.change == "field":
            return f"{tid} {e.field} is now {e.new or 'empty'}."
        if e.change == "comment":
            return f"Comment added to {tid}."
        if e.change == "created":
            return f"{tid} created."
        return f"{tid} {e.field} saved."
    return f"{len(events)} changes saved on {tid}."


# -- reverting -------------------------------------------------------------------------------------------------------------

class Refused(ValueError):
    """A revert that was not done; `conflict` is True when a later change is in the way (forcing may undo it anyway)."""

    def __init__(self, msg: str, conflict: bool = False):
        super().__init__(msg)
        self.conflict = conflict


@dataclass
class Result:
    batch: str  # the batch id of the revert's own events ('' when nothing was written)
    reverted: list = field(default_factory=list)  # the events that were undone
    skipped: list = field(default_factory=list)  # (event, why) for changes that have nothing to put back (a git commit)
    written: list = field(default_factory=list)  # the new events

    @property
    def message(self) -> str:
        n = len(self.reverted)
        if not n:
            return "Nothing to undo." + (f" {self.skipped[0][1]}" if self.skipped else "")
        ids = sorted({e.ticket for e in self.reverted})
        if n == 1:
            e = self.reverted[0]
            what = f"{e.ticket} {_short(e)}"
        else:
            what = f"{n} changes on {ids[0]}" if len(ids) == 1 else f"{n} changes on {len(ids)} tickets"
        tail = f" {len(self.skipped)} left as they were." if self.skipped else ""
        return f"Undone: {what}.{tail}"


def _short(e) -> str:
    if e.change == "field":
        return f"{e.field} is {e.old or 'empty'} again"
    if e.change == "comment":
        return "comment withdrawn"
    if e.change == "created":
        return "set aside as rejected"
    if e.field == "Decision":
        return "decision withdrawn"
    return f"{e.field} is back as it was"


def _cur(v) -> str:
    return activity._val(_plain(v), False)


def _later(evs: list, e, pred) -> "activity.Event | None":
    hits = [x for x in evs if x.seq > e.seq and pred(x)]
    return hits[-1] if hits else None


def _in_the_way(store, evs, e, pred, what: str) -> Refused:
    x = _later(evs, e, pred)
    if x is not None and NOTE.search(x.what or "") and int(NOTE.search(x.what).group(1)) == e.seq:
        return Refused(f"{e.ticket} #{e.seq} is already undone.", conflict=False)
    who = f" by {who_words(store, x)}" if x is not None else " outside Desk"
    return Refused(f"{e.ticket} {what} was changed since{who}. Undo anyway to put it back.", conflict=True)


def _restore_value(e, snap: dict | None):
    """(present?, value) the field had before the event."""
    if snap is not None:
        return bool(snap.get("had", True)) and snap.get("old") is not None, snap.get("old")
    if e.old.endswith("…") and len(e.old) >= activity.MAX_VALUE:
        raise Refused(f"{e.ticket} {e.field}: the old value was too long to keep in the log, so it cannot be put back.")
    if not e.old:
        return False, None
    return True, e.old


def _line_of(t) -> str:
    from . import identity

    a = identity.CURRENT.get()
    return (a.display() if a is not None else "desk"), (f" {a.tag()}" if a is not None else "")


def apply(store, t, e, evs: list, snaps: dict, force: bool = False, now: dt.datetime | None = None) -> None:
    """Change ticket `t` in memory so that event `e` is undone. Raises Refused when it cannot be, or should not be without force."""
    now = now or dt.datetime.now()
    if e.change == "git":
        raise Refused("A git commit is not undone here; its changes are already events of their own.")
    if is_undone(e.seq, undone_by(evs)) and not force:
        raise Refused(f"{e.ticket} #{e.seq} is already undone.")
    who, tag = _line_of(t)
    stamp_day, stamp_time = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d %H:%M")
    if e.change == "field":
        snap = snaps.get(e.seq)
        if snap is not None:  # the full value this change wrote must still be there
            same = _cur(t.meta.get(e.field)) == _cur(snap.get("new")) and (e.field in t.meta) == bool(snap.get("has", True))
        else:  # a change from before undo existed: the logged (one-line) value must still match
            same = activity._val(t.meta.get(e.field)) == e.new
        if not same and not force:
            raise _in_the_way(store, evs, e, lambda x: x.change == "field" and x.field == e.field, e.field)
        has, val = _restore_value(e, snap)
        if has:
            t.meta[e.field] = copy.deepcopy(val)
        else:
            t.meta.pop(e.field, None)
        return
    if e.change == "comment":
        words = (e.new or "").replace('"', "'")
        t.append_line("Comments", f'- {stamp_time} · {who} · Withdrawn: "{words}" (the comment of {e.ts[:16].replace("T", " ")} by {e.actor}){tag} <!-- kind: FYI -->')
        return
    if e.change == "created":
        t.append_line("Decision", f'- {stamp_day} · {who} · rejected · "Undone: this ticket was created by mistake (undoes #{e.seq})."')
        t.meta["status"] = "rejected"
        return
    if e.change != "section":
        raise Refused(f"{e.ticket} #{e.seq} is a change of a kind undo does not know ({e.change}).")
    snap = snaps.get(e.seq)
    if e.field == "Decision":
        added = activity._added_lines(snap["old"], snap["new"]) if snap else []
        named = activity._val(re.sub(r"^\s*[-*+]\s+", "", added[0]) if added else e.new).replace('"', "'")
        t.append_line("Decision", f'- {stamp_day} · {who} · reverted · "Withdraws the decision: {named}"')
        return
    if e.field == "Comments":
        raise Refused("Comments are never removed. Withdraw a comment instead.")
    if snap is None:
        raise Refused(f"{e.ticket} {e.field}: the text before this change was not kept (it was made before undo existed), so it cannot be put back.")
    if (t.sections.get(e.field) or "").strip() != (snap["new"] or "").strip() and not force:
        raise _in_the_way(store, evs, e, lambda x: x.change == "section" and x.field == e.field, e.field)
    t.set_section(e.field, snap["old"])


def revert(store, events: list, force: bool = False) -> Result:
    """Undo `events` (any tickets), newest first, each through its own save under one new batch id. Every change is checked first
    on copies, so a refusal anywhere writes nothing. A git event is skipped (it has nothing to put back)."""
    order = sorted(events, key=lambda e: (e.ts, e.seq), reverse=True)
    res = Result("")
    logs: dict[str, list] = {}
    snaps: dict[str, dict] = {}
    copies: dict = {}
    todo = []
    for e in order:
        if e.change == "git":
            res.skipped.append((e, "A git commit has nothing to put back."))
            continue
        tid = e.ticket.upper()
        if tid not in logs:
            logs[tid] = activity.read(store.home, tid)
            snaps[tid] = snapshots(store.home, tid)
            try:
                copies[tid] = store.get(tid)
            except KeyError:
                raise Refused(f"{tid} is not in this store any more.")
        apply(store, copies[tid], e, logs[tid], snaps[tid], force)  # a dry run on copies: raises before anything is written
        todo.append(e)
    if not todo:
        return res
    res.batch = new_batch()
    with activity.batch(res.batch):
        for e in todo:
            tid = e.ticket.upper()
            t = store.get(tid)
            apply(store, t, e, activity.read(store.home, tid), snapshots(store.home, tid), force)
            with activity.note(f"undoes #{e.seq}"):
                with collecting() as got:
                    store.save(t)
            res.written += got
            res.reverted.append(e)
    return res


def revert_event(store, ticket_id: str, seq: int, force: bool = False) -> Result:
    evs = [e for e in activity.read(store.home, ticket_id) if e.seq == int(seq)]
    if not evs:
        raise Refused(f"{ticket_id.upper()} has no change #{seq}.")
    return revert(store, evs, force)


def revert_batch(store, batch_id: str, force: bool = False) -> Result:
    evs = batch_events(store, batch_id)
    if not evs:
        raise Refused(f"No changes with batch {batch_id}.")
    return revert(store, evs, force)


def plan(store, events: list) -> tuple[list, list]:
    """(events that would be undone, [(event, why) that would be refused]) without writing: the dry run behind `drew undo --dry-run`."""
    ok, no = [], []
    copies, logs, snaps = {}, {}, {}
    for e in sorted(events, key=lambda e: (e.ts, e.seq), reverse=True):
        tid = e.ticket.upper()
        try:
            if tid not in copies:
                copies[tid], logs[tid], snaps[tid] = store.get(tid), activity.read(store.home, tid), snapshots(store.home, tid)
            apply(store, copies[tid], e, logs[tid], snaps[tid])
            ok.append(e)
        except (Refused, KeyError) as x:
            no.append((e, str(x).strip("'")))
    return ok, no


# -- this week's changes, grouped (the /undo page) --------------------------------------------------------------------------

@dataclass
class Group:
    key: str  # the batch id, or `<ID>#<seq>` for a change made outside a batch
    batch: str
    ts: str
    actor: str
    kind: str
    events: list
    undone: set = field(default_factory=set)  # (ticket, seq) of the events already undone

    @property
    def tickets(self) -> list[str]:
        return sorted({e.ticket for e in self.events})

    @property
    def all_undone(self) -> bool:
        return all((e.ticket, e.seq) in self.undone for e in self.events)


def week(store, now: dt.datetime | None = None, actor: str = "", ticket: str = "", limit: int = 300) -> tuple[list[Group], dict[str, int]]:
    """([groups newest first], {actor: changes}) for every change in the last seven days. Batched changes are one group; a change
    made outside a batch is grouped with the other events of the same save (same ticket, time and actor)."""
    now = now or dt.datetime.now()
    since = (now - WEEK).replace(microsecond=0).isoformat()
    root = store.home / activity.DIR
    files = [root / f"{ticket.upper()}.jsonl"] if ticket else sorted(root.glob("*.jsonl"))
    cutoff = (now - WEEK).timestamp()
    groups: dict[str, Group] = {}
    actors: dict[str, int] = {}
    for p in files:
        try:
            if p.stat().st_mtime < cutoff:
                continue
        except OSError:
            continue
        evs = activity.read(store.home, p.stem)
        gone = undone_by(evs)
        for e in evs:
            if e.ts < since or e.source in ("migration", "git"):
                continue
            actors[e.actor] = actors.get(e.actor, 0) + 1
            if actor and e.actor.lower() != actor.lower():
                continue
            key = e.batch or f"{e.ticket}@{e.ts}@{e.actor}"
            g = groups.get(key)
            if g is None:
                g = groups[key] = Group(key, e.batch, e.ts, e.actor, e.kind, [])
            g.events.append(e)
            g.ts = max(g.ts, e.ts)
            if is_undone(e.seq, gone):
                g.undone.add((e.ticket, e.seq))
    out = sorted(groups.values(), key=lambda g: g.ts, reverse=True)[:limit]
    return out, dict(sorted(actors.items(), key=lambda kv: (-kv[1], kv[0])))
