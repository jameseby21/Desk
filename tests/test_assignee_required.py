"""DESK-0167: assignee required. Protects: a blank assignee is James's "not yet scanned" marker, a warning now and an error from the date
in desk.toml; the ticket page picks the assignee from people.yaml and saves in place; main is shown as Patch; external people work."""

import datetime as dt
import re

import pytest
from fastapi.testclient import TestClient

from desk import ops
from desk.cli import main
from desk.store import Store
from desk.ui.app import create_app

JSON = {"Accept": "application/json"}


def _validate(store, capsys):
    with pytest.raises(SystemExit) as e:
        main(["--home", str(store.home), "validate", "--verbose"])  # DESK-0265: the default output groups warnings by kind; this test reads the per-ticket line
    return e.value.code, capsys.readouterr().out


def test_blank_assignee_is_a_warning_before_the_date_and_an_error_after(store, capsys):
    t = ops.new(store, "Unscanned", "jre")
    code, out = _validate(store, capsys)
    assert code == 0 and f"warning: {t.id}: no assignee (James has not scanned this)" in out
    cfg = store.home / "desk.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace('assignee_required_from = "2026-11-22"', 'assignee_required_from = "2000-01-01"'), encoding="utf-8")
    code, out = _validate(Store(store.home), capsys)
    assert code == 1 and f"error: {t.id}: no assignee (James has not scanned this)" in out


def test_the_default_date_is_2026_11_22_and_a_filled_assignee_drops_the_warning(store, capsys):
    assert store.assignee_required_from == dt.date(2026, 11, 22)
    a = ops.new(store, "One", "jre")
    ops.new(store, "Two", "jre")
    _, out = _validate(store, capsys)
    before = int(re.search(r"(\d+) warning\(s\)", out).group(1))
    assert out.count("no assignee") == 2
    ops.assign(store, a, "chaz")
    _, out = _validate(store, capsys)
    assert out.count("no assignee") == 1 and int(re.search(r"(\d+) warning\(s\)", out).group(1)) == before - 1


def test_closed_tickets_are_not_scanned_work(store, capsys):
    t = ops.new(store, "Old", "jre")
    t.meta["status"] = "rejected"
    t.set_section("Decision", "no")
    store.save(t)
    _, out = _validate(store, capsys)
    assert "no assignee" not in out


def test_assign_accepts_display_names_and_stores_the_key(store, capsys):
    t = ops.new(store, "Name me", "jre")
    main(["--home", str(store.home), "assign", t.id, "--to", "Big D"])
    got = store.get(t.id)
    assert got.meta["assignee"] == "bigd" and got.meta["assigned_at"] and got.meta["assigned_by"]
    main(["--home", str(store.home), "assign", t.id, "--to", "Patch"])
    assert store.get(t.id).meta["assignee"] == "main"


def test_dropdown_groups_main_as_patch_and_code_sessions_second(store):
    t = ops.new(store, "Pick", "jre")
    page = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    block = page[page.index('id="assignee"'):]
    block = block[:block.index("</form>")]
    labels = re.findall(r'<optgroup label="([^"]+)"', block)
    assert labels == ["People", "Code sessions"]
    people = re.findall(r"<option value=\"([^\"]*)\"[^>]*>([^<]*)<", block)
    keys = [k for k, _ in people]
    assert keys[:9] == ["", "james", "main", "chaz", "em", "bri", "joe", "lily", "bigd"], keys  # the order of the ticket: James, Patch, then the coworkers
    assert dict(people)["main"].startswith("Patch") and "code:jre" in keys and "(not assigned yet)" in block


def test_external_people_appear_under_their_own_group(store, tmp_path, monkeypatch):
    priv = tmp_path / "priv.yaml"
    priv.write_text("people:\n  - key: legal-counsel\n    name: Counsel Name\n", encoding="utf-8")
    monkeypatch.setenv("DESK_PRIVATE_PEOPLE", str(priv))
    t = ops.new(store, "Needs a lawyer", "jre", owner="james")
    page = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    assert '<optgroup label="People outside the team">' in page and "Counsel Name" in page


def test_external_assignee_is_stored_as_the_key_and_james_can_complete_it(store, tmp_path, monkeypatch):
    priv = tmp_path / "priv.yaml"
    priv.write_text("people:\n  - key: legal-counsel\n    name: Counsel Name\n", encoding="utf-8")
    monkeypatch.setenv("DESK_PRIVATE_PEOPLE", str(priv))
    t = ops.new(store, "Meet the lawyer", "jre", owner="james", type_="task")
    c = TestClient(create_app(store.home))
    r = c.post(f"/ticket/{t.id}/assign", data={"token": c.app.state.tokens.issue(), "to": "legal-counsel"}, headers=JSON)
    assert r.status_code == 200 and "Counsel Name" in r.json()["message"]
    assert store.get(t.id).meta["assignee"] == "legal-counsel"
    t = store.get(t.id)
    ops.move(store, t, "ready", actor="james")
    t = store.get(t.id)
    t.meta["status"] = "review"
    t.set_section("Report", "Met them.")
    store.save(t)
    ops.decide(store, store.get(t.id), True, "Done, I met them.", actor="james")
    assert ops.done(store, store.get(t.id)).status == "done"  # the assignee does not get in the owner's way


def test_post_assign_saves_in_place_and_records_who_and_when(store):
    t = ops.new(store, "Save me", "jre")
    c = TestClient(create_app(store.home))
    r = c.post(f"/ticket/{t.id}/assign", data={"token": c.app.state.tokens.issue(), "to": "main"}, headers=JSON)
    assert r.status_code == 200 and r.json()["message"] == "Assigned to Patch."
    got = store.get(t.id)
    assert got.meta["assignee"] == "main" and got.meta["assigned_by"] == "james" and re.match(r"\d{4}-\d{2}-\d{2}T", got.meta["assigned_at"])
    page = c.get(f"/ticket/{t.id}").text
    assert "assigned by" in page and "selected" in page[page.index('value="main"') - 20:page.index('value="main"') + 80]
    r = c.post(f"/ticket/{t.id}/assign", data={"token": c.app.state.tokens.issue(), "to": ""}, headers=JSON)
    assert r.json()["message"] == "Back to not assigned." and "assignee" not in store.get(t.id).meta


def test_post_assign_refuses_a_name_nobody_listed_and_a_spent_form(store):
    t = ops.new(store, "Refuse", "jre")
    c = TestClient(create_app(store.home))
    r = c.post(f"/ticket/{t.id}/assign", data={"token": c.app.state.tokens.issue(), "to": "stranger"}, headers=JSON)
    assert r.status_code == 422 and "not in people.yaml" in r.json()["error"] and "assignee" not in store.get(t.id).meta
    assert c.post(f"/ticket/{t.id}/assign", data={"token": "spent", "to": "main"}, headers=JSON).status_code == 409
    plain = c.post(f"/ticket/{t.id}/assign", data={"token": c.app.state.tokens.issue(), "to": "chaz"}, follow_redirects=False)
    assert plain.status_code == 303 and "done=assign" in plain.headers["location"] and plain.headers["location"].endswith("#assignee")
    assert store.get(t.id).meta["assignee"] == "chaz"


def test_an_old_spelling_selects_the_new_key(store):
    t = ops.new(store, "Old spelling", "jre", assignee="jre")
    page = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    assert re.search(r'<option value="code:jre" selected>', page)
