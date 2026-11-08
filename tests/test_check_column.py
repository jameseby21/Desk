"""DESK-0117 (Chaz R1): Done and Not accepted sit in one fixed right-hand column with headers, at any width.

Playwright is not installed here, so the alignment is proven two ways: server-rendered structure plus the stylesheet rules
(always run), and a real layout measurement in headless Edge or Chrome when one is installed (skipped otherwise)."""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from desk import ops
from desk.ui.app import STATIC, create_app

LONG = "The README documents every command and says what each one prints, " * 3  # about 200 characters
LINES = ["- [ ] Short line", f"- [x] {LONG.strip()}", "- [!] Third line — not accepted: Wrong colour", "- [ ] Fourth line"]


def _ticket(store):
    t = ops.new(store, "Column", "jre")
    t.set_section("Acceptance", "\n".join(LINES))
    store.save(t)
    return t


def _acc(page: str) -> str:
    return page.split("Acceptance (this")[1].split('id="done-means"')[0]


def test_headers_and_two_hit_areas_per_line(store):
    """Protects: the headers Done and Not accepted exist once above the list, and every line has exactly two 44 px hit buttons."""
    t = _ticket(store)
    acc = _acc(TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text)
    assert acc.count('class="chk-head"') == 1
    head = re.search(r'<div class="chk-head".*?</div>', acc, re.S).group(0)
    assert ">Done<" in head and ">Not accepted<" in head
    assert acc.index('class="chk-head"') < acc.index('class="chk2"')
    assert acc.count('class="chk2"') == len(LINES)
    for cell in re.findall(r'<span class="chk2".*?</span></span>', acc, re.S):
        assert cell.count('class="hit') == 2
    # the 200 character line keeps its box in the same cell structure: the text is the li's content, not part of .chk2
    assert LONG.strip()[:60] in acc and LONG.strip()[:60] not in "".join(re.findall(r'<span class="chk2".*?</span></span>', acc, re.S))


def test_boxes_are_buttons_labelled_with_the_line_and_pressed_state(store):
    t = _ticket(store)
    acc = _acc(TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text)
    assert 'aria-label="Done: Short line"' in acc and 'aria-label="Not accepted: Short line"' in acc
    assert acc.count('aria-pressed="true"') == 2  # line 2 done, line 3 not accepted
    assert "chk-lab" not in acc and 'type="checkbox"' not in acc


def test_page_loads_the_check_script_and_serves_it(store):
    t = _ticket(store)
    c = TestClient(create_app(store.home))
    assert '<script src="/static/checks.js" defer>' in c.get(f"/ticket/{t.id}").text
    assert c.get("/static/checks.js").status_code == 200


def test_stylesheet_fixes_the_column_and_never_moves_it_on_a_phone():
    """Protects: one 52 px track per box (44 px click area inside), 28 px visible box, and no media query that re-positions .chk2."""
    css = (STATIC / "desk.css").read_text(encoding="utf-8")
    assert re.search(r"\.hit\{[^}]*width:44px;[^}]*height:44px", css)
    assert re.search(r"\.box\{[^}]*width:28px;[^}]*height:28px", css)
    assert re.search(r"\.chk-head\{[^}]*grid-template-columns:minmax\(0,1fr\) 52px 52px", css)
    assert re.search(r"\.chk2\{[^}]*position:absolute;[^}]*right:0", css)
    assert "var(--ball)" in css.split(".box.on-done{", 1)[1].split("}", 1)[0]  # Done fills lime
    assert "var(--red)" in css.split(".box.on-no{", 1)[1].split("}", 1)[0]  # Not accepted fills red
    from desk.ui.markdown import _CROSS, _TICK
    assert 'stroke="#141414"' in _TICK and 'stroke="#FFFFFF"' in _CROSS  # ink check, white cross
    for media in re.findall(r"@media[^{]*\{(?:[^{}]*\{[^}]*\})*[^{}]*\}", css):
        assert ".chk2" not in media, "the column must stay at the right edge on a phone"


# ---- real layout, when a browser is installed -------------------------------------------------------------------------------

def _browser() -> str | None:
    for p in (shutil.which("msedge"), shutil.which("chrome"), r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
        if p and Path(p).is_file():
            return p
    return None


MEASURE = """<script>addEventListener('load',function(){var o=[];
function r(e){var x=e.getBoundingClientRect();return [Math.round(x.left*10)/10,Math.round(x.width),Math.round(x.height)];}
document.querySelectorAll('.chk2').forEach(function(c){var b=c.querySelectorAll('.hit');o.push([r(b[0]),r(b[1])]);});
var hs=[];document.querySelectorAll('.chk-head span').forEach(function(s){hs.push(r(s)[0]);});
fetch('/m',{method:'POST',body:JSON.stringify({boxes:o,heads:hs,scroll:document.documentElement.scrollWidth,inner:innerWidth})});});</script>"""


def _stop_browser(proc, profile_root: Path) -> None:
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


def measure_in_browser(html: str, width: int, timeout: float = 60) -> dict:
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
        proc = subprocess.Popen([_browser(), "--headless=new", "--disable-gpu", "--no-first-run", "--disable-extensions", f"--user-data-dir={tmp / 'profile'}",
                                 "--window-size=1400,1500", f"http://127.0.0.1:{srv.server_address[1]}/frame.html"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert ev.wait(timeout), "the browser did not run the measuring script"
    finally:
        if proc is not None:
            _stop_browser(proc, tmp)
        srv.shutdown()
        srv.server_close()
        for _ in range(20):  # the browser releases its profile a moment after it is killed
            shutil.rmtree(tmp, ignore_errors=True)
            if not tmp.exists():
                break
            time.sleep(0.25)
    return got


@pytest.mark.skipif(_browser() is None, reason="no Edge or Chrome installed")
@pytest.mark.parametrize("width", [1280, 390])
def test_check_column_alignment(store, width):
    """Protects: at 1280 px and 390 px both boxes of every line share one x position (+-1 px), a 200 character line wraps without
    moving them, the hit areas are 44 px, and the headers sit over the column. Measured in headless Edge or Chrome."""
    t = _ticket(store)
    html = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    css = (STATIC / "desk.css").read_text(encoding="utf-8")
    html = re.sub(r'<link rel="stylesheet" href="/static/desk.css">', lambda m: f"<style>{css}</style>", html)
    html = re.sub(r'<link rel="stylesheet" href="https://fonts[^>]*>|<link rel="preconnect"[^>]*>', "", html)
    html = html.replace("</body>", MEASURE + "</body>")
    data = measure_in_browser(html, width)
    boxes = data["boxes"]
    assert len(boxes) == len(LINES)
    for k in (0, 1):
        xs = [b[k][0] for b in boxes]
        assert max(xs) - min(xs) <= 1, f"box {k} moves between lines: {xs}"
        assert all(b[k][1] >= 44 and b[k][2] >= 44 for b in boxes), "click area under 44 px"
    assert abs(data["heads"][1] - boxes[0][0][0]) <= 8 and abs(data["heads"][2] - boxes[0][1][0]) <= 8  # header cells sit over the column
    assert data["scroll"] <= data["inner"] + 1, "the page scrolls sideways"
