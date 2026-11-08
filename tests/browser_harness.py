"""Headless Edge/Chrome harness for tests that need a real layout or real key events (Playwright is not installed).

`run_in_browser(html, width)` serves `html` on an ephemeral localhost port, opens it in a headless browser with a throw-away
profile, and returns the JSON the page POSTs to /m (the page's own script does the measuring). The browser is stopped by the PID this
call started, plus any leftover process whose command line carries this call's own temporary profile folder, and the profile is deleted."""

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


def run_in_browser(html: str, width: int, timeout: float = 60) -> dict:
    """Serve `html` on an ephemeral localhost port, open it in a headless browser with a throw-away profile, wait for the page's
    measuring script to POST its numbers back, then stop the browser by the PID started here and delete the profile."""
    import http.server
    import threading

    got, ev = {}, threading.Event()

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            # the page sits in an iframe of exactly `width` px, because a browser window will not shrink below about 500 px
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
    tmp = Path(tempfile.mkdtemp(prefix="desk-col-"))
    proc = None
    try:
        proc = subprocess.Popen([browser(), "--headless=new", "--disable-gpu", "--no-first-run", "--disable-extensions", f"--user-data-dir={tmp / 'profile'}",
                                 "--window-size=1400,1500", f"http://127.0.0.1:{srv.server_address[1]}/frame.html"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert ev.wait(timeout), "the browser did not run the measuring script"
    finally:
        if proc is not None:
            stop_browser(proc, tmp)
        srv.shutdown()
        srv.server_close()
        for _ in range(20):  # the browser releases its profile a moment after it is killed
            shutil.rmtree(tmp, ignore_errors=True)
            if not tmp.exists():
                break
            time.sleep(0.25)
    return got


