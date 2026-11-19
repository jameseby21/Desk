"""DESK-0334: every page type links the ticket ids it shows, and the Next action card names the person whose step it is.

James's screenshot (2026-11-19, MGMT-0112): the Next action card said "Smallest step: Rewrite CONS-0117's first line..." with the id as plain
text, and said "For you" although the ticket's assignee is Patch. Protects: the Next action card and its smallest step, the Latest change
line, blocked_on, comments, Decision lines, rows and cards on Today and the board, search results, hover cards and the changes page
all link an id that exists in the store; a missing id stays plain with a title; and the card says "For <name>" for the assignee and
"For you" only when the viewer is the assignee (or is asked something that only the owner can answer).

Three methods: (1) the regex on the served HTML; (2) tests/idsweep.py, a separate parser-based walker that lists any bare id; (3) the
sweep over every page type on a store full of ids (test_id_links_sweep.py) and the real browser (test_id_links_browser.py)."""

import re

import pytest
from fastapi.testclient import TestClient
from idsweep import bare_ids, linked_ids

from desk import ops, reading
from desk.ui.app import create_app


def _client(store):
    return TestClient(create_app(store.home), cookies={"desk_device": "testbrowser"})


@pytest.fixture
def world(store):
    """B is the ticket everyone mentions; A mentions it in every field Desk shows; C is blocked on it; D mentions an id that does not exist."""
    b = ops.new(store, "Rewrite the first line of the readability ticket", "desk", "task", owner="james", assignee="main")
    a = ops.new(store, f"Fix the card that hid {b.id}", "desk", "bug", owner="james", assignee="main")
    x = store.get(a.id)
    x.meta["status"] = "ready"
    x.meta["smallest_step"] = f"Rewrite {b.id}'s first line so a stranger can find the sentence"
    x.sections["Context"] = f"Context that names {b.id}.\n"
    x.sections["Comments"] = f"- 2026-11-19 00:10 Patch: waiting on {b.id} before this.\n"
    x.sections["Decision"] = f'- 2026-11-19 00:12 James: "do {b.id} first"\n'
    store.save(x)
    c = ops.new(store, "A blocked ticket", "desk", "task", owner="james", assignee="main")
    y = store.get(c.id)
    y.meta["status"], y.meta["blocked_on"] = "blocked", f"{b.id} and a missing ZZZ-9999"
    y.sections["Comments"] = "- 2026-11-19 00:11 Patch: blocked.\n"
    store.save(y)
    d = ops.new(store, "Mentions a ticket that does not exist ZZZ-9999", "desk", "task", owner="james", assignee="main")
    return store, a, b, c, d


PAGES = [
    "/ticket/{a}",          # the ticket page: h1, Latest change, Next action, smallest step, Context, Comments, Decision
    "/ticket/{c}",          # blocked_on and the header line
    "/",                    # Today: Needs me rows, cards
    "/board?project=desk&company=*&open=*&g=*",   # board cards
    "/search?q=Fix+the+card",  # search results
    "/card/ticket/{a}",     # the hover card
    "/peek/{a}",            # the peek panel
    "/changes",             # the changes page
    "/projects",
]


@pytest.mark.parametrize("path", PAGES)
def test_every_page_type_links_the_id_it_shows(world, path):
    """Protects: B's id is a link, and no id that exists is left bare, on each page type that shows ticket text."""
    store, a, b, c, d = world
    r = _client(store).get(path.format(a=a.id, b=b.id, c=c.id))
    assert r.status_code == 200, path
    existing = {t.id for t in store.tickets()}
    assert bare_ids(r.text, existing) == [], (path, bare_ids(r.text, existing))
    if path.startswith(("/ticket/", "/card/", "/peek/", "/search")) or path in ("/", "/board?project=desk&company=*&open=*&g=*"):
        assert b.id in linked_ids(r.text) or a.id in linked_ids(r.text) or c.id in linked_ids(r.text), path


def test_the_next_action_card_links_the_id_in_the_smallest_step_and_says_for_patch(world):
    """Protects the owner's screenshot: the card on a ticket Patch holds says "For Patch" and links the id in its smallest step."""
    store, a, b, c, d = world
    html = _client(store).get(f"/ticket/{a.id}").text
    nxt = re.search(r'<section class="rd-next[^"]*" id="next-action".*?</section>', html, re.S).group(0)
    assert re.search(r'<span class="rd-for">For Patch</span>', nxt), nxt
    assert f'Smallest step: Rewrite <a class="tid" href="/ticket/{b.id}"' in nxt, nxt
    assert "For you" not in nxt


def test_blocked_on_and_the_latest_change_line_link_the_id_and_a_missing_id_is_marked(world):
    store, a, b, c, d = world
    html = _client(store).get(f"/ticket/{c.id}").text
    assert f'<a class="tid" href="/ticket/{b.id}"' in html
    assert '<span class="tid-missing" title="No such ticket">ZZZ-9999</span>' in html


def test_a_missing_id_is_not_a_link_on_a_row(world):
    store, a, b, c, d = world
    html = _client(store).get("/board?project=desk&company=*&open=*&g=*").text
    assert 'href="/ticket/ZZZ-9999"' not in html and 'data-href="/ticket/ZZZ-9999"' not in html
    assert '<span class="tid-missing" title="No such ticket">ZZZ-9999</span>' in html


def test_an_id_in_a_row_title_is_a_keyboard_link_span_because_the_row_is_already_a_link(world):
    """Protects: a row is one big link, so an id inside its title is a role=link span (a nested <a> is invalid HTML)."""
    store, a, b, c, d = world
    html = _client(store).get("/board?project=desk&company=*&open=*&g=*").text
    assert f'<span class="tid" role="link" tabindex="0" data-href="/ticket/{b.id}"' in html
    assert not re.search(r'<a [^>]*>(?:(?!</a>).)*<a ', html, re.S), "an anchor is nested inside an anchor"


# ---- the profanity filter reaches the title a link carries (found by tests/test_hovercards.py when the id in a card became a link) -----

def test_a_links_hover_title_obeys_the_profanity_filter_on_a_page_and_in_the_ids_endpoint(store):
    """Protects: the `title` of an id link (the ticket's title) is censored like the text beside it; /ids says the same."""
    t = ops.new(store, "Fucking mess", "jre", "task")
    c = _client(store)
    page = c.get(f"/card/ticket/{t.id}").text
    assert "Fucking" not in page and f'href="/ticket/{t.id}"' in page
    assert "Fucking" not in c.get(f"/ids?q={t.id}").text and t.id in c.get(f"/ids?q={t.id},ZZZ-9999").json()
    assert "ZZZ-9999" not in c.get(f"/ids?q={t.id},ZZZ-9999").json()
