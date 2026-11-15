"""DESK-0283: lanes, snooze and park, with an only-my-lane switch.

Protects: every ticket has a lane (now, planned, later, parked; blank shows the derived lane); a snoozed ticket stays off Today and
Needs me until its date and comes back marked; parking keeps the file; 'only my lane' and rough week narrow Today and the board;
subtickets and agent-only work stay off Today unless asked for; validate warns on a Later ticket with a branch or a worker."""

import datetime as dt
import json
import re

import pytest
from fastapi.testclient import TestClient

from desk import lanes, needs_me as nmod, ops, plainwords
from desk.cli import main
from desk.rules import validate_store
from desk.ui.app import create_app

TODAY = dt.date.today()


def _mk(store, title, **meta):
    t = ops.new(store, title, "desk")
    t.meta.update(meta)
    store.save(t)
    return store.get(t.id)


def _drew(store, *args):
    main(["--home", str(store.home), *args])


def _events(store, tid):
    p = store.home / "events" / f"{tid}.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def _rows(page):
    return set(re.findall(r'href="/ticket/([A-Z]+-\d+)"', page))


def _client(store, **cookies):
    c = TestClient(create_app(store.home), base_url="http://127.0.0.1:8799")  # the lane routes answer this computer only
    for k, v in cookies.items():
        c.cookies.set(k, v)
    return c


# -- the CLI ------------------------------------------------------------------------------

def test_lane_field_round_trip_writes_the_field_and_an_event(store, capsys):
    """Protects: `drew lane ID later` writes `lane: later` and logs it; `drew lane ID` reads it; --clear goes back to the derived lane."""
    t = _mk(store, "Lane me", status="ready", owner="james")
    assert lanes.lane_of(t) == "planned" and not lanes.explicit_lane(t)
    _drew(store, "lane", t.id, "later")
    got = store.get(t.id)
    assert got.meta["lane"] == "later" and lanes.lane_of(got) == "later"
    assert any(e["field"] == "lane" and e["new"] == "later" for e in _events(store, t.id))
    capsys.readouterr()
    _drew(store, "lane", t.id)
    assert "lane later" in capsys.readouterr().out
    _drew(store, "lane", t.id, "--clear")
    assert "lane" not in store.get(t.id).meta and lanes.lane_of(store.get(t.id)) == "planned"


def test_snooze_parses_dates_and_refuses_the_past():
    """Protects: the forms the ticket names (a date, 1w, next-monday) and the ones a person types (1d, 2w); a past date snoozes nothing."""
    mon = dt.date(2026, 11, 16)  # a Monday
    assert lanes.parse_when("1w", mon) == dt.date(2026, 11, 23)
    assert lanes.parse_when("1d", mon) == dt.date(2026, 11, 17)
    assert lanes.parse_when("2w", mon) == dt.date(2026, 11, 30)
    assert lanes.parse_when("next-monday", mon) == dt.date(2026, 11, 23)  # never today
    assert lanes.parse_when("next-monday", dt.date(2026, 11, 18)) == dt.date(2026, 11, 23)
    assert lanes.parse_when("2026-11-22", mon) == dt.date(2026, 11, 22)
    for bad in ("2026-11-01", "soon", ""):
        with pytest.raises(ValueError):
            lanes.parse_when(bad, mon)


def test_snooze_hides_from_today_and_needs_me_and_clear_brings_it_back(store):
    """Protects: the ticket's How to validate: `drew snooze ID 1w` takes it off Today; `drew snooze ID --clear` brings it back."""
    t = _mk(store, "Choose the colour", status="ready", owner="james")
    c = _client(store)
    assert t.id in _rows(c.get("/").text)
    _drew(store, "snooze", t.id, "1w")
    assert store.get(t.id).meta["snooze_until"] == (TODAY + dt.timedelta(days=7)).isoformat()
    page = c.get("/").text
    assert t.id not in re.findall(r'data-ticket="([A-Z]+-\d+)"', page) and f'<a class="trow" href="/ticket/{t.id}"' not in page
    assert "1 snoozed" in page  # still findable, folded
    assert t.id not in {i.id for i in nmod.build(store)}  # the CLI's Needs me too
    _drew(store, "snooze", t.id, "--clear")
    assert "snooze_until" not in store.get(t.id).meta
    assert t.id in _rows(c.get("/").text)


def test_a_passed_snooze_comes_back_marked(store):
    """Protects: a ticket whose snooze ended in the last three days is back on Today with 'Back from snooze'; older ones are plain."""
    back = _mk(store, "Woke yesterday", status="ready", owner="james", snooze_until=(TODAY - dt.timedelta(days=1)).isoformat())
    old = _mk(store, "Woke long ago", status="ready", owner="james", snooze_until=(TODAY - dt.timedelta(days=9)).isoformat())
    page = _client(store).get("/").text
    assert back.id in _rows(page) and old.id in _rows(page)
    assert page.count("Back from snooze") == 1
    assert lanes.back_from_snooze(back, TODAY) and not lanes.back_from_snooze(old, TODAY)


def test_park_keeps_the_file_and_needs_a_reason(store, capsys):
    """Protects: parking deletes nothing and always says why."""
    t = _mk(store, "Maybe later", status="ready")
    path = store.get(t.id).path
    _drew(store, "park", t.id, "not this quarter")
    got = store.get(t.id)
    assert path.exists() and got.meta["lane"] == "parked" and got.meta["park_reason"] == "not this quarter"
    assert any(e["field"] == "park_reason" for e in _events(store, t.id))
    with pytest.raises(SystemExit):
        _drew(store, "lane", t.id, "parked")  # no reason
    assert "reason" in capsys.readouterr().err
    _drew(store, "lane", t.id, "planned")
    assert "park_reason" not in store.get(t.id).meta and store.get(t.id).path.exists()


def test_validate_warns_on_a_later_ticket_with_a_branch_or_a_worker(store):
    """Protects: Later means not built; a branch or a claimed worker on it is drift, said by `drew validate`."""
    branchy = _mk(store, "Idea with a branch", status="ready", lane="later", branch="desk/x")
    worked = _mk(store, "Idea being built", status="in_progress", owner="code:jre", lane="later")
    fine = _mk(store, "Planned with a branch", status="ready", branch="desk/y")
    _, warnings = validate_store(store)
    lane_w = [w for w in warnings if "Later lane" in w]
    assert any(branchy.id in w and "branch" in w for w in lane_w)
    assert any(worked.id in w and "worker" in w for w in lane_w)
    assert not any(fine.id in w for w in lane_w)


# -- the web ------------------------------------------------------------------------------

def test_only_my_lane_and_the_lane_filter_narrow_today_and_the_board(store):
    """Protects: the per-device switch hides every lane but Now on Today and the board; no cookie shows all."""
    now = _mk(store, "Fire", status="in_progress", owner="james", assignee="james", lane="now")
    planned = _mk(store, "Slice", status="in_progress", owner="james", assignee="james")
    assert {now.id, planned.id} <= _rows(_client(store).get("/").text)
    assert {now.id, planned.id} <= _rows(_client(store).get("/board?company=*&open=*&g=*").text)
    c = _client(store, desk_lane="now")
    page = c.get("/").text
    assert now.id in _rows(page) and planned.id not in _rows(page)
    assert 'aria-checked="true"' in page and "1 in other lanes hidden" in page
    board = c.get("/board?company=*&open=*&g=*").text
    assert now.id in board and planned.id not in board
    # the switch sets the cookie and comes back to the page it was pressed on
    r = _client(store).post("/lanes/view", data={"lane": "now", "next": "/board"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/board" and "desk_lane=now" in r.headers["set-cookie"]
    r = _client(store).post("/lanes/view", data={"lane": "x", "next": "https://elsewhere.example"}, follow_redirects=False)
    assert r.headers["location"] == "/"  # never another site


def test_rough_week_shows_only_now_and_the_smallest_step(store):
    """Protects: Rough on the Good/Normal/Rough switch shows lane Now only, each with its smallest step."""
    now = _mk(store, "Fire", status="in_progress", owner="james", assignee="james", priority="p0", smallest_step="Read the log")
    other = _mk(store, "Slice", status="in_progress", owner="james", assignee="james")
    page = _client(store).get("/?cap=rough").text
    assert now.id in _rows(page) and other.id not in _rows(page)
    assert "Smallest step: Read the log" in page and "Rough week" in page


def test_subtickets_and_agent_only_work_are_hidden_from_today_until_show_all(store):
    """Protects: only what needs the owner is on Today by default; 'Show all' (per device) brings the rest back."""
    main_t = _mk(store, "Main piece", status="in_progress", owner="james", assignee="james")
    sub = ops.new(store, "Icon check", "desk", "task", parent=main_t.id)
    sub.meta.update(status="in_progress", owner="chaz", assignee="chaz")
    store.save(sub)
    bot = _mk(store, "Agent chore", status="in_progress", owner="main", assignee="code:jre")
    mine_sub = ops.new(store, "Input from the owner", "desk", "task", parent=main_t.id)
    mine_sub.meta.update(status="ready", owner="james")
    store.save(mine_sub)
    page = _client(store).get("/").text
    rows = _rows(page)
    assert main_t.id in rows and sub.id not in rows and bot.id not in rows
    assert mine_sub.id in rows  # a subticket that asks the owner something still reaches him
    assert "2 subtickets or agent-only tickets hidden" in page
    page = _client(store, desk_show="all").get("/").text
    assert {sub.id, bot.id} <= _rows(page)


def test_lane_chip_on_rows_and_the_ticket_page_changes_the_lane_with_undo(store):
    """Protects: the chip shows the lane; a change from the web is one batch, answered with the undo contract (undo, msg)."""
    t = _mk(store, "Chip me", status="in_progress", owner="james", assignee="james")
    c = _client(store)
    assert f'data-lane-id="{t.id}"' in c.get("/").text
    page = c.get(f"/ticket/{t.id}").text
    assert 'class="lane-pick"' in page and "lane-chip lane-planned derived" in page
    tok = re.search(r'action="/ticket/%s/lane".*?name="token" value="([^"]+)"' % t.id, page, re.S).group(1)
    r = c.post(f"/ticket/{t.id}/lane", data={"token": tok, "lane": "later"}, follow_redirects=False)
    assert r.status_code == 303
    loc = r.headers["location"]
    bid = re.search(r"undo=(b-[0-9a-f]{12})", loc).group(1)
    assert loc.startswith(f"/ticket/{t.id}?") and "msg=" in loc
    assert store.get(t.id).meta["lane"] == "later"
    assert [e["batch"] for e in _events(store, t.id) if e["field"] == "lane"] == [bid]
    # the menu on rows: data and a fresh key from GET, the change and its batch from POST
    j = c.get(f"/ticket/{t.id}/lane").json()
    assert j["lane"] == "later" and j["token"]
    r = c.post(f"/ticket/{t.id}/lane", data={"token": j["token"], "snooze": "1w", "lane": "now"}, headers={"Accept": "application/json"})
    out = r.json()
    assert out["ok"] and out["lane"] == "now" and out["snoozed"] and out["batch"].startswith("b-")
    assert {e["field"] for e in _events(store, t.id) if e.get("batch") == out["batch"]} == {"lane", "snooze_until"}
    # a spent key writes nothing
    r = c.post(f"/ticket/{t.id}/lane", data={"token": j["token"], "lane": "planned"}, headers={"Accept": "application/json"})
    assert r.status_code == 409 and store.get(t.id).meta["lane"] == "now"
    # parking from the web needs the reason too
    j = c.get(f"/ticket/{t.id}/lane").json()
    r = c.post(f"/ticket/{t.id}/lane", data={"token": j["token"], "lane": "parked"}, headers={"Accept": "application/json"})
    assert r.status_code == 422 and "reason" in r.json()["error"] and store.get(t.id).meta["lane"] == "now"
    # without script the refusal comes back to the ticket page in words
    page = c.get(f"/ticket/{t.id}").text
    tok = re.search(r'name="token" value="([^"]+)"><input type="hidden" name="next" value="/ticket/%s"><input type="hidden" name="lane" value="parked">' % t.id, page).group(1)
    r = c.post(f"/ticket/{t.id}/lane", data={"token": tok, "lane": "parked", "reason": " "})
    assert "Parking needs a one-line reason" in r.text and store.get(t.id).meta["lane"] == "now"


def test_new_words_on_screen_are_plain():
    """Protects: DESK-0253 for the words this package puts on screen: a grade of 10 or under."""
    from desk.ui import routes_lanes as rl

    t = type("T", (), {"id": "DESK-1", "meta": {}, "tags": [], "status": "ready"})()
    lines = [rl._sentence(t, "parked", "", None), rl._sentence(t, "later", "1w", TODAY + dt.timedelta(days=7)),
             "Rough week: only the Now lane, with each smallest step.", "Parking needs a one-line reason, for example: not this quarter.",
             "Snoozed tickets stay off Today and Needs me until their date."]
    for line in lines:
        g = plainwords.grade(line)
        assert g is None or g <= 10, (line, g)


@pytest.mark.skipif(__import__("browser_harness").browser() is None, reason="no Edge or Chrome installed")
def test_the_chip_menu_opens_by_keyboard_at_phone_width(store):
    """Protects: at 390 px the page does not scroll sideways, and Enter on a row's lane chip opens the lane menu inside the screen."""
    from browser_harness import drive

    for i in range(3):
        _mk(store, f"Held ticket number {i} with a fairly long title to wrap", status="in_progress", owner="james", assignee="james")
    script = ("<script>addEventListener('load',function(){setTimeout(function(){var c=document.querySelector('.lane-chip[data-lane-id]');"
              "c.focus();c.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));setTimeout(function(){"
              "var m=document.querySelector('.lane-pop'),r=m?m.getBoundingClientRect():null;"
              "fetch('/m',{method:'POST',body:JSON.stringify({wide:document.documentElement.scrollWidth,inner:window.innerWidth,"
              "items:m?m.querySelectorAll('.lm-item').length:0,right:r?r.right:0,focus:document.activeElement.className})});},1200);},300);});</script>")
    got = drive(store, script, "/", width=390)
    assert got["wide"] <= got["inner"], got
    assert got["items"] >= 7 and got["right"] <= got["inner"], got  # three lanes, Park, four snooze choices
    assert "lm-item" in got["focus"], got


# -- fixes after review (DESK-0283) -------------------------------------------------------

def _imm(page):
    m = re.search(r'<section id="immediate".*?</section>', page, re.S)
    return m.group(0) if m else ""


def _nm(page):
    m = re.search(r'<section class="card nm" id="needs-me".*?</section>', page, re.S)
    return m.group(0) if m else ""


def test_a_subticket_the_owner_is_assigned_stays_on_his_today(store):
    """Protects: a subticket assigned to the owner (owned by someone else) is his own work in hand, so Today never hides it by default."""
    main_t = _mk(store, "Main piece", status="in_progress", owner="main", assignee="code:jre")
    held = ops.new(store, "Piece the owner holds", "desk", "task", parent=main_t.id)
    held.meta.update(status="in_progress", owner="main", assignee="james")
    store.save(held)
    other = ops.new(store, "Piece a coworker holds", "desk", "task", parent=main_t.id)
    other.meta.update(status="in_progress", owner="main", assignee="chaz")
    store.save(other)
    rows = _rows(_client(store).get("/").text)
    assert held.id in rows
    assert other.id not in rows  # a coworker's subticket still stays off by default


def test_rough_week_and_only_my_lane_keep_a_missed_hard_deadline_and_an_overdue_ask(store):
    """Protects: the lane filter never takes a missed hard deadline off Immediate Action Required, nor an overdue ask off Needs me."""
    late = _mk(store, "Filing due", status="ready", owner="james", assignee="james",
               deadline=(TODAY - dt.timedelta(days=2)).isoformat(), deadline_kind="hard")
    asked = _mk(store, "Pick the colour", status="ready", owner="james", review_by=(TODAY - dt.timedelta(days=1)).isoformat())
    calm = _mk(store, "Slice", status="in_progress", owner="main", assignee="james")
    for c, url in ((_client(store), "/?cap=rough"), (_client(store, desk_lane="now"), "/")):
        page = c.get(url).text
        assert late.id in _imm(page), url
        assert asked.id in _nm(page), url
        assert calm.id not in _rows(page), url  # the filter still narrows everything else


def test_hidden_subtickets_and_agent_work_with_a_missed_hard_deadline_stay_in_immediate(store):
    """Protects: the hide-by-default rule never takes a missed hard deadline off Immediate Action Required."""
    missed = (TODAY - dt.timedelta(days=1)).isoformat()
    bot = _mk(store, "Agent chore due", status="in_progress", owner="main", assignee="code:jre", deadline=missed, deadline_kind="hard")
    main_t = _mk(store, "Main piece", status="in_progress", owner="main", assignee="code:jre")
    sub = ops.new(store, "Coworker check due", "desk", "task", parent=main_t.id)
    sub.meta.update(status="in_progress", owner="chaz", assignee="chaz", deadline=missed, deadline_kind="hard")
    store.save(sub)
    imm = _imm(_client(store).get("/").text)
    assert bot.id in imm and sub.id in imm


def test_a_snooze_never_hides_a_hard_deadline_and_validate_warns_on_quieted_emergencies(store, capsys):
    """Protects: a snooze ends no later than the hard deadline, and validate (and the command) warns when an emergency or p0 is
    snoozed or put in Later or Parked, or a snooze runs past a hard deadline."""
    due = _mk(store, "Due today", status="ready", owner="james", priority="p0", tags=["emergency"],
              deadline=TODAY.isoformat(), deadline_kind="hard")
    _drew(store, "snooze", due.id, "2w")
    assert "hard deadline" in capsys.readouterr().err  # said at the moment it is written
    got = store.get(due.id)
    assert not lanes.is_snoozed(got, TODAY)
    page = _client(store).get("/").text
    assert due.id in _imm(page) and due.id in _nm(page)
    assert due.id in {i.id for i in nmod.build(store)}
    later = _mk(store, "Emergency put aside", status="ready", priority="p0", lane="later")
    calm = _mk(store, "Plain later", status="ready", lane="later", snooze_until=(TODAY + dt.timedelta(days=3)).isoformat())
    _, warnings = validate_store(store)
    warnings = [w for w in warnings if any(k in w for k in ("emergency", "hard deadline", "snooze", " lane"))]  # this package's warnings
    assert any(due.id in w and "hard deadline" in w for w in warnings)
    assert any(due.id in w and "emergency" in w for w in warnings)
    assert any(later.id in w and "emergency" in w for w in warnings)
    assert not any(calm.id in w for w in warnings)

