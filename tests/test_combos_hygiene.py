"""Combos (two tagged tickets done within 30 minutes) and validate's hygiene warnings."""

import datetime as dt

from fastapi.testclient import TestClient

from desk import ops
from desk.cli import main
from desk.rules import validate_store
from desk.store import Store
from desk.ticket import Ticket
from desk.ui.app import combos_for, create_app


def finish(store, title, tags=None, done_at=None):
    t = ops.new(store, title, "jre", "chore", size="xs")
    if tags is not None:
        t.meta["tags"] = tags
    store.save(t)
    ops.move(store, t, "ready", actor="main")
    ops.claim(store, t, "jre")
    ops.report(store, t, "Done it.")
    ops.decide(store, t, True, "Approved: fine.", actor="james")
    ops.done(store, t)
    t = store.get(t.id)
    if done_at:
        t.meta["done_at"] = done_at
        store.save(t, touch=False)
        t = store.get(t.id)
    return t


def test_done_writes_done_at_and_tags_round_trip(store):
    t = finish(store, "Walk the dog", tags=["life", "dog"])
    assert t.status == "done" and t.tags == ["life", "dog"]
    when = dt.datetime.fromisoformat(str(t.meta["done_at"]))
    assert when.tzinfo is None and abs(dt.datetime.now() - when) < dt.timedelta(minutes=5)
    text = t.path.read_text(encoding="utf-8")
    assert "done_at: 20" in text and "'" not in text.split("done_at:")[1].splitlines()[0]
    assert Ticket.load(t.path).meta["done_at"] == t.meta["done_at"]  # the time survives a reload


def test_tags_must_be_a_list_of_strings(store):
    t = ops.new(store, "Tagged", "jre")
    t.path.write_text(t.path.read_text(encoding="utf-8").replace("source: ''", "source: ''\ntags: life"), encoding="utf-8")
    errors, _ = validate_store(store)
    assert any("tags must be a list of strings" in e for e in errors)


def test_combos_pair_tagged_tickets_done_within_30_minutes(store):
    # Noon today, not the wall clock: a combo is two tickets done on the same day, so a run in the first 20 minutes after midnight put
    # "Laundry" on yesterday and the pair vanished (checker, 2026-11-19). Noon leaves 12 hours either side for every offset below
    # except "Yesterday", which is a whole day earlier on purpose.
    now = dt.datetime.combine(dt.date.today(), dt.time(12, 0))
    a = finish(store, "Dishes", tags=["home"], done_at=now.isoformat())
    b = finish(store, "Laundry", tags=["home"], done_at=(now - dt.timedelta(minutes=20)).isoformat())
    finish(store, "Taxes", tags=["money"], done_at=(now - dt.timedelta(hours=3)).isoformat())
    finish(store, "Untagged", tags=None, done_at=now.isoformat())
    finish(store, "Yesterday", tags=["home"], done_at=(now - dt.timedelta(days=1)).isoformat())
    pairs = combos_for(store.tickets())
    assert [(x.id, y.id) for x, y in pairs] == [(b.id, a.id)]

    client = TestClient(create_app(store.home))
    assert "Combos" not in client.get("/").text  # off by default
    cfg = store.home / "desk.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace("combos = false", "combos = true"), encoding="utf-8")
    assert Store(store.home).combos is True
    page = client.get("/").text
    assert '"Laundry" and "Dishes" completed together, +100 for efficiency!' in page
    assert "Taxes" not in page.split("Combos")[1].split("</div>")[0]


def _tagged_pair(store, first, second):
    a = finish(store, "Dishes", tags=["home"], done_at=first.isoformat())
    b = finish(store, "Laundry", tags=["home"], done_at=second.isoformat())
    return a, b


def test_combos_unit_the_day_is_the_day_asked_for_whatever_the_clock_says(store):
    """Method 1, the function with an explicit day: a pair 20 minutes apart straddling midnight is a combo on neither day, and the
    pair at 05:00 and 05:20 is a combo on that day. This is why the first test must not depend on the wall clock."""
    day = dt.date(2026, 11, 10)
    a, b = _tagged_pair(store, dt.datetime(2026, 11, 9, 23, 50), dt.datetime(2026, 11, 10, 0, 10))
    assert combos_for(store.tickets(), on=day) == [] and combos_for(store.tickets(), on=dt.date(2026, 11, 9)) == []
    c, d = _tagged_pair(store, dt.datetime(2026, 11, 10, 5, 0), dt.datetime(2026, 11, 10, 5, 20))
    assert [(x.id, y.id) for x, y in combos_for(store.tickets(), on=day)] == [(c.id, d.id)]


def test_combos_route_a_pair_done_just_after_midnight_still_shows_on_that_day(store):
    """Method 2, the page: on today's date a pair at 00:05 and 00:25 appears on Today with the combo sentence once combos are on."""
    midnight = dt.datetime.combine(dt.date.today(), dt.time(0, 5))
    _tagged_pair(store, midnight, midnight + dt.timedelta(minutes=20))
    cfg = store.home / "desk.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace("combos = false", "combos = true"), encoding="utf-8")
    assert "completed together" in TestClient(create_app(store.home)).get("/").text


def test_combos_edge_thirty_minutes_exactly_pairs_and_thirty_one_does_not(store):
    """Edge: the window is 30 minutes, closed at the end."""
    noon = dt.datetime.combine(dt.date.today(), dt.time(12, 0))
    a, b = _tagged_pair(store, noon, noon + dt.timedelta(minutes=30))
    assert [(x.id, y.id) for x, y in combos_for(store.tickets())] == [(a.id, b.id)]
    finish(store, "Late", tags=["home"], done_at=(noon + dt.timedelta(minutes=61)).isoformat())  # 31 minutes after the second
    assert [(x.id, y.id) for x, y in combos_for(store.tickets())] == [(a.id, b.id)]


def test_validate_warns_on_long_titles_and_old_reviews(store, capsys):
    long_title = "A" * 71
    ops.new(store, long_title, "jre")
    t = ops.new(store, "Short", "jre")
    ops.move(store, t, "ready", actor="main")
    ops.claim(store, t, "jre")
    ops.report(store, t, "Proof.")
    t = store.get(t.id)
    t.meta["updated"] = (dt.date.today() - dt.timedelta(days=8)).isoformat()
    store.save(t, touch=False)
    errors, warnings = validate_store(store)
    assert errors == []
    assert any("title is 71 characters" in w for w in warnings)
    assert any(f"{t.id}: in review for 8 days" in w for w in warnings)
    try:
        main(["--home", str(store.home), "validate"])
    except SystemExit as e:
        assert e.code == 0
    out = capsys.readouterr().out
    assert "warning:" in out and "0 error(s), 9 warning(s)" in out  # 4 ticket warnings (long title, old review, no Tests text and GPS on the review ticket) + 3 projects without a brand block + 2 tickets with no assignee (DESK-0167)
