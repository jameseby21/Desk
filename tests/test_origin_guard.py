"""Desk answers only pages opened from this computer (standards rule 80 item 10, decision 110; found in the DESK-0290 review).

Protects: a page on another site cannot read one-time tokens through a renamed host (DNS rebinding) and cannot post a decision,
an assignment or a lane change that would be recorded as the owner."""

from fastapi.testclient import TestClient

from desk import ops
from desk.ui.app import create_app

JSON = {"Accept": "application/json"}


def _review(store):
    t = ops.new(store, "Ship the thing", "desk")
    t.meta["status"] = "review"
    t.set_section("Report", "Built and checked; the tests pass.")
    store.save(t)
    return t


def _approve(c, t):
    a = {x["key"]: x for x in c.get(f"/palette/actions/{t.id}").json()["actions"]}["approve"]
    return a["url"], {**a["fields"], a["words_field"]: "approved by a foreign page"}


def test_a_page_on_another_host_gets_no_tokens_and_cannot_approve(store):
    """A request whose Host is not this computer (a rebound domain) is refused before any route: no tokens, no page, no write."""
    t = _review(store)
    app = create_app(store.home)
    url, fields = _approve(TestClient(app, base_url="http://127.0.0.1:8766"), t)
    evil = TestClient(app, base_url="http://evil.example:8766")
    r = evil.get(f"/palette/actions/{t.id}", headers=JSON)
    assert r.status_code == 403 and "token" not in r.text and "this computer" in r.json()["error"]
    assert evil.get(f"/ticket/{t.id}").status_code == 403
    r = evil.post(url, data=fields, headers={**JSON, "Origin": "http://evil.example:8766"})
    assert r.status_code == 403
    assert store.get(t.id).status == "review" and "foreign page" not in store.get(t.id).section("Decision")


def test_a_cross_site_post_to_this_computer_changes_nothing(store):
    """A post that names this computer as Host but comes from another origin (or says cross-site, or a null origin) changes nothing."""
    t = _review(store)
    app = create_app(store.home)
    home = TestClient(app, base_url="http://127.0.0.1:8766")
    url, fields = _approve(home, t)
    for extra in ({"Origin": "http://evil.example"}, {"Origin": "http://127.0.0.1:9999"}, {"Origin": "null"},
                  {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}):
        r = home.post(url, data=fields, headers={**JSON, **extra})
        assert r.status_code == 403, (extra, r.text)
    assert store.get(t.id).status == "review"
    r = home.post(url, data=fields, headers={**JSON, "Origin": "http://127.0.0.1:8766", "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200 and store.get(t.id).status == "approved"  # the page's own post still goes through, token unspent


def test_this_computer_and_the_test_client_are_served(store):
    """127.0.0.1, localhost and [::1] on any port are served as before, and so is the test client's own host name."""
    app = create_app(store.home)
    for base in ("http://127.0.0.1:8766", "http://localhost:8766", "http://[::1]:8766", "http://testserver"):
        assert TestClient(app, base_url=base).get("/").status_code == 200, base
    page = TestClient(app, base_url="http://localhost:8766").get("/", headers={"Origin": "http://evil.example"})
    assert page.status_code == 200  # a plain read with a foreign Origin header reveals nothing a browser lets that page read
