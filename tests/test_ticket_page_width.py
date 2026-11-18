"""DESK-0305 checker F2: DESK-0082 ran off the screen (scrollWidth 991 against 375 at 390 px, 1279 against 1265 at 1280 px), clipping
'Your call' and the Assignee. Cause: the ticket page's two columns were `2fr 1fr` (one `1fr` on a phone), and an `fr` track is
`minmax(auto, fr)`, so a column grows to its content's min-content width; a Not accepted reason is one line that never wraps
(`.nr-reason .why`, white-space:nowrap, so More opens the rest), and its whole text became the column's minimum width. Protects: no
content sets the width of a column, so every one-line reason ellipsizes inside its row and the page is never wider than the window,
at 320, 390 and 1280 px. Three methods: a static read of the stylesheet, the page's own scroll width in a real browser, and a sweep of
every element's box in a real browser."""

import re
from pathlib import Path

import pytest
from browser_harness import browser, inline_assets, run_in_browser
from fastapi.testclient import TestClient

from desk import ops
from desk.ui.app import create_app

STATIC = Path(__file__).resolve().parents[1] / "src" / "desk" / "ui" / "static"
LONG = ("This is not a human-readable message and so therefore is rejected on principle. More formally: does not follow project "
        "guidelines and rules, and the same goes for every other message on this screen that reads like a log line instead of a sentence. ") * 3
TOKEN = "https://example.invalid/" + "a" * 300  # one unbroken token, the hardest case for a one-line reason


def _ticket(store):
    """The shape of DESK-0082: three Not accepted lines with reasons, one of them long, one a single unbroken token."""
    t = ops.new(store, "A ticket with long Not accepted reasons", "desk")
    t.set_section("Acceptance", "- [!] Posting the same comment three times stores one line\n"
                                f"  > Not accepted (must fix): {LONG.strip()}\n"
                                "- [!] A reused form token is refused with a plain message\n"
                                f"  > Not accepted (should): {TOKEN}\n"
                                "- [!] A short one\n  > Not accepted (nit): too vague\n- [ ] Still open")
    store.save(t)
    return t


def _measure(store, width, script):
    t = _ticket(store)
    html = inline_assets(TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text, STATIC)
    assert html.count('class="nr-reason"') == 3, "the fixture renders three reasons"
    return run_in_browser(html.replace("</body>", script + "</body>", 1), width)


# -- method 1: a static read of the stylesheet --------------------------------------------------------------------------------

def test_static_no_column_of_the_ticket_page_takes_its_width_from_its_content():
    """Protects the cause: every `.cols` rule (the two-column desktop layout and the one-column phone layout) declares each track as
    `minmax(0, …fr)`, never a bare `fr`, whose minimum is the content's min-content width."""
    css = re.sub(r"/\*.*?\*/", "", (STATIC / "desk.css").read_text(encoding="utf-8"), flags=re.S)
    tracks = re.findall(r"(?:^|[{}\s,])\.cols\{[^}]*grid-template-columns:([^;}]+)", css)
    assert len(tracks) >= 2, tracks  # the desktop rule and the phone rule
    for value in tracks:
        parts = re.findall(r"minmax\([^)]*\)|[^\s]+", value.strip())
        assert parts and all(re.fullmatch(r"minmax\(0,\s*[\d.]+fr\)", p) for p in parts), value


# -- method 2: the page's own scroll width in a real browser --------------------------------------------------------------------

PAGE = """<script>addEventListener('load',function(){setTimeout(function(){var de=document.documentElement;
var aside=document.querySelector('.cols>aside'),call=Array.prototype.filter.call(document.querySelectorAll('h2'),function(h){return h.textContent==='Your call';})[0],
 asg=document.getElementById('assignee');
function right(e){return e?Math.round(e.getBoundingClientRect().right):-1;}
fetch('/m',{method:'POST',body:JSON.stringify({cw:de.clientWidth,sw:de.scrollWidth,bodySW:document.body.scrollWidth,aside:right(aside),call:right(call),asg:right(asg)})});},300);});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
@pytest.mark.parametrize("width", [390, 1280])
def test_browser_a_long_not_accepted_reason_never_makes_the_page_wider_than_the_window(store, width):
    """Protects the owner's screen: with three Not accepted reasons (one 600 characters, one a 324-character unbroken link), the page
    scrolls only up and down (scrollWidth equals clientWidth), and 'Your call' and the Assignee end inside the window."""
    d = _measure(store, width, PAGE)
    assert d["sw"] <= d["cw"] and d["bodySW"] <= d["cw"], d
    assert 0 < d["aside"] <= d["cw"] and 0 < d["call"] <= d["cw"] and 0 < d["asg"] <= d["cw"], d


# -- method 3: a sweep of every element's box in a real browser -----------------------------------------------------------------

SWEEP = """<script>addEventListener('load',function(){setTimeout(function(){var w=document.documentElement.clientWidth,over=[],rows=[];
document.querySelectorAll('body *').forEach(function(e){var r=e.getBoundingClientRect();if(!r.width||r.right<=w+1||e.closest('[hidden]'))return;
 for(var p=e.parentElement;p&&p!==document.body;p=p.parentElement){var s=getComputedStyle(p).overflowX;if(s==='auto'||s==='scroll')return;}
 over.push(e.tagName+'.'+String(e.className).slice(0,30)+' right '+Math.round(r.right));});
document.querySelectorAll('.nr-reason').forEach(function(row){var why=row.querySelector('.why'),rr=row.getBoundingClientRect(),wr=why.getBoundingClientRect(),cs=getComputedStyle(why);
 rows.push({inRow:wr.left>=rr.left-0.5&&wr.right<=rr.right+0.5,lines:Math.round(wr.height/parseFloat(cs.lineHeight)),cut:why.scrollWidth>why.clientWidth,
  ellipsis:cs.textOverflow,more:row.querySelector('.more').textContent.length});});
fetch('/m',{method:'POST',body:JSON.stringify({w:w,over:over.slice(0,12),rows:rows})});},300);});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
@pytest.mark.parametrize("width", [320, 390, 1280])
def test_browser_every_reason_ellipsizes_inside_its_row_and_nothing_on_the_page_passes_the_right_edge(store, width):
    """Protects each element, not only the total: no element outside a scrolling box ends past the window's right edge (the 320 px
    phone included), each reason stays one line inside its own row, and the long ones are cut with an ellipsis while More still holds
    the whole text."""
    d = _measure(store, width, SWEEP)
    assert d["over"] == [], d
    assert len(d["rows"]) == 3 and all(r["inRow"] and r["lines"] == 1 and r["ellipsis"] == "ellipsis" for r in d["rows"]), d
    assert d["rows"][0]["cut"] and d["rows"][1]["cut"] and d["rows"][0]["more"] >= len(LONG.strip()), d
