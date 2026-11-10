"""DESK-0207: the Projects page filters for real. James (2026-11-09) clicked "temujin 5" on /projects and saw every project with
zero counts: the assignee chip narrowed the tickets but every project row was still drawn, and the bare key read as the project when
it was the Temujin code session. Protects: rows that match nothing are hidden, the count beside a choice equals the open tickets in
the rows it draws, labels say what they are, the chip row is gone, and Tag and Type filter the Projects page, Today and the board."""

import re

import pytest
from fastapi.testclient import TestClient

from desk import ops
from desk.ui.app import create_app

AREAS = {"temujin": ("Temujin", "Personal"), "jre": ("jRE", "Personal"), "desk": ("Desk", "jebyLabs (free and open tools)"),
         "jeby-us": ("jeby.us", "Client work (JA Insights)"), "lab": ("Lab", None)}


def _profile(store, key, title, area=None):
    fm = f"---\nkey: {key}\nname: {title}\n" + (f"area: {area}\n" if area else "") + "---\n"
    (store.projects_dir / f"{key}.md").write_text(fm + f"# {title}\n", encoding="utf-8")


def _mk(store, title, project, **meta):
    t = ops.new(store, title, project, meta.pop("type", "feature"))
    t.meta.update(meta)
    t.write()
    return t


def _fx(store):
    """Five projects; Lab has no tickets. Temujin holds five tickets across three assignees, two of them its own session's open ones."""
    for key, (title, area) in AREAS.items():
        _profile(store, key, title, area)
    _mk(store, "Animatic", "temujin", type="upgrade", assignee="temujin", status="inbox", tags=["200-percent", "motion"], track="Pitch")
    _mk(store, "Color script", "temujin", type="upgrade", assignee="temujin", status="ready", tags=["200-percent", "look"], track="Pitch")
    _mk(store, "License", "temujin", type="task", assignee="temujin", status="done", tags=["licensing"])
    _mk(store, "Logline", "temujin", type="task", assignee="james", status="ready")
    _mk(store, "Share folder", "temujin", type="task", assignee="chaz", status="ready")
    _mk(store, "Filter bug", "desk", type="bug", assignee="main", status="ready", tags=["filters"], track="Desk UI v1")
    _mk(store, "Old fix", "desk", type="bug", assignee="main", status="done")
    _mk(store, "Desk work", "desk", type="bug", assignee="main", status="in_progress", tags=["filters"])
    _mk(store, "Errand", "jre", type="feature", assignee="jre", status="in_progress", tags=["motion"])
    _mk(store, "Client thing", "jeby-us", type="feature", assignee="chaz", status="ready")
    return TestClient(create_app(store.home))


def _rows(page):
    return re.findall(r'<details class="proj" data-key="p" data-val="([^"]+)"', page)


def _tracks(page):
    return re.findall(r'<details class="track" data-key="t" data-val="([^"]+)"', page)


def _row_open(page):
    """The open tickets in the project rows drawn (a track's own line reads "N open of M" and is not counted twice)."""
    return sum(int(n) for n in re.findall(r'<span class="counts">(\d+) open ·', page))


def _opts(page, name):
    """(value, label, open count) of every choice in one dropdown, in page order."""
    return [(v, lab.strip(), int(n)) for v, lab, n in
            re.findall(rf'name="{name}" value="([^"]*)"[^>]*>\s*<span>([^<]*)</span>\s*<i>(\d+) open</i>', page)]


def test_an_assignee_with_tickets_in_one_project_draws_one_row(store):
    """Protects: James's screenshot. The Temujin session's filter draws Temujin alone, says so, and offers a way back."""
    c = _fx(store)
    page = c.get("/projects?assignee=temujin").text  # the address James had open
    assert _rows(page) == ["temujin"]
    assert "Showing 1 of 5 projects" in page
    assert re.search(r'<a href="/projects"[^>]*>Clear filters</a>', page)
    assert "0 open · 0" not in page  # no zero-count rows
    assert sorted(_rows(c.get("/projects").text)) == ["desk", "jeby-us", "jre", "lab", "temujin"]  # no filter: every project (in score order since DESK-0136, 2026-11-10)
    assert "Showing" not in c.get("/projects").text


def test_tracks_with_no_matching_ticket_are_not_drawn(store):
    c = _fx(store)
    assert sorted(_tracks(c.get("/projects?assignee=temujin").text)) == ["pitch", "untracked"]
    assert _tracks(c.get("/projects?tag=look").text) == ["pitch"]


def test_every_count_says_open_and_equals_the_open_tickets_in_the_rows_it_draws(store):
    c = _fx(store)
    page = c.get("/projects").text
    for name in ("assignee", "tag", "type", "company"):
        opts = _opts(page, name)
        assert opts, name
        for value, label, n in opts:
            shown = c.get(f"/projects?{name}={value}").text
            assert _row_open(shown) == n, (name, value, label, n)
    assert ("code:temujin", "temujin session", 2) in _opts(page, "assignee")  # inbox and ready; the done one is not open
    assert ("licensing", "licensing", 0) in _opts(page, "tag")  # only a done ticket carries it


def test_people_first_by_name_then_code_sessions_named_as_sessions(store):
    c = _fx(store)
    page = c.get("/projects").text
    assert [lab for _v, lab, _n in _opts(page, "assignee")] == ["Chaz", "James", "Patch", "jre session", "temujin session"]
    assert page.index('<li class="dd-head">People</li>') < page.index('<li class="dd-head">Code sessions</li>')
    assert '<summary><b>Assigned to</b> <span class="dd-sel">temujin session</span>' in c.get("/projects?assignee=temujin").text


def test_the_chip_row_is_gone_and_four_dropdowns_hold_their_values_in_the_address(store):
    c = _fx(store)
    page = c.get("/projects").text
    assert "trackchips" not in page and 'class="chip' not in page.split('<div class="tree">')[0]
    assert [b for b in re.findall(r'<details class="dd"[^>]*><summary><b>([^<]+)</b>', page)] == ["Assigned to", "Tag", "Type", "Company"]
    assert re.findall(r'<form class="dd-panel" method="get" action="([^"]+)"', page) == ["/projects"] * 4
    page = c.get("/projects?assignee=temujin&tag=look").text
    assert _rows(page) == ["temujin"] and 'value="code:temujin" checked' in page and 'value="look" checked' in page
    assert re.search(r'<input type="hidden" name="tag" value="look">', page)  # the other dropdowns' forms keep the tag
    link = re.search(r'<a class="name" href="([^"]*p=temujin)"', page).group(1)
    assert "assignee=code:temujin" in link and "tag=look" in link  # opening a project keeps the filter


def test_company_filter_draws_only_its_projects_with_tickets(store):
    c = _fx(store)
    assert sorted(_rows(c.get("/projects?company=personal").text)) == ["jre", "temujin"]  # rows are in score order since DESK-0136 (2026-11-10)
    page = c.get("/projects?company=unsorted").text  # Lab is the only Unsorted project and has no tickets
    assert _rows(page) == [] and "Showing 0 of 5 projects" in page and "No project has a ticket that matches" in page


def test_the_intro_line_is_plain_words(store):
    page = _fx(store).get("/projects").text
    intro = re.search(r"<h1>Projects</h1>\s*<p[^>]*>(.*?)</p>", page, re.S).group(1)
    assert "<code>" not in intro and "projects/" not in intro and "track:" not in intro and ".md" not in intro
    assert "track" in intro.lower() and "address" in intro


def test_tag_filter_narrows_projects_today_and_the_board(store):
    c = _fx(store)
    assert _rows(c.get("/projects?tag=motion").text) == ["jre", "temujin"]
    assert _rows(c.get("/projects?tag=filters").text) == ["desk"]
    today = c.get("/?tag=motion").text
    assert "Errand" in today and "Desk work" not in today
    assert re.search(r'<option value="motion" selected>motion \(2\)</option>', today)  # Today's dropdown, held in the address
    assert "Desk work" in c.get("/").text
    cards = re.findall(r'<div class="cc-title" title="([^"]*)"', c.get("/board?company=*&tag=motion").text)
    assert sorted(cards) == ["Animatic", "Errand"]
    board = c.get("/board?tag=motion").text
    assert '<summary><b>Tag</b> <span class="dd-sel">motion</span>' in board and 'name="tag" value="motion" checked' in board


def test_type_filter_narrows_projects_today_and_the_board(store):
    c = _fx(store)
    assert _rows(c.get("/projects?type=bug").text) == ["desk"]
    today = c.get("/?type=bug").text
    assert "Desk work" in today and "Errand" not in today
    assert re.search(r'<option value="bug" selected>Bug \(3\)</option>', today)
    cards = re.findall(r'<div class="cc-title" title="([^"]*)"', c.get("/board?company=*&type=bug").text)
    assert sorted(cards) == ["Desk work", "Filter bug", "Old fix"]  # the done one sits in the Finished line
    assert '<summary><b>Type</b>' in c.get("/board").text


def test_board_fragments_keep_the_tag_and_type_filters(store):
    """A group opened after the page loaded, and its "N more", still apply Tag and Type."""
    c = _fx(store)
    frag = c.get("/board/group?project=temujin&slug=pitch&tag=look").text
    assert re.findall(r'<div class="cc-title" title="([^"]*)"', frag) == ["Color script"]
    frag = c.get("/board/group?project=desk&slug=untracked&type=feature").text
    assert re.findall(r'<div class="cc-title" title="([^"]*)"', frag) == []


# -- a real browser at phone width ---------------------------------------------------------------------------------------------------
from browser_harness import browser, drive  # noqa: E402

PHONE = """<script>addEventListener('load',function(){
if(window.top!==window||location.pathname!=='/board')return;
var sleep=function(ms){return new Promise(function(r){setTimeout(r,ms);});},out={wide:[],small:[],openScroll:[]};
(async function(){
 var fr=document.createElement('iframe');fr.src=%s;fr.style.cssText='width:390px;height:1400px;border:0';document.body.appendChild(fr);
 for(var i=0;i<100&&!(fr.contentDocument&&fr.contentDocument.querySelector('.tree'));i++){await sleep(100);}await sleep(400);
 var d=fr.contentDocument,w=fr.contentWindow,main=d.querySelector('main');
 out.w=w.innerWidth;out.scrollW=d.documentElement.scrollWidth;out.rows=d.querySelectorAll('details.proj').length;
 out.dds=d.querySelectorAll('details.dd').length;
 main.querySelectorAll('*').forEach(function(e){var r=e.getBoundingClientRect();if(r.width&&r.right>w.innerWidth+1)out.wide.push(e.tagName+'.'+e.className+' '+Math.round(r.right));});
 main.querySelectorAll('*').forEach(function(e){if(e.children.length===0&&e.textContent.trim()&&parseFloat(w.getComputedStyle(e).fontSize)<12)out.small.push(e.tagName+'.'+e.className+' '+w.getComputedStyle(e).fontSize);});
 var dds=d.querySelectorAll('details.dd');
 for(var j=0;j<dds.length;j++){dds[j].open=true;await sleep(150);
   var p=dds[j].querySelector('.dd-panel').getBoundingClientRect();
   out.openScroll.push([d.documentElement.scrollWidth,Math.round(p.left),Math.round(p.right)]);dds[j].open=false;}
 fetch('/m',{method:'POST',body:JSON.stringify(out)});})();});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_projects_page_fits_a_390_px_phone_with_nothing_under_12_px(store):
    """Protects: the filtered Projects page at phone width, a ticket peek open and each dropdown opened in turn: no sideways scroll,
    nothing in the page under 12 px (James's floor, 2026-11-08)."""
    _fx(store)
    _profile(store, "temujin", "Temujin, a long project title that has to wrap on a narrow phone screen without going sideways", "Personal")
    peek = next(t for t in store.tickets() if t.meta.get("title") == "Animatic")
    url = f"/projects?assignee=temujin&tag=200-percent&p=temujin&t=pitch&k={peek.id}"
    got = drive(store, PHONE % repr(url), "/board")
    assert got["rows"] == 1 and got["dds"] == 4, got
    assert got["scrollW"] <= got["w"] and got["wide"] == [] and got["small"] == [], got
    assert all(sw <= got["w"] and left >= 0 and right <= got["w"] + 1 for sw, left, right in got["openScroll"]), got
