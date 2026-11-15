"""Undo in the browser (DESK-0284): the toast after every write, the routes that revert, and the /undo page.

`UndoMiddleware` gives every web POST its own batch id (activity.batch), so each write route is undoable as one without being edited.
When a request wrote events under that batch, the answer carries the undo: a redirect gets `undo=<batch>&msg=<sentence>` in its address,
and every answer gets the `X-Desk-Undo` and `X-Desk-Undo-Msg` headers, which static/undo.js reads after an in-place post. A route that
opens its own batch (a bulk action of another package) keeps it; this middleware then adds nothing.

The revert routes are posted by the toast (with the `X-Desk-Undo-Request` header, which another site's page cannot send without a
preflight this server never answers) or by the /undo page's forms (with a one-time key). Who reverts is the request's verified identity
(decision 110: nobody acts as anyone else). Docs: docs/COMMANDS.md (In the UI: the undo toast, `/undo`)."""

from __future__ import annotations

import re
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .. import activity, undo
from .. import people as peoplemod
from .. import timeline as tlmod

SKIP = ("/undo", "/static", "/events", "/bugs/")  # a bug report filed from an error page is not taken back from a toast
_BATCH_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_NEXT = re.compile(r"^/(?!/)[A-Za-z0-9_\-/?=&%.]*$")


def _with_query(url: str, params: str) -> str:
    base, hash_, frag = url.partition("#")
    return base + ("&" if "?" in base else "?") + params + (hash_ + frag)


def undo_params(batch: str, msg: str) -> str:
    return f"undo={quote(batch)}&msg={quote(msg)}"


class UndoMiddleware:
    """Pure ASGI: a batch id for every POST, and the undo handed back when the request wrote something under it."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST" or scope["path"].startswith(SKIP):
            return await self.app(scope, receive, send)
        bid = undo.new_batch()
        with activity.batch(bid), undo.collecting() as got:
            async def send_wrapped(message):
                if message["type"] == "http.response.start":
                    mine = [e for e in got if e.batch == bid]
                    if mine:
                        msg = undo.saved_sentence(mine)
                        headers = []
                        for k, v in message.get("headers", []):
                            if k.lower() == b"location" and 300 <= message.get("status", 200) < 400:
                                v = _with_query(v.decode("latin-1"), undo_params(bid, msg)).encode("latin-1")
                            headers.append((k, v))
                        headers += [(b"x-desk-undo", bid.encode()), (b"x-desk-undo-msg", quote(msg).encode())]
                        message = {**message, "headers": headers}
                await send(message)

            await self.app(scope, receive, send_wrapped)


def router(templates, ctx, store, tokens) -> APIRouter:
    r = APIRouter()

    def allowed(request: Request, token: str) -> bool:
        return request.headers.get("x-desk-undo-request") == "1" or tokens.use(token)

    def answer(request: Request, do, next_: str, retry: str):
        js = "application/json" in request.headers.get("accept", "")
        s = store()
        try:
            res = do(s)
        except undo.Refused as e:
            if js:
                return JSONResponse({"error": str(e), "conflict": e.conflict, "token": tokens.issue()}, status_code=409 if e.conflict else 422)
            q = f"error={quote(str(e))}" + (f"&retry={quote(retry)}" if e.conflict else "")
            return RedirectResponse(f"/undo?{q}", status_code=303)
        except ValueError as e:  # a save the store refused (a rule)
            msg = f"Could not undo: {e}. Nothing was changed."
            return JSONResponse({"error": msg, "token": tokens.issue()}, status_code=422) if js else RedirectResponse(f"/undo?error={quote(msg)}", status_code=303)
        if js:
            return JSONResponse({"ok": True, "message": res.message, "batch": res.batch, "count": len(res.reverted), "token": tokens.issue()})
        back = next_ if _NEXT.fullmatch(next_ or "") else "/undo"
        return RedirectResponse(_with_query(back, undo_params(res.batch, res.message)) if res.batch else _with_query(back, f"msg={quote(res.message)}"), status_code=303)

    def forbidden(request: Request):
        msg = "That undo did not come from a Desk page, so nothing was changed. Reload the page and try again."
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"error": msg, "token": tokens.issue()}, status_code=403)
        return HTMLResponse(f"<p>{msg}</p><p><a href=\"/undo\">Open the undo log</a></p>", status_code=403)

    @r.post("/undo/batch/{batch_id}")
    def post_undo_batch(request: Request, batch_id: str, token: str = Form(""), force: str = Form(""), next: str = Form("")):
        """Undo every change of one batch, newest first, or nothing when one of them changed since (unless force=1)."""
        if not _BATCH_ID.fullmatch(batch_id):
            return JSONResponse({"error": "That is not a batch id."}, status_code=404)
        if not allowed(request, token):
            return forbidden(request)
        return answer(request, lambda s: undo.revert_batch(s, batch_id, force=force == "1"), next, f"/undo/batch/{batch_id}")

    @r.post("/undo/event/{ticket_id}/{seq}")
    def post_undo_event(request: Request, ticket_id: str, seq: int, token: str = Form(""), force: str = Form(""), next: str = Form("")):
        """Undo one change of one ticket (its number in the ticket's log)."""
        if not re.fullmatch(r"[A-Za-z0-9-]{1,40}", ticket_id):
            return JSONResponse({"error": "No such ticket."}, status_code=404)
        if not allowed(request, token):
            return forbidden(request)
        return answer(request, lambda s: undo.revert_event(s, ticket_id, seq, force=force == "1"), next, f"/undo/event/{ticket_id.upper()}/{int(seq)}")

    @r.get("/undo", response_class=HTMLResponse)
    def undo_page(request: Request, actor: str = "", ticket: str = "", error: str = "", retry: str = ""):
        """This week's changes by anyone, newest first, grouped by batch, each with Undo; filter by who made them."""
        s = store()
        ticket = ticket.strip().upper() if re.fullmatch(r"[A-Za-z0-9-]{0,40}", ticket.strip()) else ""
        groups, actors = undo.week(s, actor=actor.strip(), ticket=ticket)
        pe = peoplemod.load(s.home)

        def name(key: str) -> str:
            p = pe.get(key)
            return p.name if p else key

        retry = retry if re.fullmatch(r"/undo/(batch/[A-Za-z0-9_-]{1,64}|event/[A-Za-z0-9-]{1,40}/\d{1,9})", retry or "") else ""
        return templates.TemplateResponse(request, "undo.html", ctx(request, s, groups=groups, actors=actors, actor=actor.strip(), ticket=ticket,
                                                                     error=error[:400], retry=retry, name=name, kind_label=tlmod.kind_label, kind_colour=lambda k: tlmod.KIND_COLOURS.get(k, tlmod.PALETTE[-1]),
                                                                     when=tlmod.when_text, s=s))

    return r
