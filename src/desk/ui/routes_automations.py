"""The Automations page (DESK-0292): the rules, what each would change today, Run now (the sweep) and Undo for the run just made.

Progress: with script, Run now and Undo start a job in the background and automations.js polls `/automations/job/<id>`, which says
which rule and which ticket of how many it is on, then the plain result sentence. Without script both post and redirect when done.
Undo here restores from the run's journal (`drew auto undo`), so it works on its own; DESK-0284's toast offers the same Undo once
merged, and the page then hides its own button. Docs: docs/AUTOMATIONS.md, docs/COMMANDS.md (Automations)."""

from __future__ import annotations

import contextvars
import secrets
import threading
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .. import activity, identity
from .. import automations as au

KEEP_JOBS = 20  # finished jobs kept for their last poll


def _wants_json(request: Request) -> bool:
    return "application/json" in (request.headers.get("accept") or "")


def router(templates, ctx, store, tokens) -> APIRouter:
    r = APIRouter()
    jobs: dict = {}
    lock = threading.Lock()

    def busy() -> bool:
        return any(j["state"] == "running" for j in jobs.values())

    def start(work) -> str:
        """Run `work(job)` in the background as the person who pressed the button (the copied request context carries who)."""
        jid = secrets.token_hex(6)
        job = {"state": "running", "step": "Starting", "done": 0, "total": 0, "message": "", "undo": ""}
        with lock:
            for old in [k for k, j in jobs.items() if j["state"] != "running"][:-KEEP_JOBS]:
                jobs.pop(old, None)
            jobs[jid] = job

        def run():
            try:
                work(job)
            except Exception as e:  # the person sees one plain sentence, never a stuck bar
                job.update(state="failed", message=f"The run stopped: {e}. Some tickets may have changed; the page lists what is left.")

        threading.Thread(target=contextvars.copy_context().run, args=(run,), daemon=True).start()
        return jid

    def run_now(job=None) -> tuple[str, str]:
        """(the result sentence, the batch to undo or ""). With a job, fills in which rule and ticket it is on."""
        s = store()
        rules, repos = au.load(s.home)
        me = identity.CURRENT.get()  # who pressed the button is kept in the run's record; the changes are by automation

        def tick(i, n, rule, k, m):
            if job is not None:
                job.update(step=f"Rule {i} of {n}: {rule}", done=k, total=m)

        res = au.sweep(s, rules, repos, apply=True, started_by=me.name if me is not None else "", tick=tick)
        msg = res.summary().split(" Undo:")[0]
        return msg, (res.batch if res.changed and au.journal_open(s.home, res.batch) else "")

    def undo_now(batch: str, job=None) -> str:
        s = store()

        def progress(k, n, tid):
            if job is not None:
                job.update(step=f"Restoring {tid}", done=k, total=n)

        with activity.batch("b-" + secrets.token_hex(6)):
            done, kept = au.undo(s, batch, progress=progress)
        return f"Undone: {len(done)} ticket(s) restored from run {batch}" + (f"; {len(kept)} changed since, so left as they are." if kept else ".")

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
        undo = request.query_params.get("undo", "")
        return templates.TemplateResponse(request, "automations.html", ctx(
            request, s, rules=rules, problem=problem, res=res, today=today, msg=request.query_params.get("msg", ""),
            undo=undo if au.journal_open(s.home, undo) else ""))

    @r.post("/automations/run")
    def run(request: Request, token: str = Form("")):
        if not tokens.use(token):
            return _answer(request, "That button works once; nothing more was run.", status=409)
        if busy():
            return _answer(request, "A run is already going. Wait for it to finish, then look again.", status=409)
        if _wants_json(request):
            def work(job):
                msg, batch = run_now(job)
                job.update(state="done", message=msg, undo=batch)
            return JSONResponse({"job": start(work)})
        msg, batch = run_now()
        return RedirectResponse("/automations?" + urlencode({"msg": msg, **({"undo": batch} if batch else {})}), status_code=303)

    @r.post("/automations/undo")
    def undo(request: Request, token: str = Form(""), batch: str = Form("")):
        if not tokens.use(token):
            return _answer(request, "That button works once; nothing more was undone.", status=409)
        if busy():
            return _answer(request, "A run is already going. Wait for it to finish, then undo.", status=409)
        if not au.journal_open(store().home, batch):
            return _answer(request, f"Run {batch} has nothing left to undo (it was undone already, or changed nothing).", status=404)
        if _wants_json(request):
            def work(job):
                job.update(state="done", message=undo_now(batch, job))
            return JSONResponse({"job": start(work)})
        return RedirectResponse("/automations?" + urlencode({"msg": undo_now(batch)}), status_code=303)

    @r.get("/automations/job/{jid}")
    def job(jid: str):
        j = jobs.get(jid)
        if j is None:
            return JSONResponse({"state": "failed", "message": "That run is not known here (Desk may have restarted). Reload the page."}, status_code=404)
        return JSONResponse(dict(j), headers={"Cache-Control": "no-store"})

    return r


def _answer(request: Request, msg: str, status: int):
    if _wants_json(request):
        return JSONResponse({"error": msg}, status_code=status)
    return RedirectResponse("/automations?" + urlencode({"msg": msg}), status_code=303)
