"""DESK-0336: /analytics counts what moved, who is active, what is waiting, how fast, what is happening now and what the checkers found,
all from the store's files. Every behaviour is checked three ways, by different methods:
  1. the module's numbers on a fixture store (unit),
  2. the same numbers recomputed here straight from the files, with no call into desk.analytics (independent recount),
  3. the route and the real page (HTTP, then a headless browser at 1280, 390 and 320 px, light and dark).
Edge cases: an empty store, a day with no events, an actor never seen, a damaged log line, a slow validate, a report with no verdict.
Protects: a number on the page that disagrees with the files, a live block that needs a reload, a page wider than a phone."""

import datetime as dt
import json
import re
import statistics
import threading
from pathlib import Path

import pytest
from browser_harness import browser, cdp_measure, live_app
from fastapi.testclient import TestClient

from desk import activity, analytics as an, ops
from desk.store import Store
from desk.ui.app import create_app

NEEDS_BROWSER = pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
NOW = dt.datetime(2026, 11, 19, 12, 0, 0)


def _ev(tid, ts, actor="claude-code", change="field", field="", old="", new="", what="", kind="code"):
    return activity.Event(ticket=tid, ts=ts, actor=actor, kind=kind, source="cli", change=change, field=field, old=old, new=new, what=what or f"{field}: {old} -> {new}")


def _mk(store, title, project, status, created, assignee="", updated=None, tags=None, done_at=None):
    t = ops.new(store, title, project)
    t.meta.update(status=status, created=created, assignee=assignee, updated=updated or created)
    if tags:
        t.meta["tags"] = tags
    if done_at:
        t.meta["done_at"] = done_at
    t.write()  # straight to disk: a fixture needs a done ticket without the approval line the real save would demand
    return t.id


@pytest.fixture
def world(store):
    """A store with known numbers. `now` is 2026-11-19 12:00. Returns the ids and the events written."""
    a = _mk(store, "A open ready", "jre", "ready", "2026-11-18", assignee="james")
    b = _mk(store, "B done", "jre", "done", "2026-11-10", assignee="main")
    c = _mk(store, "C rejected", "desk", "rejected", "2026-11-01", assignee="main", updated="2026-11-15")
    d = _mk(store, "D open review", "desk", "review", "2026-11-05", assignee="james")
    e = _mk(store, "E old inbox", "jre", "inbox", "2026-10-01", assignee="james", updated="2026-11-01", tags=["repetition"])
    f = _mk(store, "F open ready, chaz", "desk", "ready", "2026-11-19", assignee="chaz")
    g = _mk(store, "G done by date", "desk", "done", "2026-11-02", assignee="main", done_at="2026-11-12")
    import shutil

    shutil.rmtree(store.home / "events", ignore_errors=True)  # the saves above logged real-time events; the fixture writes its own
    evs = [
        _ev(b, "2026-11-11T08:00:00", field="status", old="inbox", new="ready"),
        _ev(b, "2026-11-12T08:00:00", field="status", old="ready", new="review"),
        _ev(b, "2026-11-14T08:00:00", field="status", old="review", new="done"),
        _ev(d, "2026-11-10T09:00:00", field="status", old="in_progress", new="review"),
        _ev(d, "2026-11-17T10:00:00", field="assignee", old="main", new="james"),
        _ev(a, "2026-11-19T10:00:00", actor="james", kind="owner", change="comment", field="comment", what="James commented"),
        _ev(a, "2026-11-19T11:30:00", change="comment", field="comment", what="Patch answered (see D-9999 and " + d + ")"),
        _ev(a, "2026-11-12T09:00:00", actor="chaz", kind="coworker", change="comment", field="comment", what="Chaz looked"),
        _ev(f, "2026-11-18T09:00:00", actor="subagent:abc", kind="subagent", change="comment", field="comment", what="agent note"),
        _ev(f, "2026-11-15T09:00:00", actor="qc-checker", kind="main", change="comment", field="comment", what="checker note"),
        _ev(f, "2026-11-01T09:00:00", actor="zed", kind="person", change="comment", field="comment", what="someone else"),
        _ev(f, "2026-11-19T09:00:00", change="git", field="git", what="Saved in git: x"),  # a commit copied from git: not counted
        _ev(f, "2026-11-19T11:50:00", actor="desk", kind="code", change="field", field="priority", old="p2", new="p1"),
    ]
    activity.append(store.home, evs)
    return dict(a=a, b=b, c=c, d=d, e=e, f=f, g=g)


# -- raw recount: no desk.analytics call ----------------------------------------------------

def raw_tickets(home):
    out = {}
    for p in (Path(home) / "tickets").glob("*.md"):
        fm = p.read_text(encoding="utf-8").split("---")[1]
        meta = dict(re.findall(r"^([a-z_]+): *(.*)$", fm, flags=re.M))
        out[meta["id"].strip('"')] = {k: v.strip('"') for k, v in meta.items()}
    return out


def raw_events(home):
    out = []
    for p in (Path(home) / "events").glob("*.jsonl"):
        out += [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    return out


def last_30_days():
    return [(NOW.date() - dt.timedelta(days=i)).isoformat() for i in range(29, -1, -1)]


# -- 1. flow --------------------------------------------------------------------------------

def test_flow_counts_match_a_recount_from_the_files(store, world):
    d = an.build(store, NOW)["flow"]
    raw, evs = raw_tickets(store.home), raw_events(store.home)
    days = last_30_days()
    created = {x: sum(1 for t in raw.values() if t["created"] == x) for x in days}
    closed_ev = {}
    for e in evs:
        if e["change"] == "field" and e["field"] == "status" and e["new"] in ("done", "rejected"):
            closed_ev[e["ticket"]] = e["ts"][:10]
    closed = dict.fromkeys(days, 0)
    for tid, t in raw.items():
        if t["status"] in ("done", "rejected"):
            day = closed_ev.get(tid) or t.get("done_at") or t["updated"]
            if day in closed:
                closed[day] += 1
    assert [x["created"] for x in d["days"]] == [created[x] for x in days]
    assert [x["closed"] for x in d["days"]] == [closed[x] for x in days]
    assert d["created_total"] == sum(created.values()) == 6  # E was created before the window
    assert d["closed_total"] == sum(closed.values()) == 3
    assert d["closed_from_file_dates"] == 2  # C from its last update, G from its done_at


def test_flow_open_by_status_and_project_match_the_files(store, world):
    d = an.build(store, NOW)["flow"]
    raw = raw_tickets(store.home)
    open_t = [t for t in raw.values() if t["status"] not in ("done", "rejected")]
    assert d["open_total"] == len(open_t) == 4
    assert d["by_status"] == {"ready": 2, "review": 1, "inbox": 1}
    by_proj = {}
    for t in open_t:
        by_proj[t["project"]] = by_proj.get(t["project"], 0) + 1
    assert {r["project"]: r["n"] for r in d["by_project"]} == by_proj == {"jre": 2, "desk": 2}
    assert d["other_projects"] == 0


def test_flow_top_ten_projects_and_the_rest_summed(store):
    for i in range(12):
        key = f"p{i:02d}"
        (store.projects_dir / f"{key}.md").write_text(f"# {key}\n", encoding="utf-8")
        for _ in range(12 - i):  # p00 has 12 open tickets, p11 has 1
            _mk(store, "x", key, "ready", "2026-11-18")
    d = an.build(store, NOW)["flow"]
    assert [r["project"] for r in d["by_project"]] == [f"p{i:02d}" for i in range(10)]
    assert (d["other_projects"], d["other_n"]) == (2, 2 + 1)


def test_flow_a_day_with_no_events_is_a_zero_not_a_gap(store, world):
    d = an.build(store, NOW)["flow"]
    assert len(d["days"]) == 30 and d["days"][0]["date"] == "2026-10-21" and d["days"][-1]["date"] == "2026-11-19"
    quiet = next(x for x in d["days"] if x["date"] == "2026-10-25")
    assert quiet == {"date": "2026-10-25", "created": 0, "closed": 0}


# -- 2. people ------------------------------------------------------------------------------

def test_people_last_activity_and_weekly_counts_match_a_recount(store, world):
    d = an.build(store, NOW)["people"]
    rows = {r["key"]: r for r in d["rows"]}
    evs = [e for e in raw_events(store.home) if e["change"] != "git"]

    def mine(names):
        return [e for e in evs if e["actor"] in names]

    for key, names in (("owner", {"james"}), ("patch", {"claude-code", "main", "desk", "patch"}), ("chaz", {"chaz"}), ("agents", {"subagent:abc", "qc-checker"})):
        got = mine(names)
        assert rows[key]["total"] == len([e for e in got if dt.datetime.fromisoformat(e["ts"]) <= NOW]), key
        assert rows[key]["last"].isoformat() == max(e["ts"] for e in got), key
        weeks = [0] * 4
        for e in got:
            w = (NOW.date() - dt.date.fromisoformat(e["ts"][:10])).days // 7
            if w < 4:
                weeks[3 - w] += 1
        assert rows[key]["weeks"] == weeks, key
    assert rows["owner"]["weeks"] == [0, 0, 0, 1]
    assert rows["patch"]["weeks"] == [0, 0, 3, 4]  # 11-10, 11-11, 11-12 are a week back; 11-14, 11-17 and two on 11-19 are this week
    assert rows["other"]["total"] == 1 and rows["other"]["label"] == "Other names"


def test_people_an_actor_never_seen_is_listed_and_named_in_the_idle_line(store, world):
    d = an.build(store, NOW)["people"]
    rows = {r["key"]: r for r in d["rows"]}
    for k in ("em", "joe", "bri", "bigd"):
        assert rows[k]["last"] is None and rows[k]["idle"] and rows[k]["weeks"] == [0, 0, 0, 0]
    assert rows["chaz"]["idle"]  # last seen 11-12, more than 7 days before 11-19 12:00? 7 d 3 h: yes
    assert an.idle_sentence(d["idle"]) == "No ticket activity in the last 7 days from Chaz, Em, Joe, Lily, Bri and Big D."
    assert an.idle_sentence(["Em"]) == "No ticket activity in the last 7 days from Em."
    assert an.idle_sentence([]) == "Everyone listed touched a ticket in the last 7 days."
    assert an.idle_sentence(["A", "B", "C"]) == "No ticket activity in the last 7 days from A, B and C."


def test_people_commits_copied_from_git_are_not_activity(store, world):
    rows = {r["key"]: r for r in an.build(store, NOW)["people"]["rows"]}
    assert rows["patch"]["total"] == 7  # b: 3 status, d: 2, a: 1 comment, f: 1 priority change
    # (git event on F at 09:00 on 11-19 is the only event after the 08:00 one and must not count)
    assert rows["patch"]["last"] == dt.datetime(2026, 11, 19, 11, 50)


# -- 3. waiting -----------------------------------------------------------------------------

def test_waiting_series_matches_a_replay_of_the_assignee_changes(store, world):
    w = an.build(store, NOW)["waiting"]
    raw, evs = raw_tickets(store.home), raw_events(store.home)
    ass = [e for e in evs if e["field"] == "assignee"]

    def held(tid, day_end):
        mine = sorted((e for e in ass if e["ticket"] == tid), key=lambda e: e["ts"])
        cur = raw[tid]["assignee"]
        if mine:
            cur = mine[0]["old"]
            for e in mine:
                if e["ts"] <= day_end:
                    cur = e["new"]
        return cur

    for row in w["queue_series"]:
        end = row["date"] + "T23:59:59"
        n = 0
        for tid, t in raw.items():
            if t["created"] > row["date"] or held(tid, end) != "james":
                continue
            closed = t["status"] in ("done", "rejected")
            if closed:
                st = [e["ts"] for e in evs if e["ticket"] == tid and e["field"] == "status" and e["new"] in ("done", "rejected")]
                when = (max(st) if st else (t.get("done_at") or t["updated"]) + "T12:00:00")
                if when <= end:
                    continue
            n += 1
        assert row["n"] == n, row["date"]
    today = w["queue_series"][-1]["n"]
    assert today == w["queue_now"] == len([t for t in raw.values() if t["assignee"] == "james" and t["status"] not in ("done", "rejected")]) == 3
    d_before = next(x for x in w["queue_series"] if x["date"] == "2026-11-16")["n"]
    d_after = next(x for x in w["queue_series"] if x["date"] == "2026-11-17")["n"]
    assert d_after == d_before + 1  # D was reassigned to him on 11-17 10:00
    assert w["first_logged"] == "2026-11-17"


def test_waiting_oldest_and_stuck(store, world):
    w = an.build(store, NOW)["waiting"]
    assert [r["id"] for r in w["oldest"]] == [world["e"], world["d"], world["a"]]  # E since its creation 10-01, D since 11-17 10:00, A since 11-18
    assert w["oldest"][1]["days"] == 2 and w["oldest"][0]["days"] == 49
    stuck = {r["status"]: r for r in w["stuck"]}
    assert set(stuck) == {"review", "inbox"}  # A and F are younger than 7 days
    assert stuck["review"]["oldest"][0] == {"id": world["d"], "days": 9, "src": "log"}
    assert stuck["inbox"]["oldest"][0]["id"] == world["e"] and stuck["inbox"]["oldest"][0]["src"] == "updated"
    assert w["stuck_total"] == 2


def test_waiting_with_no_assignment_changes_says_so_and_shows_todays_count(store):
    _mk(store, "only one", "jre", "ready", "2026-11-18", assignee="james")
    w = an.build(store, NOW)["waiting"]
    assert w["first_logged"] == "" and w["queue_now"] == 1 and w["queue_series"][-1]["n"] == 1 and w["queue_series"][0]["n"] == 0
    page = TestClient(create_app(store.home)).get("/analytics").text
    assert "No assignment change is logged yet" in page


# -- 4. speed -------------------------------------------------------------------------------

def test_speed_medians_match_a_recount(store, world):
    s = an.build(store, NOW)["speed"]
    assert s["ready_to_review"] == 1.0 and s["ready_to_review_n"] == 1
    assert s["review_to_done"] == 2.0 and s["review_to_done_n"] == 1
    # a second ticket, so the median is of two values: 3 days and 1 day ready to review -> 2
    t = _mk(store, "H", "jre", "review", "2026-11-05")
    activity.append(store.home, [_ev(t, "2026-11-13T00:00:00", field="status", old="x", new="ready"), _ev(t, "2026-11-16T00:00:00", field="status", old="ready", new="review")])
    s = an.build(store, NOW)["speed"]
    assert s["ready_to_review"] == statistics.median([1.0, 3.0]) == 2.0 and s["ready_to_review_n"] == 2


def test_speed_no_data_is_none_and_old_moves_are_left_out(store, world):
    t = _mk(store, "old", "jre", "done", "2026-09-01")
    activity.append(store.home, [_ev(t, "2026-09-02T00:00:00", field="status", old="ready", new="review"), _ev(t, "2026-09-05T00:00:00", field="status", old="review", new="done")])
    assert an.build(store, NOW)["speed"]["review_to_done_n"] == 1  # still only B's
    assert an.speed([], {}, NOW) == {"ready_to_review": None, "ready_to_review_n": 0, "review_to_done": None, "review_to_done_n": 0}


# -- 5. live --------------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_real_validate(monkeypatch):
    """A page never runs the real `drew validate` (seconds on a big store) inside a test, and no test reads another's git cache."""
    monkeypatch.setattr(an, "VALIDATION", an.Validation(runner=lambda s: ([], [])))
    an._GIT_CACHE.clear()


def _validation():
    return an.Validation(runner=lambda s: ([], ["w1", "w2"]))


def test_live_lists_the_last_hour_newest_first_and_links_only_real_tickets(store, world):
    an._GIT_CACHE.clear()
    L = an.live(store, NOW, validation=_validation(), wait=True)
    assert [e["time"] for e in L["events"]] == ["11:50:00", "11:30:00"]  # git event and older events left out
    assert L["count"] == 2 and L["quiet"] == "2 events in the last hour."
    patch = L["events"][1]
    assert patch["who"] == "Patch" and patch["ticket"] == world["a"]
    ids = {p["t"]: p["id"] for p in patch["parts"] if p["id"] or re.match(r"[A-Z]+-\d+", p["t"])}
    assert ids.get(world["d"]) is True and "D-9999" not in ids  # a made-up id is plain text
    assert L["validate"]["errors"] == 0 and L["validate"]["warnings"] == 2 and L["validate_text"].startswith("0 errors, 2 warnings")


def test_live_says_so_in_a_sentence_when_nothing_happened(store, world):
    L = an.live(store, NOW + dt.timedelta(days=3), validation=_validation(), wait=True)
    assert L["count"] == 0 and L["events"] == []
    assert L["quiet"] == f"Nothing happened in the last hour. The last event was desk on {world['f']} at 2026-11-19 11:50."


def test_live_on_an_empty_store(store):
    L = an.live(store, NOW, validation=_validation(), wait=True)
    assert L["quiet"] == "Nothing happened in the last hour, and the store has no recorded events yet."
    assert L["last"] is None and L["events"] == []


def test_live_shows_the_stores_latest_commit(store, world):
    import subprocess

    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", f"first (see {world['a']})"]):
        subprocess.run(["git", *args], cwd=store.home, check=True, capture_output=True)
    an._GIT_CACHE.clear()
    L = an.live(store, NOW, validation=_validation(), wait=True)
    assert L["store_commit"]["subject"] == f"first (see {world['a']})" and re.fullmatch(r"[0-9a-f]{7,}", L["store_commit"]["sha"])
    assert [p["id"] for p in L["commits"][0]["parts"]] == [False, True, False]
    assert L["desk"].startswith("Desk ")


def test_live_without_git_says_so(store):
    an._GIT_CACHE.clear()
    assert an.store_git(store.home) == {"latest": None, "commits": []}


def test_a_damaged_log_line_is_skipped_not_fatal(store, world):
    p = store.home / "events" / f"{world['a']}.jsonl"
    with p.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    assert an.build(store, NOW)["people"]["rows"][0]["total"] == 1


def test_validation_never_blocks_the_page_and_reports_a_failure_in_words(store):
    gate = threading.Event()

    def slow(s):
        gate.wait(5)
        return ([], [])

    v = an.Validation(runner=slow)
    first = v.get(store)
    assert first["running"] and first["errors"] is None and an.validate_text(first).startswith("Checking now")
    gate.set()
    for _ in range(50):
        if not v.get(store)["running"]:
            break
        threading.Event().wait(0.1)
    assert v.get(store)["errors"] == 0
    bad = an.Validation(runner=lambda s: (_ for _ in ()).throw(RuntimeError("no store")))
    got = bad.get(store, wait=True)
    assert got["errors"] is None and "no store" in an.validate_text(got)


# -- 6. meta --------------------------------------------------------------------------------

def test_meta_lists_the_reports_with_their_stated_verdict_and_rate(store, world):
    d = store.intake_dir
    d.mkdir(exist_ok=True)
    (d / "2026-11-19-qc-pass5.md").write_text("# QC pass 5\n\n**Verdict: FAIL (narrow).** Eleven of twelve hold.\n", encoding="utf-8")
    (d / "2026-11-19-claims-audit.md").write_text("# Claims audit\n\n- **False rate:** 25 of 96 (26 percent).\n", encoding="utf-8")
    (d / "2026-11-18-yes-no-triage.md").write_text("# Triage\n\nNo verdict here.\n", encoding="utf-8")
    (d / "2026-11-18-notes.md").write_text("# Not a checker report\n", encoding="utf-8")
    sub = d / "2026-11-13-pass-2" / "reports"
    sub.mkdir(parents=True)
    (sub / "qc-checker-run-4.md").write_text("# Old QC report\n", encoding="utf-8")
    m = an.build(store, NOW)["meta"]
    by = {r["file"]: r for r in m["reports"]}
    assert set(by) == {"2026-11-19-qc-pass5.md", "2026-11-19-claims-audit.md", "2026-11-18-yes-no-triage.md", "qc-checker-run-4.md"}
    assert by["2026-11-19-qc-pass5.md"]["verdict"].startswith("FAIL (narrow)")
    assert by["2026-11-19-claims-audit.md"]["rate"] == "25 of 96 (26 percent)"
    assert by["2026-11-18-yes-no-triage.md"]["verdict"] == "" and by["2026-11-18-yes-no-triage.md"]["rate"] == ""
    assert by["qc-checker-run-4.md"]["date"] == "2026-11-13" and by["qc-checker-run-4.md"]["rel"] == "intake/2026-11-13-pass-2/reports/qc-checker-run-4.md"
    assert [r["date"] for r in m["reports"]] == sorted((r["date"] for r in m["reports"]), reverse=True)
    assert m["repetition_open"] == 1 and m["repetition_ids"] == [world["e"]]
    raw_rep = [t for t in raw_tickets(store.home).values() if "repetition" in t.get("tags", "") and t["status"] not in ("done", "rejected")]
    assert len(raw_rep) == m["repetition_open"]


def test_meta_with_no_intake_folder(store):
    assert an.build(store, NOW)["meta"] == {"reports": [], "repetition_open": 0, "repetition_ids": []}


# -- the empty store, and the route ---------------------------------------------------------

def test_empty_store_builds_every_block(store):
    d = an.build(store, NOW)
    assert d["flow"]["open_total"] == 0 and d["flow"]["created_total"] == 0 and len(d["flow"]["days"]) == 30
    assert all(r["last"] is None for r in d["people"]["rows"]) and d["waiting"]["oldest"] == [] and d["waiting"]["stuck"] == []
    assert d["speed"]["ready_to_review"] is None


def test_the_page_renders_on_an_empty_store_and_is_in_the_navigation(store):
    c = TestClient(create_app(store.home))
    r = c.get("/analytics")
    assert r.status_code == 200 and "No open tickets." in r.text and "never seen in the log" in r.text
    assert 'href="/analytics">Analytics</a>' in c.get("/").text


@pytest.fixture
def client(store, world, monkeypatch):
    monkeypatch.setattr(an, "VALIDATION", _validation())
    an._GIT_CACHE.clear()
    return TestClient(create_app(store.home))


def test_route_numbers_appear_on_the_page_and_every_ticket_id_is_a_link(client, store, world):
    html = client.get("/analytics").text
    d = an.build(store)
    assert f"{d['flow']['created_total']} in 30 days" in html and f"{d['flow']['closed_total']} in 30 days" in html
    assert f"{d['waiting']['queue_now']} today" in html
    assert "No ticket activity in the last 7 days from" in html and "Checks: what the checkers and audits found" in html
    shown = set(re.findall(r"\b(?:JRE|DESK)-\d{4}\b", re.sub(r"<[^>]+>", " ", html)))
    linked = set(re.findall(r'href="/ticket/((?:JRE|DESK)-\d{4})"', html))
    assert shown and shown <= linked, shown - linked


def test_route_live_json_is_small_json_and_not_cached(client, store, world):
    r = client.get("/analytics/live.json")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    j = r.json()
    assert set(j) >= {"now", "count", "events", "quiet", "desk", "store_commit", "validate_text", "commits", "more"}
    assert len(r.content) < 20000


def test_route_page_embeds_the_poll_settings(client):
    html = client.get("/analytics").text
    assert 'data-live-url="/analytics/live.json"' in html and 'data-every="30"' in html and "analytics.js" in html


def test_page_uses_hatching_and_words_so_colour_is_not_the_only_cue(client):
    html = client.get("/analytics").text
    css = (Path(__file__).resolve().parents[1] / "src" / "desk" / "ui" / "static" / "analytics.css").read_text(encoding="utf-8")
    assert "left bar" in html and "right hatched bar" in html and "repeating-linear-gradient" in css
    assert "Show every day as a table" in html


# -- the real page --------------------------------------------------------------------------

EXPR = "JSON.stringify({sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth, live: !!document.getElementById('an-events'), bars: document.querySelectorAll('.an-day').length})"


@NEEDS_BROWSER
@pytest.mark.parametrize("dark", [False, True])
def test_browser_no_sideways_scroll_at_1280_390_and_320(store, world, monkeypatch, dark):
    monkeypatch.setattr(an, "VALIDATION", _validation())
    with live_app(create_app(store.home)) as base:
        got = cdp_measure(base, ["/analytics"], [1280, 390, 320], EXPR, dark=dark)
    for key, v in got.items():
        v = json.loads(v)
        assert v["sw"] <= v["cw"] and v["live"] and v["bars"] == 60, (key, v)


@NEEDS_BROWSER
def test_browser_the_live_block_updates_without_a_reload(store, world, monkeypatch):
    """An event written while the page is open shows in the live list after one poll, and the page itself did not reload."""
    monkeypatch.setattr(an, "VALIDATION", _validation())
    app = create_app(store.home)

    def touch():
        activity.append(store.home, [_ev(world["f"], dt.datetime.now().isoformat(timespec="seconds"), actor="lily", kind="coworker", change="comment", field="comment", what="LIVE-MARKER hello")])
        return {"ok": True}

    app.add_api_route("/_touch", touch)
    expr = """(async () => {
      window.__marker = 'same-page';
      const before = document.querySelectorAll('#an-events li').length;
      await fetch('/_touch');
      await window.DeskAnalytics.poll();
      for (let i = 0; i < 40; i++) { if (document.getElementById('an-events').textContent.includes('LIVE-MARKER')) break; await new Promise(r => setTimeout(r, 100)); }
      return JSON.stringify({before, text: document.getElementById('an-events').textContent, quiet: document.getElementById('an-quiet').textContent, same: window.__marker === 'same-page',
                             links: [...document.querySelectorAll('#an-events a')].map(a => a.getAttribute('href'))});
    })()"""
    with live_app(app) as base:
        got = json.loads(cdp_measure(base, ["/analytics"], [1280], expr)["/analytics@1280"])
    assert "LIVE-MARKER" in got["text"] and "Lily" in got["text"] and got["same"]
    assert got["quiet"].endswith("in the last hour.") and any(h == f"/ticket/{world['f']}" for h in got["links"])


def test_stuck_boundary_is_seven_days_not_three(store):
    """Protects the 7-day line: a ticket untouched 6 days is not stuck, one untouched 7 and a half days is."""
    young = _mk(store, "six days", "jre", "ready", "2026-11-01", updated="2026-11-13")
    old = _mk(store, "seven days", "jre", "ready", "2026-11-01", updated="2026-11-12")
    w = an.build(store, NOW)["waiting"]
    ids = [o["id"] for r in w["stuck"] for o in r["oldest"]]
    assert old in ids and young not in ids and w["stuck_days"] == 7
