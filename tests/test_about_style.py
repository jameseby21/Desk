"""About page, style settings, mascots, served CSS."""

from fastapi.testclient import TestClient

from desk import style
from desk.ui.app import create_app


def client(store):
    return TestClient(create_app(store.home))


def test_about_renders_why(store, monkeypatch):
    from pathlib import Path

    monkeypatch.setenv("DESK_REPO", str(Path(__file__).resolve().parents[1]))  # WHY.md is read from the repo, not copied
    r = client(store).get("/about")
    assert r.status_code == 200 and "Ticket tools count cards" in r.text
    assert "<title>Why Desk</title>" in r.text and 'href="/about"' in r.text


def test_settings_round_trip(store):
    c = client(store)
    assert style.load(store.home)["name"] == "plain"
    assert 'value="plain" checked' in c.get("/settings").text
    r = c.post("/settings", data={"name": "dogs", "dog": "fuzz"}, follow_redirects=False)
    assert r.status_code == 303
    assert (store.home / ".desk" / "settings.toml").read_text(encoding="utf-8") == '[style]\nname = "dogs"\ndog = "fuzz"\nprofanity_filter = true\n\n[ui]\nautorefresh = true\n'
    page = c.get("/settings").text
    assert 'value="dogs" checked' in page and 'value="fuzz" checked' in page
    assert c.post("/settings", data={"name": "bogus"}).status_code == 400


def test_mascot_only_in_dogs_and_cat(store):
    c = client(store)
    assert "/static/mascots/" not in c.get("/").text
    c.post("/settings", data={"name": "dogs", "dog": "drew"})
    page = c.get("/").text
    assert "/static/mascots/drew-light.svg" in page and "/static/mascots/drew-dark.svg" in page
    c.post("/settings", data={"name": "cat", "dog": "drew"})
    assert "/static/mascots/soot-light.svg" in c.get("/").text
    assert c.get("/static/mascots/soot-light.svg").status_code == 200


def test_css_served_with_tokens(store):
    r = client(store).get("/static/desk.css")
    assert r.status_code == 200
    for token in ("--paper:#F5F3EC", "--ball:#C8F03C", "--red:#D7262E", "Bricolage Grotesque", "prefers-reduced-motion"):
        assert token in r.text
    assert 'href="/static/desk.css"' in client(store).get("/").text


def test_about_without_repo_says_where_to_look(store, monkeypatch):
    monkeypatch.delenv("DESK_REPO", raising=False)
    from desk.ui import app as appmod

    monkeypatch.setattr(appmod, "CONTENT", store.home / "no-such-dir")
    assert "set DESK_REPO" in client(store).get("/about").text
