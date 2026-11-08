"""Request timing: a Server-Timing header (db, render, total) on every response and a log line for slow requests.

`db` is time spent reading the markdown store (tickets, profiles); `render` is template time without the store reads
the templates trigger; `total` is wall time until the response starts."""

from __future__ import annotations

import functools
import logging
import time
from contextvars import ContextVar

SLOW_MS = 300.0
log = logging.getLogger("desk.slow")
_acc: ContextVar[dict | None] = ContextVar("desk_timing", default=None)


def timed_db(fn):
    """Count the time of a store read as `db` when a request is being timed. Nested reads are counted once."""
    @functools.wraps(fn)
    def wrapper(*a, **k):
        acc = _acc.get()
        if acc is None or acc["depth"]:
            return fn(*a, **k)
        acc["depth"] = 1
        t0 = time.perf_counter()
        try:
            return fn(*a, **k)
        finally:
            acc["db"] += time.perf_counter() - t0
            acc["depth"] = 0
    return wrapper


def timed_render(fn):
    """Count the time of a template render as `render`, minus the store reads made while it ran."""
    @functools.wraps(fn)
    def wrapper(*a, **k):
        acc = _acc.get()
        if acc is None:
            return fn(*a, **k)
        db0, t0 = acc["db"], time.perf_counter()
        try:
            return fn(*a, **k)
        finally:
            acc["render"] += max(0.0, time.perf_counter() - t0 - (acc["db"] - db0))
    return wrapper


def header(acc: dict, total: float) -> str:
    return f"db;dur={acc['db'] * 1000:.1f}, render;dur={acc['render'] * 1000:.1f}, total;dur={total * 1000:.1f}"


class TimingMiddleware:
    """Pure ASGI, so streaming responses (server-sent events) are left alone."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        acc = {"db": 0.0, "render": 0.0, "depth": 0}
        token = _acc.set(acc)
        t0 = time.perf_counter()

        async def send_wrapped(message):
            if message["type"] == "http.response.start":
                total = time.perf_counter() - t0
                message = {**message, "headers": [*message.get("headers", []), (b"server-timing", header(acc, total).encode())]}
                if total * 1000 > SLOW_MS and not scope["path"].startswith("/events"):
                    log.warning("slow request %s %s %.0f ms (%s)", scope["method"], scope["path"], total * 1000, header(acc, total))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapped)
        finally:
            _acc.reset(token)
