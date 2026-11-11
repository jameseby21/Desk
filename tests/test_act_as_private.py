"""DESK-0254: the Act as menu hides private people (rule 27: the people file outside the repo) until "Show private people" is ticked, and
then lists them by business key, never by name. The file here is synthetic and lives in a temp folder; the real one is never read.
Protects: a screenshot of any Desk screen showing a private person's real name."""

import re

from fastapi.testclient import TestClient

from desk import identity, people
from desk.ui.app import create_app

PRIVATE_NAME = "Quentin Zzyzx-Fictional"
PRIVATE = f"people:\n  - key: family-investor\n    name: {PRIVATE_NAME}\n    role: Family investor\n    kind: external\n"


def _client(store, tmp_path, monkeypatch):
    priv = tmp_path / "priv" / "people.yaml"
    priv.parent.mkdir()
    priv.write_text(PRIVATE, encoding="utf-8")
    monkeypatch.setenv("DESK_PRIVATE_PEOPLE", str(priv))
    monkeypatch.setattr(identity, "os_user", lambda: "jeby")
    people.link_os_user(store.home, "james", "jeby")
    c = TestClient(create_app(store.home))
    assert people.load(store.home).get("family-investor").private  # the synthetic file is loaded as private
    return c


def _menu(html):
    return re.search(r'<form class="actas".*?</form>', html, re.S).group(0)


def test_private_people_are_hidden_from_act_as_until_the_toggle_is_on(store, tmp_path, monkeypatch):
    c = _client(store, tmp_path, monkeypatch)
    menu = _menu(c.get("/").text)
    assert "family-investor" not in menu and PRIVATE_NAME not in menu and "Family investor" not in menu
    assert 'name="show_private"' in menu and " checked" not in menu  # the toggle is there and off


def test_with_the_toggle_on_private_people_show_by_key_never_by_name(store, tmp_path, monkeypatch):
    c = _client(store, tmp_path, monkeypatch)
    r = c.post("/act-as", data={"token": c.app.state.tokens.issue(), "person": "", "show_private": "1", "back": "/"}, follow_redirects=False)
    assert r.status_code == 303 and "desk_show_private=1" in r.headers["set-cookie"]
    page = c.get("/").text
    menu = _menu(page)
    assert '<option value="family-investor">family-investor</option>' in menu and "checked" in menu
    assert PRIVATE_NAME not in page and "Family investor" not in menu


def test_untick_hides_them_again_and_a_private_person_cannot_be_picked_while_hidden(store, tmp_path, monkeypatch):
    c = _client(store, tmp_path, monkeypatch)
    c.post("/act-as", data={"token": c.app.state.tokens.issue(), "show_private": "1"}, follow_redirects=False)
    c.post("/act-as", data={"token": c.app.state.tokens.issue(), "person": ""}, follow_redirects=False)  # box unticked
    assert "family-investor" not in _menu(c.get("/").text)
    r = c.post("/act-as", data={"token": c.app.state.tokens.issue(), "person": "family-investor"}, follow_redirects=False)
    assert "desk_act_as=family-investor" not in r.headers.get("set-cookie", "")  # refused while hidden
    assert PRIVATE_NAME not in c.get("/").text


def test_team_people_still_show_by_name_whatever_the_toggle(store, tmp_path, monkeypatch):
    c = _client(store, tmp_path, monkeypatch)
    assert "Chaz" in _menu(c.get("/").text)
