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

def created_at(ts):
    return ev(ts, "created", "created", seq=1)


def test_no_status_record_is_not_recorded_then_the_current_status_now():
    """Protects the empty log (DESK-0305 review, finding 4): with no record of any status, the strip never claims the current status
    since the created date; the span is Not recorded and the current status starts when the file was last written, or now."""
    ps = timeline.periods("2026-11-10", [], "ready", NOW)
    assert spans(ps) == [("", "2026-11-10T00:00:00", "2026-11-15T12:00:00", False), ("ready", "2026-11-15T12:00:00", "2026-11-15T12:00:00", True)]
    assert ps[0].unknown and ps[0].label == "Not recorded" and ps[0].css == "unknown" and ps[0].share == 100.0
    assert ps[1].text == "Ready (now); when it started is not recorded"
    ps = timeline.periods("2026-11-10", [], "ready", NOW, saved_at=dt.datetime(2026, 11, 13, 10, 0))
    assert spans(ps) == [("", "2026-11-10T00:00:00", "2026-11-13T10:00:00", False), ("ready", "2026-11-13T10:00:00", "2026-11-15T12:00:00", True)]
    assert ps[1].text == "Ready, from Fri 13 Nov 10:00 or earlier, for at least 2 days (now)"
    assert not ps[0].text.startswith("Ready") and "no status record covers this time" in ps[0].text


def test_one_move_is_two_periods_with_widths_by_time():
    """Protects one status change: the old status from created to the move, the new one from the move to now, widths by time."""
    ps = timeline.periods("2026-11-14", [created_at("2026-11-14T00:00:00"), move("2026-11-15T00:00:00", "inbox", "ready", 2)], "ready", NOW)
    assert spans(ps) == [("inbox", "2026-11-14T00:00:00", "2026-11-15T00:00:00", False), ("ready", "2026-11-15T00:00:00", "2026-11-15T12:00:00", True)]
    assert [p.share for p in ps] == [66.667, 33.333]
    assert ps[0].text == "Inbox, from Sat 14 Nov, for 1 day" and ps[1].text == "Ready, from Sun 15 Nov, for 12 hours (now)"


def test_a_ticket_that_loops_back_shows_the_earlier_status_twice():
    """Protects review -> changes requested -> review: each visit is its own segment, in order."""
    evs = [created_at("2026-11-10T08:00:00"), move("2026-11-11T09:00:00", "inbox", "review", 2), move("2026-11-12T09:00:00", "review", "changes_requested", 3),
           move("2026-11-13T09:00:00", "changes_requested", "review", 4), move("2026-11-14T09:00:00", "review", "done", 5)]
    ps = timeline.periods("2026-11-10", evs, "done", NOW)
    assert [p.status for p in ps] == ["inbox", "review", "changes_requested", "review", "done"]
    assert [p.current for p in ps] == [False, False, False, False, True]
    assert abs(sum(p.share for p in ps) - 100) < 0.01


def test_out_of_order_timestamps_are_put_in_time_order():
    """Protects a log whose lines are not in time order (a migration appended later): periods follow the timestamps."""
    evs = [move("2026-11-13T00:00:00", "ready", "in_progress", 2), move("2026-11-11T00:00:00", "inbox", "ready", 3), created_at("2026-11-10T00:00:00")]
    ps = timeline.periods("2026-11-10", evs, "in_progress", NOW)
    assert spans(ps) == [("inbox", "2026-11-10T00:00:00", "2026-11-11T00:00:00", False), ("ready", "2026-11-11T00:00:00", "2026-11-13T00:00:00", False),
                         ("in_progress", "2026-11-13T00:00:00", "2026-11-15T12:00:00", True)]


def test_same_second_moves_keep_their_order_and_a_zero_length_period():
    """Protects claim-and-start in one second: the sequence number orders them and the short status still has a segment."""
    evs = [move("2026-11-12T10:00:00", "claimed", "in_progress", 4), move("2026-11-12T10:00:00", "ready", "claimed", 3), created_at("2026-11-10T09:00:00")]
    ps = timeline.periods("2026-11-10", evs, "in_progress", NOW)
    assert [p.status for p in ps] == ["ready", "claimed", "in_progress"]
    assert ps[1].seconds == 0 and ps[1].share == 0 and ps[1].text.endswith("for under a minute")


def test_an_unknown_status_is_kept_as_written_and_marked_other():
    """Protects a status Desk does not know (an old or hand-typed one): shown as written, coloured as other, never dropped."""
    ps = timeline.periods("2026-11-10", [created_at("2026-11-10T09:00:00"), move("2026-11-12T00:00:00", "inbox", "parked", 2)], "parked", NOW)
    assert [(p.status, p.known, p.css, p.label) for p in ps] == [("inbox", True, "inbox", "Inbox"), ("parked", False, "other", "parked")]


def test_a_ticket_created_before_the_log_existed_is_not_recorded_until_its_first_record():
    """Protects old tickets (DESK-0305 review, findings 1 and 4): from the created date to the first logged move nothing says what the
    status was, so that span is Not recorded, with what the move says about the moment before it; it is never given a status."""
    ps = timeline.periods("2026-01-05", [move("2026-11-01T00:00:00", "ready", "done", 1)], "done", NOW)
    assert spans(ps)[0] == ("", "2026-01-05T00:00:00", "2026-11-01T00:00:00", False)
    assert ps[0].share > 90 and ps[0].text.endswith("no status record covers this time; it was Ready just before Sun 1 Nov")


def test_a_very_old_ticket_reads_in_years_and_names_the_year():
    """Protects a ticket years old: the duration says years and the dates carry the year."""
    ps = timeline.periods("2019-03-01", [created_at("2019-03-01T09:00:00")], "blocked", NOW, saved_at=dt.datetime(2019, 3, 1, 9, 0, 2))
    assert ps[0].text == "Blocked, from Fri 1 Mar 2019 09:00, for 7 years (now)"


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


# -- git saves never bury what changed last (DESK-0305 review, finding 6) ---------------------------------------------------------

def _burst(store):
    """The busy ticket, then the owner's comment, then a burst of 6 git saves by a code session (the normal case: 88% of a real
    store's events are git saves). All seven sit inside one day, a few minutes before now."""
    t = _busy(store, n=20)
    start = dt.datetime.now().replace(second=0, microsecond=0) - dt.timedelta(minutes=10)
    if (start + dt.timedelta(minutes=7)).date() != start.date():
        start = start.replace(hour=0, minute=0) + dt.timedelta(days=1)  # keep the burst on one day
    evs = [activity.Event(t.id, start.isoformat(), "owner-a", "owner", "ui", "comment", "comment", "", "x", "Why is the header split again?")]
    evs += [activity.Event(t.id, (start + dt.timedelta(minutes=1 + i)).isoformat(), "claude-code", "code", "git", "git", "git", "git-user", f"s{i}",
                           f"Saved in git: chore(tickets): pass {i} of the store sync after the owner's comment", key=f"git:burst{i}") for i in range(6)]
    activity.append(store.home, evs)
    return store.get(t.id), start


def test_unit_latest_change_is_the_newest_meaningful_change_with_the_git_saves_counted():
    """Protects the Latest change line: a comment followed by 6 git saves is the latest change, and the line counts the saves after it;
    a ticket with only git saves still names its newest save."""
    def row(i, ekind, what):
        e = ev(f"2026-11-15T10:{50 - i:02d}:00", "git" if ekind == "git" else "comment", "git" if ekind == "git" else "comment", what=what)
        return timeline.Row(e, "", "Someone", "Owner", "", what, {"actor": "a"}, ekind=ekind, index=i)
    rs = [row(i, "git", f"save {i}") for i in range(6)] + [row(6, "comment", "the comment"), row(7, "status", "Status: Ready → Sent for review")]
    latest, after = timeline.latest_of(rs)
    assert latest.what == "the comment" and after == 6
    assert timeline.latest_line(latest, after).endswith("(+6 git saves since)")
    only = [row(i, "git", f"save {i}") for i in range(3)]
    assert timeline.latest_of(only) == (only[0], 0) and timeline.latest_of([]) == (None, 0)


def test_route_consecutive_git_saves_are_one_folded_row_and_the_first_12_keep_the_people_and_the_status_changes(store):
    """Protects the first screen of the rail: the 6 saves after the owner's comment are one row ('6 git saves, HH:MM–HH:MM') that opens
    in place, their own rows start hidden, the first 12 positions hold the comment and the decisions with 4 git rows, the fold among them (unfolded,
    7 of the 12 were git saves), and Latest change names the comment with '+6 git saves since'."""
    t, start = _burst(store)
    page = _page(store, t)
    folds = re.findall(r'<li class="tl-foldh ek-git" data-run="(f\d+)" data-group="git"([^>]*)>.*?<span class="tl-fn">([^<]+)</span>', page)
    assert len(folds) == 1 and "hidden" not in folds[0][1].split(" data-")[0], folds
    a, b = (start + dt.timedelta(minutes=1)).strftime("%H:%M"), (start + dt.timedelta(minutes=6)).strftime("%H:%M")
    assert folds[0][2] == f"6 git saves, {a}–{b}"
    members = re.findall(r'<li class="tl-ev ek-git"[^>]*data-run="' + folds[0][0] + r'"([^>]*)>', page)
    assert len(members) == 6 and all(m.startswith(" hidden") for m in members)
    visible = re.findall(r'<li class="(tl-ev ek-[a-z]+|tl-foldh ek-git)[^"]*"(?:(?!>).)*?data-group="([a-z]+)"(?:(?!>).)*?( hidden)?(?: data-actor)', page)
    shown = [g for cls, g, hid in visible if not hid]
    assert len(shown) == 12 and "comments" in shown and "decisions" in shown and shown.count("git") == 4, shown  # the fold and three single saves between other events
    latest = re.search(r'<p class="tl-latest" id="tl-latest".*?</p>', page, re.S).group(0)
    assert "Why is the header split again?" in latest and "+6 git saves since" in latest and "Saved in git" not in latest.split("title=")[0]
    assert "Showing the newest 17 of 27 events." in page  # 11 single rows and the fold's 6 saves


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
@pytest.mark.parametrize("width", [320, 390])
def test_browser_on_a_phone_the_latest_line_keeps_who_and_what_and_the_first_status_change_is_near_the_top(store, width):
    """Protects the phone (finding 6): at 320 and 390 px the Latest change shows who and at least 20 characters of what changed, the
    first status change or decision is among the first 6 rows of the rail (the 11th unfolded) and within 1.5 phone screens (844 px) of the timeline's top
    (the page's own header above the title is not the timeline's), and the fold opens its 6 saves in place."""
    t, _ = _burst(store)
    html = _page(store, t)
    css = (STATIC / "desk.css").read_text(encoding="utf-8")
    js = (STATIC / "timeline.js").read_text(encoding="utf-8")
    measure = """<script>addEventListener('load',function(){setTimeout(function(){
var lat=document.getElementById('tl-latest'),box=lat.getBoundingClientRect(),who=lat.querySelector('.tl-who'),what=lat.querySelector('.tl-what');
function inside(el,n){var tn=el.firstChild;if(!tn)return false;var g=document.createRange();g.setStart(tn,0);g.setEnd(tn,Math.min(n,tn.textContent.length));
 return Array.prototype.every.call(g.getClientRects(),function(r){return r.width===0||(r.bottom<=box.bottom+0.5&&r.right<=box.right+0.5&&r.top>=box.top-0.5);});}
function vis(){return Array.prototype.filter.call(document.querySelectorAll('.tl-ev'),function(li){return li.offsetHeight>0;}).length;}
var rows=Array.prototype.filter.call(document.querySelectorAll('.tl-ev,.tl-foldh'),function(li){return li.offsetHeight>0;});
var first=rows.filter(function(li){return /ek-(status|decision)/.test(li.className);})[0],top0=document.getElementById('timeline').getBoundingClientRect().top;
var out={whoShown:!!who&&who.offsetWidth>0&&inside(who,who.textContent.length),what20:!!what&&inside(what,20),whatLen:what?what.textContent.length:0,
 firstTop:first?first.getBoundingClientRect().top-top0:-1,firstRow:rows.indexOf(first),before:vis(),w:document.documentElement.clientWidth,over:[]};
document.querySelectorAll('#timeline *').forEach(function(e){var r=e.getBoundingClientRect();if(r.width&&r.right>out.w+1&&!e.closest('[hidden]'))out.over.push(e.className+' '+Math.round(r.right));});
var b=document.querySelector('.tl-foldb');out.after=out.back=-1;if(b){b.click();out.after=vis();out.exp=b.getAttribute('aria-expanded');b.click();out.back=vis();}
fetch('/m',{method:'POST',body:JSON.stringify(out)});},300);});</script>"""
    html = re.sub(r'<link rel="stylesheet" href="/static/desk.css[^"]*">', lambda m: f"<style>{css}</style>", html)
    html = re.sub(r'<link rel="stylesheet" href="https://fonts[^>]*>|<link rel="preconnect"[^>]*>', "", html)
    html = re.sub(r'<script src="[^"]*"[^>]*></script>', "", html).replace("</body>", f"<script>{js}</script>" + measure + "</body>")
    d = run_in_browser(html, width)
    assert d["whoShown"] and d["what20"] and d["whatLen"] >= 20, d
    assert 0 <= d["firstRow"] < 6 and 0 < d["firstTop"] < 1.5 * 844, d
    assert d["after"] == d["before"] + 6 and d["exp"] == "true" and d["back"] == d["before"], d
    assert d["over"] == [], d  # nothing in the timeline past the right edge (the page-wide 320 px scroll is DESK-0303)


# -- the strip reads without colour or hover (DESK-0305 review, finding 5) --------------------------------------------------------

def test_unit_date_marks_and_the_time_on_the_owner():
    """Protects the axis and the 'on you' words: a strip over 2 days gets a mark at the midnight nearest its middle (quarter marks too),
    none near either end; a 1-day strip gets none; review, and blocked on the owner, count as waiting on the owner."""
    start = dt.datetime(2026, 11, 8, 4, 10)
    marks = timeline.axis_marks(start, NOW)
    assert [m[2] for m in marks].count("mid") == 1 and all(12 <= m[0] <= 88 for m in marks)
    mid = next(m for m in marks if m[2] == "mid")
    assert mid[1] == "Thu 12 Nov" and 45 < mid[0] < 55
    assert timeline.axis_marks(NOW - dt.timedelta(hours=30), NOW) == []
    ps = timeline.periods("2026-11-10", [created_at("2026-11-10T09:00:00"), move("2026-11-11T09:00:00", "inbox", "review", 2),
                                         move("2026-11-12T09:00:00", "review", "blocked", 3)], "blocked", NOW)

    class T:
        meta = {"blocked_on": "owner-a: a decision"}
    assert timeline.waiting_on_owner(ps, T, "owner-a") == "now, for 3 days; 4 days in all"
    assert [p.on_owner for p in ps] == [False, True, True]
    T.meta = {"blocked_on": "a vendor"}
    assert timeline.waiting_on_owner(ps, T, "owner-a") == "1 day in all" and [p.on_owner for p in ps] == [False, True, False]


def test_route_the_key_names_every_period_in_words_outside_hover_text(store):
    """Protects the key under the strip: every period, the thin ones included, has its status name and its from and for words in the
    page's visible text (not only a title or screen-reader text); review periods say 'on you'; the strip carries a date mark."""
    t = _busy(store)
    v = timeline.view(store, t)
    page = _page(store, t)
    key = page[page.index('id="tl-key"'):page.index("</ol>", page.index('id="tl-key"'))]
    items = re.findall(r'<li class="tl-k( tl-k-now)?"[^>]*><i class="tl-sw st-([a-z_]+)"[^>]*></i><b class="tl-kname">([^<]+)</b>(.*?)</li>', key, re.S)
    assert len(items) == len(v.periods) == 10
    for (now, css, name, rest), p in zip(items, v.periods):
        words = re.sub(r"<[^>]+>", "", rest)
        assert css == p.css and name == p.label and bool(now) == p.current
        assert words.replace("on you", "", 1) == p.text[len(p.label):].lstrip(", ").replace("from ", "from ", 1), (words, p.text)
        assert ("on you" in words) == (p.status == "review")
    thin = [p for p in v.periods if p.share < 18]
    assert thin and all(p.label in key for p in thin)
    assert re.search(r'<span class="tl-tick mid" style="left:[0-9.]+%">[A-Z][a-z]{2} \d{1,2} [A-Z][a-z]{2}</span>', page)
    assert re.search(r'<p class="tl-onyou" id="tl-onyou"><span class="tl-onme">On you</span> \d+ hours in all</p>', page)  # approved now: past time only
    x = store.get(t.id)
    x.meta["status"] = "review"
    x.write()
    assert re.search(r'<p class="tl-onyou" id="tl-onyou"><span class="tl-onme">On you</span> now, for ', _page(store, store.get(t.id)))


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
@pytest.mark.parametrize("width", [320, 390, 1280])
def test_browser_every_strip_colour_has_a_visible_name_and_the_time_on_the_owner_reads_without_hover(store, width):
    """Protects the phone (finding 5): at 320, 390 and 1280 px every colour drawn in the strip has a visible name on one line (in its
    segment or in the key), each review period says 'on you' in visible words, the time on the owner is a visible line when the ticket
    waits on them, and a date mark sits between Created and Now; nothing in the timeline passes the right edge."""
    t = _busy(store)
    x = store.get(t.id)
    x.meta["status"] = "review"
    x.write()
    html = _page(store, store.get(t.id))
    css = (STATIC / "desk.css").read_text(encoding="utf-8")
    js = (STATIC / "timeline.js").read_text(encoding="utf-8")
    measure = """<script>addEventListener('load',function(){setTimeout(function(){
var w=document.documentElement.clientWidth,missing=[],split=[],noOwner=[],over=[];
function shown(el){return !!el&&el.offsetWidth>0&&el.offsetHeight>0&&getComputedStyle(el).visibility!=='hidden';}
function oneLine(el){var g=document.createRange();g.selectNodeContents(el);var tops={};Array.prototype.forEach.call(g.getClientRects(),function(r){if(r.width>0)tops[Math.round(r.top)]=1;});return Object.keys(tops).length===1;}
document.querySelectorAll('.tl-seg').forEach(function(s){var css=(s.className.match(/st-([a-z_]+)/)||[])[1],lab=s.querySelector('.tl-lab');
 if(lab&&shown(lab))return;
 var k=Array.prototype.filter.call(document.querySelectorAll('.tl-k .tl-sw.st-'+css),function(sw){var n=sw.parentNode.querySelector('.tl-kname');return shown(sw.parentNode)&&shown(n);});
 if(!k.length)missing.push(css);});
document.querySelectorAll('.tl-k').forEach(function(li){var n=li.querySelector('.tl-kname');if(shown(li)&&!oneLine(n))split.push(n.textContent);
 if(li.querySelector('.tl-sw.st-review')&&shown(li)&&!shown(li.querySelector('.tl-onme')))noOwner.push(n.textContent);});
document.querySelectorAll('#timeline *').forEach(function(e){var r=e.getBoundingClientRect();if(r.width&&r.right>w+1&&!e.closest('[hidden]')&&getComputedStyle(e).visibility!=='hidden')over.push(e.className+' '+Math.round(r.right));});
var ticks=Array.prototype.filter.call(document.querySelectorAll('.tl-tick'),shown).map(function(t){return t.textContent;});
fetch('/m',{method:'POST',body:JSON.stringify({w:w,missing:missing,split:split,noOwner:noOwner,over:over,ticks:ticks,onyou:shown(document.getElementById('tl-onyou')),
 reviews:document.querySelectorAll('.tl-k .tl-sw.st-review').length,segs:document.querySelectorAll('.tl-seg').length})});},300);});</script>"""
    html = re.sub(r'<link rel="stylesheet" href="/static/desk.css[^"]*">', lambda m: f"<style>{css}</style>", html)
    html = re.sub(r'<link rel="stylesheet" href="https://fonts[^>]*>|<link rel="preconnect"[^>]*>', "", html)
    html = re.sub(r'<script src="[^"]*"[^>]*></script>', "", html).replace("</body>", f"<script>{js}</script>" + measure + "</body>")
    d = run_in_browser(html, width)
    assert d["segs"] == 11 and d["missing"] == [] and d["split"] == [], d  # the ten logged periods and review, set in the file
    assert d["reviews"] == 3 and d["noOwner"] == [] and d["onyou"], d
    assert len(d["ticks"]) >= 1 and d["over"] == [], d


# -- keyboard focus keeps the segment's colour; the current status is never clipped (findings 7 and 8) ----------------------------

def _hex_lum(h):
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _contrast(a, b):
    la, lb = sorted((_hex_lum(a), _hex_lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_static_the_focus_ring_is_outside_the_segment_in_ink_with_3_to_1_contrast_in_both_themes():
    """Protects finding 7 by reading the stylesheet: the segment's focus ring has no negative offset and is not the lime focus colour
    of dark mode; it is ink with a card-coloured gap, and ink on card is at least 3:1 in light and dark; the strip leaves room for it
    and right-aligns its segments so the oldest overflow, not the current one (finding 8)."""
    css = (STATIC / "desk.css").read_text(encoding="utf-8")
    rule = re.search(r"\.tl-seg:focus-visible\{([^}]*)\}", css).group(1)
    assert "outline-offset:-" not in rule and "var(--focus)" not in rule and "var(--ball)" not in rule, rule
    assert re.search(r"box-shadow:0 0 0 2px var\(--card\),0 0 0 5px var\(--ink\)", rule), rule
    light = dict(re.findall(r"--(ink|card|ball):(#[0-9A-Fa-f]{6})", css.split("@media (prefers-color-scheme:dark){:root{")[0]))
    dark = dict(re.findall(r"--(ink|card|ball):(#[0-9A-Fa-f]{6})", css.split("@media (prefers-color-scheme:dark){:root{")[1].split("}}")[0]))
    dark = {**light, **dark}
    for theme in (light, dark):
        assert _contrast(theme["ink"], theme["card"]) >= 3 and theme["ink"].lower() != theme["ball"].lower(), theme
    strip = re.search(r"\.tl-strip\{([^}]*)\}", css).group(1)
    assert "padding:6px" in strip and "justify-content:flex-end" in strip and "overflow:hidden" in strip, strip


FOCUS = r"""<script>addEventListener('load',function(){setTimeout(function(){
var segs=Array.prototype.slice.call(document.querySelectorAll('.tl-seg')),strip=document.querySelector('.tl-strip'),info=document.getElementById('tl-st-info');
var sb=strip.getBoundingClientRect(),cs=getComputedStyle(strip),clip={l:sb.left,r:sb.right,t:sb.top,b:sb.bottom};
var card=getComputedStyle(document.querySelector('.tl-strip-wrap')).backgroundColor,res=[],n0=document.querySelector('.tl-now').getBoundingClientRect();
var nowIn=n0.left>=sb.left-0.5&&n0.right<=sb.right+0.5&&n0.width>=5;  // before any focus: focusing scrolls a clipped strip
function rgb(c){var m=c.match(/[0-9.]+/g);return m?m.slice(0,3).map(Number):[0,0,0];}
function lum(c){return 0.2126*f(c[0])+0.7152*f(c[1])+0.0722*f(c[2]);function f(x){x/=255;return x<=0.03928?x/12.92:Math.pow((x+0.055)/1.055,2.4);}}
function ratio(a,b){var x=lum(rgb(a)),y=lum(rgb(b));return (Math.max(x,y)+0.05)/(Math.min(x,y)+0.05);}
segs.forEach(function(s){var before=getComputedStyle(s).backgroundColor;s.focus();var st=getComputedStyle(s),r=s.getBoundingClientRect();
 var outer=(st.boxShadow.match(/rgba?\([^)]*\)\s+0px\s+0px\s+0px\s+([0-9.]+)px(?!\s*inset)/g)||[]),ring=0,col='';
 outer.forEach(function(x){var m=x.match(/(rgba?\([^)]*\)).*?([0-9.]+)px$/);if(m&&parseFloat(m[2])>ring){ring=parseFloat(m[2]);col=m[1];}});
 var off=st.outlineStyle!=='none'?parseFloat(st.outlineOffset):null;
 res.push({bg:before===st.backgroundColor,outside:ring>=3||(off!==null&&off>=0),ring:ring,
  inClip:r.left-ring>=clip.l-0.5&&r.right+ring<=clip.r+0.5&&r.top-ring>=clip.t-0.5,
  lime:/200, 240, 60/.test(st.boxShadow+st.outlineColor),contrast:col?ratio(col,card):(st.outlineStyle!=='none'?ratio(st.outlineColor,card):0),
  said:info.textContent===s.getAttribute('data-text')});s.blur();});
fetch('/m',{method:'POST',body:JSON.stringify({res:res,n:segs.length,nowIn:nowIn})});},300);});</script>"""


def _focus_page(store, t, width, dark):
    html = _page(store, t)
    css = (STATIC / "desk.css").read_text(encoding="utf-8")
    if dark:  # the headless browser has no dark preference switch: the page's own dark rules are applied unconditionally
        css = css.replace("@media (prefers-color-scheme:dark){", "@media all{")
    js = (STATIC / "timeline.js").read_text(encoding="utf-8")
    html = re.sub(r'<link rel="stylesheet" href="/static/desk.css[^"]*">', lambda m: f"<style>{css}</style>", html)
    html = re.sub(r'<link rel="stylesheet" href="https://fonts[^>]*>|<link rel="preconnect"[^>]*>', "", html)
    html = re.sub(r'<script src="[^"]*"[^>]*></script>', "", html).replace("</body>", f"<script>{js}</script>" + FOCUS + "</body>")
    return run_in_browser(html, width)


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_browser_a_focused_segment_keeps_its_colour_and_its_ring_is_outside_never_lime(store, dark):
    """Protects finding 7 in a real layout, light and dark at 1280 px: focusing each of the 10 segments, the thin ones included, leaves
    its background colour as it was, draws a ring of 3 px or more outside it that is not lime and stands out 3:1 against the card,
    and the line under the strip names the focused segment."""
    d = _focus_page(store, _busy(store), 1280, dark)
    assert d["n"] == 10, d
    for i, r in enumerate(d["res"]):
        assert r["bg"] and r["outside"] and not r["lime"] and r["contrast"] >= 3 and r["said"], (i, r)


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_browser_at_320_px_every_segment_in_order_shows_a_whole_ring_and_a_long_history_keeps_the_current_status(store):
    """Protects focus on a phone and the newest period (findings 7 and 8): at 320 px, focusing every segment of a 60-period strip in
    order names each under the strip and leaves its whole ring inside the strip's visible box; the current segment is whole on the
    right, with the oldest periods the ones that overflow."""
    t = ops.new(store, "A long history", "desk")
    activity.path(store.home, t.id).unlink()
    base = dt.datetime.now().replace(microsecond=0) - dt.timedelta(days=30)
    flow = ["ready", "in_progress", "review", "changes_requested"]
    evs = [activity.Event(t.id, base.isoformat(), "owner-a", "owner", "ui", "created", "created", "", "x", "Created: x")]
    for i in range(60):
        evs.append(activity.Event(t.id, (base + dt.timedelta(hours=11 * (i + 1))).isoformat(), "main-a", "main", "cli", "field", "status",
                                  flow[i % 4] if i else "inbox", flow[(i + 1) % 4], "status change"))
    activity.append(store.home, evs)
    x = store.get(t.id)
    x.meta["status"], x.meta["created"] = flow[60 % 4], base.date().isoformat()
    x.write()
    d = _focus_page(store, store.get(t.id), 320, False)
    assert d["n"] == 61 and d["nowIn"], d
    shown = [r for r in d["res"] if r["inClip"]]
    assert all(r["said"] and r["outside"] for r in d["res"]), d["res"][:3]
    assert d["res"][-1]["inClip"] and len(shown) >= 20, (len(shown), d["res"][-3:])
