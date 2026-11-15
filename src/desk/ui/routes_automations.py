"""The Automations page (DESK-0292): the rules, what each would change today, and Run now (the sweep, undoable as one batch).
Docs: docs/AUTOMATIONS.md, docs/COMMANDS.md (Automations)."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import automations as au
from .. import identity


def router(templates, ctx, store, tokens) -> APIRouter:
    r = APIRouter()

    @r.get("/automations", response_class=HTMLResponse)
    def page(request: Request):
        s = store()
        rules, problem, res = [], "", None
        try:
            rules, repos = au.load(s.home)
            res = au.sweep(s, rules, repos, apply=False)
        except au.AutomationError as e:
            problem = str(e)
        today: dict = {}
        if res is not None:
            for i in res.items:
                today.setdefault(i.rule, []).append(i)
        return templates.TemplateResponse(request, "automations.html", ctx(
            request, s, rules=rules, problem=problem, res=res, today=today, msg=request.query_params.get("msg", "")))

    @r.post("/automations/run")
    def run(token: str = Form("")):
        if not tokens.use(token):
            return RedirectResponse("/automations?" + urlencode({"msg": "That button works once; nothing more was run."}), status_code=303)
        s = store()
        rules, repos = au.load(s.home)
        me = identity.CURRENT.get()  # who pressed the button is kept in the run's record; the changes are by automation
        res = au.sweep(s, rules, repos, apply=True, started_by=me.name if me is not None else "")
        q = {"msg": res.summary().split(" Undo:")[0]}
        if res.changed:
            q["undo"] = res.batch  # DESK-0284's toast offers Undo for the whole run
        return RedirectResponse("/automations?" + urlencode(q), status_code=303)

    return r
