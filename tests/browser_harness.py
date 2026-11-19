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


def inline_assets(html: str, static_dir: Path) -> str:
    """A Desk page made self-contained for `run_in_browser`: desk.css and timeline.js from `static_dir` inlined, web fonts and other
    script tags dropped (the page is served alone, with no /static route)."""
    import re

    css = (static_dir / "desk.css").read_text(encoding="utf-8")
    js = (static_dir / "timeline.js").read_text(encoding="utf-8")
    html = re.sub(r'<link rel="stylesheet" href="/static/desk.css[^"]*">', lambda m: f"<style>{css}</style>", html)
    html = re.sub(r'<link rel="stylesheet" href="https://fonts[^>]*>|<link rel="preconnect"[^>]*>', "", html)
    return re.sub(r'<script src="[^"]*"[^>]*></script>', "", html).replace("</body>", f"<script>{js}</script></body>", 1)


class _CDP:
    """A minimal Chrome DevTools Protocol client over a stdlib WebSocket (no websocket package is installed)."""

    def __init__(self, ws_url: str):
        import base64
        import os
        import socket
        from urllib.parse import urlparse

        u = urlparse(ws_url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=300)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(1)
        assert b" 101 " in buf.split(b"\r\n", 1)[0], buf
        self.n = 0

    def _read(self, k: int) -> bytes:
        out = b""
        while len(out) < k:
            chunk = self.sock.recv(k - len(out))
            if not chunk:
                raise ConnectionError("the browser closed the DevTools connection")
            out += chunk
        return out

    def _recv(self) -> dict:
        import struct

        data = b""
        while True:
            b0, b1 = self._read(2)
            ln = b1 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._read(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._read(8))[0]
            data += self._read(ln)
            if b0 & 0x80:
                return json.loads(data.decode("utf-8"))

    def call(self, method: str, **params) -> dict:
        import os
        import struct

        self.n += 1
        raw = json.dumps({"id": self.n, "method": method, "params": params}).encode()
        mask = os.urandom(4)
        n = len(raw)
        head = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536 else bytes([0x80 | 127]) + struct.pack(">Q", n))
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(raw)))
        while True:
            msg = self._recv()
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def close(self) -> None:
        self.sock.close()


def cdp_measure(base: str, paths: list[str], widths: list[int], expression: str, settle: float = 0.8) -> dict:
    """Open each of `paths` on the server at `base` in a headless browser whose window is set to exactly `width` px by Chrome DevTools
    Protocol device metrics (a headless --window-size below 500 px is not a phone), run `expression` in the page and return
    {"path@width": its JSON-able value}. The browser (by the PID this call started) and its throw-away profile are gone on return."""
    import socket
    import urllib.request

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    tmp = Path(tempfile.mkdtemp(prefix="desk-cdp-"))
    proc = None
    try:
        proc = subprocess.Popen([browser(), "--headless=new", "--disable-gpu", "--no-first-run", "--disable-extensions", f"--user-data-dir={tmp / 'profile'}",
                                 f"--remote-debugging-port={port}", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ws = None
        for _ in range(80):
            try:
                tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2).read())
                ws = next((t["webSocketDebuggerUrl"] for t in tabs if t.get("type") == "page"), None)
                if ws:
                    break
            except OSError:
                pass
            time.sleep(0.25)
        assert ws, "the browser did not open its DevTools port"
        cdp = _CDP(ws)
        out = {}
        try:
            cdp.call("Page.enable")
            for w in widths:
                cdp.call("Emulation.setDeviceMetricsOverride", width=w, height=900, deviceScaleFactor=1, mobile=False)
                for p in paths:
                    cdp.call("Page.navigate", url=base + p)
                    time.sleep(settle)
                    for _ in range(40):  # wait for the page to finish loading
                        if cdp.call("Runtime.evaluate", expression="document.readyState", returnByValue=True)["result"].get("value") == "complete":
                            break
                        time.sleep(0.25)
                    time.sleep(settle)
                    res = cdp.call("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True)
                    out[f"{p}@{w}"] = res["result"].get("value")
        finally:
            cdp.close()
        return out
    finally:
        if proc is not None:
            stop_browser(proc, tmp)
        for _ in range(20):
            shutil.rmtree(tmp, ignore_errors=True)
            if not tmp.exists():
                break
            time.sleep(0.25)
