"""Server-sent events for auto-refresh: poll file modification times, no watchdog dependency."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

POLL_SECONDS = 1.5
HEARTBEAT_SECONDS = 15


def _mt(p: Path) -> int:
    try:
        return p.stat().st_mtime_ns
    except OSError:
        return 0


def ticket_stamp(store, ticket_id: str) -> int:
    """Latest mtime of the ticket's file and of anything under tickets/assets/<ID>/."""
    tid = ticket_id.upper()
    latest = 0
    for p in store.ticket_paths():
        if p.name.upper().startswith(tid + "-") or p.stem.upper() == tid:
            latest = max(latest, _mt(p))
    folder = store.ticket_assets_dir(tid)
    if folder.is_dir():
        latest = max([latest, _mt(folder)] + [_mt(f) for f in folder.rglob("*")])
    return latest


def tickets_stamp(store) -> int:
    """Latest mtime of the tickets directory and the ticket files in it (a new or removed ticket moves the directory)."""
    return max([_mt(store.tickets_dir)] + [_mt(p) for p in store.ticket_paths()])


async def stream(stamp, since: int | None = None, poll: float = POLL_SECONDS, timeout: float = 0, stopping=None):
    """Yield SSE text: a `change` event whenever stamp() differs from the last one seen.
    `since` is the stamp the page was rendered at, so a change between render and connect is not missed.
    `timeout` (seconds, 0 = until the client leaves) lets tests and probes end the stream. `stopping()` turning true (the server is shutting
    down, DESK-0169) ends it at the next look: an open stream would otherwise keep the server from ever stopping on Ctrl+C."""
    yield "retry: 3000\n\n"
    last = since if since is not None else stamp()
    start = beat = time.monotonic()
    while True:
        if stopping is not None and stopping():
            return
        cur = stamp()
        if cur != last:
            last = cur
            yield f"event: change\ndata: {cur}\n\n"
        now = time.monotonic()
        if timeout and now - start >= timeout:
            return
        if now - beat >= HEARTBEAT_SECONDS:
            beat = now
            yield ": ping\n\n"
        await asyncio.sleep(min(poll, 0.5) if stopping is not None else poll)
