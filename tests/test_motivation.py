"""DESK-0293: the decided-today count and the streak come from the event log; the empty state lists agent work; every Motivation
switch works from /settings; the focus timer is offered on Now tickets, survives a reload and ends with Done or Snooze."""

import datetime as dt
import re

import pytest
from browser_harness import browser, drive
from fastapi.testclient import TestClient

from desk import activity, identity, motivation as mot, ops, plainwords
from desk.ui.app import create_app

JSON = {"Accept": "application/json"}
TODAY = dt.date(2026, 11, 18)  # a Wednesday


@pytest.fixture
def as_owner():
    token = identity.CURRENT.set(identity.Actor("james", "person", "browser", "ui", "os-user", True))
    yield
    identity.CURRENT.reset(token)


def _review(store, title="Build the thing", owner="james", assignee="patch"):
    t = ops.new(store, title, "desk", "feature")
    t.meta.update({"status": "review", "owner": owner, "assignee": assignee})
    t.set_section("Report", "Built and tested.")
    store.save(t)
    return store.get(t.id)


def _status_event(store, tid, day, new, actor="james", kind="owner", hm="10:00:00", confirmed=True):
    activity.append(store.home, [activity.Event(tid, f"{day.isoformat()}T{hm}", actor, kind, "ui", "field", "status", "review", new,
                                                f"status: review → {new}", confirmed=confirmed)])


@pytest.fixture
def owner_listed(store, monkeypatch):
    """The web server runs as an operating-system user listed for the owner in people.yaml, so web writes are confirmed as the owner."""
    from desk import people

    monkeypatch.setattr(identity, "os_user", lambda: "owner-login")
    people.link_os_user(store.home, "james", "owner-login")


def test_decided_today_counts_the_owners_decisions_once_each_and_nobody_elses(store, as_owner):
    """Protects the definition: the owner's last status change of the day into approved, changes_requested, rejected or done."""
    a, b, c, d = (_review(store, f"Ticket {i}") for i in range(4))
    ops.decide(store, a, True, "Ship it.", actor="james")
    ops.done(store, store.get(a.id))  # approved then closed: still one ticket decided
    ops.decide(store, b, False, "Needs a test first.", actor="james")
    ops.decide(store, c, True, "Fine.", actor="james")
    today = dt.date.today()
    _status_event(store, c.id, today, "review", hm="23:59:59")  # an undo puts it back: no longer decided
    _status_event(store, d.id, today, "approved", actor="patch", kind="main")  # someone else's decision never counts
    counts = mot.daily_counts(store.home, "james", today)
    assert counts.get(today) == 2, counts
    assert mot.state(store.home, "james")["decided"] == 2


def test_a_claimed_owner_name_never_counts_as_the_owners_decision(store):
    """Protects decision 110: an approval made under the owner's name but only claimed (--as, DESK_ACTOR, an assumed owner) never counts."""
    a, b = _review(store, "Claimed on the command line"), _review(store, "Claimed in the browser")
    claimed = identity.resolve_cli(store, claimed="james", env={"CLAUDECODE": "1"})
    assert claimed.name == "james" and not claimed.confirmed
    token = identity.CURRENT.set(claimed)
    try:
        ops.decide(store, a, True, "Ship it, looks good.", actor="james")
    finally:
        identity.CURRENT.reset(token)
    ev = [e for e in activity.read(store.home, a.id) if e.field == "status"][-1]
    assert (ev.actor, ev.kind, ev.new, ev.confirmed) == ("james", "owner", "approved", False)
    c = TestClient(create_app(store.home))  # nobody listed for this machine: the owner is only assumed
    r = c.post(f"/ticket/{b.id}/decide", data={"token": c.app.state.tokens.issue(), "action": "approve", "words": "Yes, go."}, headers=JSON)
    assert r.status_code == 200 and store.get(b.id).status == "approved"
    _status_event(store, a.id, dt.date.today(), "done", hm="23:59:58", confirmed=False)
    assert mot.state(store.home, "james")["decided"] == 0
    assert c.get("/motivation/state.json").json()["decided"] == 0


def test_streak_runs_back_from_yesterday_skips_weekends_and_adds_today_once_met():
    """Protects the streak rule: a weekday below the goal ends it, a weekend below it does not, today adds only when met."""
    goal = 2
    mon, tue, sat, sun = TODAY - dt.timedelta(days=2), TODAY - dt.timedelta(days=1), TODAY - dt.timedelta(days=4), TODAY - dt.timedelta(days=3)
    fri, thu = TODAY - dt.timedelta(days=5), TODAY - dt.timedelta(days=6)
    counts = {tue: 2, mon: 3, sun: 0, sat: 1, fri: 2, thu: 0}
    assert mot.streak(counts, TODAY, goal) == 3  # tue, mon, (weekend skipped), fri; thu below the goal ends it
    assert mot.streak({**counts, TODAY: 2}, TODAY, goal) == 4
    assert mot.streak({**counts, sat: 2}, TODAY, goal) == 4  # a weekend day that met the goal adds
    assert mot.streak({**counts, mon: 1}, TODAY, goal) == 1  # a missed Monday ends it after Tuesday
    assert mot.streak(counts, TODAY, 0) == 0  # goal off: no streak


def test_streak_is_computed_from_past_days_in_the_event_log(store):
    """Protects: the streak is read from events, not stored anywhere."""
    t = [_review(store, f"Past {i}") for i in range(4)]
    y, yy = TODAY - dt.timedelta(days=1), TODAY - dt.timedelta(days=2)
    for x in t[:2]:
        _status_event(store, x.id, y, "approved")
    for x in t[2:]:
        _status_event(store, x.id, yy, "rejected")
    s = mot.state(store.home, "james", today=TODAY, cfg={**mot.DEFAULTS, "goal": 2})
    assert (s["decided"], s["streak"], s["met"]) == (0, 2, False), s
    assert s["text"] == "0 of 2 decided today · 2-day streak"
    assert not list((store.home / ".desk").glob("*streak*"))


def test_approving_from_the_web_puts_the_header_count_up_by_one(store, owner_listed):
    """Protects How to validate: approve one ask, the decided-today count goes up by one."""
    t = _review(store, "Approve: the plan")
    c = TestClient(create_app(store.home))
    before = c.get("/motivation/state.json").json()["decided"]
    r = c.post(f"/ticket/{t.id}/decide", data={"token": c.app.state.tokens.issue(), "action": "approve", "words": "Yes, go."}, headers=JSON)
    assert r.status_code == 200, r.text
    after = c.get("/motivation/state.json").json()
    assert after["decided"] == before + 1 and after["text"].startswith(f"{before + 1} of 5 decided today")


def test_empty_needs_me_says_so_and_lists_what_agents_are_doing(store):
    """Protects the empty-state reward: nothing needs the owner, so Today lists claimed and in-progress work with who and how long."""
    w = ops.new(store, "Wire the export", "desk", "feature")
    w.meta.update({"status": "in_progress", "owner": "main", "assignee": "patch"})
    store.save(w)
    mine = ops.new(store, "My own errand", "desk", "task")
    mine.meta.update({"status": "claimed", "owner": "main", "assignee": "james"})
    store.save(mine)
    c = TestClient(create_app(store.home))
    page = c.get("/").text
    block = page[page.index('id="agents-at-work"'):]
    block = block[:block.index("</div>")]
    assert "Nothing needs you right now" in page
    assert w.id in block and "Wire the export" in block and "patch" in block.lower() and "since today" in block
    assert mine.id not in block  # the owner's own work is not agent work
    rows = mot.agents_at_work(store)["rows"]
    assert [r["id"] for r in rows] == [w.id] and rows[0]["status"] == "in_progress"


def test_the_agents_list_is_not_drawn_while_something_needs_the_owner(store):
    """Protects: the reward shows only when Needs me is empty."""
    t = ops.new(store, "Approve: the logo", "desk", "decision")
    t.meta.update({"status": "ready", "owner": "james"})
    store.save(t)
    page = TestClient(create_app(store.home)).get("/").text
    assert 'id="agents-at-work"' not in page


def test_start_focus_is_offered_on_now_tickets_only(store):
    """Protects: the focus timer belongs to the Now lane (p0, an emergency, or lane: now)."""
    now = ops.new(store, "Fix the outage", "desk", "bug", priority="p0")
    planned = ops.new(store, "Tidy the docs", "desk", "docs")
    c = TestClient(create_app(store.home))
    assert 'data-focus-start="%s"' % now.id in c.get(f"/ticket/{now.id}").text
    assert "data-focus-start" not in c.get(f"/ticket/{planned.id}").text
    assert 'data-minutes="25"' in c.get(f"/ticket/{now.id}").text


def test_motivation_switches_save_and_change_the_pages(store):
    """Protects the settings switch: celebration and focus off reach the header and the ticket page; bad numbers save nothing."""
    now = ops.new(store, "Fix the outage", "desk", "bug", priority="p0")
    c = TestClient(create_app(store.home))
    page = c.get("/settings").text
    assert '<legend class="label">Motivation</legend>' in page and 'action="/settings/motivation"' in page
    assert 'data-celebrate="1"' in page
    r = c.post("/settings/motivation", data={"focus": "off", "focus_minutes": "40", "goal": "3", "count": "on", "celebrate": "off"}, headers=JSON)
    assert r.status_code == 200 and r.json()["ok"]
    assert mot.load(store.home) == {"focus": False, "focus_minutes": 40, "goal": 3, "count": True, "celebrate": False}
    assert 'data-celebrate="0"' in c.get("/").text
    assert "data-focus-start" not in c.get(f"/ticket/{now.id}").text
    assert c.get("/motivation/state.json").json()["goal"] == 3
    bad = c.post("/settings/motivation", data={"focus": "on", "focus_minutes": "500", "goal": "3"}, headers=JSON)
    assert bad.status_code == 400 and "Nothing was saved" in bad.json()["error"]
    assert mot.load(store.home)["focus_minutes"] == 40
    assert (store.home / ".desk" / "motivation.toml").read_bytes().count(b"\r") == 0


def test_style_save_keeps_the_motivation_settings(store):
    """Protects: the style form rewrites settings.toml whole, so Motivation keeps its own file."""
    mot.save(store.home, focus=True, focus_minutes=30, goal=4, count=True, celebrate=False)
    c = TestClient(create_app(store.home))
    c.post("/settings", data={"name": "cat", "dog": "drew"}, headers=JSON)
    assert mot.load(store.home)["goal"] == 4 and mot.load(store.home)["celebrate"] is False


def test_snooze_from_the_timer_sets_tomorrow_in_one_undoable_batch(store):
    """Protects: Snooze writes snooze_until through Store.save inside a batch and hands back the undo toast's parameters."""
    t = ops.new(store, "Fix the outage", "desk", "bug", priority="p0")
    c = TestClient(create_app(store.home))
    r = c.post(f"/focus/snooze/{t.id}", data={"token": c.app.state.tokens.issue()}, follow_redirects=False)
    assert r.status_code == 303
    loc = r.headers["location"]
    batch = re.search(r"undo=(b-[0-9a-f]{12})", loc).group(1)
    assert "msg=" in loc
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    assert str(store.get(t.id).meta["snooze_until"]) == tomorrow
    ev = [e for e in activity.read(store.home, t.id) if e.field == "snooze_until"]
    assert ev and ev[-1].batch == batch
    again = c.post(f"/focus/snooze/{t.id}", data={"token": "used-or-made-up"}, headers=JSON)
    assert again.status_code == 409 and again.json()["token"]


def test_new_motivation_words_are_plain():
    """Protects DESK-0253 for the new screen text: grade 10 or under and no jargon word."""
    from pathlib import Path

    folder = Path(__file__).resolve().parent.parent / "src" / "desk" / "ui" / "templates"
    for name in ("_motivation_head.html", "_motivation_focus.html", "_motivation_agents.html", "_motivation_settings.html"):
        raw = (folder / name).read_text(encoding="utf-8")
        text = re.sub(r"\{#.*?#\}|\{%.*?%\}|\{\{.*?\}\}|<[^>]+>", " ", raw, flags=re.S)
        text = " ".join(text.split())
        assert not plainwords.jargon(text), (name, plainwords.jargon(text))
        g = plainwords.grade(text)
        assert g is None or g <= plainwords.MAX_GRADE, (name, g)


def test_the_celebration_honours_reduced_motion_and_the_switch():
    """Protects: no animation under prefers-reduced-motion, and the switch turns the celebration off."""
    from pathlib import Path

    static = Path(__file__).resolve().parent.parent / "src" / "desk" / "ui" / "static"
    css, js = (static / "motivation.css").read_text(encoding="utf-8"), (static / "motivation.js").read_text(encoding="utf-8")
    reduced = css[css.index("@media (prefers-reduced-motion:reduce)"):]
    assert "animation:none" in reduced.split("}")[0] and ".mot-burst{display:none" in reduced
    assert 'matchMedia("(prefers-reduced-motion: reduce)")' in js and 'getAttribute("data-celebrate") !== "1"' in js


FOCUS = """<script>addEventListener('load',function(){
var out={},sleep=function(ms){return new Promise(function(r){setTimeout(r,ms);});};
(async function(){
 var step=sessionStorage.getItem('t-step')||'0';
 if(step==='0'){sessionStorage.setItem('t-step','1');document.getElementById('focus-start').click();await sleep(300);location.reload();return;}
 var f=document.getElementById('mot-focus');
 if(step==='1'){await sleep(1300);sessionStorage.setItem('t-clock',f.hidden?'':f.textContent);
   var s=JSON.parse(localStorage.getItem('desk-focus'));s.end=Date.now()-1000;localStorage.setItem('desk-focus',JSON.stringify(s));
   sessionStorage.setItem('t-step','2');location.reload();return;}
 await sleep(600);
 out.clock=sessionStorage.getItem('t-clock');out.ended=f.textContent;
 out.buttons=[].map.call(f.querySelectorAll('button'),function(b){return b.textContent;});
 fetch('/m',{method:'POST',body:JSON.stringify(out)});})();});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_focus_timer_survives_a_reload_and_ends_with_done_or_snooze(store):
    """Protects Acceptance 1: the timer is in the header after a reload and ends with the two buttons."""
    t = ops.new(store, "Fix the outage", "desk", "bug", priority="p0")
    got = drive(store, FOCUS, f"/ticket/{t.id}")
    assert got["clock"].startswith(f"Focus {t.id} · 2"), got  # 24:5x or 25:00 left
    assert "Time is up" in got["ended"] and "done or snooze?" in got["ended"], got
    assert got["buttons"][:2] == ["Done", "Snooze till tomorrow"], got


def test_the_agents_list_follows_todays_filters(store):
    """Protects: a Tag (or Company, Track, Sprint, Type) filter on Today narrows the agents list like every other row."""
    a = ops.new(store, "Tagged work", "desk", "feature")
    a.meta.update({"status": "in_progress", "owner": "main", "assignee": "patch", "tags": ["export"]})
    store.save(a)
    b = ops.new(store, "Other work", "desk", "feature")
    b.meta.update({"status": "in_progress", "owner": "main", "assignee": "patch"})
    store.save(b)
    page = TestClient(create_app(store.home)).get("/?tag=export").text
    block = page[page.index('id="agents-at-work"'):]
    block = block[:block.index("</div>")]
    assert a.id in block and b.id not in block
