"""DESK-0034: the board's filters are three searchable dropdowns (Project, Assignee, Track; Sprint when sprints exist) with counts
and removable tokens, held in the URL, instead of ninety-nine chips. Protects: the 99-chip board James reported, and the
broad-context board of DESK-0084, which stays as built."""

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


def test_three_dropdowns_replace_the_chip_walls(store):
    for i in range(8):
        _mk(store, f"T{i}", track=f"Track {i}", assignee="chaz" if i % 2 else "main", status="ready")
    page = _client(store).get("/board").text
    assert 'class="trackchips"' not in page
    for name in ("Project", "Assignee", "Track"):
        assert f'<summary><b>{name}</b>' in page
    assert page.count('class="dd"') == 3
    assert re.search(r'name="track" value="track-3"', page) and re.search(r'name="assignee" value="chaz"', page)
    assert re.search(r'name="project" value="desk"[^>]*>\s*<span>[^<]*desk[^<]*</span>\s*<i>8</i>', page, re.S)  # a count beside each choice
    assert len(re.findall(r'class="chip[^"]*"[^>]*href="/board\?', page)) == 0  # no filter chips


def test_the_filter_lives_in_the_url_and_shows_as_removable_tokens(store):
    _mk(store, "In desk", "desk", assignee="chaz", status="ready")
    _mk(store, "In jre", "jre", assignee="main", status="ready")
    _mk(store, "In jeby-us", "jeby-us", assignee="chaz", status="ready")
    cl = _client(store)
    page = cl.get("/board?company=unsorted&project=desk,jre").text  # DESK-0183: filters open what is left inside a company
    assert sorted(_cards(page)) == ["In desk", "In jre"]
    toks = re.findall(r'<a class="tok" href="([^"]*)"[^>]*>([^<]*)', page)
    assert [label.strip() for _h, label in toks] == ["desk", "jre"]
    gone = [h for h, label in toks if label.strip() == "jre"][0]  # removing a token is a plain link to the address without it
    assert gone == "/board?project=desk&amp;company=unsorted"  # the drill stays when a token goes (DESK-0183)
    assert sorted(_cards(cl.get("/board?company=unsorted&project=desk&project=jre").text)) == ["In desk", "In jre"]  # a form posts repeated keys
    assert _cards(cl.get("/board?company=unsorted&project=desk,jre&assignee=chaz").text) == ["In desk"]
    assert 'value="chaz" checked' in cl.get("/board?assignee=chaz").text


def test_dropdown_counts_follow_the_other_filters(store):
    _mk(store, "a", "desk", assignee="chaz", status="ready")
    _mk(store, "b", "desk", assignee="main", status="ready")
    _mk(store, "c", "jre", assignee="main", status="ready")
    page = _client(store).get("/board?assignee=main").text
    assert re.search(r'name="project" value="desk"[^>]*>\s*<span>[^<]*</span>\s*<i>1</i>', page, re.S)


def test_projects_level_view_is_unchanged_and_filter_opens_the_columns(store):
    _mk(store, "Only", track="T", status="ready")
    cl = _client(store)
    assert 'class="bproj"' in cl.get("/board?company=unsorted").text and not _cards(cl.get("/board?company=unsorted").text)  # DESK-0183: one click below companies
    assert _cards(cl.get("/board?company=unsorted&project=desk").text) == ["Only"]


# -- a real browser: typing in a dropdown narrows its list; opening one closes the others ---------------------------------------------
import pytest
from browser_harness import browser, drive

SCRIPT = """<script>addEventListener('load',function(){
var out={},sleep=function(ms){return new Promise(function(r){setTimeout(r,ms);});};
(async function(){
 await sleep(300);
 var dds=document.querySelectorAll('details.dd');dds[0].open=true;await sleep(100);
 var q=dds[0].querySelector('.dd-q');out.focused=document.activeElement===q;
 q.value='jr';q.dispatchEvent(new Event('input',{bubbles:true}));
 var vis=function(d){return Array.prototype.filter.call(d.querySelectorAll('li[data-text]'),function(li){return !li.hidden;}).map(function(li){return li.dataset.text;});};
 out.narrowed=vis(dds[0]);
 q.value='zzz';q.dispatchEvent(new Event('input',{bubbles:true}));out.none=vis(dds[0]).length;out.noneMsg=!dds[0].querySelector('li.dd-none[hidden]');
 dds[1].open=true;await sleep(100);out.firstClosed=!dds[0].open;
 document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));out.escClosed=!dds[1].open;
 fetch('/m',{method:'POST',body:JSON.stringify(out)});})();});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_typing_narrows_a_dropdown_and_only_one_is_open(store):
    _mk(store, "a", "desk", status="ready")
    _mk(store, "b", "jre", status="ready")
    got = drive(store, SCRIPT, "/board")
    assert got["focused"] and got["narrowed"] == ["jre jre"], got
    assert got["none"] == 0 and got["noneMsg"], got
    assert got["firstClosed"] and got["escClosed"], got
