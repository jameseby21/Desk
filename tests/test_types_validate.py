"""Meaningful ticket types, `drew migrate type`, and the How to validate section."""

import pytest

from desk import ops, retype
from desk.cli import main
from desk.rules import RuleError, validate_store
from desk.ticket import TYPES, Ticket


def _put(store, title, type_, **meta):
    t = ops.new(store, title, "desk", "feature")
    t.meta.update(type=type_, **meta)
    t.write()
    return t


def test_new_types_and_legacy_alias(store, capsys):
    assert TYPES == ["feature", "upgrade", "bug", "task", "docs", "decision", "research", "intake", "release"]
    t = _put(store, "Old", "slice")
    errors, warnings = validate_store(store)
    assert not errors and any("legacy type `slice`; run drew migrate type" in w for w in warnings)
    with pytest.raises(SystemExit):
        main(["--home", str(store.home), "new", "--project", "desk", "--type", "slice", "x"])
    assert t.id


@pytest.mark.parametrize("title,type_,tags,want", [
    ("Approve: the plan", "slice", [], "decision"), ("Direction: pick one", "chore", [], "decision"),
    ("Docs: write it", "slice", [], "docs"), ("Anything", "slice", ["quickstart"], "docs"),
    ("Release 0.3", "slice", [], "release"), ("Ship", "chore", ["release"], "release"),
    ("Do: call the bank", "slice", [], "task"), ("Tidy", "chore", [], "task"),
    ("200% look pass", "slice", [], "upgrade"), ("Brand: marks", "slice", [], "upgrade"), ("Plain", "slice", ["upgrade"], "upgrade"),
    ("Fix Bug in login", "slice", [], "bug"), ("Build a thing", "slice", [], "feature"),
    ("Choose: x", "bug", [], "bug"), ("Choose: x", "research", [], "research"),
])
def test_classify_rules(store, title, type_, tags, want):
    t = Ticket(meta={"title": title, "type": type_, "tags": tags})
    assert retype.classify(t) == want


def test_migrate_type_dry_run_then_apply(store, capsys):
    a = _put(store, "Build it", "slice")
    b = _put(store, "Do: file it", "chore")
    c = _put(store, "Already", "feature")
    before = a.path.read_text(encoding="utf-8")
    main(["--home", str(store.home), "migrate", "type", "--dry-run"])
    out = capsys.readouterr().out
    assert "slice -> feature" in out and "chore -> task" in out and "Dry run" in out
    assert a.path.read_text(encoding="utf-8") == before
    main(["--home", str(store.home), "migrate", "type", "--apply"])
    assert store.get(a.id).meta["type"] == "feature" and store.get(b.id).meta["type"] == "task" and store.get(c.id).meta["type"] == "feature"
    assert store.get(a.id).path.read_text(encoding="utf-8").replace("type: feature", "type: slice") == before


def test_new_writes_heading_with_hint(store, monkeypatch):
    import desk.ticket
    monkeypatch.setattr("desk.store.new_ticket", desk.ticket.new_ticket)  # the real template, not the test fixture's steps
    t = ops.new(store, "Bug thing", "desk", "bug")
    text = t.path.read_text(encoding="utf-8")
    assert "## How to validate\n<!-- Write this before the work starts. Reproduce the bug first" in text
    assert text.index("## Acceptance") < text.index("## How to validate") < text.index("## Prompt")


def _ready(store, t):
    ops.move(store, t, "ready", actor="main")
    ops.claim(store, t, "desk")
    return store.get(t.id)


def test_report_refuses_without_validate_steps(store):
    t = ops.new(store, "Work", "desk")
    t.sections["How to validate"] = "<!-- only the hint -->\n"
    store.save(t)
    t = _ready(store, store.get(t.id))
    with pytest.raises(RuleError, match="Write How to validate first"):
        ops.report(store, t, "Did it.")
    t.set_section("How to validate", "1. Run `drew list` and see the ticket.")
    store.save(t)
    assert ops.report(store, store.get(t.id), "Did it.").status == "review"


def test_validate_warns_on_legacy_review_ticket_and_review_week_line(store):
    t = ops.new(store, "Legacy", "desk")
    t.meta.update(status="review")
    t.set_section("Report", "Ran it.")
    t.sections["How to validate"] = ""
    t.write()
    errors, warnings = validate_store(store)
    assert not errors and any("no How to validate" in w for w in warnings)


def test_review_week_line_appends_validate(store):
    from desk import review as rv

    t = ops.new(store, "Choose: colour", "desk", "decision", owner="james")
    t.set_section("How to validate", "Find the line in Decision.\nsecond")
    store.save(t)
    assert rv._rest(store.get(t.id)).endswith(" · validate: Find the line in Decision.")
