"""DESK-0149 (Chaz M1 to M6): Needs me lists the tickets with an ask for the owner and the tickets someone else changed since the owner
last acted. Reading a ticket is not acting. Protects: the reason lines word for word, Mark seen, and the three styles' empty states."""

import datetime as dt
import re

from fastapi.testclient import TestClient

from desk import needs_me, ops, style as styles
from desk.cli import main
from desk.ui.app import create_app

NOW = dt.datetime(2026, 11, 1, 12, 0)


def _comment(store, t, when, who, text="a note"):
    t = store.get(t.id)
    t.append_line("Comments", f"- {when} · {who} · {text} <!-- kind: Question -->")
    t.meta["updated"] = when[:10]  # the day of the newest line, as a real edit would leave it
    store.save(t, touch=False)
    return store.get(t.id)


def _decision(store, title, owner="james", status="ready", **meta):
    t = ops.new(store, title, "desk", "decision", owner=owner)
    t.meta["status"] = status
    t.meta.update(meta)
    t.set_section("Context", "Pick one.\n\nOptions: A one; B two; C three.")
    store.save(t)
    return store.get(t.id)


def _at(base, **delta):
    return (base + dt.timedelta(**delta)).strftime("%Y-%m-%d %H:%M")


def _fixture(store, base=NOW - dt.timedelta(hours=9)):
    ask = _decision(store, "Choose the colour of the thing", review_by="2026-11-10")
    due = _decision(store, "Choose the plan", review_by="2026-10-30")
    changed = ops.new(store, "Changed by Patch", "desk", "feature", owner="main")
    changed.meta["status"] = "in_progress"
    store.save(changed)
    changed = _comment(store, changed, _at(base), "james", "please look")
    changed = _comment(store, changed, _at(base, hours=2, minutes=10), "lily", "done?")
    changed = _comment(store, changed, _at(base, hours=2, minutes=12), "lily", "and one more")
    viewed = ops.new(store, "Only viewed", "desk", "feature", owner="main")
    viewed.meta["status"] = "in_progress"
    store.save(viewed)
    _comment(store, viewed, _at(base), "patch", "update")
    answered = ops.new(store, "James had the last word", "desk", "feature", owner="main")
    answered.meta["status"] = "in_progress"
    store.save(answered)
    answered = _comment(store, answered, _at(base, hours=1), "patch", "hi")
    _comment(store, answered, _at(base, hours=3), "james", "ok")
    return ask, due, changed, viewed, answered


def test_asks_changes_and_viewed_only(store):
    ask, due, changed, viewed, answered = _fixture(store)
    items = needs_me.build(store, now=NOW)
    by = {i.id: i for i in items}
    assert set(by) == {ask.id, due.id, changed.id}  # the viewed-only and the answered ones are not listed
    assert [i.group for i in items] == ["ask", "ask", "change"]  # asks first, overdue first among them
    assert items[0].id == due.id and items[0].overdue


def test_reason_lines_word_for_word(store):
    ask, due, changed, *_ = _fixture(store)
    by = {i.id: i for i in needs_me.build(store, now=NOW)}
    assert by[ask.id].why == "ask: choose · 3 options · due Nov 10"
    assert by[ask.id].chip == "Choose one" and by[ask.id].act == "Choose"
    assert by[due.id].why == "ask: choose · 3 options · due Oct 30 overdue" and by[due.id].overdue and not by[ask.id].overdue
    assert by[changed.id].why == "changed: 2 comments by Lily · 5:12 AM"


def test_review_waiting_on_the_owner_is_an_approve_ask(store):
    t = ops.new(store, "Built thing", "desk", "feature", owner="main", size="m")
    t.meta["status"] = "review"
    t.set_section("Report", "done")
    store.save(t)
    i = needs_me.build(store, now=NOW)[0]
    assert (i.kind, i.act, i.why) == ("approve", "Review", "ask: approve · from Patch")


def test_acting_clears_a_change_and_a_later_change_brings_it_back(store):
    *_, changed, viewed, answered = _fixture(store, base=dt.datetime.now() - dt.timedelta(hours=6))
    ops.comment(store, store.get(changed.id), "answering", actor="james")
    assert changed.id not in {i.id for i in needs_me.build(store)}  # his own action removes it
    _comment(store, store.get(changed.id), (dt.datetime.now() + dt.timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M"), "chaz", "again")
    assert changed.id in {i.id for i in needs_me.build(store)}


def test_status_report_and_proof_changes_by_someone_else_count_after_his_last_action(store):
    t = ops.new(store, "Watched", "desk", "feature", owner="main")
    t.meta["status"] = "in_progress"
    store.save(t)
    ops.comment(store, store.get(t.id), "watching", actor="james")
    assert not needs_me.build(store)
    t = store.get(t.id)
    t.set_section("Report", "built it")
    t.append_line("Evidence", "- [x] ran it")
    store.save(t)
    why = needs_me.build(store)[0].why
    assert re.match(r"changed: report updated by Patch, proof added by Patch · \d", why)


def test_mark_seen_removes_a_change_but_never_an_ask(store):
    ask, due, changed, *_ = _fixture(store)
    needs_me.mark(store, [store.get(changed.id), store.get(ask.id)], now=NOW + dt.timedelta(hours=1))
    ids = {i.id for i in needs_me.build(store, now=NOW + dt.timedelta(hours=2))}
    assert changed.id not in ids and ask.id in ids


def test_reading_the_ticket_page_changes_nothing(store):
    *_, changed, viewed, answered = _fixture(store)
    c = TestClient(create_app(store.home))
    before = {i.id for i in needs_me.build(store, now=NOW)}
    for tid in before:
        assert c.get(f"/ticket/{tid}").status_code == 200
    assert {i.id for i in needs_me.build(store, now=NOW)} == before


def test_today_first_block_counts_and_mark_seen_buttons(store):
    ask, due, changed, *_ = _fixture(store)
    c = TestClient(create_app(store.home))
    page = c.get("/").text
    assert page.index('id="immediate"') < page.index('id="needs-me"') < page.index('id="needs-you"')  # DESK-0193 keeps the top row
    assert "2 asks for you · 1 changed by someone else" in page
    assert page.count("Asks for you") == 1 and "Changed since you last acted" in page
    assert "Mark all changes seen" in page and "All open (" in page
    assert '<span class="nm-count">3</span>' in page
    tok = re.search(r'action="/needs-me/seen"[^>]*>\s*<input type="hidden" name="token" value="([^"]+)"', page).group(1)
    r = c.post("/needs-me/seen", data={"token": tok, "all": "1"}, follow_redirects=False)
    assert r.status_code == 303
    after = c.get("/").text
    assert "2 asks for you · 0 changed by someone else" in after and "Mark all changes seen" not in after


def test_board_chip_filters_and_shows_reason_lines(store):
    ask, due, changed, viewed, answered = _fixture(store)
    c = TestClient(create_app(store.home))
    assert "Needs me · 3" in c.get("/board").text and "3 open" in c.get("/board?needs=1").text  # DESK-0183: the top level counts companies
    page = c.get("/board?company=unsorted&needs=1").text  # inside a company the view opens what is left, like every filter
    assert "Needs me · 3" in page
    assert "Only viewed" not in page and "Changed by Patch" in page
    assert re.search(r"ask: choose · 3 options · due (Nov 10|Tue)", page)
    every = c.get("/board?company=unsorted&open=*&g=*").text
    assert "Only viewed" in every and "Needs me · 3" in every


def test_only_the_empty_state_differs_across_styles(store):
    outs = {}
    for name, dog in (("plain", "drew"), ("dogs", "drew"), ("cat", "drew")):
        styles.save(store.home, name, dog)
        page = TestClient(create_app(store.home)).get("/").text
        outs[name] = re.search(r'<div class="nm-empty">(.*?)</div>', page, re.S).group(1)
    assert "Nothing needs you" in outs["plain"]
    assert "Nothing to fetch for you" in outs["dogs"]
    assert "Nothing on your doorstep" in outs["cat"]


def test_cli_needs_me_prints_the_reason_line(store, capsys, monkeypatch):
    ask, due, changed, *_ = _fixture(store)
    main(["--home", str(store.home), "needs-me"])
    out = capsys.readouterr().out
    assert re.search(r"ask: choose · 3 options · due (Nov 10|Tue)", out) and "2 asks for you · 1 changed by someone else" in out
    assert "Only viewed" not in out
    main(["--home", str(store.home), "needs-me", "--seen", changed.id])
    capsys.readouterr()
    main(["--home", str(store.home), "needs-me", "--as", "james"])
    assert "Changed by Patch" not in capsys.readouterr().out
