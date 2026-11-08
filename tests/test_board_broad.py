"""DESK-0084: the board opens at broad context (projects, then tracks or sprints, with counts and the top priority); drilling in shows
the tickets; collapsing hides them; what is open lives in the URL. Protects: the clutter James reported, and Chaz's calm-board rules."""

import re

from fastapi.testclient import TestClient

from desk import boardview as bv, ops
from desk.ui.app import create_app


def _mk(store, title, project="desk", **meta):
    t = ops.new(store, title, project)
    t.meta.update(meta)
    t.write()
    return t


def _cards(page):
    """The titles of the ticket cards drawn on the page (the top-ticket links of a row carry titles too, so they do not count)."""
    return re.findall(r'<div class="cc-title" title="([^"]*)"', page)


def _fx(store):
    a = _mk(store, "Alpha in first", track="First real use", priority="p0", status="ready")
    b = _mk(store, "Beta in first", track="First real use", priority="p2", status="review")
    c = _mk(store, "Gamma in second", track="Second", priority="p3", status="ready")
    d = _mk(store, "Delta untracked", "jre", priority="p1", status="ready")
    return a, b, c, d, TestClient(create_app(store.home))


def test_board_opens_at_projects_only_no_ticket_cards(store):
    a, b, c, d, cl = _fx(store)
    page = cl.get("/board").text
    assert 'class="bproj"' in page and "Desk" in page and "jRE" in page
    assert _cards(page) == []  # no ticket card until someone drills in
    assert "top p0" in page and "top p1" in page  # the top priority of each project
    assert "3 open" in page and "1 open" in page  # counts
    assert f'href="/ticket/{a.id}"' in page  # the top ticket of the project is a link, not a card


def test_projects_sort_by_urgency_then_size(store):
    _fx(store)
    rows = bv.build(store, store.tickets())
    assert [r.key for r in rows] == ["desk", "jre"]  # desk has a p0, jre only a p1


def test_groups_show_counts_and_top_priority(store):
    a, b, c, d, cl = _fx(store)
    rows = bv.build(store, store.tickets())
    desk = rows[0]
    by = {g.name: g for g in desk.groups}
    assert by["First real use"].open == 2 and by["First real use"].top == "p0" and by["First real use"].top_ticket.id == a.id
    assert by["Second"].top == "p3" and by["Second"].total == 1
    assert [g.name for g in desk.groups] == ["First real use", "Second"]
    jre = rows[1]
    assert [g.name for g in jre.groups] == ["No track"]


def test_drill_in_shows_tickets_and_collapse_hides_them(store):
    a, b, c, d, cl = _fx(store)
    drilled = cl.get("/board?open=desk&g=desk:first-real-use").text
    assert sorted(_cards(drilled)) == ["Alpha in first", "Beta in first"]
    assert re.search(r'<details class="bproj"[^>]*data-val="desk" open>', drilled)
    assert re.search(r'<details class="bgroup"[^>]*data-val="desk:first-real-use"[^>]* open>', drilled)
    collapsed = cl.get("/board?open=-").text
    assert _cards(collapsed) == []
    everything = cl.get("/board?open=*&g=*").text
    assert sorted(_cards(everything)) == ["Alpha in first", "Beta in first", "Delta untracked", "Gamma in second"]


def test_group_fragment_loads_the_columns_of_one_group(store):
    a, b, c, d, cl = _fx(store)
    frag = cl.get("/board/group", params={"project": "desk", "slug": "second"}).text
    assert "Gamma in second" in frag and "Alpha in first" not in frag and "<html" not in frag
    untracked = cl.get("/board/group", params={"project": "jre", "slug": "untracked"}).text
    assert "Delta untracked" in untracked


def test_group_by_sprint_uses_the_sprint_field_and_a_sprint_track(store):
    _mk(store, "In sprint six", sprint="sprint 6 2026")
    _mk(store, "Sprint track", track="Sprint 0007")
    _mk(store, "Neither")
    rows = bv.build(store, store.tickets(), by="sprint")
    names = {g.name for g in rows[0].groups}
    assert names == {"sprint 6 2026", "Sprint 0007", "No sprint"}
    page = TestClient(create_app(store.home)).get("/board?by=sprint").text
    assert "sprint 6 2026" in page and "No sprint" in page


def test_filters_still_narrow_and_open(store):
    a, b, c, d, cl = _fx(store)
    page = cl.get("/board?track=second").text
    assert _cards(page) == ["Gamma in second"] and 'name="track" value="first-real-use"' in page
    assert _cards(cl.get("/board?project=jre").text) == ["Delta untracked"]


def test_calm_cards_at_most_two_chips_plus_n_and_clamped_title(store):
    long = "A very long title " * 12
    t = _mk(store, long.strip(), track="T", priority="p1", status="ready", tags=["one", "two", "three"])
    page = TestClient(create_app(store.home)).get("/board?open=desk&g=desk:t").text
    card = page[page.index('class="ccard'):]
    card = card[:card.index("</a>")]
    chips = re.findall(r'<span class="chip[^"]*"', card)
    assert len(chips) <= 3 and "+" in card  # priority, the +N, and no more than one badge
    assert 'class="cc-title"' in card and f'title="{long.strip()}"' in card  # the full title on hover
    css = (create_app(store.home).router and open("src/desk/ui/static/desk.css", encoding="utf-8").read())
    assert "-webkit-line-clamp:3" in css and "overflow-x:auto" in css and "position:sticky" in css  # three lines, own scroll, sticky headers
    assert t.id in page


def test_a_long_column_shows_eight_then_show_more(store):
    for i in range(11):
        _mk(store, f"Ready card {i}", track="Big", priority="p2", status="ready")
    page = TestClient(create_app(store.home)).get("/board?open=desk&g=desk:big").text
    assert "Show 3 more" in page and len(_cards(page)) == 11


def test_review_by_age_and_proof_dot_helpers(store):
    import datetime as dt

    t = _mk(store, "X", status="review", review_by=(dt.date.today() + dt.timedelta(days=3)).isoformat())
    assert bv.review_by_text(t) == "in 3 d" and bv.proof_dot(t) == "none"
    t.sections["Report"] = "Done.\n"
    assert bv.proof_dot(t) == "ran"
    t.sections["Acceptance"] = "- [!] broke\n"
    assert bv.proof_dot(t) == "notrun"


# -- a real browser: drilling in loads the cards in place and writes the state into the address --------------------------------

import pytest  # noqa: E402
from browser_harness import browser, drive  # noqa: E402

DRILL = """<script>addEventListener('load',function(){
var out={},sleep=function(ms){return new Promise(function(r){setTimeout(r,ms);});};
async function until(f,n){for(var i=0;i<(n||100)&&!f();i++){await sleep(100);}return f();}
(async function(){
 out.cardsAtStart=document.querySelectorAll('.ccard').length;
 window.__alive=1;
 var p=document.querySelector('details.bproj');p.open=true;await sleep(200);
 var g=p.querySelector('details.bgroup');g.open=true;
 out.loaded=await until(function(){return document.querySelectorAll('.ccard').length>0;});
 out.cards=document.querySelectorAll('.ccard').length;out.sameDocument=window.__alive===1;
 out.url=location.search;
 g.open=false;await sleep(300);out.urlAfterClose=location.search;
 fetch('/m',{method:'POST',body:JSON.stringify(out)});})();});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_drilling_in_loads_cards_in_place_and_keeps_state_in_the_address(store):
    _mk(store, "Alpha in first", track="First real use", priority="p0", status="ready")
    got = drive(store, DRILL, "/board")
    assert got["cardsAtStart"] == 0 and got["loaded"] and got["cards"] == 1 and got["sameDocument"], got
    assert "open=desk" in got["url"] and "g=desk:first-real-use" in got["url"].replace("%3A", ":"), got
    assert "g=" not in got["urlAfterClose"] and "open=desk" in got["urlAfterClose"], got
