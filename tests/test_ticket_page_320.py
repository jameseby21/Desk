"""DESK-0303 / DESK-0305 fix round: two real tickets still scrolled sideways on a 320 px phone.

Protects: (1) CONS-0117 was 352 px wide in a 305 px window because the deadline chip ("Soft deadline 2026-11-17 · missed by 1 day") is
one nowrap line 320 px long; (2) JEBYUS-0077 was 334 px wide because a Failed checks line holds a long web address with nothing to break
it. Neither is clipped by a scrolling box, so the whole page scrolled sideways. Each is checked by the stylesheet (a read), the page's
own scroll width in a real browser at 320 px, and a sweep of every element's box, with the nearby edge cases (a hard deadline, the
widths 375 and 390, an ordinary short chip that must stay on one line)."""

import re
from pathlib import Path

import pytest
from browser_harness import browser, cdp_measure, live_app
from fastapi.testclient import TestClient

from desk import ops
from desk.ui.app import STATIC, create_app

NEEDS_BROWSER = pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
URL = "https://github.com/example-org/example-repo/pull/11/files?diff=split&w=1&token_free_long_query=" + "a1" * 60


def _css() -> str:
    return re.sub(r"/\*.*?\*/", "", (Path(STATIC) / "desk.css").read_text(encoding="utf-8"), flags=re.S)


def _rule(css: str, selector: str) -> str:
    m = re.search(r"(?:^|[}\s,])" + re.escape(selector) + r"\{([^}]*)\}", css)
    return m.group(1) if m else ""


def _fixture(store):
    """Two tickets: a soft deadline missed by a day and a hard one, and one with a long address in a failed check."""
    a = ops.new(store, "Deadline chip ticket", "desk")
    a.meta.update({"deadline": "2026-11-17", "deadline_kind": "soft"})
    store.save(a)
    h = ops.new(store, "Hard deadline chip ticket", "desk")
    h.meta.update({"deadline": "2026-11-17", "deadline_kind": "hard"})
    store.save(h)
    b = ops.new(store, "Failed check ticket", "desk")
    b.set_section("Acceptance", f"- [!] PR #11 ({URL}) is merged and the live site shows it\n- [ ] Still open")
    store.save(b)
    return a.id, h.id, b.id


# -- method 1: a read of the stylesheet ---------------------------------------------------------------------------------------------

def test_static_the_deadline_chip_may_wrap_and_never_exceeds_its_row():
    """The deadline chip drops the chip's nowrap and caps its width at the row's."""
    rule = _rule(_css(), ".chip.deadline")
    assert "white-space:normal" in rule and "max-width:100%" in rule, rule


def test_static_a_failed_check_line_breaks_inside_a_long_address():
    """A Failed checks line carries overflow-wrap:break-word (a word that fits stays whole; the reviewed list of anywhere rules is not widened)."""
    assert "overflow-wrap:break-word" in _rule(_css(), ".failed-checks li")


def test_route_both_shapes_render_on_the_page(store):
    """The fixture shows what the checker saw: the chip text and the failed check with the address, on the ticket page."""
    a, h, b = _fixture(store)
    c = TestClient(create_app(store.home), base_url="http://127.0.0.1:8799")
    assert "deadline 2026-11-17" in c.get(f"/ticket/{a}").text and "missed by" in c.get(f"/ticket/{a}").text
    assert 'class="failed-checks"' in c.get(f"/ticket/{b}").text and "Not run" in c.get(f"/ticket/{b}").text


# -- methods 2 and 3: a real browser at phone widths (Chrome DevTools Protocol device metrics) -----------------------------

EXPR = """(function(){var de=document.documentElement,w=de.clientWidth,over=[];
document.querySelectorAll('body *').forEach(function(e){var r=e.getBoundingClientRect();if(!r.width||r.right<=w+1||e.closest('[hidden]'))return;
 for(var p=e.parentElement;p&&p!==document.body;p=p.parentElement){var s=getComputedStyle(p).overflowX;if(s==='auto'||s==='scroll')return;}
 if(getComputedStyle(e).position==='fixed')return;
 over.push(e.tagName+'.'+String(e.className).slice(0,30)+' right '+Math.round(r.right));});
var chip=document.querySelector('.chip.deadline'),ids=document.querySelector('.row .chip');
return {sw:de.scrollWidth,cw:w,over:over.slice(0,8),chipH:chip?Math.round(chip.getBoundingClientRect().height):0,idH:ids?Math.round(ids.getBoundingClientRect().height):0};})()"""


@NEEDS_BROWSER
@pytest.mark.parametrize("width", [320, 375, 390])
def test_browser_neither_ticket_scrolls_sideways_and_no_element_passes_the_right_edge(store, width):
    """Method 2 and 3 together: the page's scroll width equals the window's, and the per-element sweep finds nothing past the edge,
    for the deadline chip (soft and hard) and the long address in a failed check."""
    a, h, b = _fixture(store)
    with live_app(create_app(store.home)) as base:
        got = cdp_measure(base, [f"/ticket/{a}", f"/ticket/{h}", f"/ticket/{b}"], [width], EXPR)
    for key, d in got.items():
        assert d["sw"] <= d["cw"] and d["over"] == [], (key, d)


@NEEDS_BROWSER
def test_browser_edge_a_short_chip_stays_one_line_and_the_long_deadline_chip_wraps_instead_of_growing(store):
    """Edge: the id chip is still one line high (the wrap is only for the deadline chip); the long deadline chip is two lines on 320 px."""
    a, h, b = _fixture(store)
    with live_app(create_app(store.home)) as base:
        got = cdp_measure(base, [f"/ticket/{a}"], [320, 1280], EXPR)
    assert got[f"/ticket/{a}@320"]["chipH"] > got[f"/ticket/{a}@320"]["idH"]
    assert got[f"/ticket/{a}@1280"]["chipH"] == got[f"/ticket/{a}@1280"]["idH"]
