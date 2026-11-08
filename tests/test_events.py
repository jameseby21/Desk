"""Server-sent events and auto-refresh."""

import os
import threading
import time

from fastapi.testclient import TestClient

from desk import ops, style
from desk.ui.app import create_app


def _touch_later(path, delay=0.4):
    def go():
        time.sleep(delay)
        os.utime(path, (time.time() + 5, time.time() + 5))
    threading.Thread(target=go, daemon=True).start()


def test_ticket_stream_emits_on_file_touch_and_on_asset_change(store):
    t = ops.new(store, "Watch me", "jre")
    c = TestClient(create_app(store.home, poll=0.1))
    _touch_later(t.path)
    r = c.get(f"/events/{t.id}?timeout=2.5")
    assert r.headers["content-type"].startswith("text/event-stream")
    assert "event: change" in r.text
    assert "event: change" not in c.get(f"/events/{t.id}?timeout=0.6").text
    folder = store.ticket_assets_dir(t.id)
    folder.mkdir(parents=True)
    img = folder / "a.png"
    img.write_bytes(b"x")
    _touch_later(img)
    assert "event: change" in c.get(f"/events/{t.id}?timeout=2.5").text


def test_directory_stream_and_since_catches_up(store):
    t = ops.new(store, "Another", "jre")
    c = TestClient(create_app(store.home, poll=0.1))
    _touch_later(t.path)
    assert "event: change" in c.get("/events?timeout=2.5").text
    assert "event: change" in c.get("/events?since=1&timeout=0.3").text  # the page was rendered before this change


def test_pages_subscribe_and_setting_switches_it_off(store):
    t = ops.new(store, "Page", "jre")
    c = TestClient(create_app(store.home))
    page = c.get(f"/ticket/{t.id}").text
    assert "new EventSource" in page and f"/events/{t.id}" in page
    for url in ("/", "/board", "/projects"):
        assert '"/events"' in c.get(url).text
    assert "new EventSource" not in c.get("/settings").text
    assert style.load(store.home)["autorefresh"] is True
    c.post("/settings", data={"name": "plain", "dog": "drew", "profanity": "on", "autorefresh": "off"})
    assert style.load(store.home)["autorefresh"] is False and "new EventSource" not in c.get(f"/ticket/{t.id}").text
    assert "autorefresh = false" in (store.home / ".desk" / "settings.toml").read_text(encoding="utf-8")
