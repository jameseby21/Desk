"""DESK-0305: the visual timeline on every ticket page (the owner, fourth ask, 2026-11-15: "I'm still not seeing a visual timeline of
events on a ticket"). Protects: a status strip (one segment per status period, created to now, width by time) and an event rail (one
dot per event, newest first, grouped by day, the first 12 shown, filter chips), open by default under the title, at 1280, 390 and
320 px, with no word split inside a label, and a 2,000 event ticket rendering in under 300 ms. Three methods: unit tests of the
builders, route tests through TestClient, and a real-browser measurement in headless Edge or Chrome."""

import datetime as dt
import re
import time
from pathlib import Path

import pytest
from browser_harness import browser, run_in_browser
from fastapi.testclient import TestClient

from desk import activity, ops, timeline
from desk.ui.app import create_app

NOW = dt.datetime(2026, 11, 15, 12, 0, 0)


def ev(ts, change="field", field="status", old="", new="", what="", tid="DESK-0001", actor="owner-a", kind="owner", seq=0):
    return activity.Event(tid, ts, actor, kind, "cli", change, field, old, new, what or f"{field}: {old} → {new}", seq=seq)


def move(ts, old, new, seq=0):
    return ev(ts, old=old, new=new, seq=seq)


def spans(ps):
    return [(p.status, p.start.isoformat(), p.end.isoformat(), p.current) for p in ps]


# -- unit: the period builder --------------------------------------------------------------

def test_no_status_events_is_one_period_from_created_to_now_in_the_current_status():
    """Protects the empty log: one segment, created to now, in the ticket's status, the whole width."""
    ps = timeline.periods("2026-11-10", [], "ready", NOW)
    assert spans(ps) == [("ready", "2026-11-10T00:00:00", "2026-11-15T12:00:00", True)]
    assert ps[0].share == 100.0


def test_one_move_is_two_periods_with_widths_by_time():
    """Protects one status change: the old status from created to the move, the new one from the move to now, widths by time."""
    ps = timeline.periods("2026-11-14", [move("2026-11-15T00:00:00", "inbox", "ready", 2)], "ready", NOW)
    assert spans(ps) == [("inbox", "2026-11-14T00:00:00", "2026-11-15T00:00:00", False), ("ready", "2026-11-15T00:00:00", "2026-11-15T12:00:00", True)]
    assert [p.share for p in ps] == [66.667, 33.333]
    assert ps[0].text == "Inbox, from Sat 14 Nov, for 1 day" and ps[1].text == "Ready, from Sun 15 Nov, for 12 hours (now)"


def test_a_ticket_that_loops_back_shows_the_earlier_status_twice():
    """Protects review -> changes requested -> review: each visit is its own segment, in order."""
    evs = [move("2026-11-11T09:00:00", "inbox", "review", 2), move("2026-11-12T09:00:00", "review", "changes_requested", 3),
           move("2026-11-13T09:00:00", "changes_requested", "review", 4), move("2026-11-14T09:00:00", "review", "done", 5)]
    ps = timeline.periods("2026-11-10", evs, "done", NOW)
    assert [p.status for p in ps] == ["inbox", "review", "changes_requested", "review", "done"]
    assert [p.current for p in ps] == [False, False, False, False, True]
    assert abs(sum(p.share for p in ps) - 100) < 0.01


def test_out_of_order_timestamps_are_put_in_time_order():
    """Protects a log whose lines are not in time order (a migration appended later): periods follow the timestamps."""
    evs = [move("2026-11-13T00:00:00", "ready", "in_progress", 2), move("2026-11-11T00:00:00", "inbox", "ready", 3)]
    ps = timeline.periods("2026-11-10", evs, "in_progress", NOW)
    assert spans(ps) == [("inbox", "2026-11-10T00:00:00", "2026-11-11T00:00:00", False), ("ready", "2026-11-11T00:00:00", "2026-11-13T00:00:00", False),
                         ("in_progress", "2026-11-13T00:00:00", "2026-11-15T12:00:00", True)]


def test_same_second_moves_keep_their_order_and_a_zero_length_period():
    """Protects claim-and-start in one second: the sequence number orders them and the short status still has a segment."""
    evs = [move("2026-11-12T10:00:00", "claimed", "in_progress", 4), move("2026-11-12T10:00:00", "ready", "claimed", 3)]
    ps = timeline.periods("2026-11-10", evs, "in_progress", NOW)
    assert [p.status for p in ps] == ["ready", "claimed", "in_progress"]
    assert ps[1].seconds == 0 and ps[1].share == 0 and ps[1].text.endswith("for under a minute")


def test_an_unknown_status_is_kept_as_written_and_marked_other():
    """Protects a status Desk does not know (an old or hand-typed one): shown as written, coloured as other, never dropped."""
    ps = timeline.periods("2026-11-10", [move("2026-11-12T00:00:00", "inbox", "parked", 2)], "parked", NOW)
    assert [(p.status, p.known, p.css, p.label) for p in ps] == [("inbox", True, "inbox", "Inbox"), ("parked", False, "other", "parked")]


def test_a_ticket_created_before_the_log_existed_opens_with_the_status_before_the_first_move():
    """Protects old tickets: the first segment runs from the created date to the first logged move, in that move's old status."""
    ps = timeline.periods("2026-01-05", [move("2026-11-01T00:00:00", "ready", "done", 1)], "done", NOW)
    assert spans(ps)[0] == ("ready", "2026-01-05T00:00:00", "2026-11-01T00:00:00", False)
    assert ps[0].share > 90


def test_a_very_old_ticket_reads_in_years_and_names_the_year():
    """Protects a ticket years old: the duration says years and the dates carry the year."""
    ps = timeline.periods("2019-03-01", [], "blocked", NOW)
    assert ps[0].text == "Blocked, from Fri 1 Mar 2019, for 7 years (now)"


def test_a_log_older_than_the_created_date_and_a_status_the_log_never_reached():
    """Protects two inconsistent records: the log's oldest event starts the strip, and the file's status closes it as current."""
    evs = [ev("2026-11-08T10:00:00", "created", "created"), move("2026-11-09T10:00:00", "inbox", "ready", 2)]
    ps = timeline.periods("2026-11-12", evs, "approved", NOW)
    assert ps[0].start == dt.datetime(2026, 11, 8, 10, 0) and [p.status for p in ps] == ["inbox", "ready", "approved"]
    assert ps[-1].current and not ps[1].current


def test_duration_and_day_words():
    """Protects the plain words of the strip and the rail: one unit, Today, Yesterday, then a short weekday date."""
    assert [timeline.duration_text(s) for s in (5, 60, 3 * 3600, 86400, 9 * 86400, 70 * 86400, 800 * 86400)] == [
        "under a minute", "1 minute", "3 hours", "1 day", "1 week", "2 months", "2 years"]
    today = dt.date(2026, 11, 15)
    assert [timeline.day_label(d, today) for d in (today, dt.date(2026, 11, 14), dt.date(2026, 11, 13), dt.date(2025, 11, 13))] == [
        "Today", "Yesterday", "Fri 13 Nov", "Thu 13 Nov 2025"]


def test_event_kinds_sort_every_change_into_a_dot_and_a_chip():
    """Protects the rail's colours and chips: each logged change is one of eight kinds and one of five chip groups."""
    cases = {("created", "created"): "created", ("field", "status"): "status", ("section", "Decision"): "decision", ("comment", "comment"): "comment",
             ("field", "priority"): "field", ("section", "Context"): "section", ("git", "git"): "git", ("section", "Attachments"): "attachment"}
    for (change, fld), kind in cases.items():
        assert timeline.event_kind(ev("2026-11-10T10:00:00", change, fld)) == kind
    assert set(timeline.GROUP_OF.values()) == {k for k, _ in timeline.CHIPS} - {"all"}
    assert timeline.STRONG == {"status", "decision"}



# -- the strip's own clock (finding: the period words used the machine's date, not the strip's now) -----------------------------

def _clock(monkeypatch, today):
    """Make the machine's date `today` for the timeline module only; datetime itself stays real."""
    import types

    class FakeDate(dt.date):
        @classmethod
        def today(cls):
            return today

    monkeypatch.setattr(timeline, "dt", types.SimpleNamespace(datetime=dt.datetime, date=FakeDate, timedelta=dt.timedelta))


def test_period_words_take_the_year_from_the_strips_now_not_the_machine_clock(monkeypatch):
    """Protects the strip's dates against the machine clock: with now in 2025 and the clock in 2030, a 2025 start has no year
    and, with now in 2026, a 2025 start says 2025; unit method, clock patched."""
    _clock(monkeypatch, dt.date(2030, 6, 1))
    now = dt.datetime(2025, 11, 15, 12, 0)
    ps = timeline.periods("2025-11-14", [ev("2025-11-14T00:00:00", "created", "created", seq=1), move("2025-11-15T00:00:00", "inbox", "ready", 2)],
                          "ready", now)
    assert ps[-1].text == "Ready, from Sat 15 Nov, for 12 hours (now)", ps[-1].text
    _clock(monkeypatch, dt.date(2025, 6, 1))
    later = timeline.periods("2025-11-14", [ev("2025-11-14T08:00:00", "created", "created", seq=1), move("2025-11-14T09:00:00", "inbox", "ready", 2)],
                             "ready", NOW)
    assert later[-1].text.startswith("Ready, from Fri 14 Nov 2025 09:00, for "), later[-1].text  # now in 2026, start in 2025: the year is named


def test_one_move_reads_the_same_in_the_new_year(monkeypatch):
    """Protects test_one_move_is_two_periods_with_widths_by_time from going red on 1 Jan 2027 with no code change: the same strip,
    the clock patched to 2027-01-02, reads the same words."""
    _clock(monkeypatch, dt.date(2027, 1, 2))
    ps = timeline.periods("2026-11-14", [ev("2026-11-14T00:00:00", "created", "created", seq=1), move("2026-11-15T00:00:00", "inbox", "ready", 2)],
                          "ready", NOW)
    assert ps[0].text == "Inbox, from Sat 14 Nov, for 1 day" and ps[1].text == "Ready, from Sun 15 Nov, for 12 hours (now)"


# -- route: through the real app ----------------------------------------------------------

def _busy(store, n=20):
    """A ticket with n events over the last four days: status moves, a decision, comments, field and section edits, a git save."""
    t = ops.new(store, "A ticket with a busy life", "desk")
    activity.path(store.home, t.id).unlink()
    base = dt.datetime.now().replace(microsecond=0) - dt.timedelta(days=4)
    flow = ["inbox", "triage", "ready", "claimed", "in_progress", "review", "changes_requested", "in_progress", "review", "approved"]
    evs = [activity.Event(t.id, base.isoformat(), "owner-a", "owner", "ui", "created", "created", "", t.meta["title"], "Created: busy")]
    for i in range(1, n):
        ts = (base + dt.timedelta(hours=4 * i)).isoformat()
        if i < len(flow):
            evs.append(activity.Event(t.id, ts, "main-a", "main", "cli", "field", "status", flow[i - 1], flow[i], f"status: {flow[i - 1]} → {flow[i]}"))
        elif i % 4 == 0:
            evs.append(activity.Event(t.id, ts, "owner-a", "owner", "ui", "section", "Decision", "", "Approved", "Decision changed: approved"))
        elif i % 4 == 1:
            evs.append(activity.Event(t.id, ts, "coworker-a", "coworker", "ui", "comment", "comment", "", "Boards posted", "Boards posted"))
        elif i % 4 == 2:
            evs.append(activity.Event(t.id, ts, "claude-code", "code", "git", "git", "git", "git-user", "abc", "Saved in git: fix"))
        else:
            evs.append(activity.Event(t.id, ts, "main-a", "main", "cli", "field", "priority", "p2", "p1", "priority: p2 → p1"))
    activity.append(store.home, evs)
    x = store.get(t.id)
    x.meta["status"] = flow[-1]
    x.meta["created"] = base.date().isoformat()
    x.write()
    return store.get(t.id)


def _page(store, t):
    return TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text


def test_the_strip_and_the_rail_are_open_under_the_title_with_the_latest_line_kept(store):
    """Protects the owner's ask: the visual timeline is on the page without a click, right under the title block, latest change kept."""
    t = _busy(store)
    page = _page(store, t)
    tl = page[page.index('id="timeline"'):page.index('<div class="cols">')]
    assert page.index("<h1") < page.index('id="score"') < page.index('id="timeline"') < page.index('<div class="cols">')
    assert 'id="tl-latest"' in tl and "Latest change" in tl
    assert 'id="tl-strip"' in tl and 'id="tl-rail"' in tl
    assert "<details" not in tl.split('<details class="tl-more-f"')[0]  # nothing above the extra filters is folded
    assert "Expand all events" not in page


def test_the_strip_has_one_segment_per_period_with_widths_dates_and_the_current_one_marked(store):
    """Protects the strip's markup: a list item per status period, flex width by time, title text, the current one marked, ends named."""
    t = _busy(store)
    page = _page(store, t)
    segs = re.findall(r'<li class="tl-seg st-([a-z_]+)( tl-now)?" style="flex-grow:([0-9.]+)"[^>]*title="([^"]+)"', page)
    assert [s[0] for s in segs] == ["inbox", "triage", "ready", "claimed", "in_progress", "review", "changes_requested", "in_progress", "review", "approved"]
    assert [bool(s[1]) for s in segs] == [False] * 9 + [True]
    assert abs(sum(float(s[2]) for s in segs) - 100) < 0.05
    assert segs[-1][3].startswith("Approved, from ") and segs[-1][3].endswith("(now)")
    assert '<ol class="tl-strip"' in page and 'aria-label="Status over time, oldest first"' in page
    assert re.search(r'<span class="tl-end">Created [A-Z][a-z]{2} \d{1,2} [A-Z][a-z]{2}</span>', page) and '<span class="tl-end">Now</span>' in page


def test_the_rail_shows_the_first_12_then_show_all_with_counts_and_filter_data(store):
    """Protects the rail: every event is on the page newest first, the first 12 visible, the rest hidden behind Show all N events,
    each row carries its chip group, and the chips show their counts."""
    t = _busy(store, n=30)
    page = _page(store, t)
    rows = re.findall(r'<li class="tl-ev ek-([a-z]+)( tl-strong)?" data-group="([a-z]+)" data-i="(\d+)"([^>]*)>', page)
    assert len(rows) == 30 and [int(r[3]) for r in rows] == list(range(30))
    assert [("hidden" in r[4]) for r in rows] == [False] * 12 + [True] * 18
    assert all(bool(r[1]) == (r[0] in ("status", "decision")) for r in rows)
    groups = [r[2] for r in rows]
    for key, label in timeline.CHIPS:
        n = len(groups) if key == "all" else groups.count(key)
        assert f'data-filter="{key}" aria-pressed="{"true" if key == "all" else "false"}">{label} <span class="tl-n">{n}</span>' in page, key
    assert ">Show all 30 events</button>" in page and 'aria-controls="tl-list"' in page
    assert ">Status: Inbox → To sort</div>" in page and "status: inbox → triage" not in page  # plain words, not the log's keys
    assert page.count('class="tl-day"') >= 4 and ">Today</h3>" in page


def test_a_ticket_with_no_status_events_has_one_segment_and_a_short_log_has_no_show_all(store):
    """Protects the plainest ticket: one segment in its status from created to now, and no Show all button under 13 events."""
    t = ops.new(store, "Quiet one", "desk")
    page = _page(store, t)
    assert len(re.findall(r'<li class="tl-seg ', page)) == 1 and 'class="tl-seg st-inbox tl-now"' in page
    assert 'id="tl-showall"' in page and re.search(r'<button[^>]*id="tl-showall"[^>]*hidden', page)
    assert page.count('<li class="tl-ev ') == 1 and ">Created<" in page


def test_event_text_and_names_are_escaped_on_the_rail(store):
    """Protects the Python-built rail (built outside Jinja for speed): a comment or name holding markup is shown as text, never run."""
    t = ops.new(store, "Escaping", "desk")
    activity.append(store.home, [activity.Event(t.id, dt.datetime.now().replace(microsecond=0).isoformat(), 'x"><b>', "person", "ui", "comment",
                                                "comment", "", "x", '<script>alert(1)</script> & "quoted"')])
    tl = _page(store, t).split('id="tl-list"')[1].split("</ol>\n    <button")[0]
    assert "<script>" not in tl and "&lt;script&gt;alert(1)&lt;/script&gt; &amp; &#34;quoted&#34;" in tl
    assert 'x&#34;&gt;&lt;b&gt;' in tl and '"><b>' not in tl


def test_the_timeline_script_handles_filters_and_show_all_on_the_client(store):
    """Protects filtering and Show all without a reload: the script reads data-group and toggles hidden; no fetch, no form."""
    js = (STATIC / "timeline.js").read_text(encoding="utf-8")
    assert "data-group" in js or "dataset.group" in js
    assert "tl-showall" in js and "aria-pressed" in js and "aria-expanded" in js
    assert "fetch(" not in js and "location.reload" not in js


def test_timeline_words_are_plain(store):
    """Protects plain words: the timeline's own captions say what they show, with no internal names (seq, jsonl, ekind, dims)."""
    t = _busy(store)
    page = _page(store, t)
    tl = page[page.index('id="timeline"'):page.index('<div class="cols">')]
    visible = re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>", "", tl, flags=re.S))
    for word in ("jsonl", "ekind", "dims", " seq ", "None", "null", "undefined"):
        assert word not in visible, word
    for phrase in ("Latest change", "Status over time", "Show all 20 events", "Show only"):
        assert phrase in tl, phrase


def test_a_2000_event_ticket_renders_in_under_300_ms(store):
    """Protects speed on a long-lived ticket: the page with 2,000 events renders on the server in under 300 ms (best of three)."""
    t = ops.new(store, "Two thousand events", "desk")
    base = dt.datetime.now().replace(microsecond=0) - dt.timedelta(days=200)
    kinds = [("field", "status"), ("comment", "comment"), ("field", "priority"), ("section", "Context"), ("git", "git"), ("section", "Decision")]
    flow = timeline.STATUSES
    evs = []
    for i in range(2000):
        change, fld = kinds[i % len(kinds)]
        old, new = (flow[i % len(flow)], flow[(i + 1) % len(flow)]) if fld == "status" else ("a", "b")
        evs.append(activity.Event(t.id, (base + dt.timedelta(hours=2 * i)).isoformat(), ("owner-a", "main-a", "coworker-a")[i % 3], ("owner", "main", "coworker")[i % 3],
                                  "cli", change, fld, old, new, f"event {i}: {fld} changed"))
    activity.append(store.home, evs)
    client = TestClient(create_app(store.home))
    first = client.get(f"/ticket/{t.id}")
    assert first.status_code == 200 and first.text.count('<li class="tl-ev ') == 2001
    best = 9.0
    for _ in range(3):
        t0 = time.perf_counter()
        client.get(f"/ticket/{t.id}")
        best = min(best, time.perf_counter() - t0)
    assert best < 0.3, f"{best * 1000:.0f} ms"


# -- browser: a real layout ---------------------------------------------------------------

STATIC = Path(__file__).resolve().parents[1] / "src" / "desk" / "ui" / "static"
MEASURE = """<script>addEventListener('load',function(){setTimeout(function(){
var w=document.documentElement.clientWidth,over=[],split=[],clipped=[];
document.querySelectorAll('#timeline *').forEach(function(e){var r=e.getBoundingClientRect();if(r.width&&r.right>w+1&&!e.closest('[hidden]'))over.push(e.className+' '+Math.round(r.right));});
function words(sel){document.querySelectorAll(sel).forEach(function(el){if(!el.offsetWidth||getComputedStyle(el).visibility==='hidden')return;
 var tw=document.createTreeWalker(el,NodeFilter.SHOW_TEXT),t;while((t=tw.nextNode())){var re=/\\S+/g,m;while((m=re.exec(t.textContent))){
 var g=document.createRange();g.setStart(t,m.index);g.setEnd(t,m.index+m[0].length);var rs=Array.prototype.filter.call(g.getClientRects(),function(x){return x.width>0;});if(rs.length>1)split.push(m[0]);}}});}
words('.tl-lab,.tl-chip,.tl-dayh,.tl-ekind,.tl-actor,.tl-cap,.tl-end,#tl-showall,.tl-time');
document.querySelectorAll('.tl-seg').forEach(function(s){var l=s.querySelector('.tl-lab');if(!l||getComputedStyle(l).visibility==='hidden')return;
 var a=s.getBoundingClientRect(),b=l.getBoundingClientRect();if(b.left<a.left-0.5||b.right>a.right+0.5)clipped.push(l.textContent);});
var strip=document.getElementById('tl-strip'),rail=document.getElementById('tl-rail');
function vis(){return Array.prototype.filter.call(document.querySelectorAll('.tl-ev'),function(li){return li.offsetHeight>0;}).length;}
var out={w:w,scrollW:document.documentElement.scrollWidth,over:over,split:split,clipped:clipped,stripH:strip.offsetHeight,railH:rail.offsetHeight,
 segs:document.querySelectorAll('.tl-seg').length,labels:Array.prototype.filter.call(document.querySelectorAll('.tl-lab'),function(l){return getComputedStyle(l).visibility!=='hidden';}).length,
 first:vis(),nowH:document.querySelector('.tl-now').offsetHeight,pastH:document.querySelector('.tl-seg').offsetHeight};
document.getElementById('tl-showall').click();out.all=vis();out.btn=document.getElementById('tl-showall').textContent;
document.querySelector('.tl-chip[data-filter=decisions]').click();out.dec=vis();
out.decOk=Array.prototype.every.call(document.querySelectorAll('.tl-ev'),function(li){return li.offsetHeight===0||li.getAttribute('data-group')==='decisions';});
document.querySelector('.tl-chip[data-filter=all]').click();out.back=vis();
fetch('/m',{method:'POST',body:JSON.stringify(out)});},300);});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
@pytest.mark.parametrize("width", [1280, 390, 320])
def test_the_visual_timeline_in_a_real_browser(store, width):
    """Protects the drawn timeline at 1280, 390 and 320 px: strip and rail visible without a click, nothing in them past the right
    edge, no label word on two lines or clipped in its segment, the current status taller, Show all and the chips work in place."""
    t = _busy(store, n=30)
    with_long = store.get(t.id)
    activity.append(store.home, [activity.Event(t.id, dt.datetime.now().replace(microsecond=0).isoformat(), "coworker-a", "coworker", "ui", "comment", "comment", "",
                                                "x", "A very long comment " + "that goes on and on without a break " * 8)])
    html = _page(store, with_long)
    css = (STATIC / "desk.css").read_text(encoding="utf-8")
    js = (STATIC / "timeline.js").read_text(encoding="utf-8")
    html = re.sub(r'<link rel="stylesheet" href="/static/desk.css[^"]*">', lambda m: f"<style>{css}</style>", html)
    html = re.sub(r'<link rel="stylesheet" href="https://fonts[^>]*>|<link rel="preconnect"[^>]*>', "", html)
    html = re.sub(r'<script src="[^"]*"[^>]*></script>', "", html).replace("</body>", f"<script>{js}</script>" + MEASURE + "</body>")
    d = run_in_browser(html, width)
    assert d["stripH"] > 0 and d["railH"] > 0 and d["first"] == 12, d
    assert d["over"] == [], d
    assert d["split"] == [] and d["clipped"] == [], d
    assert d["segs"] == 10 and d["labels"] >= 1 and d["nowH"] > d["pastH"], d
    assert d["all"] == 31 and d["btn"] == "Show the newest 12", d
    assert 0 < d["dec"] < 31 and d["decOk"] and d["back"] == 31, d


def test_the_strip_line_and_the_rail_agree_on_the_year_across_new_year(store, monkeypatch):
    """Protects one clock per page: with the page's now injected in 2027 and a ticket that spans New Year, the status line under the
    strip and the rail's day heading both name 2026 for 31 Dec; route method through the real app."""
    t = ops.new(store, "Across the new year", "desk")
    activity.path(store.home, t.id).unlink()
    activity.append(store.home, [activity.Event(t.id, "2026-12-30T10:00:00", "owner-a", "owner", "ui", "created", "created", "", "x", "Created: x"),
                                 activity.Event(t.id, "2026-12-31T09:00:00", "main-a", "main", "cli", "field", "status", "inbox", "ready", "status: inbox → ready")])
    x = store.get(t.id)
    x.meta["status"], x.meta["created"] = "ready", "2026-12-30"
    x.write()
    real = timeline.view
    monkeypatch.setattr(timeline, "view", lambda s, tk, filters=None, now=None: real(s, tk, filters, now=dt.datetime(2027, 1, 5, 9, 0)))
    page = _page(store, t)
    info = re.search(r'<p class="tl-st-info" id="tl-st-info"[^>]*>([^<]*)</p>', page).group(1)
    assert "Thu 31 Dec 2026" in info, info
    assert ">Thu 31 Dec 2026</h3>" in page
