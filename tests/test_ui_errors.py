"""Errors file themselves: the bug page, /bugs/file, and restart detection."""

import re

from fastapi.testclient import TestClient

from desk import buildstamp, ops
from desk.ui import app as app_mod
from desk.ui.app import create_app


def _client(store):
    app = create_app(store.home)

    @app.get("/boom/{n}")
    def boom(n: int):
        raise ValueError(f"kaboom {n}")

    return TestClient(app, raise_server_exceptions=False)


def _file(c, path):
    page = c.get(path)
    assert page.status_code == 500 and "Desk hit a bug" in page.text and "ValueError" in page.text and "kaboom" in page.text
    eid = re.search(r'name="eid" value="([^"]+)"', page.text).group(1)
    assert 'action="/bugs/file"' in page.text and "File as a bug" in page.text
    return c.post("/bugs/file", data={"eid": eid}, follow_redirects=False)


def test_error_page_files_one_bug_and_repeats_add_a_comment(store):
    c = _client(store)
    r = _file(c, "/boom/1")
    assert r.status_code == 303
    t = store.get(r.headers["location"].rsplit("/", 1)[1])
    assert (t.project, t.meta["type"], t.meta["owner"], t.tags) == ("desk", "bug", "main", ["ui-error"])
    assert t.meta["title"] == "UI error: ValueError on /boom/1" and len(t.meta["title"]) <= 60
    assert "Route: GET /boom/1" in t.section("Context") and "Traceback summary" in t.section("Context")
    assert t.section("How to validate") == "open /boom/1; no error"
    # same type and route again: a Comments line, not a second ticket
    r2 = _file(c, "/boom/1")
    assert r2.headers["location"] == r.headers["location"]
    again = store.get(t.id)
    assert "seen again" in again.section("Comments") and len(store.tickets()) == 1
    # another route is another ticket
    assert _file(c, "/boom/2").headers["location"] != r.headers["location"] and len(store.tickets()) == 2


def test_long_route_title_is_truncated_and_unknown_eid_refused(store):
    c = _client(store)
    r = _file(c, "/boom/" + "9" * 80)
    assert len(store.get(r.headers["location"].rsplit("/", 1)[1]).meta["title"]) == 60
    assert c.post("/bugs/file", data={"eid": "nope"}).status_code == 410


def test_stale_build_shows_yellow_bar_and_not_a_bug_report(store, tmp_path, monkeypatch):
    t = ops.new(store, "Any", "jre")
    stamp_dir = tmp_path / "templates"
    stamp_dir.mkdir()
    monkeypatch.setattr(app_mod, "STAMP_DIR", stamp_dir)
    monkeypatch.setattr(buildstamp, "RUNNING", "aaaaaaa")
    c = _client(store)
    (stamp_dir / ".build-stamp").write_text("aaaaaaa\n", encoding="utf-8")
    assert "restart drew-ui" not in c.get(f"/ticket/{t.id}").text  # same stamp: normal page
    (stamp_dir / ".build-stamp").write_text("bbbbbbb\n", encoding="utf-8")
    page = c.get(f"/ticket/{t.id}")
    assert page.status_code == 200 and "Desk was updated on disk; restart drew-ui to load it" in page.text and 'class="stale-bar"' in page.text
    crash = c.get("/boom/1")  # a page that fails because new files met old code: the bar, not a 500 bug report
    assert crash.status_code == 503 and "restart drew-ui" in crash.text and "File as a bug" not in crash.text
    monkeypatch.setattr(buildstamp, "RUNNING", "")  # a checkout carries no stamp: never stale
    assert "restart drew-ui" not in c.get(f"/ticket/{t.id}").text
