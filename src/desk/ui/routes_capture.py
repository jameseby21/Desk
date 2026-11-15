"""Capture routes (DESK-0286): the palette's Create row, the /capture page the Ctrl+Alt+J shortcut opens, paste-to-split at
/capture/paste, and a file dropped on any page (a new ticket around it) or on a ticket (attached to it).

Every write runs inside one `activity.batch(...)` and comes back with `undo=<batch>` and `msg=<sentence>` on the redirect (the DESK-0284
toast reads them), or with `batch` and `message` in the JSON for the in-page path. The creator is the viewer's verified identity
(`acting(request).name`); there is no way to capture as someone else (decision 110).
Docs: docs/COMMANDS.md (In the UI: Capture)."""

from __future__ import annotations

import datetime as dt
import re
import tempfile
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .. import activity, capture as cap, ops
from ..rules import RuleError
from ..ticket import TYPES

LANE_WORD = {"now": "Now", "later": "Later", "planned": "Planned", "parked": "Parked"}
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9]*-\d+$")


def _json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "")


def ticket_url(tid: str, batch: str, msg: str, anchor: str = "", done: str = "") -> str:
    q = (f"done={done}&at={dt.datetime.now().strftime('%H:%M')}&" if done else "") + f"undo={quote(batch)}&msg={quote(msg)}"
    return f"/ticket/{tid}?{q}" + (f"#{anchor}" if anchor else "")


def created_text(store, t, named: bool) -> str:
    where = store.project_title(t.project)
    lane = LANE_WORD.get(str(t.meta.get("lane") or ""), "Later")
    tail = "" if named else " No project was named, so it went to the default; change it on the ticket if that is wrong."
    return f"Created {t.id} in {where}, {lane}: {t.meta.get('title')}.{tail}"


async def read_limited(file: UploadFile, limit_mb: float) -> bytes | None:
    """The upload's bytes, or None as soon as it passes the store's size limit (desk.toml [attachments] max_mb)."""
    limit = int(limit_mb * 1024 * 1024)
    data = bytearray()
    while chunk := await file.read(1024 * 1024):
        data += chunk
        if len(data) > limit:
            return None
    return bytes(data)


def too_big(limit_mb: float) -> str:
    return f"That file is larger than the {limit_mb:g} MB limit (desk.toml [attachments] max_mb). Nothing was saved."


def attach_bytes(store, t, filename: str, data: bytes) -> Path:
    """Write the dropped bytes under their own name and attach them through ops.attach (the same writer as screenshots)."""
    name = Path(filename or "file").name or "file"
    if cap.refused_name(name):  # the routes refuse first; this keeps any later caller from writing a program
        raise RuleError(cap.refused_name(name))
    with tempfile.TemporaryDirectory(prefix="desk-drop-") as tmp:
        src = Path(tmp) / cap.stored_name(name)
        src.write_bytes(data)
        return ops.attach(store, t, src, caption=Path(name).stem[:60] or name, actor=None)


def router(store, templates, ctx, tokens, acting) -> APIRouter:
    r = APIRouter()

    def page(request: Request, name: str, status: int = 200, **kw):
        s = store()
        return templates.TemplateResponse(request, name, ctx(request, s, **kw), status_code=status)

    def projects_of(s):
        return [(k, s.project_title(k)) for k in s.projects()]

    @r.get("/capture", response_class=HTMLResponse)
    def capture_page(request: Request, text: str = "", error: str = ""):
        """The one-field capture box: a sentence, an optional MVP slice line, an optional file. The Ctrl+Alt+J shortcut opens it."""
        s = store()
        return page(request, "capture.html", text=text, mvp="", error=error, limit_mb=s.max_attach_mb, guess=cap.guess(s, text).as_dict(s) if text.strip() else None)

    @r.get("/capture/token.json")
    def capture_token():
        """A one-time key for the in-page capture and drop (a page on another site cannot read it)."""
        return JSONResponse({"token": tokens.issue(), "max_mb": store().max_attach_mb, "refused": list(cap.REFUSED_EXTENSIONS)})

    @r.get("/capture/guess.json")
    def capture_guess(q: str = ""):
        """What a capture of `q` would make: title, project, type and Now or Later, for the palette's Create row."""
        s = store()
        g = cap.guess(s, q)
        return JSONResponse({**g.as_dict(s), "lane_word": LANE_WORD[g.lane], "capture": cap.is_capture(q)})

    @r.post("/capture")
    async def post_capture(request: Request, text: str = Form(""), mvp: str = Form(""), token: str = Form(""), how: str = Form("capture box"),
                           file: UploadFile | None = File(None)):
        """Create the ticket, and attach the file when one came with it, as one batch. JSON: {ok, id, url, batch, message}."""
        js = _json(request)
        s = store()

        def fail(msg: str, code: int):
            if js:
                return JSONResponse({"error": msg, "token": tokens.issue()}, status_code=code)
            return page(request, "capture.html", status=code, text=text, mvp=mvp, error=msg, limit_mb=s.max_attach_mb, guess=None)

        if not tokens.use(token):  # spent at once, so a double Enter or a double drop can never write twice
            return fail("That capture was already sent, so no second ticket was made. Reload the page for a fresh box.", 409)
        data, fname = None, ""
        if file is not None and (file.filename or ""):
            fname = file.filename or ""
            why = cap.refused_name(fname)
            if why:
                return fail(why, 415)
            data = await read_limited(file, s.max_attach_mb)
            if data is None:
                return fail(too_big(s.max_attach_mb), 413)
        sentence = text if text.strip() else (f"Look at {Path(fname).name}" if fname else "")
        how = how if how in ("palette", "capture box", "file drop") else "capture box"
        me = acting(request)
        b = cap.batch_id()
        try:
            s.sync_before()
            with activity.batch(b):
                t = cap.create(s, sentence, me.name, mvp=mvp, how=how)
                if data is not None:
                    attach_bytes(s, s.get(t.id), fname, data)
            s.sync_after(f"desk: capture {t.id}")
        except (RuleError, ValueError, OSError) as e:
            return fail(str(e), 422)
        _, named = cap.guess_project(s, cap.strip_new(sentence))
        t = s.get(t.id)
        msg = created_text(s, t, named) + (f" {Path(fname).name} is attached." if data is not None else "")
        url = ticket_url(t.id, b, msg, done="new")
        if js:
            return JSONResponse({"ok": True, "id": t.id, "url": url, "batch": b, "message": msg, "lane": t.meta.get("lane"), "project": t.project, "type": t.meta.get("type")})
        return RedirectResponse(url, status_code=303)

    @r.get("/capture/paste", response_class=HTMLResponse)
    def paste_page(request: Request, made: str = "", msg: str = ""):
        """Paste a chat excerpt or an email; Desk proposes one ticket per ask. After a save, `made` lists what was created."""
        s = store()
        ids = [x for x in made.split(",") if _ID.match(x)]
        done = []
        for x in ids:
            try:
                done.append(s.get(x))
            except KeyError:
                continue
        return page(request, "capture_paste.html", text="", rows=None, made=done, msg=msg, projects=projects_of(s), types=TYPES, lane_word=LANE_WORD)

    @r.post("/capture/paste", response_class=HTMLResponse)
    def paste_split(request: Request, text: str = Form("")):
        """Split the pasted text into proposed rows. Nothing is written here."""
        s = store()
        rows = cap.split_asks(s, text)
        return page(request, "capture_paste.html", text=text, rows=rows, made=[], msg="", projects=projects_of(s), types=TYPES, lane_word=LANE_WORD)

    @r.post("/capture/paste/create")
    def paste_create(request: Request, token: str = Form(""), pick: list[str] = Form([]), title: list[str] = Form([]), project: list[str] = Form([]),
                     type: list[str] = Form([]), said: list[str] = Form([])):
        """Create a ticket for every ticked row, in one batch, so one Undo takes them all back."""
        s = store()
        if not tokens.use(token):  # spent at once, so a double Enter or a double drop can never write twice
            return page(request, "capture_paste.html", status=409, text="", rows=None, made=[], msg="That list was already saved, so nothing was made twice.",
                        projects=projects_of(s), types=TYPES, lane_word=LANE_WORD)
        chosen = sorted({int(x) for x in pick if x.isdigit() and int(x) < len(title)})
        if not chosen:
            return page(request, "capture_paste.html", status=422, text="\n".join(said), rows=cap.split_asks(s, "\n".join(said)), made=[],
                        msg="Tick at least one row to create it. Nothing was saved.", projects=projects_of(s), types=TYPES, lane_word=LANE_WORD)
        def at(xs: list[str], i: int) -> str:
            return xs[i] if i < len(xs) else ""

        def text_of(i: int) -> str:
            return at(said, i) or title[i]

        def again(msg: str):  # the ticked rows as the person left them (edited titles, chosen projects), so nothing is retyped
            rows = [cap.Proposed(title=title[i], said=text_of(i), project=at(project, i), named=True, type=at(type, i) or "task",
                                 lane=cap.guess_lane(text_of(i))) for i in chosen]
            return page(request, "capture_paste.html", status=422, text="\n".join(said), rows=rows, made=[], msg=msg,
                        projects=projects_of(s), types=TYPES, lane_word=LANE_WORD)

        for n, i in enumerate(chosen, 1):  # every row is checked before any is written, so a bad row never leaves half a list
            problem = cap.row_problem(s, text_of(i), title=title[i], project=at(project, i))
            if problem:
                return again(f"Row {n}: {problem} Nothing was saved.")
        me = acting(request)
        b = cap.batch_id()
        made = []
        try:
            s.sync_before()
            with activity.batch(b):
                for i in chosen:
                    ty = at(type, i) if at(type, i) in TYPES else ""
                    made.append(cap.create(s, text_of(i), me.name, title=title[i], project=at(project, i), type_=ty, how="pasted text"))
        except (RuleError, ValueError, OSError) as e:
            if not made:
                return again(f"{e} Nothing was saved.")
            s.sync_after(f"desk: capture {len(made)} from pasted text")  # the tickets already written are kept and synced
            msg = f"{e} {len(made)} ticket(s) were made before it stopped; Undo takes them back."
            return RedirectResponse(f"/capture/paste?made={','.join(t.id for t in made)}&undo={quote(b)}&msg={quote(msg)}", status_code=303)
        s.sync_after(f"desk: capture {len(made)} from pasted text")
        msg = f"Created {len(made)} ticket{'s' if len(made) != 1 else ''}: {', '.join(t.id for t in made)}."
        return RedirectResponse(f"/capture/paste?made={','.join(t.id for t in made)}&undo={quote(b)}&msg={quote(msg)}", status_code=303)

    @r.post("/ticket/{ticket_id}/drop")
    async def post_drop(request: Request, ticket_id: str, file: UploadFile = File(...), token: str = Form("")):
        """A file of any type dropped on a ticket page is attached to it (size limit from desk.toml; programs and scripts refused)."""
        js = _json(request)
        s = store()

        def fail(msg: str, code: int):
            if js:
                return JSONResponse({"error": msg, "token": tokens.issue()}, status_code=code)
            # `error` is what the ticket page shows as a refusal; `msg` is for the success toast and would read as one
            return RedirectResponse(f"/ticket/{quote(ticket_id)}?error={quote(msg)}#attachments", status_code=303)

        if not tokens.use(token):  # spent at once, so a double Enter or a double drop can never write twice
            return fail("That drop was already sent; nothing was attached twice.", 409)
        try:
            t = s.get(ticket_id)
        except KeyError:
            return fail(f"There is no ticket {ticket_id}.", 404)
        why = cap.refused_name(file.filename or "")
        if why:
            return fail(why, 415)
        data = await read_limited(file, s.max_attach_mb)
        if data is None:
            return fail(too_big(s.max_attach_mb), 413)
        b = cap.batch_id()
        try:
            s.sync_before()
            with activity.batch(b):
                dest = attach_bytes(s, t, file.filename or "file", data)
            s.sync_after(f"desk: attach {t.id}")
        except (RuleError, ValueError, OSError) as e:
            return fail(str(e), 422)
        msg = f"Attached {dest.name} to {t.id}."
        url = ticket_url(t.id, b, msg, anchor="attachments")
        if js:
            return JSONResponse({"ok": True, "name": dest.name, "url": url, "batch": b, "message": msg})
        return RedirectResponse(url, status_code=303)

    return r
