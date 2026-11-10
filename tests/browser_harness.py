"""Headless Edge/Chrome harness for tests that need a real layout or real key events (Playwright is not installed).

`run_in_browser(html, width)` serves `html` on an ephemeral localhost port, opens it in a headless browser with a throw-away
profile, and returns the JSON the page POSTs to /m (the page's own script does the measuring). The browser is stopped by the PID this
call started, plus any leftover process whose command line carries this call's own temporary profile folder, and the profile is deleted."""

import contextlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def browser() -> str | None:
    for p in (shutil.which("msedge"), shutil.which("chrome"), r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
        if p and Path(p).is_file():
            return p
    return None


def stop_browser(proc, profile_root: Path) -> None:
    """Stop the browser this test started: the PID returned by Popen and its tree, then any leftover process whose command line
    carries this test's own throw-away profile folder (Edge can re-launch itself under another PID). Nothing else is touched."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                        f"Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -like '*{profile_root.name}*' }} | "
                        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"], capture_output=True)
    else:
        proc.kill()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        pass


def _browse(url: str, ev, timeout: float, width: int = 1400) -> None:
    """Open `url` in a headless browser with a throw-away profile; return once `ev` is set (or fail after `timeout`).
    The browser and the profile folder are gone when this returns, even when it fails."""
    tmp = Path(tempfile.mkdtemp(prefix="desk-col-"))
    proc = None
    try:
        proc = subprocess.Popen([browser(), "--headless=new", "--disable-gpu", "--no-first-run", "--disable-extensions", f"--user-data-dir={tmp / 'profile'}",
                                 f"--window-size={width},1500", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert ev.wait(timeout), "the browser did not run the test script"
    finally:
        if proc is not None:
            stop_browser(proc, tmp)
        for _ in range(20):  # the browser releases its profile a moment after it is killed
            shutil.rmtree(tmp, ignore_errors=True)
            if not tmp.exists():
                break
            time.sleep(0.25)


def run_in_browser(html: str, width: int, timeout: float = 30) -> dict:
    """Serve `html` on an ephemeral localhost port (inside an iframe of exactly `width` px, because a browser window will not shrink
    below about 500 px), open it headless, and return the JSON the page POSTs to /m."""
    import http.server
    import threading

    got, ev = {}, threading.Event()

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = (html if self.path.startswith("/page.html") else
                    f'<!doctype html><body style="margin:0"><iframe src="/page.html" style="width:{width}px;height:1400px;border:0"></iframe>').encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            got.update(json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}"))
            self.send_response(204)
            self.end_headers()
            ev.set()

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        _browse(f"http://127.0.0.1:{srv.server_address[1]}/frame.html", ev, timeout)
    finally:
        srv.shutdown()
        srv.server_close()
    return got


class Inject:
    """ASGI wrapper for a test: adds `script` before </body> on every HTML page, takes the page's POST /m (its result) into `sink`,
    and lets a test stand in for the server on chosen requests with `intercept(scope) -> coroutine | None` (a number delays the real
    answer by that many seconds; a coroutine function taking (receive, send) answers the request itself, for example with a 500)."""

    def __init__(self, app, script: str, sink: dict, ev, intercept=None):
        self.app, self.script, self.sink, self.ev, self.intercept = app, script, sink, ev, intercept

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope["path"] == "/m" and scope["method"] == "POST":
            body, more = b"", True
            while more:
                msg = await receive()
                body, more = body + msg.get("body", b""), msg.get("more_body", False)
            self.sink.update(json.loads(body or b"{}"))
            self.ev.set()
            await send({"type": "http.response.start", "status": 204, "headers": []})
            return await send({"type": "http.response.body", "body": b""})
        if self.intercept and (handler := self.intercept(scope)):
            if isinstance(handler, (int, float)):  # a delay: wait, then let the real app answer
                import asyncio

                await asyncio.sleep(handler)
            else:
                return await handler(receive, send)
        start, chunks = {}, []

        async def wrapped(msg):
            if msg["type"] == "http.response.start":
                start.update(msg)
                if any(k == b"content-type" and v.startswith(b"text/html") for k, v in msg["headers"]):
                    return None  # held back until the body is complete
                return await send(msg)
            if start and any(k == b"content-type" and v.startswith(b"text/html") for k, v in start["headers"]):
                chunks.append(msg.get("body", b""))
                if not msg.get("more_body"):
                    body = b"".join(chunks).replace(b"</body>", self.script.encode("utf-8") + b"</body>")
                    headers = [(k, v) for k, v in start["headers"] if k != b"content-length"] + [(b"content-length", str(len(body)).encode())]
                    await send({**start, "headers": headers})
                    await send({"type": "http.response.body", "body": body})
                return None
            return await send(msg)

        await self.app(scope, receive, wrapped)


@contextlib.contextmanager
def live_app(asgi_app):
    """Run an ASGI app with uvicorn on a free localhost port in a thread (never 8765); yields the base URL; stops it afterwards."""
    import socket
    import threading

    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(asgi_app, host="127.0.0.1", port=port, log_level="error"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        th.join(15)


def drive(store, script: str, path: str, *, intercept=None, timeout: float = 30, app=None, width: int = 1400) -> dict:
    """Open `path` of a real Desk app (on `store`, with `script` added to its pages) in a headless browser and return what the
    page's script POSTed to /m. The server and the browser are stopped before this returns."""
    import threading

    from desk.ui.app import create_app

    got, ev = {}, threading.Event()
    with live_app(Inject(app or create_app(store.home), script, got, ev, intercept)) as base:
        _browse(base + path, ev, timeout, width)
    return got
