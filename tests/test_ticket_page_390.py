"""DESK-0303: at 390 px a real ticket page does not scroll sideways and both boxes of every acceptance line are visible.

James's report (2026-11-15): the Walk and Order row, the tag row, the title and the Not accepted box of every line ran off the right
edge. The older browser test passed because its fixture had none of those parts. This file's ticket has all of them: a long title
that holds one unbroken token, eight tags (one long), a source, a parent, and acceptance lines that are long, done, not accepted
with a long reason, and open.

The phone is measured as a real one: headless Edge or Chrome driven over the DevTools Protocol with device metrics of exactly 390
(and 375, and 320) px wide; a headless --window-size below 500 px is not a phone. Three methods: the page's own scroll width, the
position of the four hit areas of every line, and a sweep of every element's right edge. The fixture is checked too, so a page that
quietly lost the Walk and Order row cannot make the browser tests pass for nothing."""

import re

import pytest
from fastapi.testclient import TestClient

from browser_harness import browser, cdp_measure, live_app
from desk import ops
from desk.ui.app import STATIC, create_app

TOKEN = "C:/Users/someone/Documents/jeby_Industries/desk/src/desk/ui/templates/a_very_long_unbroken_template_name_that_never_breaks.html"
TITLE = f"Bug: the ticket page is wider than a phone and hides the Not accepted boxes when a title carries {TOKEN} as well"
REASON = "This is not a human-readable message and so therefore is rejected on principle. " * 4
TAGS = ["ui", "lane:now", "phone", "layout", "a-rather-long-tag-name-for-the-tag-row", "bug", "p1-review", "walk-and-order"]
LINES = [
    "- [ ] At 390 px the scroll width equals the window width",
    f"- [x] A long line that says what must be true and keeps going so that it wraps over several lines on a phone. {TOKEN}",
    f"- [!] Both boxes of every line are visible\n  > Not accepted (must fix): {REASON.strip()}",
    "- [ ] Short",
    "- [x] Another done line",
]


@pytest.fixture
def phone_ticket(store):
    parent = ops.new(store, "A parent ticket", "desk")
    t = ops.new(store, TITLE, "desk", "bug", parent=parent.id)
    t.meta["tags"] = TAGS
    t.meta["source"] = f"James, 2026-11-15, in chat with a screenshot: {TOKEN}"
    t.set_section("Acceptance", "\n".join(LINES))
    store.save(t)
    return t


# Run in the page: the window, the scroll width, where every hit area sits, and every element that ends past the right edge.
MEASURE = """(function(){var de=document.documentElement,cw=de.clientWidth;
var rows=[].map.call(document.querySelectorAll('.chk2'),function(c){return [].map.call(c.querySelectorAll('.hit'),function(h){var r=h.getBoundingClientRect();
 return {l:Math.round(r.left*10)/10,r:Math.round(r.right*10)/10,w:Math.round(r.width)};});});
var over=[];document.querySelectorAll('body *').forEach(function(e){var r=e.getBoundingClientRect();
 var spill=e.clientWidth>0&&e.scrollWidth>e.clientWidth+1&&getComputedStyle(e).overflowX==='visible';  /* its own text runs past its box */
 if(!r.width||(r.right<=cw+1&&!spill)||e.closest('[hidden]')||e.closest('details:not([open]):not(summary)'))return;
 for(var p=e.parentElement;p&&p!==document.body;p=p.parentElement){var s=getComputedStyle(p).overflowX;if(s==='auto'||s==='scroll'||s==='hidden')return;}
 over.push((spill?'SPILL ':'')+e.tagName+'.'+String(e.className).slice(0,30)+' '+Math.round(r.left)+'-'+Math.round(r.right)+' '+(e.textContent||'').trim().slice(0,30));});
var walk=document.getElementById('tnav'),wr=walk?walk.getBoundingClientRect():null;
return {iw:innerWidth,cw:cw,sw:de.scrollWidth,bsw:document.body.scrollWidth,rows:rows,over:over.slice(0,12),
 walk:wr?{l:Math.round(wr.left),r:Math.round(wr.right)}:null,tags:document.querySelectorAll('.tag').length,
 title:(document.querySelector('h1')||{}).textContent||''};})()"""

WIDTHS = [390, 375, 320]


@pytest.fixture
def measured(store, phone_ticket):
    with live_app(create_app(store.home)) as base:
        return phone_ticket, cdp_measure(base, [f"/ticket/{phone_ticket.id}"], WIDTHS, MEASURE)


def test_fixture_has_the_walk_and_order_row_the_tags_a_long_title_and_every_kind_of_line(store, phone_ticket):
    """Protects the other tests from passing for nothing: the page they measure carries the Walk and Order row, eight tags, the long
    title with its unbroken token, and five lines with two boxes each (done, not accepted with a reason, open)."""
    page = TestClient(create_app(store.home)).get(f"/ticket/{phone_ticket.id}").text
    assert "Which tickets Previous and Next walk through" in page and "How Previous and Next line the tickets up" in page
    assert page.count('class="tag"') == len(TAGS) and "a_very_long_unbroken_template_name" in page
    assert page.count('class="chk2"') == len(LINES)
    acc = page.split("Acceptance (what")[1].split('id="done-means"')[0]
    assert acc.count('aria-pressed="true"') == 3  # two done lines and one not accepted
    assert "Must fix" in page and "This is not a human-readable message" in page


def test_static_the_title_breaks_an_over_long_token_but_never_a_word_that_fits():
    """Protects the cause (method 0, a read of the stylesheet): the h1 breaks an unbroken token with overflow-wrap:break-word, which
    leaves every word that fits on a line whole (DESK-0302), instead of letting it widen the page."""
    css = re.sub(r"/\*.*?\*/", "", (STATIC / "desk.css").read_text(encoding="utf-8"), flags=re.S)
    blocks = [b for sel, b in re.findall(r"([^{}]+)\{([^{}]*)\}", css) if "h1" in [x.strip() for x in sel.split(",")]]
    assert any("overflow-wrap:break-word" in b for b in blocks), blocks
    assert not any("overflow-wrap:anywhere" in b or "word-break:break-all" in b for b in blocks), blocks


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
@pytest.mark.parametrize("width", WIDTHS)
def test_browser_the_ticket_page_does_not_scroll_sideways(measured, width):
    """Protects the phone (method 1, the page's own scroll width): at 390, 375 and 320 px the page's scroll width equals the window's
    client width, so there is nothing to scroll to on the right, and the Walk and Order row ends inside the window."""
    t, got = measured
    d = got[f"/ticket/{t.id}@{width}"]
    assert d["tags"] == len(TAGS) and d["walk"], d
    assert d["iw"] - d["cw"] <= 17, d  # only a classic scroll bar may separate the two
    assert d["sw"] == d["cw"] and d["bsw"] <= d["cw"], d
    assert d["walk"]["r"] <= d["cw"], d


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
@pytest.mark.parametrize("width", WIDTHS)
def test_browser_both_boxes_of_every_acceptance_line_are_visible(measured, width):
    """Protects James's Not accepted boxes (method 2, where each hit area sits): at 390, 375 and 320 px both 44 px boxes of all five
    lines lie inside the window, the Not accepted one to the right of the Done one, the same x on every line."""
    t, got = measured
    d = got[f"/ticket/{t.id}@{width}"]
    assert len(d["rows"]) == len(LINES) and all(len(r) == 2 for r in d["rows"]), d["rows"]
    for done, no in d["rows"]:
        assert done["l"] >= 0 and no["r"] <= d["cw"] + 0.5, (d["cw"], done, no)
        assert done["r"] <= no["l"] + 0.5 and done["w"] >= 44 and no["w"] >= 44, (done, no)
    assert len({r[0]["l"] for r in d["rows"]}) == 1 and len({r[1]["l"] for r in d["rows"]}) == 1, d["rows"]


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
@pytest.mark.parametrize("width", WIDTHS)
def test_browser_no_element_ends_past_the_right_edge(measured, width):
    """Protects each part, not only the total (method 3, a sweep of every box): the title, the tag row, the Walk and Order row and
    every acceptance line end inside the window outside any scrolling or clipping box."""
    t, got = measured
    assert not got[f"/ticket/{t.id}@{width}"]["over"], got[f"/ticket/{t.id}@{width}"]["over"]
