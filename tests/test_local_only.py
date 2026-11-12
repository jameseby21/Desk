"""DESK-0074: Desk is a local tool. Protects: a page on another site cannot make the owner's browser change tickets (cross-site Origin/Referer
refused), a GET never changes the store, and the server binds to this computer unless the owner asks otherwise, with a warning when they do."""

import hashlib

import pytest
from fastapi.testclient import TestClient

from desk import ops
from desk.ui import app as appmod
from desk.ui.app import create_app


def _post(c, tid, **headers):
    return c.post(f"/ticket/{tid}/comment", data={"body": "hello"}, headers={"Accept": "application/json", **headers}, follow_redirects=False)


@pytest.fixture
def client(store):
    t = ops.new(store, "Talk", "jre")
    return TestClient(create_app(store.home), base_url="http://127.0.0.1:8765"), t.id


@pytest.mark.parametrize("hdr", [{"Origin": "https://evil.example"}, {"Origin": "http://127.0.0.1.evil.example:8765"}, {"Referer": "https://evil.example/page"}, {"Origin": "null"}])
def test_cross_site_post_is_refused(client, hdr):
    c, tid = client
    r = _post(c, tid, **hdr)
    assert r.status_code == 403
    assert "another site" in r.text


@pytest.mark.parametrize("hdr", [{}, {"Origin": "http://127.0.0.1:8765"}, {"Origin": "http://localhost:8765"}, {"Referer": "http://localhost:8765/ticket/x"}])
def test_same_origin_or_no_origin_post_still_works(client, hdr):
    c, tid = client
    assert _post(c, tid, **hdr).status_code != 403


def test_cross_site_post_changes_nothing(client, store):
    c, tid = client
    before = _snapshot(store.home)
    _post(c, tid, Origin="https://evil.example")
    assert _snapshot(store.home) == before


def _snapshot(home):
    h = hashlib.sha256()
    for p in sorted(home.rglob("*")):
        if p.is_file() and ".git" not in p.parts and ".desk" not in p.parts:  # .desk holds the rebuildable search index, a cache, not store data
            h.update(str(p.relative_to(home)).encode() + p.read_bytes())
    return h.hexdigest()


def test_get_requests_change_nothing(store):
    t = ops.new(store, "Talk", "jre")
    app = create_app(store.home)
    c = TestClient(app, base_url="http://127.0.0.1:8765")
    paths = []
    for r in app.routes:
        if "GET" in getattr(r, "methods", ()) and not r.path.startswith(("/static", "/events", "/openapi", "/docs", "/redoc")):
            p = r.path
            if "{" in p:
                if p.count("{") == 1 and "ticket_id" in p:
                    p = p.replace("{ticket_id}", t.id)
                elif p.count("{") == 1 and "project" in p:
                    p = p.replace("{project}", "jre").replace("{key}", "jre")
                else:
                    continue
            paths.append(p)
    assert paths
    before = _snapshot(store.home)
    for p in paths:
        c.get(p)
    assert _snapshot(store.home) == before, "a GET changed the store"


def test_default_bind_is_localhost_only(monkeypatch):
    monkeypatch.delenv("DESK_UI_HOST", raising=False)
    assert appmod.bind_address([]) == ("127.0.0.1", None)


@pytest.mark.parametrize("addr", ["127.0.0.1", "localhost", "::1"])
def test_loopback_choice_has_no_warning(addr, monkeypatch):
    monkeypatch.delenv("DESK_UI_HOST", raising=False)
    assert appmod.bind_address(["--host", addr]) == (addr, None)


def test_other_address_is_allowed_with_a_clear_warning(monkeypatch):
    monkeypatch.delenv("DESK_UI_HOST", raising=False)
    host, warn = appmod.bind_address(["--host", "0.0.0.0"])
    assert host == "0.0.0.0" and "WARNING" in warn and "no login" in warn
    monkeypatch.setenv("DESK_UI_HOST", "192.168.1.5")
    assert appmod.bind_address([])[0] == "192.168.1.5"


def test_dns_rebinding_post_is_refused(client):
    """An attacker domain re-pointed at 127.0.0.1 sends Host and Origin that agree; the allowlist, not the Host header, decides."""
    c, tid = client
    r = _post(c, tid, Host="evil.example:8765", Origin="http://evil.example:8765")
    assert r.status_code in (400, 403)


def test_get_for_a_foreign_host_is_refused(client):
    c, _ = client
    assert c.get("/", headers={"Host": "evil.example"}).status_code == 400


def test_owner_requests_still_pass(client):
    c, tid = client
    assert c.get("/", headers={"Host": "localhost:8765"}).status_code != 400
    assert _post(c, tid, Host="127.0.0.1:8765", Origin="http://127.0.0.1:8765").status_code not in (400, 403)
    assert _post(c, tid).status_code not in (400, 403)


def test_lan_bind_address_is_an_allowed_host(store, monkeypatch):
    monkeypatch.setenv("DESK_UI_HOST", "192.168.1.5")
    c = TestClient(create_app(store.home), base_url="http://192.168.1.5:8765")
    assert c.get("/").status_code != 400
    assert c.get("/", headers={"Host": "evil.example"}).status_code == 400
