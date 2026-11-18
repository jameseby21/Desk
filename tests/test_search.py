"""DESK-0113: search. Protects: any ticket is reachable by part of its id or title, with typos, with filters, grouped by project."""

import datetime as dt
import json

from fastapi.testclient import TestClient

from desk import ops, search
from desk.cli import main
from desk.ui.app import create_app


def _mk(store, title, project="desk", **meta):
    t = ops.new(store, title, project)
    t.meta.update(meta)
    t.write()
    return t


def _index(store):
    ix = search.Index(None, stale_days=store.stale_days)
    ix.refresh(store, force=True)
    return ix


def _ids(res):
    return [h.id for _, _, hs in res.groups for h in hs]


def test_id_and_prefix_match_inside_the_id(store):
    a = _mk(store, "Alpha schedule")
    b = _mk(store, "Beta", "jre")
    ix = _index(store)
    assert a.id in _ids(ix.search("DESK-000"))
    assert _ids(ix.search("desk-0001")) == [a.id]
    assert _ids(ix.search("JRE-0001")) == [b.id]
    assert a.id in _ids(ix.search("0001")) and b.id in _ids(ix.search("0001"))  # digits alone match mid-string


def test_partial_word_matches_mid_string(store):
    t = _mk(store, "Rescheduling the weekly review")
    ix = _index(store)
    assert _ids(ix.search("chedul")) == [t.id]
    assert _ids(ix.search("sc")) == [t.id]  # two letters: a plain contains


def test_typo_tolerance(store):
    t = _mk(store, "Scheduler reads every profile")
    _mk(store, "Something unrelated entirely")
    ix = _index(store)
    res = ix.search("shceduler")
    assert _ids(res) == [t.id] and res.fuzzy
    assert not ix.search("zzzzzzzz").total


def test_every_filter(store):
    a = _mk(store, "Alpha", status="ready", priority="p0", assignee="chaz", track="First real use", sprint="sprint 6 2026", tags=["ui", "x"], updated="2026-10-01")
    b = _mk(store, "Beta", "jre", status="review", priority="p2", assignee="main", owner="james", type="bug", updated=dt.date.today().isoformat())
    c = _mk(store, "Approve: gamma this", "jre", status="ready", type="decision", updated="2026-01-01")
    d = _mk(store, "Delta", status="done", updated=dt.date.today().isoformat())
    ix = _index(store)
    q = lambda s: set(_ids(ix.search(s)))  # noqa: E731
    assert q("project:jre") == {b.id, c.id}
    assert q("status:review") == {b.id}
    assert q("status:ready,review") == {a.id, b.id, c.id}
    assert q("type:bug") == {b.id}
    assert q("tag:ui") == {a.id} and q("tag:u") == set()
    assert q("assignee:chaz") == {a.id}
    assert q("owner:james") == {b.id}
    assert q("priority:p0") == {a.id}
    assert q("track:first") == {a.id}
    assert q('track:"first real use"') == {a.id}
    assert q("sprint:6") == {a.id}
    assert q("ask:approve") == {c.id}
    assert q("ask:review") == {b.id}
    assert q("is:stale") == {a.id, c.id}
    assert q("is:open") == {a.id, b.id, c.id}
    assert q("is:closed") == {d.id}
    assert q("before:2026-06-01") == {c.id}
    assert q("after:2026-09-01") == {a.id, b.id, d.id}
    assert q("-project:jre") == {a.id, d.id}
    assert q("project:jre gamma") == {c.id}


def test_unknown_field_is_plain_text(store):
    t = _mk(store, "Fix foo:bar parsing")
    assert _ids(_index(store).search("foo:bar")) == [t.id]


def test_results_group_by_project_with_counts_and_rank_by_priority(store):
    lo = _mk(store, "Search thing low", priority="p3")
    hi = _mk(store, "Search thing high", priority="p0")
    other = _mk(store, "Search over there", "jre")
    res = _index(store).search("search")
    assert [(p, n) for p, n, _ in res.groups] == [("desk", 2), ("jre", 1)]
    assert [h.id for h in res.groups[0][2]] == [hi.id, lo.id]
    assert res.total == 3 and other.id in _ids(res)


def test_exact_id_ranks_first(store):
    _mk(store, "Mentions DESK-0002 in the title")
    t2 = _mk(store, "The second")
    res = _index(store).search("DESK-0002")
    assert _ids(res)[0] == t2.id


def test_refresh_is_incremental_and_sees_edits_and_deletes(store):
    a = _mk(store, "First")
    ix = _index(store)
    assert ix.refresh(store, force=True) == 0
    a.meta["title"] = "Renamed zebra"
    a.write()
    assert ix.refresh(store, force=True) == 1 and _ids(ix.search("zebra")) == [a.id]
    a.path.unlink()
    assert ix.refresh(store, force=True) == 1 and not ix.search("zebra").total


def test_cli_search_and_json(store, capsys):
    t = _mk(store, "Scheduler reads every profile", assignee="chaz")
    main(["--home", str(store.home), "search", "schedul", "assignee:chaz"])
    out = capsys.readouterr().out
    assert t.id in out and "desk (1)" in out
    main(["--home", str(store.home), "search", "--json", "shceduler"])
    data = json.loads(capsys.readouterr().out)
    assert data["groups"][0]["hits"][0]["id"] == t.id and data["fuzzy"]
    assert (store.home / ".desk" / "search.sqlite").is_file()


def test_ui_search_json_page_and_palette_on_every_page(store):
    t = _mk(store, "Scheduler reads every profile")
    c = TestClient(create_app(store.home))
    r = c.get("/search.json", params={"q": "DESK-000"})
    assert r.status_code == 200 and r.json()["groups"][0]["hits"][0]["id"] == t.id
    page = c.get("/search", params={"q": "schedul"})
    assert page.status_code == 200 and t.id in page.text and "desk" in page.text
    assert "No ticket matches" in c.get("/search", params={"q": "qqqqqqqq"}).text
    for path in ("/", "/board", "/projects", f"/ticket/{t.id}", "/about", "/settings", "/search"):
        body = c.get(path).text
        assert "/static/search.js" in body and 'id="search-open"' in body, path
