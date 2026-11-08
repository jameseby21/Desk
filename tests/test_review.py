"""The weekly Ticket review: generated checkboxes, sync, KPI, stats."""

import datetime as dt

import pytest

from desk import ops, review as rv
from desk.cli import main


@pytest.fixture
def rstore(store):
    (store.projects_dir / "management.md").write_text("# Management\n", encoding="utf-8")
    return store


def mk(store, title, status="ready", type_="chore", owner="james", **meta):
    t = ops.new(store, title, "desk", type_, owner=owner)
    t.meta.update(status=status, **meta)
    if status == "review":
        t.set_section("Report", "Built it.")
    store.save(t)
    return t


def run(store, *argv):
    main(["--home", str(store.home), *argv])


def test_new_groups_and_refuses_second(rstore, capsys):
    a = mk(rstore, "Choose: public code, private tickets?", type_="decision", review_by="2026-11-10")
    b = mk(rstore, "Approve the logo", type_="decision")
    c = mk(rstore, "Some built thing", status="review", owner="main")
    mk(rstore, "Ordinary ready work", owner="main")
    run(rstore, "review", "new")
    path = capsys.readouterr().out.strip()
    assert path.endswith(".md")
    rt = next(t for t in rstore.tickets() if rv.is_review_ticket(t))
    m = rv.monday_of(dt.date.today())
    assert rt.meta["title"] == rv.title_for(m) and rt.meta["title"].startswith("Ticket review: week of ")
    assert (rt.meta["type"], rt.status, rt.meta["priority"], rt.meta["size"], rt.meta["owner"], rt.meta["assignee"]) == ("chore", "ready", "p1", "s", "james", "james")
    assert rt.meta["review_by"] == (m + dt.timedelta(days=4)).isoformat()
    acc = rt.section("Acceptance")
    assert "**Choose**" in acc and "**Approve**" in acc and "**Review**" in acc
    assert f"- [ ] {a.id} · Choose: public code, private tickets? · by Nov 10" in acc
    assert f"- [ ] {b.id} · Approve the logo" in acc and f"- [ ] {c.id} · Some built thing" in acc
    assert "Ordinary ready" not in acc
    with pytest.raises(SystemExit):
        run(rstore, "review", "new")
    assert rt.id in capsys.readouterr().err


def test_new_needs_management_profile(store, capsys):
    with pytest.raises(SystemExit):
        run(store, "review", "new")
    assert "management" in capsys.readouterr().err


def test_sync_checks_adds_keeps_and_kpi(rstore, capsys):
    a = mk(rstore, "Choose: one thing", type_="decision")
    b = mk(rstore, "Built thing", status="review", owner="main")
    run(rstore, "review", "new")
    rt = next(t for t in rstore.tickets() if rv.is_review_ticket(t))
    rt.set_section("Acceptance", "- [ ] hand-written line\n\n" + rt.section("Acceptance"))
    rstore.save(rt)
    # a decided, a new arrival, b vanishes
    a = rstore.get(a.id)
    a.append_line("Decision", f"- {dt.date.today().isoformat()} · james · approved · \"yes please\"")
    a.meta["status"] = "approved"
    rstore.save(a)
    n = mk(rstore, "Provide the invoice", type_="decision")
    capsys.readouterr()
    run(rstore, "review", "sync")
    out = capsys.readouterr().out
    assert "added" in out
    rt = rstore.get(rt.id)
    acc = rt.section("Acceptance")
    assert "- [ ] hand-written line" in acc
    assert f"- [x] {a.id} · Choose: one thing" in acc and "(resolved)" in acc
    assert f"- [ ] {b.id} · Built thing" in acc and f"- [ ] {n.id} · Provide the invoice" in acc
    rep = rt.section("Report")
    assert "<!-- kpi:start -->" in rep
    assert "Waiting at creation: 2" in rep and "Added during the week: 1" in rep
    assert "Decided this week: 1" in rep and "Still waiting: 2" in rep
    assert "Median days in review: 0" in rep and f"Oldest waiting: {b.id}, 0 days" in rep
    before = rt.render()
    run(rstore, "review", "sync")
    assert rstore.get(rt.id).render() == before  # idempotent


def test_stats_decisions_table(rstore, capsys):
    a = mk(rstore, "Approve x", type_="decision")
    a.append_line("Decision", f"- {dt.date.today().isoformat()} · james · approved · \"ok\"")
    a.meta["status"] = "approved"
    rstore.save(a)
    run(rstore, "review", "new")
    capsys.readouterr()
    run(rstore, "stats")
    out = capsys.readouterr().out
    assert "Decisions per week" in out and rv.monday_of(dt.date.today()).isoformat() in out
    assert "Review weeks: 1 created, 0 closed" in out
    assert len(rv.decisions_per_week(rstore)) == 8


def test_today_page_links_review(rstore):
    from fastapi.testclient import TestClient
    from desk.ui.app import create_app
    mk(rstore, "Built thing", status="review", owner="main")
    c = TestClient(create_app(rstore.home))
    assert "waiting" not in c.get("/").text.split("Ticket review")[0] or "Ticket review" not in c.get("/").text
    run(rstore, "review", "new")
    text = c.get("/").text
    assert "Ticket review: week of" in text and "1 waiting" in text
