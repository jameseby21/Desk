"""Motivation in the web UI (DESK-0293): the header count, the focus timer's snooze, the Motivation settings and the template helpers.

`register(app, templates, store)` is the one line app.py calls. Writes to a ticket go through Store.save inside activity.batch, and a
form post comes back with `undo=<batch>&msg=<sentence>` for the undo toast (DESK-0282 contract). Docs: docs/COMMANDS.md (Motivation)."""

from __future__ import annotations

import datetime as dt
import html
import secrets
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .. import activity, motivation as mot
from ..rules import RuleError


def _on(v: str) -> bool:
    return str(v).strip().lower() in ("on", "1", "true", "yes")


def register(app, templates, store) -> APIRouter:
    router = APIRouter()

    def cfg() -> dict:
        return mot.load(store().home)

    def agents(tickets=None) -> dict:
        return mot.agents_at_work(store(), tickets=tickets)

    templates.env.globals.update(motivation=cfg, focus_ok=mot.focus_ok, agents_at_work=agents)

    def wants_json(request: Request) -> bool:
        return "application/json" in request.headers.get("accept", "")

    @router.get("/motivation/state.json")
    def motivation_state():
        """The header's line: decided today, the goal, the streak, and the switches the script needs."""
        s = store()
        c = mot.load(s.home)
        out = mot.state(s.home, s.owner, cfg=c)
        out.update({"count_on": c["count"], "celebrate": c["celebrate"]})
        return JSONResponse(out, headers={"Cache-Control": "no-store"})

    @router.post("/settings/motivation")
    def post_motivation(request: Request, focus: str = Form("on"), focus_minutes: str = Form("25"), goal: str = Form("5"),
                        count: str = Form("on"), celebrate: str = Form("on")):
        js = wants_json(request)
        try:
            mot.save(store().home, focus=_on(focus), focus_minutes=int(focus_minutes or 0), goal=int(goal or 0),
                     count=_on(count), celebrate=_on(celebrate))
        except ValueError as e:
            msg = str(e) if "Nothing was saved" in str(e) else "Type whole numbers for the minutes and the goal. Nothing was saved."
            return JSONResponse({"error": msg}, status_code=400) if js else HTMLResponse(f"<p>{html.escape(msg)}</p>", status_code=400)
        if js:
            return JSONResponse({"ok": True, "message": "Saved. Other pages show it the next time you open them."})
        return RedirectResponse("/settings?saved=1#motivation", status_code=303)

    @router.post("/focus/snooze/{ticket_id}")
    def post_focus_snooze(request: Request, ticket_id: str, token: str = Form("")):
        """The focus timer's Snooze: the ticket leaves Today and Needs me until tomorrow (`snooze_until`, the lanes contract)."""
        js = wants_json(request)
        tokens = request.app.state.tokens
        if not tokens.use(token):
            msg = "That button was already used; reload the page."
            return JSONResponse({"error": msg, "token": tokens.issue()}, status_code=409) if js else HTMLResponse(f"<p>{html.escape(msg)}</p>", status_code=409)
        s = store()
        until = (dt.date.today() + dt.timedelta(days=1)).isoformat()
        batch = "b-" + secrets.token_hex(6)
        try:
            t = s.get(ticket_id)
            s.sync_before()
            with activity.batch(batch):
                t.meta["snooze_until"] = until
                s.save(t)
            s.sync_after(f"desk: snooze {t.id}")
        except (KeyError, RuleError, ValueError) as e:
            msg = e.args[0] if isinstance(e, KeyError) and e.args else str(e)
            return JSONResponse({"error": msg, "token": tokens.issue()}, status_code=404 if isinstance(e, KeyError) else 422) if js \
                else HTMLResponse(f"<p>{html.escape(msg)}</p>", status_code=422)
        msg = f"{t.id} is snoozed until tomorrow."
        if js:
            return JSONResponse({"ok": True, "message": msg, "batch": batch})
        return RedirectResponse(f"/ticket/{t.id}?undo={batch}&msg={quote(msg)}", status_code=303)

    app.include_router(router)
    return router
