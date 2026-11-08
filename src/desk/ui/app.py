"""Desk UI: a small FastAPI + Jinja2 app over the ticket store. Plain HTML forms, no build step."""

from __future__ import annotations

import datetime as dt
import html
import os
import re
import secrets
from collections import OrderedDict
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import docs as docsmod, ops, review as rv, style as styles, tracks as trk, version as ver
from ..config import find_home
from ..rules import RuleError
from ..store import Store, id_parts, split_profile
from ..ticket import SECTIONS, STATUSES
from ..words import screen
from urllib.parse import quote

from .markdown import has_mermaid, render as md_html

TEMPLATES = Path(__file__).parent / "templates"
STATIC = Path(__file__).parent / "static"
CONTENT = Path(__file__).parent / "content"
_MARK = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><style>.b{fill:#141414}'
         '@media(prefers-color-scheme:dark){.b{fill:#F5F3EC}}</style><rect class="b" width="64" height="64" rx="14"/>'
         '<circle cx="32" cy="32" r="19" fill="#C8F03C"/><path d="M14.5 26c6 1.5 9.5 6 9.5 12M49.5 38c-6-1.5-9.5-6-9.5-12" fill="none" '
         'stroke="#fff" stroke-width="2.4" stroke-linecap="round"/><path d="M23 32.5l6.5 6.5L42 25" fill="none" stroke="#141414" stroke-width="4.6" '
         'stroke-linecap="round" stroke-linejoin="round"/></svg>')
FAVICON = quote(_MARK, safe="")
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


MAX_TOKENS = 5000
_OK_TEXT = {"comment": "Comment added", "decide": "Decision recorded", "check": "Checkbox saved", "move": "Ticket moved"}
_AT = re.compile(r"\d{2}:\d{2}")


class Tokens:
    """One-time form tokens, kept in memory for this process. A token works once; a reused one is refused."""

    def __init__(self, cap: int = MAX_TOKENS):
        self._live: OrderedDict[str, None] = OrderedDict()
        self._cap = cap

    def issue(self) -> str:
        tok = secrets.token_urlsafe(16)
        self._live[tok] = None
        while len(self._live) > self._cap:
            self._live.popitem(last=False)
        return tok

    def use(self, tok: str) -> bool:
        return self._live.pop(tok or "", 0) is None


def why_text(home: Path) -> str:
    """WHY.md is read from where it lives: the repo's docs/WHY.md (DESK_REPO, or the store's desk/ folder). The copy packaged
    into the wheel at build time is only a fallback for installs without the repo."""
    repo = ver.find_repo(None, home)
    for p in ([repo / "docs" / "WHY.md"] if repo else []) + [CONTENT / "WHY.md"]:
        if p.is_file():
            return p.read_text(encoding="utf-8")
    return "WHY.md not found: set DESK_REPO to your Desk repo."


def create_app(home: Path | None = None) -> FastAPI:
    app = FastAPI(title="Desk")
    tokens = Tokens()
    app.state.tokens = tokens
    templates = Jinja2Templates(directory=str(TEMPLATES))
    mermaid_src = "/static/mermaid.min.js" if (STATIC / "mermaid.min.js").exists() else MERMAID_CDN
    templates.env.globals.update(screen=screen, md=md_html, has_mermaid=has_mermaid, statuses=STATUSES,
                                 sections=SECTIONS, mermaid_src=mermaid_src, by_project=by_project, token=tokens.issue, track_slug=trk.track_slug, track_of=trk.track_of,
                                 build=ver.describe())
    templates.env.filters["urlencode_path"] = lambda v: quote(str(v), safe="/#")
    if STATIC.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    def store() -> Store:
        return Store(home or find_home())

    def ctx(request: Request, st: Store, **kw) -> dict:
        known = None
        seen: set = set()
        gloss = docsmod.glossary(st.home)

        def exists(rel):
            return docsmod.resolve_doc(st.home, rel)  # the Path (truthy) or None

        def render(text, t=None, section=None, doc=False):
            """Section markdown with ticket-id links; with `t` and `section`, checkboxes are live."""
            nonlocal known
            if known is None:
                known = {x.id: (x.meta.get("title", ""), x.status) for x in st.tickets()}
            check = {"id": t.id, "section": section} if t is not None and section in ops.CHECK_SECTIONS else None
            actions = (lambda i, status: review_actions(i, status, t.id)) if check and rv.is_review_ticket(t) else None
            if check:
                check = {**check, "token": tokens.issue}
            return md_html(text, known=known, check=check, actions=actions, docs=exists, ids=doc,
                           glossary={} if doc == 'glossary' else gloss, seen=seen)

        return {"render": render, "request": request, "owner": st.owner, "style": styles.load(st.home), "favicon": FAVICON, "home": str(st.home), "today": dt.date.today().isoformat(),
                "titles": {k: st.project_title(k) for k in st.projects()}, **kw}

    @app.get("/", response_class=HTMLResponse)
    def today(request: Request, cap: str = "normal", track: str = ""):
        s = store()
        ts = s.tickets()
        chips = trk.track_chips(ts)
        if track:
            ts = [t for t in ts if trk.track_slug(trk.track_of(t)) == track and trk.track_of(t)]
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
                                                             decided=by_project(decided), cap=cap, s=s, combos=combos, review_line=review_line,
                                                             chips=chips, track=track, chip_base="/", chip_extra=f"cap={cap}"))

    @app.get("/about", response_class=HTMLResponse)
    def about(request: Request):
        s = store()
        text = why_text(s.home)
        return templates.TemplateResponse(request, "about.html", ctx(request, s, body=md_html(text)))

    @app.get("/settings", response_class=HTMLResponse)
    def settings(request: Request, saved: int = 0):
        return templates.TemplateResponse(request, "settings.html", ctx(request, store(), saved=bool(saved), styles=styles.STYLES, dogs=styles.DOGS))

    @app.post("/settings")
    def post_settings(name: str = Form("plain"), dog: str = Form("drew")):
        try:
            styles.save(store().home, name, dog)
        except ValueError:
            return HTMLResponse("<p>Unknown style</p>", status_code=400)
        return RedirectResponse("/settings?saved=1", status_code=303)

    @app.get("/board", response_class=HTMLResponse)
    def board(request: Request, project: str | None = None, track: str = ""):
        s = store()
        ts = [t for t in s.tickets() if not project or t.project == project]
        chips = trk.track_chips(ts)
        if track:
            ts = [t for t in ts if trk.track_of(t) and trk.track_slug(trk.track_of(t)) == track]
        cols = [(st, by_project([t for t in ts if t.status == st])) for st in STATUSES]
        return templates.TemplateResponse(request, "board.html", ctx(request, s, cols=cols, project=project, projects=sorted(s.projects()), s=s,
                                                           chips=chips, track=track, chip_base="/board", chip_extra=f"project={project}" if project else ""))

    @app.get("/projects", response_class=HTMLResponse)
    def projects(request: Request, p: str = "", t: str = "", k: str = ""):
        """Project > track > ticket > peek. What is open lives in the URL: ?p=<project>&t=<track slug>&k=<ticket id>."""
        s = store()
        rows = trk.hierarchy(s, s.tickets())
        peek_html = ""
        if k:
            try:
                peek_html = peek_panel(s, s.get(k))
            except KeyError:
                pass
        return templates.TemplateResponse(request, "projects.html", ctx(request, s, rows=rows, sel_p=p, sel_t=t, sel_k=k.upper(), peek_html=peek_html, s=s))

    def peek_panel(s: Store, t) -> str:
        """The small panel under a ticket row: summary, acceptance, the latest report line, a link to the full page."""
        e = html.escape
        body = (t.section("Context") or t.section("Outcome")).strip()
        summary = re.sub(r"\s+", " ", body.split("\n\n")[0])[:320] if body else ""
        acc = t.acceptance()
        report = next((ln.strip() for ln in reversed(t.section("Report").splitlines()) if ln.strip()), "")
        out = ['<div class="peek">']
        out.append(f'<p><span class="label">Summary</span> {e(summary) if summary else "<span class=muted>No summary yet.</span>"}</p>')
        if acc:
            items = "".join(f'<li>{"[x]" if ok else "[ ]"} {e(txt)}</li>' for ok, txt in acc[:8])
            more = f'<li class="muted">and {len(acc) - 8} more</li>' if len(acc) > 8 else ""
            out.append(f'<div><span class="label">Acceptance ({sum(1 for ok, _ in acc if ok)} of {len(acc)} true)</span><ul>{items}{more}</ul></div>')
        else:
            out.append('<p><span class="label">Acceptance</span> <span class="muted">none yet</span></p>')
        out.append(f'<p><span class="label">Latest report line</span> {e(report[:240]) if report else "<span class=muted>Not run.</span>"}</p>')
        out.append(f'<p><a class="btn small" href="/ticket/{e(t.id)}">Open the full ticket</a></p></div>')
        return "".join(out)

    @app.get("/peek/{ticket_id}", response_class=HTMLResponse)
    def peek(ticket_id: str):
        try:
            return HTMLResponse(peek_panel(store(), store().get(ticket_id)))
        except KeyError:
            return HTMLResponse("<p>No such ticket</p>", status_code=404)

    @app.get("/ticket/{ticket_id}", response_class=HTMLResponse)
    def ticket(request: Request, ticket_id: str, error: str | None = None, ok: str = "", at: str = "", dup: int = 0):
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
                                                             formerly=s.formerly(t), mermaid=mermaid, mentions=docsmod.mentioned_in(list(by_id.values()), ticket_id=t.id),
                                                             owning=docsmod.owning_docs(s, t), source_doc=source_doc(s, t),
                                                             notice=notice(ok, at, dup)))

    @app.get("/doc/{path:path}", response_class=HTMLResponse)
    def doc(request: Request, path: str):
        """A Markdown file from the store, read-only. Only the known folders and top-level .md files; nothing outside the store."""
        s = store()
        target = docsmod.resolve_doc(s.home, path)
        if target is None:
            return HTMLResponse("<p>No such document</p>", status_code=404)
        rel = target.relative_to(s.home.resolve()).as_posix()
        meta, body = split_profile(target.read_text(encoding="utf-8"))
        c = ctx(request, s)
        mentions = docsmod.mentioned_in(s.tickets(), path=rel)
        html_body = c["render"](body, doc="glossary" if rel == "glossary.md" else True)
        index = ""
        if rel.startswith("projects/") and rel.count("/") == 1:
            index = docsmod.doc_index(s, rel.split("/")[1][:-3])
        return templates.TemplateResponse(request, "doc.html", {**c, "rel": rel, "body_html": html_body, "meta": meta, "mentions": mentions,
                                                               "edit_url": docsmod.vscode_url(target), "doc_index": index})

    def source_doc(s: Store, t) -> dict | None:
        src = str(t.meta.get("source") or "").strip()
        base = src.split("#", 1)[0]
        target = docsmod.resolve_doc(s.home, base) if base.lower().endswith(".md") else None
        return {"path": src, "edit_url": docsmod.vscode_url(target)} if target else None

    def notice(ok: str, at: str, dup: int) -> str:
        if dup:
            return "That comment is already on the ticket"
        if ok in _OK_TEXT:
            return f"{_OK_TEXT[ok]} {at}" if _AT.fullmatch(at) else _OK_TEXT[ok]
        return ""

    def review_actions(tid: str, status: str, review_id: str) -> str:
        e = html.escape
        out = f' <a class="rv-open" href="/ticket/{e(tid)}">Open</a>'
        if status == "review":
            for action, label in (("approve", "Approve"), ("changes", "Send back")):
                out += (f'<form class="rv-form" method="post" action="/ticket/{e(tid)}/decide">'
                        f'<input type="hidden" name="token" value="{tokens.issue()}"><input type="hidden" name="action" value="{action}"><input type="hidden" name="next" value="/ticket/{e(review_id)}">'
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

    def _back(t_id: str, error: str | None = None, next_: str = "", ok: str = "", dup: bool = False) -> RedirectResponse:
        if next_ and re.fullmatch(r"/ticket/[A-Za-z0-9-]+", next_):
            return RedirectResponse(next_, status_code=303)
        url = f"/ticket/{t_id}"
        if error:
            url += f"?error={quote(error)}"
        elif dup:
            url += "?dup=1"
        elif ok:
            url += f"?ok={ok}&at={dt.datetime.now().strftime('%H:%M')}"
        return RedirectResponse(url, status_code=303)

    def _spent(ticket_id: str) -> HTMLResponse:
        e = html.escape(ticket_id)
        return HTMLResponse(
            "<!doctype html><meta charset=utf-8><title>Already sent</title><body style=\"font:16px system-ui;max-width:40em;margin:3em auto;padding:0 1em\">"
            "<h1>That form was already sent</h1><p>This button works once, so a double click or a refresh can not post twice. "
            f"Nothing more was written.</p><p><a href=\"/ticket/{e}\">Back to {e}</a></p></body>", status_code=409)

    @app.post("/ticket/{ticket_id}/decide")
    def post_decide(ticket_id: str, action: str = Form(...), words: str = Form(""), next: str = Form(""), token: str = Form("")):
        if not tokens.use(token):
            return _spent(ticket_id)
        s = store()
        try:
            t = s.get(ticket_id)
            s.sync_before()
            ops.decide(s, t, approve=(action == "approve"), reason=words, actor=s.owner)
            s.sync_after(f"desk: decide {t.id}")
        except (KeyError, RuleError, ValueError) as e:
            return _back(ticket_id, str(e), next)
        return _back(ticket_id, None, next, ok="decide")

    @app.post("/ticket/{ticket_id}/check")
    def post_check(ticket_id: str, section: str = Form(...), index: int = Form(...), checked: int = Form(...), token: str = Form("")):
        if not tokens.use(token):
            return _spent(ticket_id)
        s = store()
        try:
            s.sync_before()
            t = ops.toggle_check(s, ticket_id, section, index, bool(checked), actor=s.owner)
            s.sync_after(f"desk: check {t.id}")
        except (KeyError, RuleError, ValueError) as e:
            return _back(ticket_id, str(e))
        return _back(ticket_id, ok="check")

    @app.post("/ticket/{ticket_id}/comment")
    def post_comment(ticket_id: str, text: str = Form(""), who: str = Form(""), token: str = Form("")):
        if not tokens.use(token):
            return _spent(ticket_id)
        s = store()
        try:
            t = s.get(ticket_id)
            s.sync_before()
            res = ops.comment(s, t, text, actor=who or s.owner)
            if res.added:
                s.sync_after(f"desk: comment {t.id}")
        except (KeyError, RuleError, ValueError) as e:
            return _back(ticket_id, str(e))
        return _back(ticket_id, ok="comment", dup=not res.added)

    @app.post("/ticket/{ticket_id}/move")
    def post_move(ticket_id: str, to: str = Form(...), reason: str = Form(""), token: str = Form("")):
        if not tokens.use(token):
            return _spent(ticket_id)
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
        return _back(ticket_id, ok="move")

    return app


def main() -> None:
    import uvicorn

    home = find_home(os.environ.get("DESK_HOME"))
    port = int(os.environ.get("DESK_UI_PORT", "8765"))
    print(f"Desk UI on http://127.0.0.1:{port}  (store: {home})")
    uvicorn.run(create_app(home), host="127.0.0.1", port=port, log_level="warning")
