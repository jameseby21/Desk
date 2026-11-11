"""DESK-0117 (Chaz R1): Done and Not accepted sit in one fixed right-hand column with headers, at any width.

Playwright is not installed here, so the alignment is proven two ways: server-rendered structure plus the stylesheet rules
(always run), and a real layout measurement in headless Edge or Chrome when one is installed (skipped otherwise)."""

import re

import pytest
from fastapi.testclient import TestClient

from desk import ops
from browser_harness import browser, run_in_browser
from desk.ui.app import STATIC, create_app

LONG = "The README documents every command and says what each one prints, " * 3  # about 200 characters
LINES = ["- [ ] Short line", f"- [x] {LONG.strip()}", "- [!] Third line — not accepted: Wrong colour", "- [ ] Fourth line"]


def _ticket(store):
    t = ops.new(store, "Column", "jre")
    t.set_section("Acceptance", "\n".join(LINES))
    store.save(t)
    return t


def _acc(page: str) -> str:
    return page.split("Acceptance (what")[1].split('id="done-means"')[0]


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


MEASURE = """<script>addEventListener('load',function(){var o=[];
function r(e){var x=e.getBoundingClientRect();return [Math.round(x.left*10)/10,Math.round(x.width),Math.round(x.height)];}
document.querySelectorAll('.chk2').forEach(function(c){var b=c.querySelectorAll('.hit');o.push([r(b[0]),r(b[1])]);});
var hs=[];document.querySelectorAll('.chk-head span').forEach(function(s){hs.push(r(s)[0]);});
fetch('/m',{method:'POST',body:JSON.stringify({boxes:o,heads:hs,scroll:document.documentElement.scrollWidth,inner:innerWidth})});});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
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
    data = run_in_browser(html, width)
    boxes = data["boxes"]
    assert len(boxes) == len(LINES)
    for k in (0, 1):
        xs = [b[k][0] for b in boxes]
        assert max(xs) - min(xs) <= 1, f"box {k} moves between lines: {xs}"
        assert all(b[k][1] >= 44 and b[k][2] >= 44 for b in boxes), "click area under 44 px"
    assert abs(data["heads"][1] - boxes[0][0][0]) <= 8 and abs(data["heads"][2] - boxes[0][1][0]) <= 8  # header cells sit over the column
    assert data["scroll"] <= data["inner"] + 1, "the page scrolls sideways"
