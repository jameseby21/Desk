"""Desk UI: a small FastAPI + Jinja2 app over the ticket store. Plain HTML forms, no build step."""

from __future__ import annotations

import datetime as dt
import html
import os
import re
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import ops, review as rv
from ..config import find_home
from ..rules import RuleError
from ..store import Store, id_parts
from ..ticket import SECTIONS, STATUSES
from ..words import screen
from .markdown import has_mermaid, render as md_html

TEMPLATES = Path(__file__).parent / "templates"
STATIC = Path(__file__).parent / "static"
MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js"
COMBO_WINDOW = dt.timedelta(minutes=30)
BRAND_TYPES = {".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif"}
_ASSET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]*$")


def by_project(tickets):
    """Group for display: the current project key first, then the id."""
    return sorted(tickets, key=lambda t: (t.project, t.id))


def combos_for(tickets, on: dt.date | None = None) -> list[tuple]:
    """Pairs of tagged tickets done within 30 minutes of each other on `on` (today), from `done_at`."""
    on = on or dt.date.today()
    done = []
    for t in tickets:
        if t.status != "done" or not t.tags:
            continue
        try:
            when = dt.datetime.fromisoformat(str(t.meta.get("done_at") or ""))
        except ValueError:
            continue
        if when.date() == on:
            done.append((when, t))
    done.sort(key=lambda x: x[0])
    out = []
    for i, (wa, a) in enumerate(done):
        for wb, b in done[i + 1:]:
            if wb - wa <= COMBO_WINDOW:
                out.append((a, b))
    return out


def create_app(home: Path | None = None) -> FastAPI:
    app = FastAPI(title="Desk")
    templates = Jinja2Templates(directory=str(TEMPLATES))
    mermaid_src = "/static/mermaid.min.js" if (STATIC / "mermaid.min.js").exists() else MERMAID_CDN
    templates.env.globals.update(screen=screen, md=md_html, has_mermaid=has_mermaid, statuses=STATUSES,
                                 sections=SECTIONS, mermaid_src=mermaid_src, by_project=by_project)
    if STATIC.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    def store() -> Store:
        return Store(home or find_home())

    def ctx(request: Request, st: Store, **kw) -> dict:
        known = None

        def render(text, t=None, section=None):
            """Section markdown with ticket-id links; with `t` and `section`, checkboxes are live."""
            nonlocal known
            if known is None:
                known = {x.id: (x.meta.get("title", ""), x.status) for x in st.tickets()}
            check = {"id": t.id, "section": section} if t is not None and section in ops.CHECK_SECTIONS else None
            actions = (lambda i, status: review_actions(i, status, t.id)) if check and rv.is_review_ticket(t) else None
            return md_html(text, known=known, check=check, actions=actions)

        return {"render": render, "request": request, "owner": st.owner, "home": str(st.home), "today": dt.date.today().isoformat(),
                "titles": {k: st.project_title(k) for k in st.projects()}, **kw}

    @app.get("/", response_class=HTMLResponse)
    def today(request: Request, cap: str = "normal"):
        s = store()
        ts = s.tickets()
        week = ops.week_ago()
        mouth = [t for t in ts if t.status in ("claimed", "in_progress")]
        feet = [t for t in ts if t.status == "review"]
        decisions = [t for t in ts if t.meta.get("type") == "decision" and t.status in ("ready", "inbox", "triage")]
        others = [t for t in ts if t.status in ("ready", "blocked", "changes_requested", "approved", "triage", "inbox")]
        stale = [t for t in ts if s.is_stale(t)]
        decided = [t for t in ts if t.status in ("approved", "done", "rejected", "changes_requested") and str(t.meta.get("updated", "")) >= week]
        rt = rv.find(s, rv.monday_of(dt.date.today()))
        review_line = (rt, rv.waiting_count(rt)) if rt else None
        if cap == "rough":
            stale = []
        combos = combos_for(ts) if s.combos else []
        return templates.TemplateResponse(request, "today.html", ctx(request, s, mouth=by_project(mouth), feet=by_project(feet + decisions),
                                                             others=by_project(others), stale=by_project(stale),
                                                             decided=by_project(decided), cap=cap, s=s, combos=combos, review_line=review_line))

    @app.get("/board", response_class=HTMLResponse)
    def board(request: Request, project: str | None = None):
        s = store()
        ts = [t for t in s.tickets() if not project or t.project == project]
        cols = [(st, by_project([t for t in ts if t.status == st])) for st in STATUSES]
        return templates.TemplateResponse(request, "board.html", ctx(request, s, cols=cols, project=project, projects=sorted(s.projects()), s=s))

    @app.get("/projects", response_class=HTMLResponse)
    def projects(request: Request):
        s = store()
        ts = s.tickets()
        rows = []
        for key, path in s.projects().items():
            mine = [t for t in ts if t.project == key]
            counts = {st: len([t for t in mine if t.status == st]) for st in STATUSES}
            last = max((str(t.meta.get("updated", "")) for t in mine), default="")
            rows.append({"key": key, "title": s.project_title(key), "prefix": s.key_for(key), "aliases": s.aliases_for(key),
                         "counts": counts, "open": len([t for t in mine if t.is_open]),
                         "blocked": len([t for t in mine if t.status == "blocked"]), "last": last})
        return templates.TemplateResponse(request, "projects.html", ctx(request, s, rows=rows))

    @app.get("/ticket/{ticket_id}", response_class=HTMLResponse)
    def ticket(request: Request, ticket_id: str, error: str | None = None):
        s = store()
        try:
            t = s.get(ticket_id)
        except KeyError:
            return HTMLResponse(f"<p>No ticket {html.escape(ticket_id)}</p>", status_code=404)
        deps = []
        by_id = s.by_id()
        for d in t.meta.get("depends_on") or []:
            dep = by_id.get(str(d))
            deps.append((str(d), dep.status if dep else "missing"))
        mermaid = any(has_mermaid(t.section(name)) for name in t.sections)
        return templates.TemplateResponse(request, "ticket.html", ctx(request, s, t=t, deps=deps, stale=s.is_stale(t), error=error, s=s,
                                                             formerly=s.formerly(t), mermaid=mermaid))

    def review_actions(tid: str, status: str, review_id: str) -> str:
        e = html.escape
        out = f' <a class="rv-open" href="/ticket/{e(tid)}">Open</a>'
        if status == "review":
            for action, label in (("approve", "Approve"), ("changes", "Send back")):
                out += (f'<form class="rv-form" method="post" action="/ticket/{e(tid)}/decide">'
                        f'<input type="hidden" name="action" value="{action}"><input type="hidden" name="next" value="/ticket/{e(review_id)}">'
                        f'<input type="text" name="words" placeholder="{"why not" if action == "changes" else "in your words"}" required minlength="4">'
                        f'<button class="btn small" type="submit">{label}</button></form>')
        return out

    @app.get("/brand/{path:path}")
    def brand_asset(path: str):
        s = store()
        root = s.brand_dir.resolve()
        try:
            target = (root / path).resolve()
            target.relative_to(root)
        except (ValueError, OSError):
            return HTMLResponse("<p>No such asset</p>", status_code=404)
        if not target.is_file() or target.suffix.lower() not in BRAND_TYPES:
            return HTMLResponse("<p>No such asset</p>", status_code=404)
        return FileResponse(str(target), headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})

    @app.get("/assets/{ticket_id}/{filename}")
    def asset(ticket_id: str, filename: str):
        s = store()
        if not id_parts(ticket_id.upper()) or not _ASSET_NAME.match(filename) or ".." in filename:
            return HTMLResponse("<p>No such asset</p>", status_code=404)
        root = s.ticket_assets_dir(ticket_id).resolve()
        target = (root / filename).resolve()
        if target.parent != root or not target.is_file():
            return HTMLResponse("<p>No such asset</p>", status_code=404)
        return FileResponse(str(target))

    def _back(t_id: str, error: str | None = None, next_: str = "") -> RedirectResponse:
        url = next_ if re.fullmatch(r"/ticket/[A-Za-z0-9-]+", next_) else f"/ticket/{t_id}" + (f"?error={html.escape(error)}" if error else "")
        return RedirectResponse(url, status_code=303)

    @app.post("/ticket/{ticket_id}/decide")
    def post_decide(ticket_id: str, action: str = Form(...), words: str = Form(""), next: str = Form("")):
        s = store()
        try:
            t = s.get(ticket_id)
            s.sync_before()
            ops.decide(s, t, approve=(action == "approve"), reason=words, actor=s.owner)
            s.sync_after(f"desk: decide {t.id}")
        except (KeyError, RuleError, ValueError) as e:
            return _back(ticket_id, str(e), next)
        return _back(ticket_id, None, next)

    @app.post("/ticket/{ticket_id}/check")
    def post_check(ticket_id: str, section: str = Form(...), index: int = Form(...), checked: int = Form(...)):
        s = store()
        try:
            s.sync_before()
            t = ops.toggle_check(s, ticket_id, section, index, bool(checked), actor=s.owner)
            s.sync_after(f"desk: check {t.id}")
        except (KeyError, RuleError, ValueError) as e:
            return _back(ticket_id, str(e))
        return _back(ticket_id)

    @app.post("/ticket/{ticket_id}/comment")
    def post_comment(ticket_id: str, text: str = Form(""), who: str = Form("")):
        s = store()
        try:
            t = s.get(ticket_id)
            s.sync_before()
            ops.comment(s, t, text, actor=who or s.owner)
            s.sync_after(f"desk: comment {t.id}")
        except (KeyError, RuleError, ValueError) as e:
            return _back(ticket_id, str(e))
        return _back(ticket_id)

    @app.post("/ticket/{ticket_id}/move")
    def post_move(ticket_id: str, to: str = Form(...), reason: str = Form("")):
        s = store()
        try:
            t = s.get(ticket_id)
            s.sync_before()
            if to == "unblock":
                ops.unblock(s, t, actor=s.owner)
            elif to == "rejected":
                ops.reject(s, t, reason, actor=s.owner)
            elif to == "done":
                ops.done(s, t)
            else:
                ops.move(s, t, to, actor=s.owner, reason=reason)
            s.sync_after(f"desk: move {t.id}")
        except (KeyError, RuleError, ValueError) as e:
            return _back(ticket_id, str(e))
        return _back(ticket_id)

    return app


def main() -> None:
    import uvicorn

    home = find_home(os.environ.get("DESK_HOME"))
    port = int(os.environ.get("DESK_UI_PORT", "8765"))
    print(f"Desk UI on http://127.0.0.1:{port}  (store: {home})")
    uvicorn.run(create_app(home), host="127.0.0.1", port=port, log_level="warning")
