"""DESK-0148: Previous and Next between tickets, with an order picker remembered per person. One function over the tickets takes
(scope, order, current id) and returns the neighbours, the position and the total; the page and `drew next --after` use it."""

import pytest
from browser_harness import browser, drive
from fastapi.testclient import TestClient

from desk import nav, ops, style
from desk.cli import main
from desk.ui.app import create_app

JSON = {"Accept": "application/json"}


def _t(store, title, project="jre", created="2026-11-01", priority="p2", track="", review_by="", sprint=""):
    t = ops.new(store, title, project)
    t.meta.update(created=created, priority=priority)
    if track:
        t.meta["track"] = track
    if review_by:
        t.meta["review_by"] = review_by
    if sprint:
        t.meta["sprint"] = sprint
    store.save(t, touch=False)
    return t.id


@pytest.fixture
def world(store):
    """jre: a (01, p2, track X), b (02, p0, track Y), c (03, p3, track X, review 11-10), d (04, p1, no track, review 11-05).
    desk: e (02, p0, track X, sprint S1), f (05, p2, track X, sprint S1)."""
    ids = dict(
        a=_t(store, "A", created="2026-11-01", priority="p2", track="X"),
        b=_t(store, "B", created="2026-11-02", priority="p0", track="Y"),
        c=_t(store, "C", created="2026-11-03", priority="p3", track="X", review_by="2026-11-10"),
        d=_t(store, "D", created="2026-11-04", priority="p1", review_by="2026-11-05"),
        e=_t(store, "E", project="desk", created="2026-11-02", priority="p0", track="X", sprint="S1"),
        f=_t(store, "F", project="desk", created="2026-11-05", priority="p2", track="X", sprint="S1"),
    )
    return ids


def _seq(store, current, scope, order):
    n = nav.neighbours(store.tickets(), current, scope, order)
    return n


def _walk(store, start, scope, order):
    """Follow Next from the first ticket to the end, returning the ids in order."""
    first = start
    while (p := _seq(store, first, scope, order).prev):
        first = p
    out, cur = [first], first
    while (n := _seq(store, cur, scope, order).next):
        out.append(n)
        cur = n
    return out


def test_project_by_date_is_newest_first_and_never_skips_or_wraps(store, world):
    w = world
    assert _walk(store, w["a"], "project", "date") == [w["d"], w["c"], w["b"], w["a"]]
    first = _seq(store, w["d"], "project", "date")
    assert first.prev is None and first.next == w["c"] and first.index == 1 and first.total == 4
    assert "first ticket" in first.prev_note and first.next_note == ""
    last = _seq(store, w["a"], "project", "date")
    assert last.next is None and last.prev == w["b"] and last.index == 4 and "last ticket" in last.next_note


def test_priority_and_review_orders(store, world):
    w = world
    assert _walk(store, w["a"], "project", "priority") == [w["b"], w["d"], w["a"], w["c"]]  # p0 p1 p2 p3
    assert _walk(store, w["a"], "project", "review") == [w["d"], w["c"], w["b"], w["a"]]  # soonest review first, none last (newest first among them)
    assert _seq(store, w["a"], "project", "priority").text == "3 of 4 in jre, by priority"


def test_track_scope_crosses_projects_company_scope_is_everything_sprint_scope_needs_a_sprint(store, world):
    w = world
    assert set(_walk(store, w["a"], "track", "date")) == {w["a"], w["c"], w["e"], w["f"]}  # track X is in jre and desk
    assert _seq(store, w["a"], "track", "date").scope_name == "X"
    assert len(_walk(store, w["a"], "company", "date")) == 6
    assert _seq(store, w["a"], "company", "date").text.endswith("in the company, by date created")
    assert _walk(store, w["e"], "sprint", "date") == [w["f"], w["e"]]
    assert _walk(store, w["a"], "sprint", "date") == [w["a"]]  # no sprint: the walk is this ticket alone
    assert _seq(store, w["a"], "sprint", "date").text == "1 of 1 in no sprint, by date created"
    assert _walk(store, w["d"], "track", "date") == [w["d"]]  # untracked, only one in jre


def test_unknown_scope_order_or_ticket_is_refused(store, world):
    with pytest.raises(ValueError):
        nav.neighbours(store.tickets(), world["a"], "galaxy", "date")
    with pytest.raises(ValueError):
        nav.neighbours(store.tickets(), world["a"], "project", "mood")
    with pytest.raises(KeyError):
        nav.neighbours(store.tickets(), "JRE-9999")


def test_the_choice_is_kept_per_person_beside_the_style(store):
    assert style.load_nav(store.home) == ("project", "date")
    style.save_nav(store.home, "track", "priority")
    assert style.load_nav(store.home) == ("track", "priority")
    style.save(store.home, "dogs", "fuzz")  # saving the style must not forget the walk
    assert style.load_nav(store.home) == ("track", "priority") and style.load(store.home)["name"] == "dogs"
    style.save_nav(store.home, "project", "date")
    assert style.load(store.home)["name"] == "dogs" and "[nav]" not in (store.home / ".desk" / "settings.toml").read_text()
    with pytest.raises(ValueError):
        style.save_nav(store.home, "x", "date")


def test_page_has_real_links_the_position_and_the_picker(store, world):
    w = world
    c = TestClient(create_app(store.home))
    page = c.get(f"/ticket/{w['b']}").text
    assert f'rel="next" href="/ticket/{w["a"]}"' in page and f'rel="prev" href="/ticket/{w["c"]}"' in page
    assert "3 of 4 in jre, by date created" in page
    assert 'name="scope"' in page and 'name="order"' in page and "By priority score" in page and "Across the company" in page and "Within this sprint" in page
    end = c.get(f"/ticket/{w['d']}").text
    assert 'rel="prev"' not in end and "first ticket in this order" in end
    assert '<script src="/static/nav.js?v=' in page


def test_picking_an_order_is_remembered_and_changes_the_links(store, world):
    w = world
    c = TestClient(create_app(store.home))
    r = c.post("/nav", data={"token": c.app.state.tokens.issue(), "scope": "track", "order": "priority", "back": w["a"]}, headers=JSON)
    assert r.status_code == 200 and r.json()["ok"]
    assert style.load_nav(store.home) == ("track", "priority")
    page = TestClient(create_app(store.home)).get(f"/ticket/{w['a']}").text  # a fresh app: the choice came from the file
    assert "of 4 in X, by priority" in page and '<option value="track" selected>' in page and '<option value="priority" selected>' in page
    bad = c.post("/nav", data={"token": c.app.state.tokens.issue(), "scope": "galaxy", "order": "date", "back": w["a"]}, headers=JSON)
    assert bad.status_code == 422 and bad.json()["error"] and bad.json()["token"]
    plain = c.post("/nav", data={"token": c.app.state.tokens.issue(), "scope": "company", "order": "date", "back": w["a"]}, follow_redirects=False)
    assert plain.status_code == 303 and plain.headers["location"] == f"/ticket/{w['a']}"
    assert style.load_nav(store.home) == ("company", "date")


def test_drew_next_after_prints_the_neighbour_in_the_saved_walk(store, world, capsys):
    w = world
    main(["--home", str(store.home), "next", "--after", w["b"]])
    out = capsys.readouterr().out
    assert w["a"] in out and "3 of 4 in jre, by date created" in out
    main(["--home", str(store.home), "next", "--after", w["a"], "--scope", "track", "--order", "priority"])
    assert "in X, by priority" in capsys.readouterr().out
    main(["--home", str(store.home), "next", "--after", w["a"], "--previous"])
    assert w["b"] in capsys.readouterr().out
    main(["--home", str(store.home), "next", "--after", w["a"]])
    assert "last ticket" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["--home", str(store.home), "next", "--after", "JRE-9999"])


KEYS = """<script>addEventListener('load',function(){
var step=sessionStorage.getItem('nav-step')||'0',fire=function(k){document.dispatchEvent(new KeyboardEvent('keydown',{key:k,bubbles:true}));};
if(step==='0'){sessionStorage.setItem('nav-step','1');sessionStorage.setItem('nav-first',location.pathname);setTimeout(function(){fire('j');},300);return;}
if(step==='1'){sessionStorage.setItem('nav-step','2');sessionStorage.setItem('nav-second',location.pathname);setTimeout(function(){fire('ArrowLeft');},300);return;}
if(step==='2'){var out={first:sessionStorage.getItem('nav-first'),second:sessionStorage.getItem('nav-second'),third:location.pathname};
 /* typing in a field must not navigate */
 var ta=document.getElementById('comment-text');ta.focus();ta.dispatchEvent(new KeyboardEvent('keydown',{key:'j',bubbles:true}));
 setTimeout(function(){out.stayed=location.pathname===out.third;sessionStorage.setItem('nav-step','3');fetch('/m',{method:'POST',body:JSON.stringify(out)});},600);}
});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_keys_j_and_left_arrow_walk_the_list_and_typing_does_not(store, world):
    """Protects: j goes to Next, the left arrow to Previous, and a letter typed into the comment box goes to the box."""
    w = world
    got = drive(store, KEYS, f"/ticket/{w['c']}")
    assert got["first"] == f"/ticket/{w['c']}" and got["second"] == f"/ticket/{w['b']}" and got["third"] == f"/ticket/{w['c']}" and got["stayed"], got
