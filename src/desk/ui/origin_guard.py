"""Desk answers only pages opened from this computer (standards rule 80 item 10; decision 110: nobody acts as another person).

Desk listens on 127.0.0.1 only, but a page on another site can still reach it: by renaming its own domain to 127.0.0.1 (DNS
rebinding: the browser then lets it read Desk's pages and their one-time tokens), or by posting a form across sites. Either way the
write would be recorded as the owner. Two checks, before any route runs:

- every request names this computer as its Host (127.0.0.1, localhost or ::1, any port; `testserver` is the test client's own name);
- every post (and put, patch, delete) comes from this same origin: an Origin header, when the browser sends one, must equal the
  Host, and `Sec-Fetch-Site` must not say cross-site or same-site.

A refused request gets 403 and one plain sentence; nothing is read or written. Found in the DESK-0290 review."""

from __future__ import annotations

import json

LOCAL = frozenset({"127.0.0.1", "localhost", "::1", "testserver"})
WRITES = frozenset({"POST", "PUT", "PATCH", "DELETE"})
REFUSED = "Desk only answers pages opened on this computer (127.0.0.1 or localhost). Nothing was read or changed."


def host_name(netloc: str) -> str:
    """`127.0.0.1:8765` -> `127.0.0.1`, `[::1]:8765` -> `::1`, lower case."""
    n = netloc.strip().lower()
    if n.startswith("["):
        return n[1:n.find("]")] if "]" in n else n
    return n.rsplit(":", 1)[0] if n.count(":") == 1 else n


def refusal(scope) -> str:
    """'' when the request may go on; otherwise why it is refused (for the log, never shown with any data)."""
    h = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
    host = h.get("host", "")
    if host and host_name(host) not in LOCAL:
        return "host"
    if scope["type"] == "http" and scope.get("method", "GET").upper() in WRITES:
        origin = h.get("origin")
        if origin is not None and (origin.strip().lower() == "null" or origin.strip().lower().split("://", 1)[-1].rstrip("/") != host.lower()):
            return "origin"
        if h.get("sec-fetch-site", "").lower() in ("cross-site", "same-site"):
            return "fetch-site"
    return ""


class LocalOnly:
    """Pure ASGI, outermost: refuses a foreign Host or a cross-origin write with 403 before identity, routes or tokens are touched."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or not refusal(scope):
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            return await send({"type": "websocket.close", "code": 1008})
        accept = next((v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"accept"), "")
        if "application/json" in accept:
            body, kind = json.dumps({"error": REFUSED}).encode("utf-8"), b"application/json"
        else:
            body, kind = REFUSED.encode("utf-8"), b"text/plain; charset=utf-8"
        await send({"type": "http.response.start", "status": 403,
                    "headers": [(b"content-type", kind), (b"content-length", str(len(body)).encode()), (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": body})
