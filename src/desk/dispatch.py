"""Undispatched work (DESK-0166): a ticket that is ready, unblocked and Patch's, with nobody started on it, is a hand-off nobody made.

James, after a delivered design sat an hour with its build tickets ready and no worker: dispatch is the main session's job, and Desk
should notice before he does. A ticket counts as undispatched when it is `ready`, assigned to main (Patch), every dependency is in
review, approved or done, `blocked_on` is empty (a worker name or a reason there means someone knows about it), and the ticket file has
not changed for more than an hour. The hour is read from the file's modification time, since `updated` holds only a date."""

from __future__ import annotations

import datetime as dt
import time

from . import people as peoplemod
from .store import Store
from .ticket import Ticket

AFTER_HOURS = 1.0
SETTLED = {"review", "approved", "done"}


def idle_hours(t: Ticket, now: float | None = None) -> float:
    """Hours since the ticket file last changed (its `updated` date at midnight when there is no file)."""
    now = now if now is not None else time.time()
    try:
        return (now - t.path.stat().st_mtime) / 3600
    except (OSError, AttributeError):
        try:
            return (now - dt.datetime.fromisoformat(str(t.meta.get("updated"))[:10]).timestamp()) / 3600
        except ValueError:
            return 0.0


def undispatched(store: Store, after_hours: float = AFTER_HOURS, now: float | None = None, tickets: list[Ticket] | None = None) -> list[tuple[Ticket, float]]:
    """(ticket, idle hours) for every undispatched ticket, the longest-waiting first."""
    pe = peoplemod.load(store.home)
    every = tickets if tickets is not None else store.tickets()
    by_id = {t.id: t for t in every}
    out = []
    for t in every:
        if t.status != "ready" or pe.resolve(str(t.meta.get("assignee") or "")) != "main":
            continue
        if str(t.meta.get("blocked_on") or "").strip():
            continue  # a running worker or a reason is named
        deps = [by_id.get(str(d)) for d in (t.meta.get("depends_on") or [])]
        if any(d is None or d.status not in SETTLED for d in deps):
            continue
        h = idle_hours(t, now)
        if h > after_hours:
            out.append((t, h))
    out.sort(key=lambda x: -x[1])
    return out


def warning(store: Store, t: Ticket, hours: float) -> str:
    return (f"{t.id}: undispatched for {hours:.0f} h ({repo_of(store, t.project)}): ready, unblocked and assigned to Patch with no worker; "
            "start one (`drew dispatch` lists them by repo) or set blocked_on to the worker or the reason")


def repo_of(store: Store, project: str) -> str:
    meta, _ = store.project_profile(project)
    return str(meta.get("repo") or project)


def count(store: Store) -> int:
    return len(undispatched(store))


def by_repo(store: Store, rows: list[tuple[Ticket, float]]) -> list[tuple[str, list[tuple[Ticket, float]]]]:
    """Grouped by the project's repository (its `repo:` profile line, else the project key), the group with the oldest ticket first."""
    groups: dict[str, list] = {}
    for t, h in rows:
        groups.setdefault(repo_of(store, t.project), []).append((t, h))
    return sorted(groups.items(), key=lambda kv: -max(h for _, h in kv[1]))


def brief(t: Ticket, hours: float) -> dict:
    eff = t.meta.get("effort_hours")
    return {"id": t.id, "title": str(t.meta.get("title") or ""), "size": str(t.meta.get("size") or ""),
            "effort": f"{eff[0]}-{eff[1]} h" if isinstance(eff, list) and len(eff) == 2 else "", "deps": [str(d) for d in (t.meta.get("depends_on") or [])],
            "idle_hours": round(hours, 1)}
