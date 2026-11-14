"""DESK-0260: every list of tickets shows tiles, not a wall of linked text.
Protects: James, 2026-11-13, "we need ticket tiles not a wall of linked text". One tile element per ticket, the whole tile is the
one link, and it carries id, title, status, priority, assignee and the first line of Context."""

import re

from fastapi.testclient import TestClient

from desk import ops
from desk.ui.app import create_app

CTX = "## Context\n**Why this exists.** The first line says why, with a [link](http://x.y) and `code`.\n\nSecond paragraph stays out of the tile.\n"


def _mk(store, title, **meta):
    t = ops.new(store, title, "desk")
    t.meta.update(meta)
    t.sections["Context"] = CTX.split("\n", 1)[1]
    t.write()
    return t


def _tiles(page):
    return re.findall(r'<a class="tile[^"]*" href="/ticket/([A-Z]+-\d+)"(.*?)</a>', page, re.S)


def test_today_renders_one_tile_per_ticket_with_the_five_fields_and_one_link(store):
    a = _mk(store, "Fix the double comment", status="in_progress", priority="p0", type="bug", assignee="chaz")
    b = _mk(store, "Plain feature", status="in_progress", priority="p2", assignee="main")
    page = TestClient(create_app(store.home)).get("/").text
    tiles = {tid: body for tid, body in _tiles(page)}
    assert set(tiles) >= {a.id, b.id}
    for t, who, prio in ((a, "Chaz", "Urgent"), (b, "Patch", "Normal")):
        body = tiles[t.id]
        assert t.id in body and t.meta["title"] in body
        assert 'class="tl-status"' in body and 'class="tl-prio' in body and prio in body
        assert who in body
        assert "The first line says why, with a link and code." in body
        assert "Second paragraph" not in body and "<a " not in body  # the tile is the one link
    assert page.count(f'href="/ticket/{a.id}"') >= 1


def test_search_results_are_tiles(store):
    t = _mk(store, "Findable thing", status="ready", priority="p1", assignee="chaz")
    page = TestClient(create_app(store.home)).get("/search?q=Findable").text
    assert t.id in dict(_tiles(page))


def test_board_cards_show_the_context_line(store):
    _mk(store, "Board thing", status="ready", priority="p1", assignee="chaz", track="T")
    page = TestClient(create_app(store.home)).get("/board?open=desk&g=desk:t").text
    assert "The first line says why, with a link and code." in page


def test_context_line_is_plain_and_short():
    from desk.ticket import Ticket
    t = Ticket.__new__(Ticket)
    t.sections = {"Context": "\n**Why.** " + "word " * 80}
    line = t.context_line(100)
    assert len(line) <= 101 and line.endswith("…") and "*" not in line
    t.sections = {"Context": ""}
    assert t.context_line() == ""
