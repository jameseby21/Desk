"""Tickets as static hand-offs: optional fields, Prompt and Evidence sections, clone, stats."""

import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient

from desk import ops
from desk.cli import main
from desk.rules import validate_store
from desk.ticket import SECTIONS, Ticket
from desk.ui.app import create_app


def test_optional_handoff_fields_validate_when_present(store):
    a = ops.new(store, "First", "jre")
    b = ops.new(store, "Second", "jre", assignee="codex")
    assert store.get(b.id).meta["assignee"] == "codex"
    b.meta.update({"blocks": [a.id], "related": [a.id], "cloned_from": a.id, "done_at": "2026-11-08", "evidence": "commit abc123, 3 tests"})
    store.save(b)
    errors, warnings = validate_store(store)
    assert errors == [] and warnings == []
    text = b.path.read_text(encoding="utf-8")
    assert "assignee: codex" in text and "blocks: [JRE-0001]" in text and "done_at: 2026-11-08" in text

    b.meta.update({"blocks": ["JRE-9999"], "related": ["JRE-8888"], "cloned_from": "JRE-7777", "done_at": "yesterday", "assignee": ["x"]})
    b.write()  # bypass save() to test the validator on the file
    errors, warnings = validate_store(store)
    assert any("blocks `JRE-9999` does not exist" in e for e in errors)
    assert any("done_at must be an ISO date or timestamp" in e for e in errors)
    assert any("assignee must be a string" in e for e in errors)
    assert any("related `JRE-8888` does not exist" in w for w in warnings)
    assert any("cloned_from `JRE-7777` does not exist" in w for w in warnings)


def test_cli_new_accepts_assignee(store, capsys):
    main(["--home", str(store.home), "new", "--project", "jre", "--assignee", "chaz", "Call the school"])
    path = capsys.readouterr().out.strip()
    assert Ticket.load(store.tickets_dir / path.split("tickets")[-1].strip("\\/")).meta["assignee"] == "chaz"


def test_prompt_and_evidence_sections_and_unknown_sections_survive_a_write(store):
    assert SECTIONS.index("Prompt") == SECTIONS.index("Acceptance") + 1
    assert SECTIONS.index("Evidence") == SECTIONS.index("Report") + 1
    t = ops.new(store, "Hand-off", "jre")
    t.set_section("Prompt", "You are in repo X. Run the tests, then fix the failing one.")
    t.set_section("Evidence", "- `uv run pytest` 36 passed\n- commit e125efb")
    store.save(t)
    # a hand-written file: no Prompt heading, plus an unknown section
    text = t.path.read_text(encoding="utf-8").replace("\n## Prompt\nYou are in repo X. Run the tests, then fix the failing one.\n", "")
    text += "\n## Notes from the field\nKeep this.\n"
    t.path.write_text(text, encoding="utf-8")
    again = Ticket.load(t.path)
    assert "Prompt" not in again.sections and again.section("Notes from the field") == "Keep this."
    ops.comment(store, again, "touched")
    final = t.path.read_text(encoding="utf-8")
    assert "## Prompt\n" in final and "## Evidence\n- `uv run pytest` 36 passed" in final
    assert "## Notes from the field\nKeep this.\n" in final  # unknown sections are kept, after the canonical ones
    assert final.index("## Comments") < final.index("## Notes from the field")
    errors, _ = validate_store(store)
    assert errors == []
    page = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    assert "Evidence" in page and "36 passed" in page and "Notes from the field" in page


def test_clone_copies_the_handoff_and_sets_cloned_from(store, capsys):
    src = ops.new(store, "Weekly review", "jre", assignee="james")
    src.meta["tags"] = ["weekly"]
    src.set_section("Context", "Why.")
    src.set_section("Outcome", "What.")
    src.set_section("Acceptance", "- [x] step one\n- [ ] step two")
    src.set_section("Prompt", "Do the thing.")
    src.set_section("Report", "Ran it last week.")
    store.save(src)
    main(["--home", str(store.home), "clone", src.id, "Weekly review, week 2", "--project", "desk"])
    path = capsys.readouterr().out.strip()
    assert path.endswith("DESK-0001-weekly-review-week-2.md")
    c = store.get("DESK-0001")
    assert c.meta["cloned_from"] == src.id and c.project == "desk" and c.status == "inbox"
    assert c.meta["assignee"] == "james" and c.tags == ["weekly"]
    assert c.section("Context") == "Why." and c.section("Prompt") == "Do the thing."
    assert c.section("Acceptance") == "- [ ] step one\n- [ ] step two"  # boxes start unchecked
    assert c.section("Report") == ""
    with pytest.raises(ops.RuleError):
        ops.clone(store, src, "   ")


def test_stats_counts_and_json(store, capsys):
    monday = dt.date.today() - dt.timedelta(days=dt.date.today().weekday())
    a = ops.new(store, "A", "jre", "chore", size="xs")
    ops.new(store, "B", "jre", "slice")
    ops.new(store, "C", "desk", "bug")
    ops.move(store, a, "ready", actor="main")
    ops.claim(store, a, "jre")
    ops.report(store, a, "Done.")
    ops.decide(store, a, True, "Approved: yes.", actor="james")
    ops.done(store, a)
    b = store.get("JRE-0002")
    ops.move(store, b, "ready", actor="main")
    ops.claim(store, b, "jre")
    ops.report(store, b, "Built.")
    old = store.get("DESK-0001")
    old.meta["created"] = "2026-01-05"
    store.save(old, touch=False)

    st = ops.stats(store)
    assert (st["total"], st["done"], st["in_review"], st["open"]) == (3, 1, 1, 2)
    assert st["by_status"] == {"done": 1, "inbox": 1, "review": 1}
    assert st["by_project"] == {"desk": 1, "jre": 2} and st["by_type"] == {"bug": 1, "chore": 1, "slice": 1}
    assert st["created_per_week"] == {"2026-01-05": 1, monday.isoformat(): 2}
    assert st["done_per_week"] == {monday.isoformat(): 1}
    assert ops.stats(store, since="2026-06-01")["total"] == 2
    assert ops.stats(store, project="desk")["by_type"] == {"bug": 1}

    main(["--home", str(store.home), "stats"])
    out = capsys.readouterr().out
    assert "3 total · 1 done · 1 in review (built, not yet proven) · 2 open" in out and "By project" in out
    main(["--home", str(store.home), "stats", "--json", "--since", "2026-06-01"])
    data = json.loads(capsys.readouterr().out)
    assert data["total"] == 2 and data["since"] == "2026-06-01"
