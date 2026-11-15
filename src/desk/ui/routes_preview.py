"""File preview panel and read-only local file links (DESK-0291).

GET  /preview/<ID>/<file>         the panel for an attachment in tickets/assets/<ID>/ (a fragment preview.js shows in the side panel)
GET  /preview/<ID>/<file>/raw     the attachment's bytes, read-only, with safe headers
GET  /card/asset/<ID>/<file>      the hover card of an attachment: first lines, or a thumbnail
GET  /local?path=<abs path>       a file on this computer, read-only, only inside the allowed folders (localfiles.check)
GET  /local/preview?path=...      the panel for that file
GET  /card/local?path=...         its hover card
POST /local/reveal                {"path": ...} shows the file in Windows Explorer (path checked again)

Every route answers only this computer: the caller is a loopback address, the Host header is 127.0.0.1 or localhost with the port the
server listens on (a page from another site that renamed itself to 127.0.0.1 is refused), and a browser request from another site
(Origin or Sec-Fetch-Site) is refused. Nothing here writes to the store."""

from __future__ import annotations

import html
import os
import re
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from .. import filepreview as fp, localfiles as lf, officetext
from ..store import Store, id_parts
from .markdown import render as md_html

TEMPLATES = Path(__file__).parent / "templates"
_ASSET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]*$")  # the same names GET /assets accepts
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
_LOOPBACK_CLIENTS = {"127.0.0.1", "::1"}
CROSS_SITE = "Desk only answers its own pages on this computer."
BAD_HOST = "Desk only answers at 127.0.0.1 or localhost on its own port."


def _port(request: Request) -> int:
    server = request.scope.get("server")
    if server and server[1]:
        return int(server[1])
    return int(os.environ.get("DESK_UI_PORT", "8765"))


def refusal(request: Request, *, write: bool = False) -> str:
    """The plain reason this request is refused, or '' when it comes from this computer and from Desk's own pages."""
    client = request.client.host if request.client else ""
    if client not in _LOOPBACK_CLIENTS:
        return CROSS_SITE
    host = request.headers.get("host", "").lower()
    name, port = (host.rsplit(":", 1) + ["80"])[:2] if not host.endswith("]") and ":" in host else (host, "80")
    if name not in _LOOPBACK_HOSTS or not port.isdigit() or int(port) != _port(request):
        return BAD_HOST
    site = request.headers.get("sec-fetch-site", "").lower()
    if site and site not in ("same-origin", "none"):
        return CROSS_SITE
    origin = request.headers.get("origin")
    if origin is not None and origin.lower() != f"http://{host}":
        return CROSS_SITE
    if write and origin is None and site != "same-origin":
        return CROSS_SITE  # a write must say where it came from
    return ""


def _size(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


_KIND_WORDS = {"pdf": "PDF", "markdown": "Markdown", "text": "Text", "csv": "Table (CSV)", "image": "Picture", "video": "Video", "audio": "Sound",
               "office": "Office file (text only)", "other": "File", "folder": "Folder"}


def make_router(get_store: Callable[[], Store]) -> APIRouter:
    router = APIRouter()
    templates = Jinja2Templates(directory=str(TEMPLATES))

    def no(request: Request, reason: str, status: int = 403):
        if "application/json" in request.headers.get("accept", "") or request.method == "POST":
            return JSONResponse({"error": reason}, status_code=status)
        return HTMLResponse(f'<p class="pv-error">{html.escape(reason)}</p>', status_code=status, headers={"X-Content-Type-Options": "nosniff"})

    def asset_path(tid: str, filename: str) -> Path:
        s = get_store()
        if not id_parts(tid.upper()) or not _ASSET_NAME.match(filename) or ".." in filename or lf.denied_name(filename):
            raise lf.Refused("No such attachment.", 404)
        root = s.ticket_assets_dir(tid).resolve()
        target = (root / filename).resolve()
        if target.parent != root or not target.is_file():
            raise lf.Refused("No such attachment.", 404)
        return target

    def local_path(raw: str, want: str = "file") -> Path:
        s = get_store()
        return lf.check(s.home, raw, s.config, want=want)

    def raw(target: Path):
        media, headers = fp.raw_headers(target)
        return FileResponse(str(target), media_type=media, headers=headers)

    def panel(request: Request, target: Path, *, raw_url: str, where: str, reveal: str = ""):
        kind = "folder" if target.is_dir() else fp.kind_of(target)
        size = _size(target.stat().st_size) if kind != "folder" else "on this computer"
        data = {"name": target.name, "where": where, "raw_url": "" if kind == "folder" else raw_url, "reveal": reveal, "kind": kind, "cut": False,
                "error": "", "facts": f"{_KIND_WORDS[kind]} · {size} · read-only"}
        try:
            if kind == "markdown":
                text, data["cut"] = fp.read_text(target)
                data["html"] = md_html(text)
            elif kind == "text":
                data["text"], data["cut"] = fp.read_text(target)
            elif kind == "csv":
                data["rows"], data["cut"] = fp.read_csv(target)
            elif kind == "office":
                data["office"] = officetext.extract(target)
        except officetext.OfficeError as e:
            data["error"] = str(e)
        except OSError:
            data["error"] = "Desk could not read that file just now."
        return templates.TemplateResponse(request, "preview_panel.html", data, headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})

    def card(target: Path, raw_url: str) -> HTMLResponse:
        e = html.escape
        kind = fp.kind_of(target)
        head = (f'<div class="hc-top"><b>{e(target.name)}</b> <span class="chip">{e(_KIND_WORDS[kind])}</span></div>'
                f'<p class="hc-facts">{e(_size(target.stat().st_size))} · click to preview</p>')
        body = ""
        try:
            if kind == "image":
                body = f'<img class="hc-thumb" src="{e(raw_url)}" alt="">'
            elif kind in ("text", "markdown", "csv"):
                lines = fp.first_lines(target)
                body = f'<pre class="hc-lines">{e(lines)}</pre>' if lines else ""
            elif kind == "office":
                o = officetext.extract(target)
                bits = o.get("paragraphs") or [ln for s in o.get("slides", []) for ln in s["lines"]] or [" | ".join(r) for sh in o.get("sheets", []) for r in sh["rows"]]
                body = f'<pre class="hc-lines">{e(chr(10).join(x[:160] for x in bits[:6]))}</pre>' if bits else ""
        except (officetext.OfficeError, OSError):
            body = ""
        return HTMLResponse(head + body, headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})

    def guarded(request: Request, fn, *, write: bool = False):
        why = refusal(request, write=write)
        if why:
            return no(request, why)
        try:
            return fn()
        except lf.Refused as e:
            return no(request, str(e), e.status)

    @router.get("/preview/{ticket_id}/{filename}", response_class=HTMLResponse)
    def preview_asset(request: Request, ticket_id: str, filename: str):
        def go():
            t = asset_path(ticket_id, filename)
            base = f"/preview/{quote(ticket_id)}/{quote(t.name)}"
            return panel(request, t, raw_url=base + "/raw", where=f"Attached to {ticket_id.upper()}")
        return guarded(request, go)

    @router.get("/preview/{ticket_id}/{filename}/raw")
    def preview_asset_raw(request: Request, ticket_id: str, filename: str):
        return guarded(request, lambda: raw(asset_path(ticket_id, filename)))

    @router.get("/card/asset/{ticket_id}/{filename}", response_class=HTMLResponse)
    def card_asset(request: Request, ticket_id: str, filename: str):
        def go():
            t = asset_path(ticket_id, filename)
            return card(t, f"/preview/{quote(ticket_id)}/{quote(t.name)}/raw")
        return guarded(request, go)

    @router.get("/local")
    def local_raw(request: Request, path: str = ""):
        return guarded(request, lambda: raw(local_path(path)))

    @router.get("/local/preview", response_class=HTMLResponse)
    def local_preview(request: Request, path: str = ""):
        def go():
            t = local_path(path, want="any")
            return panel(request, t, raw_url="/local?path=" + quote(str(t), safe=""), where=str(t), reveal=str(t))
        return guarded(request, go)

    @router.get("/card/local", response_class=HTMLResponse)
    def card_local(request: Request, path: str = ""):
        def go():
            t = local_path(path)
            return card(t, "/local?path=" + quote(str(t), safe=""))
        return guarded(request, go)

    @router.post("/local/reveal")
    async def local_reveal(request: Request):
        why = refusal(request, write=True)
        if why:
            return JSONResponse({"error": why}, status_code=403)
        if not request.headers.get("content-type", "").lower().startswith("application/json"):
            return JSONResponse({"error": "Send the path as JSON."}, status_code=415)
        try:
            body = await request.json()
        except ValueError:
            body = None
        path = body.get("path") if isinstance(body, dict) else None
        if not isinstance(path, str):
            return JSONResponse({"error": "Give the path of the file to show."}, status_code=400)
        try:
            return JSONResponse({"ok": True, "message": lf.reveal(local_path(path, want="any"))})
        except lf.Refused as e:
            return JSONResponse({"error": str(e)}, status_code=e.status)
        except OSError:
            return JSONResponse({"error": "Explorer could not be started."}, status_code=500)

    return router
