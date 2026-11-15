"""DESK-0288: the daily brief, the Today tiles, flow time, the calendar feed, the weekly review that fills itself, and the pages
/brief, /brief/week, /flow, /repeats, /company and /deadlines.ics. Every test builds its own store in a temporary folder."""

import datetime as dt
import re

import pytest
from fastapi.testclient import TestClient

from desk import activity, brief, flow, ics, ops, review as rv
from desk.cli import main
from desk.ui.app import create_app

TODAY = dt.date.today()
TOMORROW = TODAY + dt.timedelta(days=1)


def mk(store, title, status="ready", type_="feature", owner="main", **meta):
    t = ops.new(store, title, "desk", type_, owner=owner)
    t.meta.update(status=status, **meta)
    if status in ("review", "approved", "done"):
        t.set_section("Report", "Built it.")
    if status in ("approved", "done"):
        t.append_line("Decision", f'- {TODAY.isoformat()} · james · approved · "Looks right."')
    store.save(t)
    return t


def ship(store, t):
    """Move a ticket to done through a save, so the event log records the move today."""
    t = store.get(t.id)
    t.append_line("Decision", f'- {TODAY.isoformat()} · james · approved · "Ship it."')
    t.meta["status"] = "done"
    store.save(t)
    return t


@pytest.fixture
def fx(store):
    """A small store: one review waiting on the owner, one snoozed, one blocked on him, deadlines near and far, one shipped today."""
    (store.projects_dir / "management.md").write_text("# Management\n", encoding="utf-8")
    rev = mk(store, "Built thing waiting for review", status="review")
    snoozed = mk(store, "Snoozed review thing", status="review", snooze_until=(TODAY + dt.timedelta(days=3)).isoformat())
    blocked = mk(store, "Needs the owner's login", status="blocked", blocked_on="james")
    near = mk(store, "Near hard deadline", deadline=(TODAY + dt.timedelta(days=3)).isoformat(), deadline_kind="hard")
    far = mk(store, "Far hard deadline", deadline=(TODAY + dt.timedelta(days=20)).isoformat(), deadline_kind="hard")
    shipped = mk(store, "Shipped thing", status="approved")
    shipped.set_section("Evidence", "Merged in https://github.com/example/desk/pull/42 and checked.")
    store.save(shipped)
    ship(store, shipped)
    return {"rev": rev, "snoozed": snoozed, "blocked": blocked, "near": near, "far": far, "shipped": shipped}


def test_brief_sections_from_a_fixture_store(store, fx):
    """Protects the three answers of /brief: decisions (snoozed left out, blocked-on-owner kept), this week's hard deadlines, yesterday's ships."""
    b = brief.build_brief(store, TOMORROW)  # "yesterday" is today, when the fixture shipped
    ids = [d["id"] for d in b["decisions"]]
    assert fx["rev"].id in ids and fx["blocked"].id in ids
    assert fx["snoozed"].id not in ids
    rev = next(d for d in b["decisions"] if d["id"] == fx["rev"].id)
    assert [a["label"] for a in rev["answers"]] == ["Approve", "Send back"]
    assert rev["answers"][0]["action"] == "approve" and rev["answers"][0]["needs_words"] and not rev["answers"][0]["words"]
    assert rev["answers"][1]["needs_words"] and rev["snooze_href"].startswith(f"/ticket/{fx['rev'].id}")
    assert [d["id"] for d in b["deadlines"]] == [fx["near"].id]
    assert [s["id"] for s in b["shipped"]] == [fx["shipped"].id]
    assert b["shipped"][0]["link"] == "https://github.com/example/desk/pull/42"
    assert b["counts"] == {"decisions": len(ids), "deadlines": 1, "shipped": 1}
    assert brief.build_brief(store, TODAY)["shipped"] == []  # shipped today is not "yesterday" yet


def test_brief_text_is_ids_titles_and_counts_only(store, fx):
    """Protects the notification's privacy line: no Context, Report or Evidence text leaves Desk in the plain-text brief."""
    t = store.get(fx["rev"].id)
    t.set_section("Context", "A private detail that must stay inside the ticket.")
    store.save(t)
    text = brief.brief_text(brief.build_brief(store, TOMORROW))
    assert text.startswith(f"Desk brief {TOMORROW.isoformat()}: 2 decisions, 1 deadline this week, 1 shipped yesterday")
    assert f"- {fx['rev'].id}: Built thing waiting for review" in text and f"- {fx['shipped'].id}: Shipped thing" in text
    assert "private detail" not in text and "Built it" not in text and "github.com" not in text


def test_lane_balance_and_review_debt(store, fx):
    """Protects the Today tiles: lane counts of open tickets, created and closed in the last 7 days from the log, review age."""
    urgent = mk(store, "Urgent thing", priority="p0")
    ts = store.tickets()
    b = brief.balance(store, ts, TODAY)
    assert b["counts"]["now"] == 1 and b["now_oldest"][0] == urgent.id
    assert sum(b["counts"].values()) == sum(1 for t in ts if t.is_open)
    assert b["created"] == len(ts) and b["closed"] == 1
    d = brief.review_debt(store, ts, TODAY + dt.timedelta(days=8))
    assert d["count"] == 2 and d["oldest_days"] == 8 and d["level"] == "high"


def _flow_ticket(store, events, status):
    t = store.create("Flow fixture", "desk", "feature", owner="main")
    t.meta["status"] = status
    t.set_section("Report", "Built it.")
    t.write()  # no save: the test writes the log itself, with known times
    base = dt.datetime(2026, 11, 1, 9, 0)
    evs = [activity.Event(t.id, base.isoformat(), "main", "main", "cli", "created", "created", "", "Flow fixture", "Created")]
    for hours, field, old, new in events:
        evs.append(activity.Event(t.id, (base + dt.timedelta(hours=hours)).isoformat(), "main", "main", "cli", "field", field, old, new, f"{field}: {old} → {new}"))
    activity.append(store.home, evs)
    return t, base


def test_flow_time_from_events(store):
    """Protects idea 77 / DESK-0242: time in each status from the log, and the four parts add up to the whole cycle time."""
    t, base = _flow_ticket(store, [(1, "status", "ready", "in_progress"), (4, "status", "in_progress", "review"),
                                   (10, "status", "review", "changes_requested"), (12, "status", "changes_requested", "in_progress"),
                                   (13, "blocked_on", "", "james"), (13, "status", "in_progress", "blocked"),
                                   (15, "status", "blocked", "in_progress"), (16, "status", "in_progress", "done")], "done")
    f = flow.of(store, store.get(t.id), now=base + dt.timedelta(days=30))
    h = {k: v / 3600 for k, v in f["parts"].items()}
    assert h == {"queued": 1, "worked": 3 + 2 + 1 + 1, "waiting": 6 + 2, "held": 0}
    assert f["total"] == 16 * 3600 and f["closed"]
    assert f["by_status"]["review"] == 6 * 3600 and f["by_status"]["blocked"] == 2 * 3600
    assert flow.human(f["total"]) == "16 h"


def test_flow_of_an_open_ticket_runs_to_now_and_blocked_on_someone_else_is_held_up(store):
    """Protects the open case: the last status counts until now, and blocked on another person is held up, not waiting on the owner."""
    t, base = _flow_ticket(store, [(2, "blocked_on", "", "chaz"), (2, "status", "ready", "blocked")], "blocked")
    f = flow.of(store, store.get(t.id), now=base + dt.timedelta(hours=5))
    assert f["parts"]["queued"] == 2 * 3600 and f["parts"]["held"] == 3 * 3600 and f["parts"]["waiting"] == 0 and not f["closed"]


def test_ics_is_valid_rfc5545(store, fx):
    """Protects /deadlines.ics: CRLF line ends, 75-octet folding, escaped text, one stable UID per date, open tickets only."""
    t = store.get(fx["near"].id)
    t.meta.update(title="Near hard deadline, with; a comma and a very long title that will need folding across lines in the feed",
                  owner="james", review_by=(TODAY + dt.timedelta(days=2)).isoformat())
    store.save(t)
    body = ics.calendar(store.tickets(), "james", base_url="http://127.0.0.1:8765/")
    assert body.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n") and body.endswith("END:VCALENDAR\r\n")
    assert "\n" not in body.replace("\r\n", "")
    for line in body.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75
    unfolded = body.replace("\r\n ", "")
    uids = re.findall(r"^UID:(.+)$", unfolded, re.M)
    assert len(uids) == len(set(uids)) == unfolded.count("BEGIN:VEVENT") == 3  # near deadline, its review-by, the far deadline
    assert f"UID:{t.id}-deadline@desk" in unfolded and f"UID:{t.id}-review-by@desk" in unfolded
    assert "with\\; a comma" in unfolded and "deadline\\, with" in unfolded
    assert f"DTSTART;VALUE=DATE:{(TODAY + dt.timedelta(days=3)):%Y%m%d}" in unfolded
    assert fx["shipped"].id not in unfolded  # done tickets wait on nobody


def test_review_new_fills_the_week_sections(store, fx, capsys):
    """Protects idea 75: `drew review new` writes decided, shipped, slipped, the balance and one recommended first version by itself."""
    mvp = mk(store, "Smallest mvp piece", size="m", tags=["mvp"])
    small = mk(store, "Tiny planned thing", size="xs")
    slip = mk(store, "Missed its date", review_by=(TODAY - dt.timedelta(days=1)).isoformat() if TODAY.weekday() else TODAY.isoformat())
    main(["--home", str(store.home), "review", "new", "--project", "management"])
    rt = next(t for t in store.tickets() if rv.is_review_ticket(t))
    rep = rt.section("Report")
    assert rv.WEEK_START in rep and rv.WEEK_END in rep
    assert f"- Shipped: 1: {fx['shipped'].id} Shipped thing (https://github.com/example/desk/pull/42)" in rep
    assert re.search(rf"- Decided: \d+: .*{fx['shipped'].id}", rep)
    assert f"Recommended first version to build (MVP): {mvp.id} Smallest mvp piece" in rep and small.id not in rep.split("MVP")[-1]
    if TODAY.weekday():  # on a Monday nothing earlier in the week can have slipped
        assert f"{slip.id} Missed its date" in rep
    assert "Where the work sits: now" in rep


def test_mvp_pick_falls_back_to_the_smallest_planned(store):
    """Protects the fallback: with nothing tagged mvp the smallest planned ticket is recommended."""
    mk(store, "Medium thing", size="m")
    small = mk(store, "Extra small thing", size="xs")
    mk(store, "Idea in the inbox", status="inbox", size="xs")  # inbox is Later, not Planned
    assert brief.mvp_pick(store.tickets()).id == small.id


def test_pages_render_answer_first(store, fx):
    """Protects the pages: /brief puts decisions before deadlines before shipped; the other pages answer; Today and the ticket page show the tiles."""
    c = TestClient(create_app(store.home))
    r = c.get("/brief")
    assert r.status_code == 200
    body = r.text
    assert body.index('id="decide"') < body.index('id="deadlines"') < body.index('id="shipped"')
    assert "2 decisions for you today" in body and f'action="/ticket/{fx["rev"].id}/decide"' in body
    assert fx["snoozed"].id not in body.split('id="deadlines"')[0]
    for url in ("/brief/week", "/flow", "/repeats", "/company"):
        assert c.get(url).status_code == 200, url
    assert c.get("/company/no-such-company").status_code == 404
    unsorted = c.get("/company/unsorted")
    assert unsorted.status_code == 200 and "Next decision" in unsorted.text
    today = c.get("/").text
    assert 'id="work-balance"' in today and 'id="review-debt"' in today
    assert 'id="flow"' in c.get(f"/ticket/{fx['shipped'].id}").text
    cal = c.get("/deadlines.ics")
    assert cal.status_code == 200 and cal.headers["content-type"].startswith("text/calendar") and cal.content.startswith(b"BEGIN:VCALENDAR\r\n")


def test_approve_from_the_brief_uses_the_decide_route(store, fx):
    """Protects the brief's answer: the Approve form on /brief, with the owner's typed words, posts what the decide route accepts."""
    c = TestClient(create_app(store.home))
    page = c.get("/brief").text
    form = page.split(f'action="/ticket/{fx["rev"].id}/decide"')[1].split("</form>")[0]
    data = dict(re.findall(r'name="(\w+)" value="([^"]*)"', form), words="Looks right to me.")
    r = c.post(f"/ticket/{fx['rev'].id}/decide", data=data, headers={"Accept": "application/json"})
    assert r.status_code == 200 and r.json()["ok"]
    assert store.get(fx["rev"].id).status == "approved"
    assert fx["rev"].id not in [d["id"] for d in brief.build_brief(store)["decisions"]]


def test_repeats_by_week_groups_the_owner_asking_twice(store):
    """Protects idea 79: two comments of the owner on one ticket asking for the same outcome show under the week of the second."""
    t = mk(store, "Something asked for twice")
    t.append_line("Comments", f"- {TODAY.isoformat()} 09:00 · james · please make the export button download the weekly report file")
    t.append_line("Comments", f"- {TODAY.isoformat()} 15:00 · james · again: make the export button download the weekly report file please")
    store.save(t)
    groups = brief.repeats_by_week(store, 2, TODAY)
    assert len(groups) == 1 and groups[0]["rows"][0]["id"] == t.id
    assert groups[0]["monday"] == TODAY - dt.timedelta(days=TODAY.weekday())


def test_drew_brief_prints_the_plain_text(store, fx, capsys):
    """Protects `drew brief`: the same plain text the notification package sends."""
    main(["--home", str(store.home), "brief", "--day", TOMORROW.isoformat()])
    out = capsys.readouterr().out
    assert out.startswith(f"Desk brief {TOMORROW.isoformat()}: 2 decisions") and fx["shipped"].id in out


def test_new_ui_sentences_read_plainly():
    """Protects DESK-0253 for the new pages: the explanation sentences grade 10 or below and use no listed jargon without a gloss."""
    from desk import plainwords

    from pathlib import Path

    tpl = Path(__file__).resolve().parents[1] / "src" / "desk" / "ui" / "templates"
    text = " ".join((tpl / n).read_text(encoding="utf-8") for n in ("brief.html", "brief_week.html", "_brief_tiles.html", "repeats.html", "company.html", "flow.html", "_flow.html"))
    words = re.sub(r"\{[{%#].*?[}%#]\}|<[^>]+>", " ", text, flags=re.S)
    words = re.sub(r"\s+", " ", words)
    assert plainwords.jargon(words) == []
    sentences = ". ".join(s for s in re.split(r"(?<=[.!?])\s", words) if len(s.split()) >= 6)
    assert (plainwords.grade(sentences) or 0) <= 10


# ---- fixer regressions (DESK-0288 review findings) --------------------------------------------------------------------------

def test_past_week_review_lists_only_dates_inside_that_week_as_slipped(store):
    """Protects the weekly review of a past week: a review-by date after that week's Sunday is not listed as slipped."""
    (store.projects_dir / "management.md").write_text("# Management\n", encoding="utf-8")
    this_monday = rv.monday_of(TODAY)
    last_monday = this_monday - dt.timedelta(days=7)
    later = this_monday + dt.timedelta(days=3)  # a fixed "today" inside this week, whatever day the test runs
    inside = mk(store, "Review-by inside last week", review_by=(last_monday + dt.timedelta(days=1)).isoformat())
    after = mk(store, "Review-by after last week", review_by=this_monday.isoformat())
    w = brief.weekly(store, last_monday, later)
    assert [r["id"] for r in w["slipped"]] == [inside.id] and w["last"] == last_monday + dt.timedelta(days=6)
    rt, _ = rv.create(store, last_monday, project="management")
    rv.apply(store, store.get(rt.id), today=later)
    line = next(x for x in store.get(rt.id).section("Report").splitlines() if x.startswith("- Slipped"))
    assert inside.id in line and after.id not in line


def test_brief_approve_needs_the_owners_typed_words(store, fx):
    """Protects rule 10 on /brief: Approve carries no fixed sentence, asks for typed words, and records exactly those words."""
    rev = next(d for d in brief.build_brief(store, TOMORROW)["decisions"] if d["id"] == fx["rev"].id)
    assert all(a["needs_words"] and not a["words"] for a in rev["answers"])
    c = TestClient(create_app(store.home))
    page = c.get("/brief").text
    assert "Approved from the daily brief" not in page
    form = page.split(f'action="/ticket/{fx["rev"].id}/decide"')[1].split("</form>")[0]
    assert 'name="action" value="approve"' in form and 'name="words" required' in form
    assert 'type="hidden" name="words"' not in form
    data = dict(re.findall(r'name="(\w+)" value="([^"]*)"', form))
    c.post(f"/ticket/{fx['rev'].id}/decide", data=data, headers={"Accept": "application/json"})
    assert store.get(fx["rev"].id).status == "review"  # no words, nothing recorded
    data.update(token=c.app.state.tokens.issue(), words="Ship it as it stands.")
    assert c.post(f"/ticket/{fx['rev'].id}/decide", data=data, headers={"Accept": "application/json"}).json()["ok"]
    t = store.get(fx["rev"].id)
    assert t.status == "approved" and '"Ship it as it stands."' in t.section("Decision")


def test_brief_answer_forms_only_for_the_owner(store, fx, monkeypatch):
    """Protects decision 110 on /brief: a viewer named as someone else sees the tiles but no answer forms."""
    from desk import identity, people

    monkeypatch.setattr(identity, "os_user", lambda: "someone-unlisted")
    people.add_person(store.home, "coworker", "A coworker")
    c = TestClient(create_app(store.home))
    assert f'action="/ticket/{fx["rev"].id}/decide"' in c.get("/brief").text  # the owner, assumed, may answer
    c.cookies.set("desk_who", "coworker")
    page = c.get("/brief").text
    assert fx["rev"].id in page and "/decide" not in page


def test_flow_skips_closed_time_and_counts_a_reopen(store):
    """Protects flow time on a ticket reopened after done: the closed gap is skipped and the time after the reopen counts."""
    t, base = _flow_ticket(store, [(1, "status", "ready", "in_progress"), (2, "status", "in_progress", "done"),
                                   (5, "status", "done", "in_progress")], "in_progress")
    f = flow.of(store, store.get(t.id), now=base + dt.timedelta(hours=10))
    assert f["total"] == 7 * 3600 and f["parts"]["worked"] == 6 * 3600 and f["by_status"].get("done", 0) == 0


def test_bad_query_values_fall_back_instead_of_422(store, fx):
    """Protects /repeats and /deadlines.ics: a bad query value gives the page with its default, never raw 422 JSON."""
    c = TestClient(create_app(store.home))
    assert c.get("/repeats?weeks=abc").status_code == 200
    cal = c.get("/deadlines.ics?all=yes")
    assert cal.status_code == 200 and cal.content.startswith(b"BEGIN:VCALENDAR\r\n")
