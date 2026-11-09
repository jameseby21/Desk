"""DESK-0139: long text follows the formatting ladder (standards rule 15-text-formatting.md). The ticket page opens a long Context, Outcome
or Proof with a lead sentence and a few bullets and folds the rest; a wall of prose gets a readability flag and its lead sentence in
bold; `drew validate` warns on a wall over 1,200 characters and on a field over 4,000 that should be a linked Markdown file.
Protects: a 163-word proof and a 13-point list filling the ticket page. The files never change; only the screen does."""

import re

import pytest
from browser_harness import browser, drive
from fastapi.testclient import TestClient

from desk import ladder, ops
from desk.rules import validate_store
from desk.ui.app import create_app

import test_ticket_page_fold as fold


def _page(store, t):
    return TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text


LEAD = "Start with the software-only path: a tool, with no signals."
BULLETS = "\n".join(f"- **Item {i}:** the reason in a few plain words." for i in range(4))
POINTS = "\n".join(f"- Point {i} to verify before relying on this." for i in range(13))
WALL = "The core UI stays open source and paid work is infrastructure only. " * 24  # one paragraph, about 1,600 characters


def test_rung_one_a_sentence_is_shown_as_it_is():
    f = ladder.shape("Why this exists: the cost of the tools.")
    assert f.head == "Why this exists: the cost of the tools." and f.rest == "" and f.flags == []


def test_rung_two_a_lead_and_bullets_that_fit_six_lines_are_not_folded():
    text = f"{LEAD}\n\n{BULLETS}"
    f = ladder.shape(text)
    assert f.rest == "" and "Item 3" in f.head and f.flags == []


def test_rung_three_a_long_structure_shows_the_lead_and_bullets_and_folds_the_list():
    f = ladder.shape(f"{LEAD}\n\n{BULLETS}\n\n{POINTS}")
    assert LEAD in f.head and "Item 3" in f.head and "Point 0" not in f.head
    assert "Point 12" in f.rest and f.label == "13 more points" and f.flags == []


def test_rung_four_a_wall_of_prose_gets_a_bold_lead_a_flag_and_the_rest_folded():
    f = ladder.shape(WALL)
    assert f.head.startswith("**The core UI stays open source and paid work is infrastructure only.**")
    assert len(f.rest) > 1000 and f.label.startswith("Show the rest") and f.flags == ["wall"]


def test_over_4000_characters_says_save_it_as_markdown():
    f = ladder.shape(WALL * 3)
    assert f.flags == ["wall", "save"]
    assert ladder.shape(f"{LEAD}\n\n" + "\n".join(f"- Point {i} with some more words to take up room on the line." for i in range(90))).flags == ["save"]


def test_a_code_block_is_never_cut_in_half():
    code = "```\n" + "\n".join(f"line {i}" for i in range(20)) + "\n```"
    f = ladder.shape(f"Run this:\n\n{code}\n\nThen look.")
    for part in (f.head, f.rest):
        assert part.count("```") % 2 == 0


def test_the_ticket_page_folds_a_long_proof_and_keeps_every_word_on_the_page(store):
    t = fold._algo(store)
    page = _page(store, t)
    assert '<details class="ladder"' in page and "more points" in page
    assert "Point 12 to verify" in page  # the folded text is in the page, one click away
    assert 'class="readflag"' not in page  # a lead and two lists is a shape, not a wall
    w = ops.new(store, "A wall", "desk")
    w.set_section("Report", WALL)
    store.save(w)
    wp = _page(store, w)
    assert 'class="readflag"' in wp and "Wall of text" in wp and "<strong>The core UI stays open source" in wp and '<details class="ladder"' in wp
    assert store.get(t.id).section("Report").count("Point") == 13  # the file is untouched


def test_validate_warns_on_a_wall_and_on_a_field_that_should_be_a_file(store):
    wall = ops.new(store, "A wall", "desk")
    wall.set_section("Context", WALL)
    store.save(wall)
    big = ops.new(store, "Too big", "desk")
    big.set_section("Report", "Ran the build. " + "More detail of what ran and printed, with words. " * 100)
    store.save(big)
    ok = ops.new(store, "Fine", "desk")
    ok.set_section("Context", f"{LEAD}\n\n{BULLETS}")
    store.save(ok)
    _errors, warnings = validate_store(store)
    mine = [w for w in warnings if "readability" in w or "save it as a markdown file" in w]
    assert any(wall.id in w and "readability" in w and "Context" in w for w in mine), mine
    assert any(big.id in w and "save it as a markdown file" in w and "Report" in w for w in mine), mine
    assert not any(ok.id in w for w in mine)


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_an_algo_0002_shaped_page_with_its_long_proof_is_under_2000_px_at_1440(store):
    t = fold._algo(store, long_proof=True)
    got = drive(store, "<script>addEventListener('load',function(){setTimeout(function(){fetch('/m',{method:'POST',body:JSON.stringify({h:document.documentElement.scrollHeight})});},400);});</script>",
                f"/ticket/{t.id}", width=1440)
    assert got["h"] < 2000, got
