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
    now = dt.datetime.now().replace(microsecond=0)
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
    assert "warning:" in out and "0 error(s), 5 warning(s)" in out  # 2 ticket warnings + 3 projects without a brand block
