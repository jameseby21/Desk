"""Desk UI: a small FastAPI + Jinja2 app over the ticket store. Plain HTML forms, no build step."""

from __future__ import annotations

import datetime as dt
import html
import os
import re
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import ops
from ..config import find_home
from ..rules import RuleError
from ..store import Store
from ..ticket import SECTIONS, STATUSES
from ..words import screen

TEMPLATES = Path(__file__).parent / "templates"


def md_html(text: str) -> str:
    """A deliberately tiny markdown subset: paragraphs, bullets, checkboxes, inline code, links."""
    out: list[str] = []
    in_list = False
    for raw in text.splitlines():
        line = raw.rstrip()
        m = re.match(r"^\s*[-*]\s*(?:\[( |x|X)\]\s*)?(.*)$", line)
        if m:
            if not in_list:
                out.append("<ul>")
                in_list = True
            box = ""
            if m.group(1) is not None:
                checked = m.group(1).lower() == "x"
                box = f'<input type="checkbox" disabled{" checked" if checked else ""}> '
            out.append(f"<li>{box}{_inline(m.group(2))}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if line.strip():
            out.append(f"<p>{_inline(line)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def _inline(s: str) -> str:
    s = html.escape(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    return s


def create_app(home: Path | None = None) -> FastAPI:
    app = FastAPI(title="Desk")
    templates = Jinja2Templates(directory=str(TEMPLATES))
    templates.env.globals.update(screen=screen, md=md_html, statuses=STATUSES, sections=SECTIONS)

    def store() -> Store:
        return Store(home or find_home())

    def ctx(request: Request, st: Store, **kw) -> dict:
        return {"request": request, "owner": st.owner, "home": str(st.home), "today": dt.date.today().isoformat(), **kw}

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
        if cap == "rough":
            stale = []
        return templates.TemplateResponse(request, "today.html", ctx(request, s, mouth=mouth, feet=feet + decisions, others=others, stale=stale,
                                                             decided=decided, cap=cap, s=s))

    @app.get("/board", response_class=HTMLResponse)
    def board(request: Request, project: str | None = None):
        s = store()
        ts = [t for t in s.tickets() if not project or t.project == project]
        cols = [(st, [t for t in ts if t.status == st]) for st in STATUSES]
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
            rows.append({"key": key, "title": s.project_title(key), "counts": counts, "open": len([t for t in mine if t.is_open]),
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
        return templates.TemplateResponse(request, "ticket.html", ctx(request, s, t=t, deps=deps, stale=s.is_stale(t), error=error))

    def _back(t_id: str, error: str | None = None) -> RedirectResponse:
        url = f"/ticket/{t_id}" + (f"?error={html.escape(error)}" if error else "")
        return RedirectResponse(url, status_code=303)

    @app.post("/ticket/{ticket_id}/decide")
    def post_decide(ticket_id: str, action: str = Form(...), words: str = Form("")):
        s = store()
        try:
            t = s.get(ticket_id)
            s.sync_before()
            ops.decide(s, t, approve=(action == "approve"), reason=words, actor=s.owner)
            s.sync_after(f"desk: decide {t.id}")
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
