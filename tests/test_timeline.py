"""DESK-0171: the ticket timeline. Protects: the latest change on one line at the top of the ticket page; Expand all events lists
every event newest first; filters narrow by actor, kind, source, project, company, track, sprint and field with counts that match;
colours come from people.yaml for people and a fixed palette otherwise, with a legend; `drew history` prints the same; at 390 px the
page never scrolls sideways."""

import re
from contextlib import contextmanager
from pathlib import Path

import pytest
from browser_harness import browser, run_in_browser
from fastapi.testclient import TestClient

from desk import activity, identity, ops, people, timeline
from desk.cli import main
from desk.ui.app import create_app


@contextmanager
def acting(name, kind="person", source="cli"):
    token = identity.CURRENT.set(identity.Actor(name, kind, "", source, "claimed", False))
    try:
        yield
    finally:
        identity.CURRENT.reset(token)


def _life(store):
    """A ticket with a small life: James creates it on the page, Patch triages and comments, Chaz comments, Patch sets its track."""
    with acting("james", "person", "ui"):
        t = ops.new(store, "A ticket with a life", "desk")
    with acting("patch", "session"):
        ops.move(store, store.get(t.id), "triage")
        ops.comment(store, store.get(t.id), "Looked at it")
    with acting("chaz", "thread"):
        ops.comment(store, store.get(t.id), "Boards posted for this")
    with acting("patch", "session"):
        x = store.get(t.id)
        x.meta["track"] = "First real use"
        store.save(x)
    return store.get(t.id)


def test_view_lists_every_event_newest_first_and_the_latest_is_the_newest(store):
    t = _life(store)
    v = timeline.view(store, t)
    assert len(v.rows) == len(activity.read(store.home, t.id)) >= 5
    keys = [(r.event.ts, r.event.seq) for r in v.rows]
    assert keys == sorted(keys, reverse=True)
    assert v.latest is v.rows[0] and v.latest.what == "track: empty → First real use"
    assert timeline.latest_line(v.latest).startswith("Patch (Main session) · track: empty → First real use · ")
    assert v.rows[-1].event.change == "created" and v.rows[-1].actor_name == "James" and v.rows[-1].kind_label == "Owner"


def test_dimensions_are_as_they_were_when_the_event_happened(store):
    t = _life(store)
    v = timeline.view(store, t)
    assert v.rows[0].dims["track"] == "First real use"  # the change that set it
    assert {r.dims["track"] for r in v.rows[1:]} == {timeline.NONE}  # before it, no track
    assert {r.dims["project"] for r in v.rows} == {"desk"} and all(r.dims["company"] for r in v.rows)


def test_filters_narrow_multi_select_and_counts_match(store):
    t = _life(store)
    v = timeline.view(store, t)
    facets = {f.dim: {val: n for val, _lab, n, _c in f.values} for f in v.facets}
    assert set(facets) == set(timeline.DIMENSIONS)
    for dim, counts in facets.items():
        assert sum(counts.values()) == len(v.rows), dim
        for val, n in counts.items():
            assert len(timeline.view(store, t, {dim: {val.lower()}}).shown) == n, (dim, val)
    both = timeline.view(store, t, {"actor": {"james", "chaz"}})
    assert len(both.shown) == facets["actor"]["james"] + facets["actor"]["chaz"]
    anded = timeline.view(store, t, {"actor": {"main"}, "field": {"comment"}})
    assert [r.what for r in anded.shown] == ["Looked at it"]
    by_label = timeline.view(store, t, {"kind": {"coworker"}})  # the label a person reads works as well as the key
    assert [r.actor_name for r in by_label.shown] == ["Chaz"]


def test_colours_people_from_people_yaml_kinds_and_the_rest_from_a_fixed_palette(store):
    t = _life(store)
    v = timeline.view(store, t)
    pe = people.People(people.SEED)
    for r in v.rows:
        assert r.colours["actor"] == pe.colour(r.dims["actor"])
        assert r.colours["kind"] == timeline.KIND_COLOURS[r.dims["kind"]]
        assert r.colours["field"] in timeline.PALETTE
    kinds = {r.dims["kind"]: r.colours["kind"] for r in v.rows}
    assert len(set(kinds.values())) == len(kinds) >= 3  # people and sessions in different tints


def test_ticket_page_shows_the_latest_line_on_top_and_every_event_under_expand(store):
    t = _life(store)
    page = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    latest = re.search(r'<p class="tl-latest" id="tl-latest".*?</p>', page, re.S).group(0)
    assert "Patch" in latest and "track: empty → First real use" in latest and "Latest change" in latest
    assert page.index('id="tl-latest"') < page.index('<div class="cols">')  # at the top, above the sections
    assert "Expand all events (5)" in page and page.count('<li class="tl-ev"') == 5
    for dim in timeline.DIMENSIONS:
        assert f'<details class="tl-f" data-dim="{dim}">' in page
    assert 'id="tl-legend"' in page and 'id="tl-by"' in page
    assert '<span class="tl-count">2</span>' in page  # counts beside the options (two comments)


def test_a_comment_posted_on_the_page_becomes_the_latest_change_with_source_ui(store):
    """End to end through the real route: the page's own write is logged as on the page, and the next render leads with it."""
    t = ops.new(store, "Posted on the page", "desk")
    client = TestClient(create_app(store.home))
    client.post(f"/ticket/{t.id}/comment", data={"token": client.app.state.tokens.issue(), "text": "Why is this like that?"})
    e = activity.read(store.home, t.id)[-1]
    assert (e.change, e.source, e.what) == ("comment", "ui", "Why is this like that?"), e
    latest = re.search(r'<p class="tl-latest" id="tl-latest".*?</p>', client.get(f"/ticket/{t.id}").text, re.S).group(0)
    assert "Why is this like that?" in latest and "on the page" not in latest  # the latest line is who, what, when


def test_ticket_with_no_events_says_so_plainly(store):
    t = ops.new(store, "Quiet", "desk")
    activity.path(store.home, t.id).unlink()
    page = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    assert "No changes recorded yet." in page and "Expand all events" not in page


def test_drew_history_prints_the_same_with_by_and_filter(store, capsys):
    t = _life(store)
    main(["--home", str(store.home), "history", t.id])
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[1] == "Latest change: " + timeline.latest_line(timeline.view(store, t).latest)
    assert "5 of 5 events, newest first:" in out and out.index("track: empty") < out.index("Created: A ticket with a life")
    main(["--home", str(store.home), "history", t.id, "--by", "kind"])
    out = capsys.readouterr().out
    assert "Kind: Main session (3)" in out and "Kind: Owner (1)" in out and "Kind: Coworker (1)" in out
    main(["--home", str(store.home), "history", t.id, "--filter", "actor=chaz"])
    out = capsys.readouterr().out
    assert "1 of 5 events" in out and "Boards posted for this" in out and "Looked at it" not in out
    main(["--home", str(store.home), "history", t.id, "--filter", "field=comment", "--json"])
    assert '"what": "Looked at it"' in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["--home", str(store.home), "history", t.id, "--filter", "colour=red"])
    assert "is not a filter" in capsys.readouterr().err


def test_a_git_user_listed_later_in_people_yaml_relabels_old_events_without_touching_the_log(store):
    t = ops.new(store, "Git user", "desk")
    activity.append(store.home, [activity.Event(t.id, "2026-11-01T10:00:00", "jeby", "person", "git", "git", "git", "", "abc", "Saved in git: x", key="git:abc")])
    before = activity.path(store.home, t.id).read_text(encoding="utf-8")
    assert timeline.view(store, t).rows[-1].actor_name == "jeby"
    people.link_os_user(store.home, "james", "jeby")
    r = timeline.view(store, t).rows[-1]
    assert (r.actor_name, r.dims["actor"], r.dims["kind"], r.kind_label) == ("James", "james", "owner", "Owner")
    assert activity.path(store.home, t.id).read_text(encoding="utf-8") == before


def test_old_tickets_have_a_timeline_after_the_migration(store):
    t = ops.new(store, "Old", "desk")
    x = store.get(t.id)
    x.set_section("Comments", "- 2026-11-01 · james · First words\n- 2026-11-02 · patch · Answer\n")
    x.write()
    activity.path(store.home, t.id).unlink()
    assert timeline.view(store, t).rows == []
    activity.migrate(store, apply=True)
    v = timeline.view(store, t)
    assert [r.what for r in v.rows] == ["Answer", "First words"] and v.latest.source_label == "from old comments"


STATIC = Path(__file__).resolve().parents[1] / "src" / "desk" / "ui" / "static"
MEASURE = """<script>addEventListener('load',function(){setTimeout(function(){
var w=document.documentElement.clientWidth,over=[],all=document.getElementById('tl-all');all.open=true;
document.querySelectorAll('.tl-f').forEach(function(d){d.open=true;});
document.querySelectorAll('#timeline *').forEach(function(e){var r=e.getBoundingClientRect();if(r.width&&r.right>w+1)over.push(e.className+' '+Math.round(r.right));});
var lat=document.getElementById('tl-latest'),lh=parseFloat(getComputedStyle(lat).lineHeight)||20;
var small=[];document.querySelectorAll('#timeline *').forEach(function(e){if(e.childNodes.length&&e.textContent.trim()&&parseFloat(getComputedStyle(e).fontSize)<12)small.push(e.className||e.tagName);});
var box=document.querySelector('.tl-f[data-dim=actor] input[value=james]');box.click();
var vis=Array.prototype.filter.call(document.querySelectorAll('.tl-ev'),function(li){return !li.hidden;}).length;
document.getElementById('tl-by').value='actor';document.getElementById('tl-by').dispatchEvent(new Event('change'));
fetch('/m',{method:'POST',body:JSON.stringify({scroll:document.documentElement.scrollWidth,inner:w,over:over,latestH:lat.getBoundingClientRect().height,lh:lh,
 small:small,vis:vis,shown:document.getElementById('tl-shown').textContent,legend:document.getElementById('tl-legend').textContent,
 events:document.querySelectorAll('.tl-ev').length})});},300);});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
@pytest.mark.parametrize("width", [1280, 390])
def test_timeline_in_a_real_browser_no_sideways_scroll_one_line_latest_and_filters_work(store, width):
    """Protects: at 1280 px and 390 px the expanded timeline never makes the page scroll sideways, the latest change stays one line,
    no text in it is under 12 px, ticking a person narrows the list and Colour by rebuilds the legend."""
    t = _life(store)
    with acting("patch", "session"):
        ops.comment(store, store.get(t.id), "A very long comment " + "that goes on and on without a break " * 12)
    html = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    css = (STATIC / "desk.css").read_text(encoding="utf-8")
    js = (STATIC / "timeline.js").read_text(encoding="utf-8")
    html = html.replace('<link rel="stylesheet" href="/static/desk.css">', f"<style>{css}</style>")
    html = re.sub(r'<link rel="stylesheet" href="https://fonts[^>]*>|<link rel="preconnect"[^>]*>', "", html)
    html = re.sub(r'<script src="[^"]*"[^>]*></script>', "", html).replace("</body>", f"<script>{js}</script>" + MEASURE + "</body>")
    data = run_in_browser(html, width)
    assert data["events"] == 6 and data["over"] == [] and data["scroll"] <= data["inner"], data
    assert data["latestH"] < 2.2 * data["lh"] + 20, data  # one line, padding included
    assert data["small"] == [], data
    assert data["vis"] == 1 and data["shown"] == "Showing 1 of 6 events, newest first.", data
    assert data["legend"] == "James 1", data
