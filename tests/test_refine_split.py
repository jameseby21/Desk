"""DESK-0145: drew refine and drew split. Protects: refinements of requirements are tracked and may slip a date with a reason, while a change
of scope never edits the ticket: it becomes a new ticket with details and a timeline, linked both ways, and the old ticket asks for a
completion decision. The UI refuses to rewrite an Outcome once the ticket is thrown and points to Split."""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from desk import ops, review
from desk.cli import main
from desk.rules import RuleError, validate_store
from desk.ui.app import create_app

JSON = {"Accept": "application/json"}


def _mk(store, title="Original", **meta):
    t = ops.new(store, title, "jre")
    t.meta.update({"status": "ready", "size": "s", "tags": ["x"], **meta})
    t.set_section("Outcome", "Search finds tickets.")
    t.set_section("Acceptance", "- [ ] first thing")
    t.write()
    return store.get(t.id)


def test_refine_adds_a_dated_acceptance_line_and_a_comment(store, capsys):
    t = _mk(store)
    main(["--home", str(store.home), "refine", t.id, "Also match ids in the middle", "--as", "james"])
    got = store.get(t.id)
    assert f"- [ ] {dt.date.today().isoformat()} refinement: Also match ids in the middle" in got.section("Acceptance")
    assert "refinement: Also match ids in the middle" in got.section("Comments") and "· james ·" in got.section("Comments")
    assert got.section("Outcome") == "Search finds tickets." and "review_by" not in got.meta


def test_refine_may_move_review_by_only_with_a_reason_and_records_the_slip(store, capsys):
    t = _mk(store, review_by="2026-11-12")
    with pytest.raises(SystemExit):
        main(["--home", str(store.home), "refine", t.id, "Needs a second pass", "--review-by", "2026-11-14"])
    assert "needs a reason" in capsys.readouterr().err and str(store.get(t.id).meta["review_by"]) == "2026-11-12"
    main(["--home", str(store.home), "refine", t.id, "Needs a second pass", "--review-by", "2026-11-14", "--reason", "the filter grammar grew"])
    got = store.get(t.id)
    assert str(got.meta["review_by"]) == "2026-11-14" and "review_by moved from 2026-11-12 to 2026-11-14: the filter grammar grew" in got.section("Comments")


def test_refine_refuses_empty_words_bad_dates_and_closed_tickets(store):
    t = _mk(store)
    with pytest.raises(RuleError):
        ops.refine(store, t, "  ")
    with pytest.raises(RuleError):
        ops.refine(store, t, "x y z", review_by="soon", reason="r")
    t.meta["status"] = "rejected"
    t.set_section("Decision", "no")
    store.save(t)
    with pytest.raises(RuleError):
        ops.refine(store, store.get(t.id), "too late")


def test_split_makes_a_linked_ticket_and_asks_for_the_completion_decision(store, capsys):
    old = _mk(store, "Search v1", priority="p1", dod=["core", "code"], assignee="main", review_by="2026-11-12")
    main(["--home", str(store.home), "split", old.id, "--title", "Search v2: saved searches", "--size", "m", "--due", "2026-11-20",
          "--details", "Saved searches came up after the first cut.", "--acceptance", "A saved search reopens from its link", "--as", "james"])
    out = capsys.readouterr().out
    assert "created from" in out
    new = [t for t in store.tickets() if t.id != old.id][0]
    got = store.get(old.id)
    assert new.meta["depends_on"] == [old.id] and new.meta["split_from"] == old.id and new.meta["size"] == "m" and str(new.meta["review_by"]) == "2026-11-20"
    assert got.meta["blocks"] == [new.id]  # linked both ways
    assert new.meta["priority"] == "p1" and new.meta["dod"] == ["core", "code"] and new.meta["tags"] == ["x"] and new.meta["assignee"] == "main"
    assert "Saved searches came up" in new.section("Context") and "- [ ] A saved search reopens from its link" in new.section("Acceptance")
    assert f"split to {new.id}: completion decision needed" in got.section("Decision") and "close it in favour of" in got.section("Decision")
    assert f"scope split out to {new.id}" in got.section("Comments") and "(due 2026-11-20)" in got.section("Comments")
    assert got.section("Outcome") == "Search finds tickets." and got.section("Acceptance") == "- [ ] first thing"  # the old ticket's promise is untouched
    errors, _ = validate_store(store)
    assert errors == []


def test_split_refuses_a_bad_size_empty_title_and_closed_ticket(store):
    t = _mk(store)
    with pytest.raises(RuleError):
        ops.split(store, t, "x", size="l")
    with pytest.raises(RuleError):
        ops.split(store, t, "   ")
    with pytest.raises(RuleError):
        ops.split(store, t, "title", due="whenever")
    assert len(store.tickets()) == 1


def test_outcome_is_refused_after_the_ticket_is_thrown_and_points_to_split(store):
    t = _mk(store, status="ready")
    c = TestClient(create_app(store.home))
    r = c.post(f"/ticket/{t.id}/outcome", data={"token": c.app.state.tokens.issue(), "text": "Search also exports."}, headers=JSON)
    assert r.status_code == 409 and "Use Split" in r.json()["error"] and "change of scope" in r.json()["error"]
    assert store.get(t.id).section("Outcome") == "Search finds tickets."
    same = c.post(f"/ticket/{t.id}/outcome", data={"token": c.app.state.tokens.issue(), "text": "Search finds tickets."}, headers=JSON)
    assert same.status_code == 200  # saving the words that are already there is not a change
    early = _mk(store, "Not thrown yet", status="inbox")
    ok = c.post(f"/ticket/{early.id}/outcome", data={"token": c.app.state.tokens.issue(), "text": "A different promise."}, headers=JSON)
    assert ok.status_code == 200 and store.get(early.id).section("Outcome") == "A different promise."


def test_ui_buttons_refine_and_split_save_in_place(store):
    t = _mk(store)
    c = TestClient(create_app(store.home))
    page = c.get(f"/ticket/{t.id}").text
    assert "Refine or split" in page and f'action="/ticket/{t.id}/refine"' in page and f'action="/ticket/{t.id}/split"' in page
    r = c.post(f"/ticket/{t.id}/refine", data={"token": c.app.state.tokens.issue(), "line": "Mention the palette"}, headers=JSON)
    assert r.status_code == 200 and "Refinement added" in r.json()["message"] and "refinement: Mention the palette" in store.get(t.id).section("Acceptance")
    r = c.post(f"/ticket/{t.id}/split", data={"token": c.app.state.tokens.issue(), "title": "Palette v2", "size": "s", "due": "2026-11-30", "details": "later"}, headers=JSON)
    new_id = r.json()["new"]
    assert r.status_code == 200 and new_id in r.json()["message"] and store.get(new_id).meta["depends_on"] == [t.id]
    bad = c.post(f"/ticket/{t.id}/split", data={"token": c.app.state.tokens.issue(), "title": "", "size": "s"}, headers=JSON)
    assert bad.status_code == 422 and "title" in bad.json()["error"]
    plain = c.post(f"/ticket/{t.id}/split", data={"token": c.app.state.tokens.issue(), "title": "Plain post", "size": "s"}, follow_redirects=False)
    assert plain.status_code == 303 and plain.headers["location"].startswith("/ticket/")
    store.get(t.id)
    closed = _mk(store, "Closed", status="rejected")
    closed.set_section("Decision", "no")
    store.save(closed)
    assert "Refine or split" not in c.get(f"/ticket/{closed.id}").text


def test_the_review_week_ticket_lists_splits_made_that_week(store):
    old = _mk(store)
    new = ops.split(store, store.get(old.id), "Part two", size="s")
    rt, _ = review.create(store, dt.date.today(), project="jre")
    report = store.get(rt.id).section("Report")
    assert f"Splits made this week: 1 ({new.id})" in report
    last_week = _mk(store, "Old split")
    older = ops.split(store, store.get(last_week.id), "Old part", size="s")
    o = store.get(older.id)
    o.meta["created"] = (dt.date.today() - dt.timedelta(days=14)).isoformat()
    o.write()
    res = review.apply(store, store.get(rt.id))
    assert res["kpi"]["splits"] == [new.id]
