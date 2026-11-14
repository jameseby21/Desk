"""DESK-0035: a drilled-in group has five working columns (Inbox with Triage, Ready, In progress with Sent back tagged red, Review,
Blocked), each showing its first 12 cards in queue order and then "N more"; Approved, Done and Let go fold into one Finished line;
cards lead with the title and only Bug, Urgent and Sent back are badges. Protects: ten columns falling off the right edge and a
589-card column rendering 589 cards."""

import re

from fastapi.testclient import TestClient

from desk import ops
from desk.ui.app import create_app


def _mk(store, title, project="desk", **meta):
    t = ops.new(store, title, project)
    t.meta.update(meta)
    t.write()
    return t


def _client(store):
    return TestClient(create_app(store.home))


def _cards(page):
    return re.findall(r'<div class="cc-title" title="([^"]*)"', page)


def test_five_working_columns_and_one_finished_line(store):
    _mk(store, "I", track="T", status="inbox")
    _mk(store, "Tr", track="T", status="triage")
    _mk(store, "R", track="T", status="ready")
    _mk(store, "P", track="T", status="in_progress")
    _mk(store, "S", track="T", status="changes_requested")
    _mk(store, "V", track="T", status="review")
    _mk(store, "B", track="T", status="blocked", blocked_on="x")
    _mk(store, "D1", track="T", status="done")
    _mk(store, "A1", track="T", status="approved")
    _mk(store, "X1", track="T", status="rejected")
    page = _client(store).get("/board?open=desk&g=desk:t").text
    heads = re.findall(r'<h3 class="colhead"[^>]*><span>([^<]*)</span>', page)
    assert heads == ["Inbox", "Ready", "In progress", "Review", "Blocked"]
    inbox = page[page.index('data-col="inbox"'):page.index('data-col="ready"')]
    assert "Triage" in inbox and "I" in _cards(inbox) and "Tr" in _cards(inbox)
    prog = page[page.index('data-col="in_progress"'):page.index('data-col="review"')]
    assert "Sent back" in prog and 'class="tag red"' in prog and _cards(prog) == ["P", "S"] or sorted(_cards(prog)) == ["P", "S"]
    assert page.count('class="finished"') == 1
    fin = page[page.index('class="finished"'):]
    assert "1 done" in fin and "1 approved" in fin and "1 let go" in fin
    assert "D1" in _cards(fin) and "A1" in _cards(fin) and "X1" in _cards(fin)  # one click away, nothing lost
    assert "D1" not in _cards(page[:page.index('class="finished"')])


def test_a_long_column_shows_twelve_in_queue_order_then_n_more(store):
    _mk(store, "Zed urgent", track="Big", status="ready", priority="p0")
    for i in range(20):
        _mk(store, f"Ready card {i:02d}", track="Big", status="ready", priority="p2")
    cl = _client(store)
    page = cl.get("/board?open=desk&g=desk:big").text
    cards = _cards(page)
    assert len(cards) == 12 and cards[0] == "Zed urgent"  # priority first, then id
    m = re.search(r'<a class="more" href="([^"]*)"[^>]*>9 more</a>', page)
    assert m, "the rest is a link, not 9 more cards in the page"
    rest = cl.get(m.group(1).replace("&amp;", "&") + "&frag=1").text
    assert len(_cards(rest)) == 9 and "Ready card 19" in rest
    full = cl.get(m.group(1).replace("&amp;", "&")).text  # without script the link is a page
    assert "<html" in full and len(_cards(full)) == 9


def test_cards_lead_with_the_title_and_only_bug_urgent_and_sent_back_are_badges(store):
    _mk(store, "Hot bug", track="T", status="ready", priority="p0", type="bug", tags=["a", "b"], assignee="chaz")
    _mk(store, "Calm one", track="T", status="ready", priority="p2", type="feature", tags=["a"], assignee="chaz")
    page = _client(store).get("/board?open=desk&g=desk:t").text
    cards = page.split('class="ccard')[1:]
    hot = [c for c in cards if "Hot bug" in c][0].split("</a>")[0]
    calm = [c for c in cards if "Calm one" in c][0].split("</a>")[0]
    assert hot.index("cc-title") < hot.index("cc-id")  # the title comes first
    assert "Bug" in hot and "Urgent" in hot
    assert not re.search(r'class="(chip|badge|tag)', calm) and "#a" not in calm and "+1" not in calm
    assert "Chaz" in calm or "chaz" in calm
    assert "DESK-" in calm


# -- a real browser: "N more" fills the column in place, the page stays the same document ------------------------------------------
import pytest  # noqa: E402
from browser_harness import browser, drive  # noqa: E402

MORE = """<script>addEventListener('load',function(){
var out={},sleep=function(ms){return new Promise(function(r){setTimeout(r,ms);});};
async function until(f,n){for(var i=0;i<(n||100)&&!f();i++){await sleep(100);}return f();}
(async function(){
 window.__alive=1;await sleep(300);
 out.before=document.querySelectorAll('.ccard').length;
 var a=document.querySelector('a[data-more]');out.hasLink=!!a;a.click();
 out.after=await until(function(){return document.querySelectorAll('.ccard').length>12;})?document.querySelectorAll('.ccard').length:-1;
 out.linkGone=!document.querySelector('a[data-more]');out.sameDocument=window.__alive===1;
 var cols=document.querySelectorAll('.col5');out.five=cols.length;
 var r=document.querySelector('.board5').getBoundingClientRect();out.fits=r.right<=window.innerWidth+1;
 fetch('/m',{method:'POST',body:JSON.stringify(out)});})();});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_n_more_fills_the_column_in_place_and_five_columns_fit(store):
    for i in range(15):
        _mk(store, f"Ready {i:02d}", track="Big", status="ready")
    got = drive(store, MORE, "/board?project=desk")
    assert got["hasLink"] and got["before"] == 12 and got["after"] == 15 and got["linkGone"] and got["sameDocument"], got
    assert got["five"] == 5 and got["fits"], got
