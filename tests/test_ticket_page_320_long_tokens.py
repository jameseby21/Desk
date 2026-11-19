"""DESK-0303 (second check, 2026-11-19): a ticket page does not scroll sideways at 320 px, whatever long unbroken text a section holds.

Protects: at 390 and 375 px every ticket fitted, but at 320 px CONS-0117 was 360 px wide (a long unbroken path in a plain-text line of the
Commands list, `UL.mentions > LI`) and JRE-0363 was 339 px wide (a long URL in the folded Context summary, `SPAN.rd-goal`). The claim
"CONS-0117 no longer scrolls sideways at 320" was false on the real store. Three methods: the stylesheet (the rule that breaks a long
token exists for these two boxes), the page's own scroll width in a real browser at exactly 320 px on a fixture whose every section holds
a long token, and a sweep that renders a copy of a real store (opt-in: set DESK_SWEEP_STORE to a store folder; it is copied first and the
copy is what the browser reads). The fixture is checked too, so the browser tests cannot pass for nothing."""

import os
import re
import shutil

import pytest
from fastapi.testclient import TestClient

from browser_harness import browser, cdp_measure, live_app
from desk import ops
from desk.store import Store
from desk.ui.app import STATIC, create_app

PATH = "C:\\Users\\someone\\Documents\\jeby_Industries\\desk\\src\\desk\\ui\\templates\\a_very_long_unbroken_template_name_that_never_breaks_anywhere.html"
URL = "https://example.com/some/very/long/path/that/never/breaks/" + "segment-" * 12 + "end?query=" + "x" * 60
WIDTH = 320

# Every section of a ticket page that shows free text, each with its own long token, so a missing rule in any one box shows up by name.
SECTIONS = {
    "Context": f"{URL} is where the trouble started.\n\nMore context.",
    "Outcome": f"**Goal.** Make {URL} fit.\n\n- {PATH}",
    "Acceptance": f"- [ ] {PATH} fits\n- [x] {URL} fits",
    "How to validate": f"1. Open {URL} and look.\n2. Run {PATH} and see it fit.",
    "Commands": f"- `uv run --no-sync pytest -q`\n- Get-ChildItem -Recurse {PATH}\n- Open {URL}",
    "Report": f"Ran {PATH} and then {URL}.",
    "Comments": f"- 2026-11-18 11:00 · james · {URL} {PATH}",
}

# What the page runs: its scroll width against the window, and the elements that end past the right edge (outside a scrolling or clipping box).
MEASURE = """(function(){var de=document.documentElement,cw=de.clientWidth,over=[];
document.querySelectorAll('body *').forEach(function(e){var r=e.getBoundingClientRect();
 if(!r.width||r.right<=cw+1||e.closest('[hidden]'))return;
 for(var p=e.parentElement;p&&p!==document.body;p=p.parentElement){var s=getComputedStyle(p).overflowX;if(s==='auto'||s==='scroll'||s==='hidden')return;}
 over.push(e.tagName+'.'+String(e.className).slice(0,30)+(e.parentElement?' < '+e.parentElement.tagName+'.'+String(e.parentElement.className).slice(0,20):'')+' '+Math.round(r.left)+'-'+Math.round(r.right));});
return {iw:innerWidth,cw:cw,sw:de.scrollWidth,bsw:document.body.scrollWidth,over:over.slice(0,8)};})()"""


@pytest.fixture
def stress(store):
    ids = {}
    for name, text in SECTIONS.items():  # one ticket per section, so a failure names the section
        t = ops.new(store, f"Long token in {name}", "desk")
        if name == "Comments":
            t.append_line("Comments", text)
        else:
            t.set_section(name, text)
        store.save(t)
        ids[name] = t.id
    every = ops.new(store, "Long token in every section", "desk")
    for name, text in SECTIONS.items():
        if name == "Comments":
            every.append_line("Comments", text)
        else:
            every.set_section(name, text)
    store.save(every)
    ids["every"] = every.id
    return ids


def test_fixture_puts_a_long_token_in_every_section_the_page_shows(store, stress):
    """Protects the browser tests from passing for nothing: each fixture ticket's page carries its token, in the box that is named."""
    c = TestClient(create_app(store.home))
    for name, tid in stress.items():
        page = c.get(f"/ticket/{tid}").text
        assert "a_very_long_unbroken_template_name" in page or "segment-segment" in page, name
    commands = c.get(f"/ticket/{stress['Commands']}").text
    assert 'class="mentions"' in commands and "Get-ChildItem" in commands.split('class="mentions"')[1]  # the plain-text line that was 360 px wide
    outcome = c.get(f"/ticket/{stress['Outcome']}").text
    assert 'class="rd-goal"' in outcome and "segment-segment" in outcome.split('class="rd-goal"')[1].split("</span>")[0]  # the folded Context summary that was 339 px wide


def _rules():
    css = re.sub(r"/\*.*?\*/", "", (STATIC / "desk.css").read_text(encoding="utf-8"), flags=re.S)
    return [(sel.strip(), body) for sel, body in re.findall(r"([^{}@]+)\{([^{}]*)\}", css)]


def _breaks(selector_part: str) -> bool:
    """True when some rule that names `selector_part` lets a long unbroken token break (overflow-wrap)."""
    return any(selector_part in [x.strip() for x in sel.split(",")] and re.search(r"overflow-wrap:\s*(anywhere|break-word)", body) for sel, body in _rules())


@pytest.mark.parametrize("selector", [".mentions li", ".rd-goal"])
def test_static_the_stylesheet_breaks_a_long_token_in_the_two_boxes_that_overflowed(selector):
    """Method 0, a read of the stylesheet: the plain-text Commands lines (`.mentions li`) and the folded Context summary (`.rd-goal`)
    each carry overflow-wrap, so an unbroken path or address wraps inside them."""
    assert _breaks(selector), f"no overflow-wrap rule names {selector}"


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_browser_every_section_fits_at_320(store, stress):
    """Method 1, a real browser at exactly 320 px (DevTools device metrics): every fixture ticket's scroll width equals the window's, and
    the element that would end past the edge is named when one does."""
    with live_app(create_app(store.home)) as base:
        got = cdp_measure(base, [f"/ticket/{tid}" for tid in stress.values()], [WIDTH], MEASURE)
    bad = {k: v for k, v in got.items() if v["sw"] > v["cw"] or v["bsw"] > v["cw"] or v["over"]}
    assert not bad, bad


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_browser_the_two_named_cases_fit_at_320_375_and_390(store, stress):
    """Edge: the Commands-list case and the Context-summary case at the three phone widths."""
    paths = [f"/ticket/{stress['Commands']}", f"/ticket/{stress['Outcome']}"]
    with live_app(create_app(store.home)) as base:
        got = cdp_measure(base, paths, [320, 375, 390], MEASURE)
    assert len(got) == 6
    assert not {k: v for k, v in got.items() if v["sw"] > v["cw"] or v["over"]}, got


@pytest.mark.skipif(browser() is None or not os.environ.get("DESK_SWEEP_STORE"), reason="opt-in: set DESK_SWEEP_STORE to a store folder to sweep a copy of it")
def test_browser_sweep_every_ticket_of_a_store_copy_at_320(tmp_path):
    """Method 2, the sweep: every ticket page of a COPY of the store in DESK_SWEEP_STORE (limit with DESK_SWEEP_LIMIT) is at most 320 px
    wide. The copy is what the browser reads; the original is never opened by the app."""
    src = os.environ["DESK_SWEEP_STORE"]
    dst = tmp_path / "copy"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".git", "node_modules", "*.pyc"))
    s = Store(dst)
    ids = sorted(t.id for t in s.all())
    if os.environ.get("DESK_SWEEP_LIMIT"):
        ids = ids[: int(os.environ["DESK_SWEEP_LIMIT"])]
    with live_app(create_app(dst)) as base:
        got = cdp_measure(base, [f"/ticket/{i}" for i in ids], [WIDTH], MEASURE, settle=0.12)
    bad = {k: v for k, v in got.items() if v and (v["sw"] > v["cw"] or v["bsw"] > v["cw"])}
    assert len(got) == len(ids) and not bad, bad
