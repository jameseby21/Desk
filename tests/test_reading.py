"""DESK-0287: reading mode. Protects: the ticket page opens on Latest change, then Next action, then the ask, with Context folded; a
Report with a summary shows it and the one ask on top and folds the rest; `validate` warns on a long Report with no summary; the
last-seen marker per viewer drives a diff since the last visit and /changes per company and project; agent comments fold to one line
unless they ask the owner something; rows on Today carry a short 'why' line; at 390 px nothing scrolls sideways."""

import datetime as dt
import re
from contextlib import contextmanager
from pathlib import Path

import pytest
from browser_harness import browser, run_in_browser
from fastapi.testclient import TestClient

from desk import identity, ops, plainwords, reading
from desk.rules import validate_store
from desk.ui.app import create_app

STATIC = Path(__file__).resolve().parents[1] / "src" / "desk" / "ui" / "static"

LONG_NO_SUMMARY = "\n".join(f"- Step {n}: ran the check against the store and wrote down every line it printed, then compared them with the day before." for n in range(12))
SUMMARY_REPORT = """- Built the reading mode for the ticket page.
- Full suite green: 700 passed.
- One open point on the diff colours.

Ask: approve the colours, or name the ones to change.

**Detail.**

""" + LONG_NO_SUMMARY


@contextmanager
def acting(name, kind="person", source="cli"):
    token = identity.CURRENT.set(identity.Actor(name, kind, "", source, "claimed", False))
    try:
        yield
    finally:
        identity.CURRENT.reset(token)


def _decision(store):
    with acting("patch", "session"):
        t = ops.new(store, "Choose the colour of the diff", "desk", "decision", owner="james", assignee="james")
        x = store.get(t.id)
        x.meta["status"] = "ready"
        x.sections["Context"] = "Two looks were drawn for the diff.\n\nOptions: A green and struck; B bold only.\n"
        x.sections["Outcome"] = "The diff colour is chosen. Then the build goes on.\n"
        store.save(x)
    return store.get(t.id)


def _browser(store):
    """A client that behaves like the person's browser: it carries the device cookie Desk gave it on an earlier visit."""
    return TestClient(create_app(store.home), cookies={"desk_device": "testbrowser"})


def _page(store, tid):
    return _browser(store).get(f"/ticket/{tid}").text


def test_ticket_page_order_is_latest_change_next_action_ask_then_folded_context(store):
    """Protects: the first block is Latest change, the second Next action, then the answer box, and Context sits inside a closed fold."""
    t = _decision(store)
    html = _page(store, t.id)
    pos = [html.find(k) for k in ('id="tl-latest"', 'id="next-action"', 'id="reply"', 'id="context-fold"', 'id="acceptance"')]
    assert all(p > 0 for p in pos) and pos == sorted(pos), pos
    fold = re.search(r'<details class="fold rd-ctx" id="context-fold">(.*?)</details>', html, re.S)
    assert fold and "Two looks were drawn" in fold.group(1)
    assert "The diff colour is chosen." in re.search(r'<span class="rd-goal">(.*?)</span>', html).group(1)  # the fold says the goal
    nxt = re.search(r'<section class="rd-next mine" id="next-action".*?</section>', html, re.S).group(0)
    assert "For you" in nxt and "Choose one" in nxt and 'href="#reply"' in nxt


def test_next_action_without_an_ask_follows_the_status_and_shows_the_smallest_step(store):
    """Protects: a ticket someone else holds says who has it and what comes next, with its smallest step."""
    with acting("patch", "session"):
        t = ops.new(store, "Draw the board icons", "desk", owner="chaz", assignee="chaz")
        x = store.get(t.id)
        x.meta["status"], x.meta["smallest_step"] = "in_progress", "One icon in both themes"
        store.save(x)
    nxt = re.search(r'id="next-action".*?</section>', _page(store, t.id), re.S).group(0)
    assert "For Chaz" in nxt and "Chaz is working on it" in nxt and "Smallest step: One icon in both themes" in nxt
    assert 'class="rd-next mine"' not in nxt


def test_report_reading_mode_puts_three_findings_and_the_ask_on_top_and_folds_the_rest(store):
    """Protects: a Report that opens with up to three findings shows them and the one ask first; the long detail is folded."""
    rv = reading.report_view(SUMMARY_REPORT, "james")
    assert rv.findings == ["Built the reading mode for the ticket page.", "Full suite green: 700 passed.", "One open point on the diff colours."]
    assert rv.ask == "approve the colours, or name the ones to change." and "Step 11" in rv.rest
    assert reading.report_view("James: pick one.\n\nShort finding in one line.\n\nMore.", "james").ask == "pick one."
    assert reading.report_view(LONG_NO_SUMMARY, "james") is None  # a list of twelve is not three findings
    assert reading.report_view(LONG_NO_SUMMARY.replace("- ", ""), "james") is None  # nor is a twelve-line paragraph
    with acting("patch", "session"):
        t = ops.new(store, "Build reading mode", "desk")
        x = store.get(t.id)
        x.sections["Report"] = SUMMARY_REPORT
        store.save(x)
    html = _page(store, t.id)
    top = html.find('class="rd-findings"')
    assert 0 < top < html.find('id="report-ask"') < html.find("Read the full report")
    rest = re.search(r'<details class="ladder rd-rest">(.*?)</details>', html, re.S).group(1)
    assert "Step 11" in rest and "Step 11" not in html[:html.find('<details class="ladder rd-rest">')]


def test_validate_warns_on_a_long_report_with_no_summary_and_not_when_it_has_one(store):
    """Protects: `drew validate` names a Report over 1,200 characters that opens with no summary (rule 15); a summary or a short
    Report is quiet, and a closed ticket's Report is a record that is not flagged."""
    with acting("patch", "session"):
        bad = ops.new(store, "Long report", "desk")
        good = ops.new(store, "Long report with a summary", "desk")
        short = ops.new(store, "Short report", "desk")
        for tid, text in ((bad.id, LONG_NO_SUMMARY), (good.id, SUMMARY_REPORT), (short.id, "Ran it; it passed.")):
            x = store.get(tid)
            x.sections["Report"] = text
            store.save(x)
    _errors, warns = validate_store(store)
    hits = [w for w in warns if "no summary on top" in w]
    assert len(LONG_NO_SUMMARY) > 1200 and len(hits) == 1 and hits[0].startswith(f"{bad.id}: Report is ") and "rule 15" in hits[0]
    assert _page(store, bad.id).count("No summary on top") == 1  # the page flags it too
    x = store.get(bad.id)
    assert reading.warnings(x, "james")
    x.meta["status"] = "done"
    assert reading.warnings(x, "james") == []


def test_last_seen_marker_gives_a_diff_since_the_last_visit_with_added_and_removed_lines(store):
    """Protects: the first visit shows no diff and records the marker; after someone edits Context and the status, the next visit shows
    the removed line struck and the added line green; the visit after that shows nothing again."""
    t = _decision(store)
    first = _page(store, t.id)
    assert 'id="since-last-visit"' not in first
    assert reading.seen_at(store.home, "james", t.id) is not None
    with acting("patch", "session"):
        x = store.get(t.id)
        x.sections["Context"] = x.sections["Context"].replace("Two looks were drawn for the diff.", "Three looks were drawn for the diff.")
        x.meta["priority"] = "p1"
        store.save(x)
    html = _page(store, t.id)
    d = re.search(r'<details class="rd-diff" id="since-last-visit">(.*?)</details>', html, re.S).group(1)
    assert re.search(r'class="rd-del">.*?<del>Two looks were drawn for the diff.</del>', d)
    assert re.search(r'class="rd-add">.*?<ins>Three looks were drawn for the diff.</ins>', d)
    assert "<b>priority</b>: <del>p2</del> &rarr; <ins>p1</ins>" in d and "1 section and 1 field changed" in d
    assert 'id="since-last-visit"' not in _page(store, t.id)
    other = reading.ticket_diff(None, store.get(t.id))
    assert other is None  # no earlier visit, no diff


def test_changes_page_lists_others_changes_since_the_last_visit_per_company_and_project(store):
    """Protects: /changes lists tickets someone else changed since this viewer's last visit, grouped by company and project, with the
    what-lines; opening it moves the marker; ?since=DAY looks further back without moving it; project and own-changes filters work."""
    with acting("patch", "session"):
        a = ops.new(store, "Fix the search box", "desk")
        b = ops.new(store, "New listing page", "jre")
    with acting("james", "person", "ui"):
        mine = ops.new(store, "My own note", "desk")
    c = _browser(store)
    first = c.get("/changes").text
    assert "in the last 7 days (your first visit)" in first and a.id in first and b.id in first and mine.id not in first
    assert "Created: Fix the search box" in first and "Patch: " in first
    assert first.find("Desk") > 0 and first.find("jRE") > 0
    assert "Nothing changed since your last visit" in c.get("/changes").text  # the marker moved
    with acting("chaz", "thread"):
        ops.comment(store, store.get(a.id), "Boards posted for the search box", actor="chaz", kind="FYI")
    again = c.get("/changes").text
    assert a.id in again and "Chaz: Boards posted for the search box" in again and b.id not in again
    today = dt.date.today().isoformat()
    dated = c.get(f"/changes?since={today}").text
    assert a.id in dated and b.id in dated
    assert "Nothing changed since your last visit" in c.get("/changes").text  # a dated look left the marker where it was
    narrowed = c.get(f"/changes?since={today}&project=jre").text
    assert b.id in narrowed and a.id not in narrowed.split('class="rd-cgroup"', 1)[1]
    assert mine.id in c.get(f"/changes?since={today}&mine=1").text
    assert "not a day Desk can read" in c.get("/changes?since=yesterday-ish").text


def test_agent_comments_fold_to_one_line_unless_they_ask_the_owner(store):
    """Protects: a main-session or coworker comment folds to one line; one with a question or an ask stays open; the owner's never folds."""
    with acting("patch", "session"):
        t = ops.new(store, "Comment folding", "desk")
        ops.comment(store, store.get(t.id), "Ran the suite, all green", actor="main", kind="FYI")
        ops.comment(store, store.get(t.id), "Should the fold keep the date", actor="main", kind="Question")
        ops.comment(store, store.get(t.id), "Shall I merge it now?", actor="main", kind="Question")
    with acting("chaz", "thread"):
        ops.comment(store, store.get(t.id), "Boards are in the folder; your call on the second one", actor="chaz", kind="FYI")
    with acting("james", "person", "ui"):
        ops.comment(store, store.get(t.id), "Looks right to me", actor="james", kind="FYI")
    html = _page(store, t.id)
    folded = re.findall(r'<details class="rd-chat"><summary><b>(.*?)</b> <span class="rd-chat-line">(.*?)</span>', html)
    assert folded == [("Patch", "Ran the suite, all green"), ("Patch", "Should the fold keep the date")]
    open_part = re.sub(r'<details class="rd-chat">.*?</details>', "", html, flags=re.S)
    assert "Shall I merge it now?" in open_part and "your call on the second one" in open_part and "Looks right to me" in open_part


def test_why_line_says_why_a_row_is_on_the_list(store):
    """Protects: the 'why' line names an ask of the viewer, a change by someone else since the viewer last looked, a deadline, the Now
    list and a ticket back from snooze, two at most; Today's rows show it."""
    t = _decision(store)
    line = reading.why_line(store, store.get(t.id), "james")
    assert line.startswith("asked of you: choose · changed by Patch")
    _page(store, t.id)  # the viewer looks: the change is no longer news
    assert "changed by" not in reading.why_line(store, store.get(t.id), "james")
    with acting("patch", "session"):
        x = store.get(t.id)
        x.meta.update({"snooze_until": (dt.date.today() - dt.timedelta(days=1)).isoformat(), "deadline": "2030-01-05", "deadline_kind": "hard"})
        store.save(x)
    parts = dict(reading.why_parts(store, store.get(t.id), "james"))
    assert parts["snooze"].startswith("back from snooze") and parts["deadline"] == "hard deadline Jan 5"
    assert reading.why_line(store, store.get(t.id), "james").startswith("back from snooze")
    with acting("patch", "session"):
        h = ops.new(store, "Draw the board icons", "desk", owner="chaz", assignee="chaz")
        x = store.get(h.id)
        x.meta["status"] = "in_progress"
        store.save(x)
    today = TestClient(create_app(store.home)).get("/").text
    row = re.search(rf'<a class="trow" href="/ticket/{h.id}">.*?</a>', today, re.S).group(0)
    assert re.search(r'<span class="tr-part tr-why">Why here: changed by Patch [A-Z][a-z]{2} \d+ · held by Chaz</span>', row), row


def test_new_words_on_screen_are_plain():
    """Protects: the Next action sentences read at grade 10 or lower and use no listed jargon (DESK-0253)."""
    for text in list(reading.STATUS_NEXT.values()) + list(reading.STATUS_NEXT_YOU.values()):
        g = plainwords.grade(" ".join([text] * 3))
        assert (g is None or g <= plainwords.MAX_GRADE) and not plainwords.jargon(text), text


MEASURE = """<script>window.addEventListener('load',function(){var w=window.innerWidth,over=[];
document.querySelectorAll('details').forEach(function(d){d.open=true;});
document.querySelectorAll('#next-action,#since-last-visit,#context-fold,.rd-report,.rd-chat,#comments').forEach(function(e){var r=e.getBoundingClientRect();if(r.right>w+1)over.push(e.id||e.className);});
fetch('/m',{method:'POST',body:JSON.stringify({scroll:document.documentElement.scrollWidth,inner:w,over:over,next:!!document.getElementById('next-action')})});});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_reading_mode_at_390_px_never_scrolls_sideways(store):
    """Protects: at phone width the Next action, the diff, the folds and the Report summary stay inside the screen."""
    t = _decision(store)
    _page(store, t.id)
    with acting("patch", "session"):
        x = store.get(t.id)
        x.sections["Context"] += "\nA long line of plain words " + "that goes on and on " * 30 + "\n"
        x.sections["Report"] = SUMMARY_REPORT
        store.save(x)
        ops.comment(store, store.get(t.id), "A long FYI " + "that goes on and on without a break " * 10, kind="FYI")
    html = _page(store, t.id)
    css = (STATIC / "desk.css").read_text(encoding="utf-8") + (STATIC / "reading.css").read_text(encoding="utf-8")
    html = re.sub(r'<link rel="stylesheet" href="/static/(desk|reading)\.css[^"]*">', "", html).replace("</head>", f"<style>{css}</style></head>")
    html = re.sub(r'<link rel="stylesheet" href="https://fonts[^>]*>|<link rel="preconnect"[^>]*>', "", html)
    html = re.sub(r'<script src="[^"]*"[^>]*></script>', "", html).replace("</body>", MEASURE + "</body>")
    data = run_in_browser(html, 390)
    assert data["next"] and data["over"] == [] and data["scroll"] <= data["inner"], data


# ---- fixes after review --------------------------------------------------------------------------------------------------------

TWO_ASKS = """Built the thing and it passes.

Details here.

- Ask: approve X.
- Ask: also decide Y before Friday
- James: said keep the old colours
- other note"""


def _shown(rv) -> str:
    return "\n".join([*rv.findings, rv.ask, rv.rest])


def test_report_reading_mode_drops_only_the_one_ask_line_and_keeps_every_other_line(store):
    """Protects: only the line shown under The ask leaves the Report body; a second ask and a quoted owner line stay in the folded rest."""
    rv = reading.report_view(TWO_ASKS, "James")
    assert rv.ask == "approve X." and "approve X" not in rv.rest
    assert "also decide Y before Friday" in rv.rest and "said keep the old colours" in rv.rest and "other note" in rv.rest
    top = reading.report_view("Ask: pick A.\nJames: B is also fine by me\n\nShort finding in one line.\n\nMore detail.", "james")
    assert top.ask == "pick A." and "B is also fine by me" in _shown(top) and "More detail." in top.rest
    with acting("patch", "session"):
        t = ops.new(store, "Two asks in a report", "desk")
        x = store.get(t.id)
        x.sections["Report"] = TWO_ASKS
        store.save(x)
    html = _page(store, t.id)
    rest = re.search(r'<details class="ladder rd-rest">(.*?)</details>', html, re.S).group(1)
    assert "also decide Y before Friday" in rest and "said keep the old colours" in rest


def test_a_lone_heading_or_bold_lead_is_not_a_report_summary(store):
    """Protects: a long Report that opens with only a heading, a bold lead or a 'label:' line has no summary (rule 15) and is warned
    about; a bold lead with sentences in the same paragraph, or a heading over a real summary, still counts."""
    for lead in ("**Changed.**", "**Changed:**", "## What I did", "Commands run:", "Report", "**Built (commit 9b4bd8c, pushed):**"):
        assert reading.report_view(lead + "\n\n" + LONG_NO_SUMMARY, "james") is None, lead
    ok = reading.report_view("**Summary.** The page opens on the change. The suite is green. One colour is open.\n\n" + LONG_NO_SUMMARY, "james")
    assert ok and ok.findings[0].startswith("**Summary.** The page opens on the change.")
    headed = reading.report_view("## Summary\n\nThe page opens on the change. The suite is green.\n\n" + LONG_NO_SUMMARY, "james")
    assert headed.findings == ["The page opens on the change. The suite is green."] and "## Summary" in headed.rest
    with acting("patch", "session"):
        t = ops.new(store, "Report with a bold lead only", "desk")
        x = store.get(t.id)
        x.sections["Report"] = "**Changed.**\n\n" + LONG_NO_SUMMARY
        store.save(x)
    _errors, warns = validate_store(store)
    assert any(w.startswith(f"{t.id}: Report is ") and "no summary on top" in w for w in warns), warns
