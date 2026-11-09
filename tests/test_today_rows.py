"""DESK-0040: Today shows what needs you, what you are holding, and a short preview of the review pile, as rows, nothing else.
Protects: the 1,277-card page James reported. Order is asks, then in hand, then three review rows with an All N link; only Bug and
Urgent stay as badges; the 30 track chips are one dropdown; the other v0 lists are folded, not removed."""

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
    ask = _mk(store, "Approve the logo rule", type="decision", status="ready", owner="james", review_by="2026-11-15")
    hand = _mk(store, "Fix the double comment", status="in_progress", priority="p0", type="bug")
    review = [_mk(store, f"Built thing {i}", status="review") for i in range(7)]
    page = _page(store)
    assert page.index("Needs you") < page.index("In hand") < page.index("Waiting for review")
    assert _rows(page)[0] == ask.id and hand.id in _rows(page)
    pile = page[page.index("Waiting for review"):]
    assert len(set(_rows(pile)) & {t.id for t in review}) == 3  # a three-row preview, not the pile
    assert "All 7" in page and "/search?q=status%3Areview" in page
    assert "Approve" in page and "due Nov 15" in page


def test_rows_not_cards_and_only_bug_and_urgent_badges(store):
    _mk(store, "Hot bug", status="in_progress", priority="p0", type="bug", tags=["x"], track="First")
    _mk(store, "Plain feature", status="in_progress", priority="p2", type="feature", tags=["y"])
    page = _page(store)
    assert 'class="card' not in page.split("<main>")[1].split("In hand")[1].split("Waiting for review")[0]
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
    assert "James asked" in page and "Nothing waiting" in page
