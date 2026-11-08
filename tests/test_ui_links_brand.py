"""Ticket-id autolinks, clickable checkboxes, review-week actions, per-project brand."""

import pytest
from fastapi.testclient import TestClient

from desk import brand, ops, review as rv
from desk.cli import main
from desk.ui.app import create_app


def run(store, *argv):
    main(["--home", str(store.home), *argv])


def test_autolink_known_not_unknown(store):
    a = ops.new(store, "Target ticket", "jre")
    t = ops.new(store, "Source", "jre")
    t.set_section("Context", f"See {a.id} and also ZZZ-9999 here.")
    store.save(t)
    page = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    assert f'href="/ticket/{a.id}"' in page and 'title="Target ticket"' in page and 'data-status="inbox"' in page
    assert "ZZZ-9999" in page and 'href="/ticket/ZZZ-9999"' not in page


def test_toggle_changes_exactly_one_line(store):
    t = ops.new(store, "Boxes", "jre")
    t.set_section("Acceptance", "- [ ] one\n- [ ] two\n```\n- [ ] in fence\n```\n- [x] three")
    store.save(t)
    before = store.get(t.id).sections["Acceptance"].split("\n")
    client = TestClient(create_app(store.home))
    page = client.get(f"/ticket/{t.id}").text
    assert page.count(f'action="/ticket/{t.id}/check"') == 3
    r = client.post(f"/ticket/{t.id}/check", data={"section": "Acceptance", "index": 1, "checked": 1}, follow_redirects=False)
    assert r.status_code == 303 and "error" not in r.headers["location"]
    after = store.get(t.id).sections["Acceptance"].split("\n")
    diff = [i for i, (x, y) in enumerate(zip(before, after)) if x != y]
    assert len(before) == len(after) and diff == [1] and after[1] == "- [x] two"
    ops.toggle_check(store, t.id, "Acceptance", 2, False, "james")
    assert store.get(t.id).sections["Acceptance"].split("\n")[5] == "- [ ] three"
    with pytest.raises(ValueError):
        ops.toggle_check(store, t.id, "Acceptance", 9, True)


def test_review_sync_preserves_manual_check_and_ui_actions(store):
    (store.projects_dir / "management.md").write_text("# Management\n", encoding="utf-8")
    a = ops.new(store, "Choose: a thing", "desk", "decision", owner="james")
    store.save(a)
    run(store, "review", "new")
    rt = next(t for t in store.tickets() if rv.is_review_ticket(t))
    client = TestClient(create_app(store.home))
    r = client.post(f"/ticket/{rt.id}/check", data={"section": "Acceptance", "index": 0, "checked": 1}, follow_redirects=False)
    assert r.status_code == 303
    assert f"- [x] {a.id}" in store.get(rt.id).section("Acceptance")
    b = ops.new(store, "Approve: another", "desk", "decision", owner="james")
    run(store, "review", "sync")
    acc = store.get(rt.id).section("Acceptance")
    assert f"- [x] {a.id}" in acc and f"- [ ] {b.id}" in acc
    page = client.get(f"/ticket/{rt.id}").text
    assert f'href="/ticket/{a.id}"' in page and ">Open<" in page


def test_review_line_approve_redirects_back(store):
    (store.projects_dir / "management.md").write_text("# Management\n", encoding="utf-8")
    t = ops.new(store, "Ship it", "jre")
    ops.move(store, t, "ready", actor="main")
    ops.claim(store, t, "jre")
    ops.report(store, t, "Ran it.")
    run(store, "review", "new")
    rt = next(x for x in store.tickets() if rv.is_review_ticket(x))
    client = TestClient(create_app(store.home))
    page = client.get(f"/ticket/{rt.id}").text
    assert "Send back" in page and f'action="/ticket/{t.id}/decide"' in page
    r = client.post(f"/ticket/{t.id}/decide", data={"action": "approve", "words": "Approve: ship it.", "next": f"/ticket/{rt.id}"},
                    follow_redirects=False)
    assert r.headers["location"] == f"/ticket/{rt.id}" and store.get(t.id).status == "approved"


def _brand_store(store):
    (store.projects_dir / "brand.md").write_text("# Brand\n", encoding="utf-8")
    d = store.home / "assets" / "brand" / "jre"
    d.mkdir(parents=True)
    (d / "mark.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    (store.projects_dir / "jre.md").write_text(
        '---\nname: jRE\nbrand: {mark: "assets/brand/jre/mark.svg", accent: "#B3121A", family: "JA Insights"}\n---\n# jRE\n', encoding="utf-8")
    (store.home / "secret.txt").write_text("nope", encoding="utf-8")


def test_brand_check_creates_once(store, capsys):
    _brand_store(store)
    keys = {k for k, _ in brand.missing(store)}
    assert "jre" not in keys and {"desk", "jeby-us", "brand"} <= keys
    created, skipped = brand.create(store)
    assert len(created) == len(keys) and not skipped
    again, skipped = brand.create(store)
    assert again == [] and len(skipped) == len(keys)
    t = store.get(created[0])
    assert t.project == "brand" and t.meta["type"] == "chore" and t.meta["owner"] == "main" and t.meta["assignee"] == "chaz"
    assert t.meta["title"].startswith("Provide: mark and accent for ") and len(t.meta["title"]) <= 60
    with pytest.raises(SystemExit):
        run(store, "validate")
    assert "no brand block" in capsys.readouterr().out


def test_brand_shown_and_traversal_404(store):
    _brand_store(store)
    t = ops.new(store, "Branded", "jre")
    u = ops.new(store, "Plain", "desk")
    client = TestClient(create_app(store.home))
    page = client.get(f"/ticket/{t.id}").text
    assert 'src="/brand/jre/mark.svg"' in page and "#B3121A" in page
    assert 'class="initial"' in client.get(f"/ticket/{u.id}").text
    assert client.get("/brand/jre/mark.svg").status_code == 200
    for bad in ("../../secret.txt", "..%2F..%2Fsecret.txt", "%2e%2e/%2e%2e/secret.txt", "jre/../../../desk.toml", "jre"):
        assert client.get(f"/brand/{bad}").status_code == 404, bad
