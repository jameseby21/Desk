"""DESK-0121 (Chaz R5): "2 of 4 done · 1 not accepted · 1 open" with a lime-and-red bar above the Acceptance list, updating without a reload."""

import re

from fastapi.testclient import TestClient

from desk import ops
from desk.ticket import check_counts
from desk.ui.app import create_app

JSON = {"Accept": "application/json"}
FOUR = "- [x] one\n- [x] two\n- [!] three — not accepted: Wrong colour\n- [ ] four"


def _ticket(store, acc=FOUR):
    t = ops.new(store, "Summary", "jre")
    t.set_section("Acceptance", acc)
    store.save(t)
    return t


def test_counts_include_nested_lines_and_skip_code():
    assert check_counts("- [x] a\n  - [ ] nested\n```\n- [!] code\n```\n- [!] b\n- [ ] c") == {"done": 1, "no": 1, "open": 2, "total": 4}
    assert check_counts("no boxes here")["total"] == 0


def test_page_shows_the_summary_line_and_a_lime_and_red_bar(store):
    t = _ticket(store)
    page = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    box = re.search(r'<div id="chk-summary".*?</div></div>', page, re.S).group(0)
    assert "<b>2 of 4 done</b> · 1 not accepted · 1 open" in box
    assert 'class="b-done" style="width:50%"' in box and 'class="b-no" style="width:25%"' in box
    assert 'aria-live="polite"' in box
    assert "of 4 true" not in page


def test_no_acceptance_lines_means_no_summary(store):
    t = _ticket(store, "just words, no boxes")
    page = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    assert 'id="chk-summary"' not in page


def test_json_post_returns_the_new_summary_and_the_fresh_section(store):
    """Protects: a box click answers with everything the page needs to change in place, so nothing reloads."""
    t = _ticket(store)
    c = TestClient(create_app(store.home))
    r = c.post(f"/ticket/{t.id}/check", data={"token": c.app.state.tokens.issue(), "section": "Acceptance", "index": 3, "state": "x"}, headers=JSON)
    j = r.json()
    assert r.status_code == 200 and j["state"] == "x"
    assert "<b>3 of 4 done</b> · 1 not accepted · 0 open" in j["summary_html"] and 'class="b-done" style="width:75%"' in j["summary_html"]
    assert j["html"].count('class="chk2"') == 4 and 'aria-pressed="true"' in j["html"] and 'name="token"' in j["html"]
    assert j["stamp"] and j["stamp"].isdigit()
    # the tokens in the fresh fragment work, and moving the not-accepted line to open gives the other numbers
    tok = re.search(r'name="token" value="([^"]+)"', j["html"]).group(1)
    r2 = c.post(f"/ticket/{t.id}/check", data={"token": tok, "section": "Acceptance", "index": 2, "state": " "}, headers=JSON)
    assert "<b>3 of 4 done</b> · 0 not accepted · 1 open" in r2.json()["summary_html"]


LIVE = """<script>addEventListener('load',function(){window.__alive=1;var out={},sum=function(){return document.getElementById('chk-summary').textContent;},
li=function(i){return document.querySelectorAll('[data-check-section=Acceptance] li.task-list-item')[i];},sleep=function(ms){return new Promise(function(r){setTimeout(r,ms);});};
(async function(){
 out.before=sum();
 li(3).querySelector('[data-act=done]').click();
 for(var i=0;i<60&&sum()===out.before;i++){await sleep(100);}
 out.after=sum();out.sameDocument=window.__alive===1;
 out.pressed=li(3).querySelector('[data-act=done]').getAttribute('aria-pressed')==='true';
 out.bar=document.querySelector('#chk-summary .b-done').style.width;
 out.focusKept=document.activeElement===li(3);
 li(2).querySelector('[data-act=no]').click();   // a Not accepted line: one click clears it
 for(i=0;i<60&&sum()===out.after;i++){await sleep(100);}
 out.afterClear=sum();
 await sleep(3500);  // longer than two auto-refresh polls: its own write must not reload the page
 out.noSelfReload=window.__alive===1;
 fetch('/m',{method:'POST',body:JSON.stringify(out)});})();});</script>"""


import pytest
from browser_harness import browser, drive


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_boxes_update_the_summary_in_place_in_a_real_browser(store):
    """Protects: a click changes the numbers and the bar at once, in the same document, keeps focus on the line, and the page does not
    reload itself afterwards when the auto-refresh sees the file change. Real server on a free port, headless browser."""
    t = _ticket(store)
    got = drive(store, LIVE, f"/ticket/{t.id}")
    assert got["before"] == "2 of 4 done · 1 not accepted · 1 open"
    assert got["after"] == "3 of 4 done · 1 not accepted · 0 open" and got["bar"] == "75%"
    assert got["afterClear"] == "3 of 4 done · 0 not accepted · 1 open"
    assert got["sameDocument"] and got["pressed"] and got["focusKept"] and got["noSelfReload"]
    text = store.get(t.id).sections["Acceptance"]
    assert text.split("\n")[3] == "- [x] four" and text.split("\n")[2].startswith("- [ ] three")  # and the file really changed
