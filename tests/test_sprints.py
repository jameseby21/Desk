"""DESK-0147: sprints. Protects: parallel named sprints ('sprint 6 2026') that cut across projects, a `sprint:` field checked against
sprints.yaml, filters on the board, Today and `drew list`, a page with burn-down numbers, and the same name in two projects."""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from desk import ops, sprints
from desk.cli import main
from desk.rules import validate_store
from desk.ui.app import create_app

NAME = "sprint 6 2026"


def _mk(store, title, project="desk", **meta):
    t = ops.new(store, title, project)
    t.meta.update(meta)
    t.write()
    return store.get(t.id)


def _new(store, capsys, *extra, name=NAME):
    main(["--home", str(store.home), "sprint", "new", name, "--start", "2026-11-09", "--end", "2026-11-22", "--owner", "james", *extra])
    capsys.readouterr()


def test_create_writes_sprints_yaml_and_lists(store, capsys):
    main(["--home", str(store.home), "sprint", "new", NAME, "--start", "2026-11-09", "--end", "2026-11-22", "--owner", "james", "--project", "desk", "--note", "ship search"])
    assert "created" in capsys.readouterr().out
    (s,) = sprints.load(store.home)
    assert (s.name, s.project, s.start, s.end, s.owner, s.note) == (NAME, "desk", "2026-11-09", "2026-11-22", "james", "ship search")
    main(["--home", str(store.home), "sprint", "list"])
    out = capsys.readouterr().out
    assert NAME in out and "2026-11-09" in out and "desk" in out


def test_create_refuses_a_duplicate_a_bad_date_an_unknown_project_and_an_end_before_the_start(store, capsys):
    _new(store, capsys, "--project", "desk")
    for args in ((NAME, "--project", "desk"), (NAME, "--start", "tomorrow"), ("other", "--project", "nosuch"), ("backwards", "--start", "2026-12-01", "--end", "2026-11-01")):
        with pytest.raises(SystemExit):
            main(["--home", str(store.home), "sprint", "new", *args])
    assert len(sprints.load(store.home)) == 1


def test_move_puts_a_ticket_in_a_sprint_and_refuses_an_unknown_one(store, capsys):
    t = _mk(store, "Plan it")
    with pytest.raises(SystemExit):
        main(["--home", str(store.home), "move", t.id, "--sprint", NAME])
    assert "drew sprint new" in capsys.readouterr().err and "sprint" not in store.get(t.id).meta
    _new(store, capsys)
    main(["--home", str(store.home), "move", t.id, "--sprint", NAME.upper()])  # the spelling in sprints.yaml wins
    assert store.get(t.id).meta["sprint"] == NAME and "sprint set to" in store.get(t.id).section("Comments")
    main(["--home", str(store.home), "move", t.id, "--sprint", ""])
    assert "sprint" not in store.get(t.id).meta
    with pytest.raises(SystemExit):
        main(["--home", str(store.home), "move", t.id])  # neither --to nor --sprint


def test_move_can_do_both_status_and_sprint(store, capsys):
    t = _mk(store, "Both")
    _new(store, capsys)
    main(["--home", str(store.home), "move", t.id, "--to", "triage", "--sprint", NAME])
    got = store.get(t.id)
    assert got.status == "triage" and got.meta["sprint"] == NAME


def test_validate_warns_on_an_unknown_sprint(store, capsys):
    _mk(store, "Orphan", sprint="sprint 9 2027")
    _mk(store, "Known", sprint=NAME)
    _new(store, capsys)
    warns = [w for w in validate_store(store)[1] if "sprint" in w]
    assert len(warns) == 1 and "sprint 9 2027" in warns[0] and "drew sprint new" in warns[0]
    t = _mk(store, "Empty", sprint="  ")
    assert any("sprint must be a non-empty string" in e for e in validate_store(store)[0]) and t


def test_list_filters_by_sprint(store, capsys):
    _new(store, capsys)
    a = _mk(store, "In it", sprint=NAME)
    _mk(store, "Not in it")
    main(["--home", str(store.home), "list", "--sprint", "Sprint 6 2026"])
    out = capsys.readouterr().out
    assert a.id in out and "Not in it" not in out


def test_board_and_today_filter_by_sprint_and_show_the_chip(store, capsys):
    _new(store, capsys)
    a = _mk(store, "Inside", sprint=NAME, status="ready")
    _mk(store, "Outside", status="ready")
    c = TestClient(create_app(store.home))
    board = c.get("/board?sprint=sprint-6-2026").text
    assert "Inside" in board and "Outside" not in board and "sprint=sprint-6-2026" in board
    assert "Outside" in c.get("/board?open=*&g=*").text and "sprint=sprint-6-2026" in c.get("/board?open=*&g=*").text  # the chip is offered
    today = c.get("/?sprint=sprint-6-2026").text
    assert "Inside" in today and "Outside" not in today
    assert f'href="/sprint/sprint-6-2026"' in c.get(f"/ticket/{a.id}").text


def test_sprint_page_lists_tickets_in_score_order_with_burn_down(store, capsys):
    _new(store, capsys, "--project", "desk")
    today = dt.date.today()
    lo = _mk(store, "Low", sprint=NAME, priority="p3", status="ready")
    hi = _mk(store, "High", sprint=NAME, priority="p0", status="ready")
    soon = _mk(store, "Mid soon", sprint=NAME, priority="p1", status="ready", review_by=(today + dt.timedelta(days=1)).isoformat())
    late = _mk(store, "Mid late", sprint=NAME, priority="p1", status="ready", review_by=(today + dt.timedelta(days=9)).isoformat())
    done = _mk(store, "Finished", sprint=NAME, priority="p2", status="done", done_at="2026-11-10T10:00:00")
    c = TestClient(create_app(store.home))
    page = c.get("/sprint/sprint-6-2026").text
    order = [page.index(t.id) for t in (hi, soon, late, done, lo)]
    assert order == sorted(order)
    assert "<b>5</b> ticket" in page and "<b>1</b> done" in page and "<b>4</b> remaining" in page and "Burn-down numbers" in page
    assert c.get("/sprint/nope").status_code == 404
    assert NAME in c.get("/sprint").text


def test_burn_down_numbers(store, capsys):
    _new(store, capsys)
    for i in range(4):
        _mk(store, f"Work {i}", sprint=NAME, status="done" if i < 2 else "ready", done_at="2026-11-11T09:00:00" if i == 0 else "2026-11-13T09:00:00")
    (g,) = sprints.groups(store, "sprint-6-2026", today=dt.date(2026, 11, 14))
    assert (g.total, g.done, g.remaining, g.days_total, g.days_left) == (4, 2, 2, 14, 8)
    series = {d: r for d, r, _ in g.series}
    assert series["2026-11-10"] == 4 and series["2026-11-11"] == 3 and series["2026-11-13"] == 2 and series["2026-11-14"] == 2
    assert g.series[0][2] == 4.0 and g.ideal_now < 4  # the ideal line falls


def test_the_same_name_in_two_projects_shows_two_groups(store, capsys):
    _new(store, capsys, "--project", "desk")
    _new(store, capsys, "--project", "jre")
    d = _mk(store, "Desk work", "desk", sprint=NAME)
    j = _mk(store, "Jre work", "jre", sprint=NAME)
    both = sprints.groups(store, "sprint-6-2026")
    assert [(g.sprint.project, [t.id for t in g.tickets]) for g in both] == [("desk", [d.id]), ("jre", [j.id])]
    page = TestClient(create_app(store.home)).get("/sprint/sprint-6-2026").text
    assert "(desk)" in page and "(jre)" in page and "Desk work" in page and "Jre work" in page
    assert not [w for w in validate_store(store)[1] if "not in sprints.yaml" in w]


def test_a_sprint_without_a_project_spans_projects_and_unknown_names_get_their_own_group(store, capsys):
    _new(store, capsys)
    a = _mk(store, "A", "desk", sprint=NAME)
    b = _mk(store, "B", "jre", sprint=NAME)
    (g,) = sprints.groups(store, "sprint-6-2026")
    assert {t.id for t in g.tickets} == {a.id, b.id}
    z = _mk(store, "Z", "desk", sprint="sprint 7 2026")
    (u,) = sprints.groups(store, "sprint-7-2026")
    assert u.sprint is None and [t.id for t in u.tickets] == [z.id] and u.name == "not in sprints.yaml"


def test_cli_show(store, capsys):
    _new(store, capsys)
    _mk(store, "Shown", sprint=NAME, priority="p0")
    main(["--home", str(store.home), "sprint", "show", NAME])
    out = capsys.readouterr().out
    assert NAME in out and "Shown" in out and "1 ticket(s)" in out and "2026-11-09 to 2026-11-22" in out
