"""DESK-0261 hotfix (decision 110: nobody acts as anyone): the Act as menu, its route and its cookie are gone from the build installed now.
Protects: the store owner's browser (which coworker threads drive) stamping a post as another person through the menu or a leftover
desk_act_as cookie, and the menu printing private people. The full identity work stays in DESK-0261's own pull request."""

import re
from pathlib import Path

from fastapi.testclient import TestClient

from desk import identity, ops, people
from desk.ui.app import create_app

GOOD = {"project": "desk", "type": "task", "priority": "p1", "size": "xs", "owner": "main", "acceptance": "One line"}

JSON = {"Accept": "application/json"}
BASE = Path(ops.__file__).parent / "ui" / "templates" / "base.html"


def _owner_browser(store, monkeypatch):
    """A browser on the owner's machine: confirmed as the store owner, the one identity that used to be allowed to Act as."""
    monkeypatch.setattr(identity, "os_user", lambda: "jeby")
    people.link_os_user(store.home, "james", "jeby")
    return TestClient(create_app(store.home))


def test_the_act_as_route_is_gone(store, monkeypatch):
    c = _owner_browser(store, monkeypatch)
    r = c.post("/act-as", data={"token": c.app.state.tokens.issue(), "person": "chaz"}, follow_redirects=False)
    assert r.status_code in (404, 405) and "desk_act_as" not in r.headers.get("set-cookie", "")


def test_no_page_and_not_the_base_template_carries_an_act_as_form_or_text(store, monkeypatch):
    t = ops.new(store, "Talk", "jre")
    c = _owner_browser(store, monkeypatch)
    for url in ("/", "/board", "/whoami", f"/ticket/{t.id}"):
        page = c.get(url).text
        assert not re.search(r"act[ -]?as|actas|/act-as", page, re.I), url
    assert not re.search(r"act[ -]?as|actas|/act-as|show_private", BASE.read_text(encoding="utf-8"), re.I)


def test_a_leftover_desk_act_as_cookie_is_ignored_and_the_real_poster_is_stamped(store, monkeypatch):
    t = ops.new(store, "Talk", "jre")
    c = _owner_browser(store, monkeypatch)
    c.cookies.set("desk_act_as", "chaz")
    r = c.post(f"/ticket/{t.id}/comment", data={"token": c.app.state.tokens.issue(), "text": "Cookie says Chaz"}, headers=JSON)
    assert r.status_code == 200
    (line,) = store.get(t.id).section("Comments").splitlines()
    info = ops.comment_identity(line.split(" · ", 2)[2])
    assert "· james ·" in line and "(as" not in line and "as" not in info and info["ok"] == "1"
    assert identity.read_log(store.home, t.id)[-1]["as"] == ""
    c.post("/new", data={**GOOD, "title": "Made by the owner", "token": c.app.state.tokens.issue()}, follow_redirects=False)
    made = [x for x in store.tickets() if x.meta.get("title") == "Made by the owner"]
    assert made and made[0].meta["creator"] == "james"  # the cookie does not change who created a ticket either
