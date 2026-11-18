"""DESK-0214 (DECISIONS 94): work that solves a repeatable, periodic process carries a visible boost, and every project shows how many
such solutions it has shipped. Protects: the `repeatable-process` tag silently counting for nothing, and the Projects page losing the count."""

import re

from fastapi.testclient import TestClient

from desk import priority
from desk.ui.app import create_app
from test_priority import _mk, _profile, _scores  # pytest puts tests/ on the path


def test_the_tag_adds_a_term_named_in_plain_words_and_explain_shows_it(store):
    _profile(store, "desk", "Desk", status="active", monetization="planned", clients=0, enabler="true")
    rep = _mk(store, "Monthly report in one click", "desk", type="task", tags=["repeatable-process"])
    plain = _mk(store, "Monthly report by hand", "desk", type="task")
    sc = _scores(store)
    assert sc.of(rep).score - sc.of(plain).score == 8 and "solves a repeatable process" in sc.of(rep).reasons()
    assert re.search(r"\+8\.0\s+tag:repeatable-process\s+solves a repeatable process\s+default", priority.explain(store, rep, sc))


def test_the_projects_page_counts_done_repeatable_solutions_per_project(store):
    _profile(store, "desk", "Desk", status="active", monetization="planned", clients=0, enabler="true")
    _mk(store, "Open repeatable thing", "desk", type="task", tags=["repeatable-process"])  # open: not counted yet
    done = _mk(store, "Shipped repeatable thing", "desk", type="task", status="done", tags=["repeatable-process"])
    _mk(store, "Shipped plain thing", "desk", type="task", status="done")
    assert priority.repeatable_done(store.tickets()) == 1
    page = TestClient(create_app(store.home)).get("/projects").text
    row = page[page.index('data-val="desk"'):]
    row = row[:row.index("</summary>")]
    assert "1 repeatable solution</span>" in row and done.id in page
    jre = page[page.index('data-val="jre"'):]
    assert "repeatable solution" not in jre[:jre.index("</summary>")]
