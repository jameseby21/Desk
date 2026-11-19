"""DESK-0040: Today shows what needs you, what you are holding, and a short preview of the review pile, as rows, nothing else.
Protects: the 1,277-card page James reported. Order is asks, then in hand, then three review rows with an All N link; only Bug and
Urgent stay as badges; the 30 track chips are one dropdown; the other v0 lists are folded, not removed."""

import datetime as dt
import re

from fastapi.testclient import TestClient

from desk import ops
from desk.ui.app import create_app


def _mk(store, title, project="desk", **meta):
    t = ops.new(store, title, project)
    t.meta.update(meta)
    t.write()
    return t


def _page(store, url="/"):
    return TestClient(create_app(store.home)).get(url).text


def _rows(page):
    return re.findall(r'<a class="trow[^"]*" href="/ticket/([A-Z]+-\d+)"', page)


def test_order_is_needs_you_then_in_hand_then_review_preview_with_all_n(store):
    # a date ten days out always prints as "Mon DD": the week-day form ("Wed") is only for the coming 7 days, so a fixed calendar date
    # in this test went red the day it came inside that week (checker, 2026-11-19)
    due = dt.date.today() + dt.timedelta(days=10)
    ask = _mk(store, "Approve the logo rule", type="decision", status="ready", owner="james", review_by=due.isoformat())
    hand = _mk(store, "Fix the double comment", status="in_progress", priority="p0", type="bug")
    review = [_mk(store, f"Built thing {i}", status="review") for i in range(7)]
    page = _page(store)
    assert page.index("Waiting on you") < page.index("Claimed tickets") < page.index("Waiting for review")
    # since the 2026-11-10 review an ask Needs me draws (the block above Needs you, DESK-0149) is not drawn again under Needs you, so
    # the ask sits once, as a Needs me row, and the first plain row is the ticket in hand
    assert f'class="nm-row" data-ticket="{ask.id}"' in page and ask.id not in _rows(page)
    assert _rows(page)[0] == hand.id
    pile = page[page.index("Waiting for review"):]
    assert len(set(_rows(pile)) & {t.id for t in review}) == 3  # a three-row preview, not the pile
    assert "All 7" in page and "/search?q=status%3Areview" in page
    assert "Approve this" in page and f"due {due.strftime('%b')} {due.day}" in page  # the Needs me row's chip and its reason line


def test_rows_not_cards_and_only_bug_and_urgent_badges(store):
    _mk(store, "Hot bug", status="in_progress", priority="p0", type="bug", tags=["x"], track="First")
    _mk(store, "Plain feature", status="in_progress", priority="p2", type="feature", tags=["y"])
    page = _page(store)
    assert 'class="card' not in page.split("<main>")[1].split("Claimed tickets")[1].split("Waiting for review")[0]
    row = lambda title: re.search(r'<a class="trow[^>]*>(?:(?!</a>).)*' + title + r'.*?</a>', page, re.S).group(0)
    hot, plain = row("Hot bug"), row("Plain feature")
    assert "Urgent" in hot and "Bug" in hot
    assert not re.search(r'class="(chip|badge)', plain) and "#y" not in plain and "ty-feature" not in plain


def test_track_chips_become_one_dropdown_and_filter_still_works(store):
    for i in range(6):
        _mk(store, f"T{i}", track=f"Track {i}", status="in_progress")
    page = _page(store)
    assert 'class="trackchips"' not in page and page.count("<select name=\"track\"") == 1
    assert page.count("track=track-") == 0  # no chip links
    only = _page(store, "/?track=track-2")
    assert _rows(only) and len(_rows(only)) == 1


def test_a_thousand_tickets_do_not_become_a_thousand_rows(store):
    for i in range(60):
        _mk(store, f"Ready {i}", status="ready")
    for i in range(30):
        _mk(store, f"Waiting {i}", status="review")
    page = _page(store)
    assert len(_rows(page)) <= 25
    assert "60 ready" in page and "All 30" in page  # the rest is counted and one click away


def test_the_rest_of_v0_is_folded_not_removed(store):
    _mk(store, "Done lately", status="done")
    _mk(store, "Old", status="ready", updated="2000-01-01")
    page = _page(store)
    assert "Decided this week" in page and "Done lately" in page
    assert "Your questions" in page and "All your questions have an answer." in page


# ---- the "due" chip: a week-day inside the coming 7 days, a calendar date beyond it (checker defect, 2026-11-19) ----------------
# Each test builds its dates from today, so none of them can go stale the way a typed "Nov 25" did.

def _ask_chip(store, days):
    d = dt.date.today() + dt.timedelta(days=days)
    _mk(store, f"Approve in {days} days", type="decision", status="ready", owner="james", review_by=d.isoformat())
    return d, _page(store)


def test_due_chip_unit_day_is_a_weekday_inside_seven_days_and_a_date_from_the_seventh():
    """Method 1, the function: needs_me._day prints the week-day for delta 0 to 6 and 'Mon DD' for 7 and for the past."""
    from desk.needs_me import _day

    now = dt.datetime(2026, 11, 19, 9, 0)  # a Thursday
    assert _day(dt.date(2026, 11, 19), now) == "Thu" and _day(dt.date(2026, 11, 25), now) == "Wed"  # delta 0 and 6
    assert _day(dt.date(2026, 11, 26), now) == "Nov 26" and _day(dt.date(2026, 11, 18), now) == "Nov 18"  # delta 7 and -1


def test_due_chip_route_a_near_date_reads_as_a_weekday_and_a_far_one_as_a_date(store):
    """Method 2, the page: three days out the chip says the week-day; ten days out it says the calendar date."""
    near, page = _ask_chip(store, 3)
    assert f"due {near.strftime('%a')}" in page
    far, page = _ask_chip(store, 10)
    assert f"due {far.strftime('%b')} {far.day}" in page


def test_due_chip_cli_the_same_wording_in_the_text_the_cli_builds(store):
    """Method 3, the text builder the CLI and the page share: the chip line for a ticket due in 6 days names the week-day, in 7 the date."""
    from desk import needs_me

    now = dt.datetime.now()
    for days, want in ((6, (now + dt.timedelta(days=6)).strftime("%a")), (7, None)):
        d = (now + dt.timedelta(days=days)).date()
        got = needs_me._day(d, now)
        assert got == (want or f"{d.strftime('%b')} {d.day}"), (days, got)


def test_due_chip_edge_overdue_says_the_date_and_overdue(store):
    """Edge: a date already past is never drawn as a week-day (it would read as next week); the chip adds 'overdue'."""
    d, page = _ask_chip(store, -2)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page))  # the chip's words are split by markup in the raw page
    assert f"due {d.strftime('%b')} {d.day} overdue" in text
