"""Popup notifications (DESK-0192): what changed on which ticket, who did it, and who should hear about it.

The server keeps one digest of every ticket (status, owner, assignee, creator, associated, how many Decision and Comments lines) and, when the
tickets folder changes, compares it with the last one and appends one event per changed ticket to a short in-memory feed. A page asks
`/notify/changes?since=<cursor>` and gets the events it has not seen that its viewer should hear about: a ticket the viewer holds a role on
(owner, assignee, creator, associated), or every ticket when the viewer is James or Patch. Nothing leaves this machine: the feed lives in the
server's memory, the browser notification is raised by the page itself, and a restart starts a fresh baseline (no popups for old changes).
Docs: docs/COMMANDS.md (In the UI)."""

from __future__ import annotations

import threading
from collections import deque

from . import identity, people as peoplemod
from .store import Store
from .ticket import Ticket
from .ui import events as ev

SEES_ALL = {"james", "main"}  # James and Patch hear about every ticket
FEED_LENGTH = 200
STATUS_WORDS = {"in_progress": "in progress", "changes_requested": "sent back"}


def _lines(text: str) -> int:
    return sum(1 for ln in (text or "").splitlines() if ln.strip())


def _list(v) -> list[str]:
    return [str(x).strip() for x in (v if isinstance(v, list) else [v]) if str(x or "").strip()]


def digest_of(t: Ticket) -> dict:
    m = t.meta
    return {"title": str(m.get("title") or ""), "status": str(m.get("status") or ""), "owner": str(m.get("owner") or ""),
            "assignee": " ".join(str(m.get("assignee") or "").split()), "creator": str(m.get("creator") or ""),
            "associated": _list(m.get("associated")), "decisions": _lines(t.section("Decision")), "comments": _lines(t.section("Comments"))}


def digest(store: Store) -> dict[str, dict]:
    return {t.id: digest_of(t) for t in store.tickets()}


def changes(old: dict | None, new: dict) -> list[str]:
    """The words for what changed between two digests of one ticket, in the order a person reads them; ['made'] for a new ticket."""
    if old is None:
        return ["created"]
    out = []
    if old["status"] != new["status"]:
        out.append(("moved to " + STATUS_WORDS.get(new["status"], new["status"].replace("_", " "))))
    if old["assignee"] != new["assignee"]:
        out.append("assigned it" if new["assignee"] else "cleared the assignee")
    if old["owner"] != new["owner"]:
        out.append("changed the owner")
    if new["decisions"] > old["decisions"]:
        out.append("recorded a decision")
    if new["comments"] > old["comments"]:
        out.append("added a comment")
    return out


def who_text(row: dict | None, pe: peoplemod.People) -> str:
    """Who made the change, in the words of the identity rule: a coworker, James, Patch, or a code session. `James (as Chaz)` when the owner acted as someone."""
    if not row:
        return "Someone"

    def name(key: str) -> str:
        p = pe.get(key)
        return p.name if p else key

    actor, kind = str(row.get("actor") or ""), str(row.get("kind") or "")
    if kind == "subagent":
        return "A Claude Code subagent"
    if actor == "claude-code":
        return "A Claude Code session"
    base = name(actor) if actor else "Someone"
    p = pe.get(actor)
    if p is None and kind == "session":
        base = f"The {actor} session"
    return f"{base} (as {name(row['as'])})" if row.get("as") else base


def roles_of(d: dict, pe: peoplemod.People) -> set[str]:
    out = {pe.resolve(d["owner"]), pe.resolve(d["assignee"]), pe.resolve(d["creator"]), *(pe.resolve(x) for x in d["associated"])}
    return {x.lower() for x in out if x}


def hears(d: dict, viewer: str, owner: str, pe: peoplemod.People) -> bool:
    """True when the viewer is James or Patch (or the store's owner), or holds a role on the ticket."""
    v = pe.resolve(viewer).lower()
    return v in SEES_ALL or v == pe.resolve(owner).lower() or v in roles_of(d, pe)


class Feed:
    """The changes since this server started, newest last. One per app; safe to ask from several tabs at once."""

    def __init__(self) -> None:
        self.snap: dict | None = None
        self.stamp = -1
        self.seq = 0
        self.items: deque = deque(maxlen=FEED_LENGTH)
        self.lock = threading.Lock()

    def refresh(self, store: Store) -> None:
        stamp = ev.tickets_stamp(store)
        with self.lock:
            if self.snap is not None and stamp == self.stamp:
                return
            new = digest(store)
            if self.snap is not None:
                last = {}
                for row in identity.read_log(store.home):
                    last[row.get("ticket")] = row
                pe = peoplemod.load(store.home)
                for tid, d in sorted(new.items()):
                    words = changes(self.snap.get(tid), d)
                    if not words:
                        continue
                    row = last.get(tid)
                    self.seq += 1
                    self.items.append({"seq": self.seq, "id": tid, "title": d["title"], "new": words == ["created"], "what": ", ".join(words) if words != ["created"] else "made this ticket",
                                       "who": who_text(row, pe), "actor": str((row or {}).get("actor") or ""), "as": str((row or {}).get("as") or ""), "href": f"/ticket/{tid}", "digest": d, "before": self.snap.get(tid)})
            self.snap, self.stamp = new, stamp

    def since(self, store: Store, cursor: int | None, viewer: str, viewer_name: str = "") -> dict:
        """{cursor, items}: the events after `cursor` that `viewer` should hear about; no cursor means 'tell me where we are' (no items)."""
        self.refresh(store)
        pe = peoplemod.load(store.home)
        mine = {pe.resolve(viewer).lower(), pe.resolve(viewer_name).lower()} - {""}
        out = []
        with self.lock:
            now = self.seq
            if cursor is not None:
                for it in self.items:
                    if it["seq"] <= cursor or not any(hears(d, viewer, store.owner, pe) for d in (it["digest"], it["before"]) if d):  # a role held before the change counts: you hear you were taken off a ticket
                        continue
                    if pe.resolve(it["actor"]).lower() in mine or pe.resolve(it["as"]).lower() in mine and it["as"]:
                        continue  # a person is not told about what they just did themselves
                    out.append({k: v for k, v in it.items() if k not in ("digest", "before")})
        return {"cursor": now, "items": out}
