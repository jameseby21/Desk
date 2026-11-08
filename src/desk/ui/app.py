"""Desk UI: a small FastAPI + Jinja2 app over the ticket store. Plain HTML forms, no build step."""

from __future__ import annotations

import datetime as dt
import html
import os
import re
import secrets
import tempfile
import threading
import traceback
from collections import OrderedDict
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import timing
from .. import asks, search as srch, nav as navmod, buildstamp as bs, comments as cm, docs as docsmod, ops, profanity, review as rv, style as styles, tracks as trk, version as ver
from ..config import find_home
from ..rules import RuleError
from ..store import Store, id_parts, split_profile
from ..ticket import SECTIONS, STATUSES, check_states, parse_checks
from ..words import screen
from urllib.parse import quote

from markupsafe import Markup

from . import events as ev
from .markdown import finish_comments, has_mermaid, prep_comments, render as md_html, summary_html

TEMPLATES = Path(__file__).parent / "templates"
STAMP_DIR = TEMPLATES  # where .build-stamp sits; looked up at request time
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
_SECTION_ANCHOR = {"Acceptance": "acceptance", "How to validate": "validate", "Evidence": "evidence", "Prompt": "prompt"}  # where a check box sits on the page
_ASSET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]*$")
IMAGE_TYPES = "PNG, JPEG, WebP or GIF"


def assignee_chips(tickets) -> list[tuple[str, int]]:
    """(assignee, open tickets) for every name that has at least one ticket, for the filter chips."""
    names: dict = {}
    for t in tickets:
        n = ops.assignee_of(t)
        if n:
            names[n] = names.get(n, 0) + t.is_open
    return sorted(names.items(), key=lambda kv: kv[0].lower())


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
_OK_TEXT = {"comment": "Comment added", "decide": "Decision recorded", "check": "Checkbox saved", "move": "Ticket moved", "attach": "Image attached"}
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

    def valid(self, tok: str) -> bool:
        """True while the token is unspent. Looking does not spend it (a post that failed leaves its key good for Try again)."""
        return (tok or "") in self._live

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


def create_app(home: Path | None = None, poll: float = ev.POLL_SECONDS) -> FastAPI:
    app = FastAPI(title="Desk")
    app.add_middleware(timing.TimingMiddleware)
    tokens = Tokens()
    app.state.tokens = tokens
    templates = Jinja2Templates(directory=str(TEMPLATES))
    templates.TemplateResponse = timing.timed_render(templates.TemplateResponse)
    mermaid_src = "/static/mermaid.min.js" if (STATIC / "mermaid.min.js").exists() else MERMAID_CDN
    templates.env.globals.update(screen=screen, md=md_html, has_mermaid=has_mermaid, statuses=STATUSES,
                                 sections=SECTIONS, mermaid_src=mermaid_src, by_project=by_project, token=tokens.issue, check_summary=lambda text: Markup(summary_html(text)), track_slug=trk.track_slug, track_of=trk.track_of,
                                 build=ver.describe())
    shown = {"censor": None}  # the profanity filter for the request being rendered; None when the person switched it off

    def finalize(v):
        fn = shown["censor"]
        return fn(v) if fn and isinstance(v, str) and not isinstance(v, Markup) else v

    templates.env.finalize = finalize
    templates.env.filters["urlencode_path"] = lambda v: quote(str(v), safe="/#")
    if STATIC.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    def store() -> Store:
        return Store(home or find_home())

    def censor_for(st: Store):
        """The display filter for this person and store, or None when [style] profanity_filter is false."""
        return profanity.make_filter(st.home) if styles.load(st.home)["profanity"] else None

    def ctx(request: Request, st: Store, **kw) -> dict:
        censor = shown["censor"] = censor_for(st)
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
            owner_t = t if t is not None else kw.get("t")
            project = getattr(owner_t, "project", "") or ""
            return md_html(text, known=known, check=check, actions=actions, docs=exists, ids=doc,
                           resolve=lambda p: docsmod.resolve_for(st, project, p)[0],
                           glossary={} if doc == 'glossary' else gloss, seen=seen, censor=censor)

        def comments_html(text):
            """The Comments section: markdown plus the kind label on each comment."""
            return finish_comments(render(prep_comments(text)))

        qp = request.query_params
        done_kind = qp.get("done") or qp.get("ok") or ""
        events_url, events_since = kw.pop("events_url", ""), kw.pop("events_since", "")
        events = {"url": events_url, "since": events_since} if events_url and styles.load(st.home)["autorefresh"] else None
        return {"build_stale": bs.stale(STAMP_DIR), "stale_text": bs.STALE_TEXT, "assign_dod": ops.ASSIGN_DOD, "events": events, "notice": notice(done_kind, qp.get("at", ""), int(qp.get("dup") or 0) if str(qp.get("dup") or "0").isdigit() else 0), "render": render, "comments_html": comments_html, "request": request, "owner": st.owner, "style": styles.load(st.home), "favicon": FAVICON, "home": str(st.home), "today": dt.date.today().isoformat(),
                "titles": {k: st.project_title(k) for k in st.projects()}, **kw}

    held: OrderedDict[str, dict] = OrderedDict()  # unhandled errors a person may file as a bug, by one-time id
    app.state.errors = held
    desk_dir = Path(__file__).resolve().parent.parent

    def error_info(request: Request, exc: BaseException) -> dict:
        frames = traceback.extract_tb(exc.__traceback__)
        mine = [f for f in frames if Path(f.filename).resolve().is_relative_to(desk_dir)]
        first = (mine or frames or [None])[0]
        where = f"{Path(first.filename).name}:{first.lineno} in {first.name}" if first else "unknown"
        summary = "\n".join(f"{Path(f.filename).name}:{f.lineno} in {f.name}" for f in (mine or frames)[-8:])
        return {"type": type(exc).__name__, "message": str(exc)[:500], "route": request.url.path, "method": request.method,
                "frame": where, "summary": summary}

    def error_page(body: str, status: int) -> HTMLResponse:
        return HTMLResponse(
            "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Desk hit a bug</title>"
            "<body style=\"font:16px system-ui;max-width:46em;margin:0 auto;padding:0 1em 3em\">" + body + "</body>", status_code=status)

    @app.exception_handler(Exception)
    async def on_error(request: Request, exc: Exception):
        e = html.escape
        if bs.stale(STAMP_DIR):  # new files on disk, old code in memory: say so instead of a bug report
            return error_page(f'<div style="background:#F2D98B;color:#141414;padding:10px 14px;margin:0 -1em 1.5em;font-weight:600" role="status">{e(bs.STALE_TEXT)}</div>'
                              '<h1>Restart needed</h1><p>The page could not load because Desk was updated while it was running.</p>', 503)
        info = error_info(request, exc)
        eid = secrets.token_urlsafe(12)
        held[eid] = info
        while len(held) > 50:
            held.popitem(last=False)
        return error_page(
            f'<h1>Desk hit a bug</h1><p><b>{e(info["type"])}</b>: {e(info["message"])}</p>'
            f'<p>On <code>{e(info["method"])} {e(info["route"])}</code></p><p>First frame: <code>{e(info["frame"])}</code></p>'
            f'<form method="post" action="/bugs/file" data-progress="none"><input type="hidden" name="eid" value="{e(eid)}">'
            '<button type="submit" style="font:inherit;padding:8px 16px">File as a bug</button></form>'
            '<p><a href="/">Back to Today</a></p>', 500)

    @app.post("/bugs/file")
    def file_bug(eid: str = Form("")):
        """Turn a held UI error into a Desk ticket (project desk, type bug). The same exception type on the same route within 24 hours
        adds a Comments line to the ticket already filed instead of a second ticket."""
        info = held.get(eid)
        if info is None:
            return error_page("<h1>That error is no longer held</h1><p>Reload the page that failed, then file it again.</p><p><a href=\"/\">Back to Today</a></p>", 410)
        s = store()
        now = dt.datetime.now()
        key = f"{info['type']} | {info['route']}"
        stamp_re = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")
        s.sync_before()
        for t in s.tickets():
            if "ui-error" not in t.tags or f"Key: {key}" not in t.section("Context"):
                continue
            seen = [dt.datetime.strptime(m, "%Y-%m-%d %H:%M") for m in stamp_re.findall(t.section("Context").split("\n\n")[0] + " " + t.section("Comments"))]
            if seen and now - max(seen) < dt.timedelta(hours=24):
                t.append_line("Comments", ops._stamp("desk-ui", f"seen again: {info['type']}: {info['message'][:120]}", at_time=True))
                s.save(t)
                s.sync_after(f"desk: ui error again {t.id}")
                return RedirectResponse(f"/ticket/{t.id}", status_code=303)
        title = f"UI error: {info['type']} on {info['route']}"[:60]
        t = ops.new(s, title, "desk", "bug", owner="main")
        t.meta["tags"] = ["ui-error"]
        t.set_section("Context", f"Filed: {now.strftime('%Y-%m-%d %H:%M')}\nKey: {key}\nRoute: {info['method']} {info['route']}\n"
                                  f"Exception: {info['type']}: {info['message']}\nFirst frame: {info['frame']}\n\nTraceback summary (desk frames, oldest first):\n\n```\n{info['summary']}\n```")
        t.set_section("How to validate", f"open {info['route']}; no error")
        s.save(t)
        s.sync_after(f"desk: file {t.id}")
        return RedirectResponse(f"/ticket/{t.id}", status_code=303)

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
        asked = cm.query(s, author=s.owner, unanswered=True)
        return templates.TemplateResponse(request, "today.html", ctx(request, s, asked=asked, mouth=by_project(mouth), feet=by_project(feet + decisions),
                                                             others=by_project(others), stale=by_project(stale),
                                                             decided=by_project(decided), cap=cap, s=s, combos=combos, review_line=review_line,
                                                             chips=chips, track=track, chip_base="/", chip_extra=f"cap={cap}", events_url="/events", events_since=ev.tickets_stamp(s)))

    def sse(stamp, since: str, timeout: float) -> StreamingResponse:
        return StreamingResponse(ev.stream(stamp, int(since) if since.isdigit() else None, poll, min(max(timeout, 0), 3600)), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/events/{ticket_id}")
    def events_ticket(ticket_id: str, since: str = "", timeout: float = 0):
        """Server-sent events: one `change` event when this ticket's file, or anything under tickets/assets/<ID>/, changes mtime."""
        s = store()
        return sse(lambda: ev.ticket_stamp(s, ticket_id), since, timeout)

    @app.get("/events")
    def events_all(since: str = "", timeout: float = 0):
        """Server-sent events for the Today, board and projects pages: the tickets directory's latest mtime."""
        s = store()
        return sse(lambda: ev.tickets_stamp(s), since, timeout)

    search_ix: dict = {}  # one index per store path, kept for the life of the process

    def search_for(s: Store, text: str, limit: int):
        ix = search_ix.get(str(s.home))
        if ix is None:
            ix = search_ix[str(s.home)] = srch.open_index(s)
        ix.refresh(s, every=1.0)  # a stat of each ticket file at most once a second
        return ix.search(text, limit=limit)

    @app.get("/search.json")
    def search_json(q: str = "", limit: int = srch.PER_GROUP):
        """The palette's data: groups by project, each with its count and top hits. Status labels follow the words setting."""
        res = search_for(store(), q[:300], min(max(limit, 1), 100))
        out = res.as_json()
        for g in out["groups"]:
            for h in g["hits"]:
                h["status_label"] = screen(h["status"])
        return JSONResponse(out)

    @app.get("/search", response_class=HTMLResponse)
    def search_page(request: Request, q: str = ""):
        s = store()
        res = search_for(s, q[:300], 50) if q.strip() else None
        return templates.TemplateResponse(request, "search.html", ctx(request, s, q=q, res=res, snippet_html=lambda x: Markup(srch.snippet_html(x))))

    @app.get("/about", response_class=HTMLResponse)
    def about(request: Request):
        s = store()
        text = why_text(s.home)
        return templates.TemplateResponse(request, "about.html", ctx(request, s, body=md_html(text)))

    @app.get("/settings", response_class=HTMLResponse)
    def settings(request: Request, saved: int = 0):
        return templates.TemplateResponse(request, "settings.html", ctx(request, store(), saved=bool(saved), styles=styles.STYLES, dogs=styles.DOGS))

    @app.post("/settings")
    def post_settings(request: Request, name: str = Form("plain"), dog: str = Form("drew"), profanity: str = Form("on"), autorefresh: str = Form("on")):
        js = "application/json" in request.headers.get("accept", "")
        try:
            styles.save(store().home, name, dog, profanity != "off", autorefresh != "off")
        except ValueError:
            if js:
                return JSONResponse({"error": "That style is not one Desk knows. Reload the page and pick again."}, status_code=400)
            return HTMLResponse("<p>Unknown style</p>", status_code=400)
        if js:
            return JSONResponse({"ok": True, "message": "Saved. Other pages show it the next time you open them."})
        return RedirectResponse("/settings?saved=1", status_code=303)

    @app.get("/board", response_class=HTMLResponse)
    def board(request: Request, project: str | None = None, track: str = "", assignee: str = ""):
        s = store()
        ts = [t for t in s.tickets() if not project or t.project == project]
        achips = assignee_chips(ts)
        if assignee:
            ts = [t for t in ts if ops.assignee_of(t).lower() == assignee.lower()]
        chips = trk.track_chips(ts)
        if track:
            ts = [t for t in ts if trk.track_of(t) and trk.track_slug(trk.track_of(t)) == track]
        cols = [(st, by_project([t for t in ts if t.status == st])) for st in STATUSES]
        return templates.TemplateResponse(request, "board.html", ctx(request, s, cols=cols, project=project, projects=sorted(s.projects()), s=s,
                                                           chips=chips, track=track, chip_base="/board", chip_extra="&".join(x for x in (f"project={project}" if project else "", f"assignee={quote(assignee)}" if assignee else "") if x),
                                                           achips=achips, assignee=assignee, abase="/board", aextra=f"project={project}" if project else "", events_url="/events", events_since=ev.tickets_stamp(s)))

    @app.get("/projects", response_class=HTMLResponse)
    def projects(request: Request, p: str = "", t: str = "", k: str = "", assignee: str = ""):
        """Project > track > ticket > peek. What is open lives in the URL: ?p=<project>&t=<track slug>&k=<ticket id>."""
        s = store()
        every = s.tickets()
        achips = assignee_chips(every)
        rows = trk.hierarchy(s, [x for x in every if ops.assignee_of(x).lower() == assignee.lower()] if assignee else every)
        peek_html = ""
        if k:
            try:
                peek_html = peek_panel(s, s.get(k))
            except KeyError:
                pass
        return templates.TemplateResponse(request, "projects.html", ctx(request, s, rows=rows, sel_p=p, sel_t=t, sel_k=k.upper(), peek_html=peek_html, s=s, achips=achips, assignee=assignee, abase="/projects", aextra="", events_url="/events", events_since=ev.tickets_stamp(s)))

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
        text = "".join(out)
        cf = censor_for(s)
        return cf(text) if cf else text

    @app.get("/peek/{ticket_id}", response_class=HTMLResponse)
    def peek(ticket_id: str):
        try:
            return HTMLResponse(peek_panel(store(), store().get(ticket_id)))
        except KeyError:
            return HTMLResponse("<p>No such ticket</p>", status_code=404)

    def card_ticket(s: Store, t) -> str:
        """The hover card of a ticket id: id, status, project, title, a one-line summary and how much of Acceptance is true."""
        e = html.escape
        body = (t.section("Context") or t.section("Outcome")).strip()
        summary = re.sub(r"\s+", " ", re.sub(r"[`*_>#\[\]]", "", body.split("\n\n")[0]))[:200] if body else ""
        acc = t.acceptance()
        facts = [f"{t.meta.get('type')}", f"{t.meta.get('priority')}", f"owner {t.meta.get('owner')}"] + ([f"{sum(1 for ok, _ in acc if ok)} of {len(acc)} accepted"] if acc else [])
        text = (f'<div class="hc-top"><b class="hc-id">{e(t.id)}</b> <span class="chip {"ball" if t.status == "review" else "red" if t.status == "blocked" else ""}">{e(screen(t.status))} · {e(t.status)}</span> '
                f'<span class="chip">{e(t.project)}</span></div><p class="hc-title">{e(str(t.meta.get("title") or ""))}</p>'
                + (f'<p class="hc-body">{e(summary)}</p>' if summary else "") + f'<p class="hc-facts">{e(" · ".join(facts))}</p>')
        cf = censor_for(s)
        return cf(text) if cf else text

    def card_doc(s: Store, target: Path) -> str:
        """The hover card of a store document: its title, path, first paragraph, and the two ways in (read here, edit in VS Code)."""
        e = html.escape
        rel = target.relative_to(s.home.resolve()).as_posix()
        _, md = split_profile(target.read_text(encoding="utf-8"))
        title = next((ln[2:].strip() for ln in md.splitlines() if ln.startswith("# ")), target.stem)
        para = next((p for p in re.split(r"\n\s*\n", md) if p.strip() and not p.lstrip().startswith(("#", "|", "```", "<!--", "---"))), "")
        para = re.sub(r"\s+", " ", re.sub(r"[`*_>#]|\[([^\]]*)\]\([^)]*\)", lambda m: m.group(1) or "", para)).strip()[:260]
        text = (f'<div class="hc-top"><b>{e(title)}</b></div><p class="hc-facts"><code>{e(rel)}</code></p>' + (f'<p class="hc-body">{e(para)}</p>' if para else "")
                + f'<p class="hc-links"><a href="/doc/{e(quote(rel, safe="/"))}">Read here</a> <a class="vsc" href="{e(docsmod.vscode_url(target))}">Edit in VS Code</a></p>')
        cf = censor_for(s)
        return cf(text) if cf else text

    @app.get("/card/ticket/{ticket_id}", response_class=HTMLResponse)
    def card_for_ticket(ticket_id: str):
        s = store()
        try:
            return HTMLResponse(card_ticket(s, s.get(ticket_id)))
        except KeyError:
            return HTMLResponse("<p>No such ticket</p>", status_code=404)

    @app.get("/card/doc/{path:path}", response_class=HTMLResponse)
    def card_for_doc(path: str):
        s = store()
        target = docsmod.resolve_doc(s.home, path)
        if target is None:
            return HTMLResponse("<p>No such document</p>", status_code=404)
        return HTMLResponse(card_doc(s, target))

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
                                                             formerly=s.formerly(t), mermaid=mermaid, mentions=docsmod.mentioned_in(list(by_id.values()), ticket_id=t.id),
                                                             owning=docsmod.owning_docs(s, t), source_doc=source_doc(s, t), acc_lines=[(i + 1, c.text) for i, c in enumerate(parse_checks(t.section("Acceptance")))], dod=dod_view(s, t), reply_ask=reply_ask(s, t), doc_links=doc_links(s, t), **nav_view(s, t), events_url=f"/events/{t.id}", events_since=ev.ticket_stamp(s, t.id)))

    def nav_view(s: Store, t) -> dict:
        """Previous and Next for this ticket in the walk this person chose (scope and order, kept in .desk/settings.toml)."""
        scope, order = styles.load_nav(s.home)
        every = s.tickets()
        return {"walk": navmod.neighbours(every, t.id, scope, order), "nav_scopes": navmod.SCOPES, "nav_orders": navmod.ORDERS,
                "nav_has_sprints": any(navmod.sprint_of(x) for x in every)}

    @app.post("/nav")
    def post_nav(request: Request, scope: str = Form(""), order: str = Form(""), back: str = Form(""), token: str = Form("")):
        """Remember which walk Previous and Next follow. JSON for the page; a plain post redirects back to the ticket."""
        js = wants_json(request)
        if not tokens.use(token):
            return refused("That form was already sent; reload the page.", 409) if js else _spent(back or "")
        try:
            styles.save_nav(store().home, scope, order)
        except ValueError:
            return refused("That is not a way Desk can line the tickets up. Reload the page and pick again.") if js else HTMLResponse("<p>Unknown order</p>", status_code=400)
        if js:
            return JSONResponse({"ok": True, "message": f"Walking {navmod.SCOPES[scope].lower()}, {navmod.ORDERS[order].lower()}."})
        return RedirectResponse(f"/ticket/{back}" if re.fullmatch(r"[A-Za-z0-9-]+", back or "") else "/", status_code=303)

    def reply_ask(s: Store, t) -> dict | None:
        """The question to draw a reply box for: a decision ticket the store's owner holds, waiting for the answer (ready, or in review)."""
        if str(t.meta.get("owner") or "").lower() != s.owner.lower() or t.status not in ("ready", "review"):
            return None
        return asks.ask_of(t)

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

    def dod_view(s: Store, t) -> dict:
        """"Done means": the Definition of Done text for this ticket, read from its source file on every request."""
        d = docsmod.dod_sources(s, t)
        for it in d["items"]:
            target = docsmod.resolve_doc(s.home, it["path"]) if it["path"] else None
            if target is not None:
                _, body = split_profile(target.read_text(encoding="utf-8"))
                it["body"] = body
                it["edit_url"] = docsmod.vscode_url(target)
        return d

    def doc_links(s: Store, t) -> dict:
        """{text as written in docs_to_update: {path, edit_url}} for each entry that is a Markdown file that exists."""
        out = {}
        for d in t.meta.get("docs_to_update") or []:
            d = str(d)
            res = docsmod.resolve_for(s, t.project, d)[0] if d.split("#", 1)[0].lower().endswith(".md") else None
            target = docsmod.resolve_doc(s.home, res.split("#", 1)[0]) if res else None
            if target:
                out[d] = {"path": res, "edit_url": docsmod.vscode_url(target)}
        return out

    def source_doc(s: Store, t) -> list[dict]:
        """Each path of `source:` (several may be separated by `;` or `,`), resolved against the store root then the repo."""
        out = []
        for part in docsmod.split_sources(t.meta.get("source")):
            res = docsmod.resolve_for(s, t.project, part)[0] if part.split("#", 1)[0].lower().endswith(".md") else None
            target = docsmod.resolve_doc(s.home, res.split("#", 1)[0]) if res else None
            out.append({"text": part, "path": res, "edit_url": docsmod.vscode_url(target)} if target else {"text": part, "path": None})
        return out

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
                out += (f'<form class="rv-form post-once" method="post" action="/ticket/{e(tid)}/decide" data-inplace="refresh" data-progress="Sending,Saving,Done">'
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

    def _back(t_id: str, error: str | None = None, next_: str = "", ok: str = "", dup: bool = False, anchor: str = "") -> RedirectResponse:
        """The no-JavaScript fallback of every form: redirect back to the ticket at the part of the page the form sits in (#comments,
        #acceptance, ...), so the browser lands where the person was and not at the top (DESK-0141)."""
        frag = f"#{anchor}" if anchor else ""
        if next_ and re.fullmatch(r"/ticket/[A-Za-z0-9-]+", next_):
            if ok and not error:  # review-week actions come back to the review ticket with the same confirmation line
                return RedirectResponse(f"{next_}?done={ok}&at={dt.datetime.now().strftime('%H:%M')}{frag}", status_code=303)
            return RedirectResponse(next_ + frag, status_code=303)
        url = f"/ticket/{t_id}"
        if error:
            url += f"?error={quote(error)}"
        elif dup:
            url += "?dup=1"
        elif ok:
            url += f"?done={ok}&at={dt.datetime.now().strftime('%H:%M')}"
        return RedirectResponse(url + frag, status_code=303)

    def wants_json(request: Request) -> bool:
        return "application/json" in request.headers.get("accept", "")

    def refused(e: BaseException | str, code: int = 422) -> JSONResponse:
        """A refusal for the in-place path: the sentence for the person and a fresh one-time key, since the form's own was spent."""
        msg = e.args[0] if isinstance(e, KeyError) and e.args else str(e)
        return JSONResponse({"error": msg, "token": tokens.issue()}, status_code=404 if isinstance(e, KeyError) else code)

    def _spent(ticket_id: str) -> HTMLResponse:
        e = html.escape(ticket_id)
        return HTMLResponse(
            "<!doctype html><meta charset=utf-8><title>Already sent</title><body style=\"font:16px system-ui;max-width:40em;margin:3em auto;padding:0 1em\">"
            "<h1>That form was already sent</h1><p>This button works once, so a double click or a refresh can not post twice. "
            f"Nothing more was written.</p><p><a href=\"/ticket/{e}\">Back to {e}</a></p></body>", status_code=409)

    @app.post("/ticket/{ticket_id}/decide")
    def post_decide(request: Request, ticket_id: str, action: str = Form(""), words: str = Form(""), next: str = Form(""), token: str = Form(""),
                    option: str = Form(""), who: str = Form(""), reply: str = Form("")):
        """Approve or send back. A form post redirects; with `Accept: application/json` the reply is `{ok, message}` (or `{error, token}`).
        The reply box of a decision ticket (DESK-0138) sends `reply=1`, `who` (the person answering) and, for a choose, `option` (the
        index of the option picked); only the store's owner may answer, and only a ticket that owner holds."""
        js = wants_json(request)
        if not tokens.use(token):
            return refused("That form was already sent; reload the page.", 409) if js else _spent(ticket_id)
        s = store()
        if who.strip() and who.strip().lower() != s.owner.lower():
            msg = f"Only {s.owner} answers here. Nothing was recorded."
            return refused(msg, 403) if js else HTMLResponse(f"<p>{html.escape(msg)}</p>", status_code=403)
        try:
            t = s.get(ticket_id)
            if reply:
                ask = asks.ask_of(t)
                if str(t.meta.get("owner") or "").lower() != s.owner.lower():
                    msg = f"{t.id} is not owned by {s.owner}, so it is not yours to answer here. Nothing was recorded."
                    return refused(msg, 403) if js else HTMLResponse(f"<p>{html.escape(msg)}</p>", status_code=403)
                if ask is None:
                    raise ValueError(f"{t.id} is not a question. Use Approve or Send back, or Move.")
                words = asks.words_for(ask, option, words)
            action = action or ("approve" if reply else "")
            if action not in ("approve", "changes"):
                raise ValueError("Pick Approve or Send back.")
            s.sync_before()
            ops.decide(s, t, approve=(action == "approve"), reason=words, actor=s.owner)
            s.sync_after(f"desk: decide {t.id}")
        except (KeyError, RuleError, ValueError) as e:
            return refused(e) if js else _back(ticket_id, str(e), next, anchor="decision")
        if js:
            return JSONResponse({"ok": True, "message": "Decision recorded in your words.", "stamp": str(ev.ticket_stamp(s, t.id))})
        return _back(ticket_id, None, next, ok="decide", anchor="decision")

    @app.post("/ticket/{ticket_id}/check")
    def post_check(request: Request, ticket_id: str, section: str = Form(...), index: int = Form(...), checked: str = Form(""), state: str = Form(""), reason: str = Form(""), severity: str = Form(""), evidence: list[str] = Form([]), token: str = Form("")):
        """Set one box. A form post redirects back with a confirmation or an error line; with `Accept: application/json` the
        reply is JSON: 200 with the new state, 422 with the refusal (a reason under 3 characters, an unknown severity)."""
        wants_json = "application/json" in request.headers.get("accept", "")
        if not tokens.use(token):
            return JSONResponse({"error": "That form was already sent; reload the page."}, status_code=409) if wants_json else _spent(ticket_id)
        s = store()
        try:
            s.sync_before()
            want = state if state in (" ", "x", "!") else (None if checked == "" and state == "" else (checked not in ("0", "")))
            real = s.get(ticket_id).id
            names = [n for n in evidence if n]
            for n in names:
                if not _ASSET_NAME.match(n) or ".." in n or not (s.ticket_assets_dir(real) / n).is_file():
                    raise ValueError(f"Screenshot {n} is not on this ticket. Paste it again.")
            t = ops.toggle_check(s, ticket_id, section, index, want, actor=s.owner, reason=reason, severity=severity,
                                 evidence=[f"assets/{real}/{n}" for n in names])
            s.sync_after(f"desk: check {t.id}")
        except (KeyError, RuleError, ValueError) as e:
            if wants_json:
                return JSONResponse({"error": str(e).strip("'\"")}, status_code=404 if isinstance(e, KeyError) else 422)
            return _back(ticket_id, str(e), anchor=_SECTION_ANCHOR.get(section, ""))
        if wants_json:
            text =t.validate_steps() if section == "How to validate" else t.section(section)
            fresh = ctx(request, s, t=t)["render"](text, t, section)  # the section again, with new states and new one-time tokens
            return JSONResponse({"ok": True, "state": check_states(t.sections.get(section) or "")[index][1], "html": fresh,
                                 "summary_html": summary_html(t.section("Acceptance")), "stamp": str(ev.ticket_stamp(s, t.id))})
        return _back(ticket_id, ok="check", anchor=_SECTION_ANCHOR.get(section, ""))

    posted: OrderedDict[str, tuple[str, int, "int | None"]] = OrderedDict()  # a post's key -> (ticket id, comment index, Acceptance line it put evidence on)
    post_lock = threading.Lock()

    @app.post("/ticket/{ticket_id}/comment")
    def post_comment(request: Request, ticket_id: str, text: str = Form(""), who: str = Form(""), token: str = Form(""),
                     evidence: list[str] = Form([]), line: str = Form(""), kind: str = Form("")):
        """Add a comment. A form post works once per token and redirects. With `Accept: application/json` the token is the post's
        KEY: the server stores at most one comment per key, a key that already made a comment returns that comment (`repeat`), and
        a failed post leaves the key good so Try again works. The reply carries the Comments again and a fresh key.
        `evidence` names screenshots already uploaded to this ticket; they go into the comment as images, and with `line` (1-based, an
        Acceptance line) as `> Evidence:` lines under that line too."""
        if "application/json" not in request.headers.get("accept", ""):
            if not tokens.use(token):
                return _spent(ticket_id)
            s = store()
            try:
                t = s.get(ticket_id)
                s.sync_before()
                res = ops.comment(s, t, text, actor=who or s.owner, kind=kind)
                if res.added:
                    s.sync_after(f"desk: comment {t.id}")
            except (KeyError, RuleError, ValueError) as e:
                return _back(ticket_id, str(e), anchor="comments")
            return _back(ticket_id, ok="comment", dup=not res.added, anchor="comments")

        def refuse(msg: str, code: int):
            body = {"error": msg}
            if code == 409:
                body["token"] = tokens.issue()  # that key is no good; here is one that is, so Try again can work
            return JSONResponse(body, status_code=code)

        with post_lock:  # one post at a time, so three requests with one key can not all pass the check
            s = store()
            try:
                t = s.get(ticket_id)
            except KeyError:
                return refuse(f"No ticket {ticket_id}", 404)
            prior = posted.get(token)
            if prior is not None and prior[0] != t.id:
                return refuse("That was posted on another ticket. Reload this page and try again.", 409)
            repeat, duplicate = prior is not None, False
            try:
                if prior is None:
                    if not tokens.valid(token):
                        return refuse("This page is out of date, so nothing was posted. Reload it, then post again.", 409)
                    names = [n for n in evidence if n]
                    for n in names:
                        if not _ASSET_NAME.match(n) or ".." in n or not (s.ticket_assets_dir(t.id) / n).is_file():
                            return refuse(f"Screenshot {n} is not on this ticket. Paste it again.", 422)
                    pick = None
                    if line.strip():
                        count = len(parse_checks(t.section("Acceptance")))
                        if not line.strip().isdigit() or not 1 <= int(line) <= count:
                            return refuse(f"This ticket has no Acceptance line {line.strip()[:12]}.", 422)
                        pick = int(line) - 1
                    rels = [f"assets/{t.id}/{n}" for n in names]
                    body = text.strip() + "".join(f" ![{Path(n).stem}]({r})" for n, r in zip(names, rels)) + (f" (Evidence for line {pick + 1})" if pick is not None else "")
                    s.sync_before()
                    who_ = who or s.owner
                    res = ops.comment(s, t, body, actor=who_, kind=kind)
                    duplicate = not res.added
                    if pick is not None:
                        ops.add_evidence(s, t.id, "Acceptance", pick, rels)
                    if res.added or pick is not None:
                        s.sync_after(f"desk: comment {t.id}")
                    idx = ops.locate_comment(res.ticket, who_, body)
                    tokens.use(token)
                    posted[token] = (t.id, -1 if idx is None else idx, pick)
                    while len(posted) > MAX_TOKENS:
                        posted.popitem(last=False)
                t = s.get(ticket_id)
                _, idx, pick = posted[token]
                lines = ops.comment_lines(t)
                render = ctx(request, s, t=t)["render"]
                out = {"ok": True, "repeat": repeat, "duplicate": duplicate, "index": idx, "line": lines[idx] if 0 <= idx < len(lines) else "",
                       "html": ctx(request, s, t=t)["comments_html"](t.section("Comments")), "stamp": str(ev.ticket_stamp(s, t.id)), "token": tokens.issue()}
                if pick is not None:  # the Acceptance line changed too: send it again with its summary
                    out["check_html"] = render(t.section("Acceptance"), t, "Acceptance")
                    out["summary_html"] = summary_html(t.section("Acceptance"))
                return JSONResponse(out)
            except (RuleError, ValueError) as e:
                return refuse(str(e), 422)

    def sniff_image(head: bytes) -> str | None:
        """The extension for a real image by its first bytes (png, jpg, gif, webp); anything else is not accepted."""
        if head.startswith(bytes.fromhex("89504e470d0a1a0a")):
            return ".png"
        if head.startswith(bytes.fromhex("ffd8ff")):
            return ".jpg"
        if head[:6] in (b"GIF87a", b"GIF89a"):
            return ".gif"
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            return ".webp"
        return None

    @app.post("/ticket/{ticket_id}/attach")
    async def post_attach(request: Request, ticket_id: str, file: UploadFile = File(...), token: str = Form("")):
        """Paste or drop a screenshot: images only, size-limited (desk.toml [attachments] max_mb). The file lands in
        tickets/assets/<ID>/ through ops.attach. With `Accept: application/json` the reply carries the Markdown to insert into the
        comment; a plain form post redirects back with a confirmation line."""
        wants_json = "application/json" in request.headers.get("accept", "")

        def fail(msg: str, code: int = 400):
            if wants_json:
                return JSONResponse({"error": msg, "token": tokens.issue()}, status_code=code)
            return _back(ticket_id, msg, anchor="attachments")

        if not tokens.use(token):
            return JSONResponse({"error": "That form was already sent; reload the page.", "token": tokens.issue()}, status_code=409) if wants_json else _spent(ticket_id)
        s = store()
        try:
            t = s.get(ticket_id)
        except KeyError:
            return fail(f"No ticket {ticket_id}", 404)
        limit_mb = s.max_attach_mb
        limit = int(limit_mb * 1024 * 1024)
        data = bytearray()
        while chunk := await file.read(1024 * 1024):
            data += chunk
            if len(data) > limit:
                mb = f"{limit_mb:g}"
                return fail(f"That file is larger than the {mb} MB limit (desk.toml [attachments] max_mb = {mb}). Allowed: {IMAGE_TYPES}, up to {mb} MB.", 413)
        ext = sniff_image(bytes(data[:16]))
        if not ext:
            return fail(f"Only {IMAGE_TYPES} images are accepted, up to {limit_mb:g} MB.", 415)
        stem = re.sub(r"[^A-Za-z0-9_-]+", "-", Path(file.filename or "").stem).strip("-")
        if stem.lower() in ("", "image", "blob", "unnamed", "screenshot"):  # what a clipboard paste is called
            stem = "screenshot"
        with tempfile.TemporaryDirectory(prefix="desk-attach-") as tmp:
            src = Path(tmp) / f"{dt.datetime.now().strftime('%Y-%m-%d-%H%M')}-{stem[:60]}{ext}"  # e.g. 2026-11-08-0341-validate-run.png
            src.write_bytes(bytes(data))
            try:
                s.sync_before()
                dest = ops.attach(s, s.get(ticket_id), src, caption=stem[:60], actor=s.owner)
                s.sync_after(f"desk: attach {t.id}")
            except (KeyError, RuleError, ValueError, OSError) as e:
                return fail(str(e))
        if wants_json:
            t = s.get(ticket_id)
            return JSONResponse({"markdown": f"![{dest.stem}](assets/{t.id}/{dest.name})", "name": dest.name, "rel": f"assets/{t.id}/{dest.name}",
                                 "url": f"/assets/{t.id}/{dest.name}", "token": tokens.issue(), "message": "Image attached.",
                                 "attachments_html": ctx(request, s, t=t)["render"](t.section("Attachments")), "stamp": str(ev.ticket_stamp(s, t.id))})
        return _back(ticket_id, ok="attach", anchor="attachments")

    @app.post("/ticket/{ticket_id}/attach/remove")
    def post_attach_remove(ticket_id: str, name: str = Form(""), token: str = Form("")):
        """The remove button on a screenshot thumbnail: deletes the file and its Attachments line unless the ticket already uses it."""
        if not tokens.use(token):
            return JSONResponse({"error": "That form was already sent; reload the page.", "token": tokens.issue()}, status_code=409)
        if not name or not _ASSET_NAME.match(name) or ".." in name:
            return JSONResponse({"error": "No such screenshot.", "token": tokens.issue()}, status_code=400)
        s = store()
        try:
            t = s.get(ticket_id)
            s.sync_before()
            ops.unattach(s, t, name)
            s.sync_after(f"desk: remove screenshot {t.id}")
        except KeyError:
            return JSONResponse({"error": "No such screenshot.", "token": tokens.issue()}, status_code=404)
        except (RuleError, ValueError) as e:
            return JSONResponse({"error": str(e), "token": tokens.issue()}, status_code=409)
        return JSONResponse({"ok": True, "token": tokens.issue()})

    @app.post("/ticket/{ticket_id}/move")
    def post_move(request: Request, ticket_id: str, to: str = Form(...), reason: str = Form(""), token: str = Form("")):
        js = wants_json(request)
        if not tokens.use(token):
            return refused("That form was already sent; reload the page.", 409) if js else _spent(ticket_id)
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
            return refused(e) if js else _back(ticket_id, str(e), anchor="decision")
        if js:
            return JSONResponse({"ok": True, "message": f"Moved to {s.get(ticket_id).status}.", "stamp": str(ev.ticket_stamp(s, t.id))})
        return _back(ticket_id, ok="move", anchor="decision")

    return app


def main() -> None:
    import uvicorn

    home = find_home(os.environ.get("DESK_HOME"))
    port = int(os.environ.get("DESK_UI_PORT", "8765"))
    print(f"Desk UI on http://127.0.0.1:{port}  (store: {home})")
    Store.background_flush = True  # commit on a timer after the idle time, and now if something was left pending
    Store(home).flush()
    Store(home).schedule_flush()
    uvicorn.run(create_app(home), host="127.0.0.1", port=port, log_level="warning")
