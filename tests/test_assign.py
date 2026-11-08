"""Assignment as a first-class field: assign, list --assignee, UI filter chips, review grouping, stats."""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from desk import ops, review as rv
from desk.cli import main
from desk.ui.app import create_app


def run(store, *argv):
    main(["--home", str(store.home), *argv])


def test_assign_sets_fields_and_logs_a_comment(store, capsys):
    t = ops.new(store, "Hand me over", "jre")
    run(store, "assign", t.id, "--to", "chaz", "--note", "logo first", "--handoff", "https://docs.google.com/document/d/abc", "--as", "james")
    out = capsys.readouterr().out
    assert f"{t.id} assigned to chaz" in out and "assignee named and notified; the hand-off record linked" in out
    got = store.get(t.id)
    assert got.meta["assignee"] == "chaz" and got.meta["handoff"] == "https://docs.google.com/document/d/abc"
    assert dt.datetime.fromisoformat(str(got.meta["assigned_at"])).date() == dt.date.today() and "T" in str(got.meta["assigned_at"])
    assert f"- {dt.date.today().isoformat()} · james · assigned to chaz: logo first" in got.section("Comments")
    text = got.render()
    assert text.index("assignee:") < text.index("assigned_at:") < text.index("handoff:")
    run(store, "assign", t.id, "--to", "codex")  # reassign without a note or a new hand-off
    got = store.get(t.id)
    assert got.meta["assignee"] == "codex" and got.meta["handoff"].startswith("https://") and "assigned to codex" in got.section("Comments")
    with pytest.raises(SystemExit):
        run(store, "assign", t.id, "--to", "  ")
    assert "assign needs a name" in capsys.readouterr().err


def test_list_filter_by_assignee(store, capsys):
    a = ops.new(store, "Alpha", "jre")
    b = ops.new(store, "Beta", "jre")
    ops.assign(store, a, "chaz")
    ops.assign(store, b, "codex")
    capsys.readouterr()
    run(store, "list", "--assignee", "chaz")
    out = capsys.readouterr().out
    assert a.id in out and b.id not in out and "assignee" in out
    run(store, "list", "--assignee", "nobody")
    assert "No tickets match." in capsys.readouterr().out


def test_board_and_projects_filter_chips(store):
    a = ops.new(store, "Alpha thing", "jre")
    b = ops.new(store, "Beta thing", "jre")
    ops.assign(store, a, "chaz")
    ops.assign(store, b, "codex")
    c = TestClient(create_app(store.home))
    page = c.get("/board?open=*&g=*").text  # the board opens at projects (DESK-0084); `*` opens everything
    assert "assignee=chaz" in page and "assignee=codex" in page and "Alpha thing" in page and "Beta thing" in page
    page = c.get("/board?assignee=chaz").text
    assert "Alpha thing" in page and "Beta thing" not in page
    page = c.get("/projects?assignee=codex").text
    assert "Beta thing" in page or "1 open" in page
    assert "assignee=chaz" in page
    only = c.get("/projects?assignee=codex&p=jre").text
    assert "Alpha thing" not in only
    shown = c.get(f"/ticket/{a.id}").text
    assert "no hand-off record linked" in shown and "assignee named and notified" in shown


def test_review_sync_groups_review_lines_by_assignee(store):
    (store.projects_dir / "management.md").write_text("# Management\n", encoding="utf-8")
    mine = []
    for title, who in (("Built one", "chaz"), ("Built two", "codex"), ("Built three", "chaz"), ("Built four", "")):
        t = ops.new(store, title, "desk", "chore", owner="main")
        t.meta.update(status="review")
        t.set_section("Report", "Built it.")
        if who:
            t.meta["assignee"] = who
        store.save(t)
        mine.append(t)
    rt, _ = rv.create(store)
    acc = store.get(rt.id).section("Acceptance")
    assert "**Review · chaz**" in acc and "**Review · codex**" in acc and "**Review · unassigned**" in acc
    chaz = acc.split("**Review · chaz**")[1].split("**Review")[0]
    assert mine[0].id in chaz and mine[2].id in chaz and mine[1].id not in chaz
    assert rv.waiting_count(store.get(rt.id)) == 4  # sub-headed groups still parse as Review lines
    rv.apply(store, store.get(rt.id))  # a second sync keeps the grouping and loses nothing
    assert store.get(rt.id).section("Acceptance").count("- [ ] ") == 4


def test_stats_per_assignee_table(store, capsys):
    a = ops.new(store, "Open one", "jre")
    b = ops.new(store, "Open two", "jre")
    c = ops.new(store, "Finished", "jre")
    ops.assign(store, a, "chaz")
    ops.assign(store, b, "chaz")
    ops.assign(store, c, "codex")
    c = store.get(c.id)
    c.meta.update(status="done", done_at=dt.datetime.now().replace(microsecond=0).isoformat())
    c.write()
    st = ops.stats(store)
    assert st["by_assignee"]["chaz"] == {"open": 2, "done_this_week": 0}
    assert st["by_assignee"]["codex"] == {"open": 0, "done_this_week": 1}
    capsys.readouterr()
    run(store, "stats")
    out = capsys.readouterr().out
    assert "By assignee" in out and "done this week" in out and "chaz" in out
