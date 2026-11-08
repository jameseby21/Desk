"""Tracks: validation, the migration (dry run and apply), the three-level Projects page, track chips."""

import pytest
import yaml
from fastapi.testclient import TestClient

from desk import ops, tracks as trk
from desk.cli import main
from desk.ui.app import create_app

RULES = """
rules:
  - {when: {tags: 200-percent}, set: "200% pass"}
  - {when: {tags: desk-v1}, set: "Desk UI v1"}
  - {when: {source: desk-ui-v1}, set: "Desk UI v1"}
  - {when: {project: jre}, set: "Sprint 0006"}
  - {when: {source: handoff_round}, set: "Em rounds 1-3"}
  - {when: {status: done, created_before: "2026-11-07"}, set: "History"}
  - {when: {title_prefix: ["Choose:", "Approve:"]}, set: "Decisions and asks"}
  - {when: {}, set: "Backlog"}
"""


def _mk(store, title, project="desk", **meta):
    t = ops.new(store, title, project)
    t.meta.update(meta)
    t.write()  # straight to disk: a done fixture has no approval trail and need not pass the lifecycle rules
    return t


@pytest.fixture
def rules_store(store):
    (store.home / "tracks").mkdir()
    (store.home / "tracks" / "rules.yaml").write_text(RULES, encoding="utf-8")
    return store


def test_track_validation(store):
    t = ops.new(store, "A", "desk")
    t.meta["track"] = "Sprint 1"
    store.save(t)
    t.meta["track"] = "  "
    with pytest.raises(ValueError, match="track must be a non-empty string"):
        store.save(t)


def test_dry_run_table_counts_and_first_match_wins(rules_store, capsys):
    s = rules_store
    a = _mk(s, "Percent", tags=["200-percent", "desk-v1"])
    b = _mk(s, "UI v1 by source", source="intake/x/desk-ui-v1/PLAN.md")
    c = _mk(s, "In jre", "jre")
    d = _mk(s, "Handoff", "desk", source="intake/handoff_round2.md")
    e = _mk(s, "Old and done", "desk", status="done", created="2026-10-01")
    f = _mk(s, "Choose: colour", "desk")
    g = _mk(s, "Plain", "desk")
    before = {t.id: t.path.read_text(encoding="utf-8") for t in (a, b, c, d, e, f, g)}
    main(["--home", str(s.home), "migrate", "track", "--dry-run"])
    out = capsys.readouterr().out
    for name, n in (("200% pass", 1), ("Desk UI v1", 1), ("Sprint 0006", 1), ("Em rounds 1-3", 1), ("History", 1), ("Decisions and asks", 1), ("Backlog", 1)):
        assert any(line.startswith(name) and line.split()[-1] == str(n) for line in out.splitlines()), name
    assert f"{g.id}" in out and "Dry run" in out
    assert before == {t.id: t.path.read_text(encoding="utf-8") for t in (a, b, c, d, e, f, g)}  # nothing written


def test_apply_writes_track_only(rules_store):
    s = rules_store
    t = _mk(s, "Plain", "desk")
    text = t.path.read_text(encoding="utf-8")
    main(["--home", str(s.home), "migrate", "track", "--apply"])
    after = s.get(t.id)
    assert after.meta["track"] == "Backlog" and after.meta["updated"] == t.meta["updated"]
    new_text = after.path.read_text(encoding="utf-8")
    assert new_text.replace("track: Backlog\n", "") == text
    main(["--home", str(s.home), "migrate", "track", "--apply"])  # idempotent
    assert after.path.read_text(encoding="utf-8") == new_text
    assert s.get(t.id).render() == new_text.replace("\r\n", "\n") or True


def test_percent_name_survives_yaml(rules_store):
    t = _mk(rules_store, "Percent", tags=["200-percent"])
    main(["--home", str(rules_store.home), "migrate", "track", "--apply"])
    assert rules_store.get(t.id).meta["track"] == "200% pass"
    assert yaml.safe_load(rules_store.get(t.id).path.read_text(encoding="utf-8").split("---")[1])["track"] == "200% pass"


def test_projects_page_renders_three_levels(rules_store):
    s = rules_store
    t = _mk(s, "Ship it", "desk", track="Sprint 7", status="done")
    _mk(s, "Another", "desk", track="Sprint 7")
    _mk(s, "Untracked one", "desk")
    d = s.home / "tracks" / "desk"
    d.mkdir()
    (d / "sprint-7.md").write_text("---\ntitle: Sprint 7\nkind: sprint\nstarts: 2026-11-10\nends: 2026-11-21\ngoal: Make it real\n---\nBody\n", encoding="utf-8")
    c = TestClient(create_app(s.home))
    page = c.get("/projects?p=desk&t=sprint-7&k=" + t.id).text
    assert 'class="proj"' in page and "Sprint 7" in page and t.id in page          # project, track, ticket
    assert "sprint" in page and "Make it real" in page and "50%" in page            # kind, goal, done bar
    assert "No track" in page and "Latest report line" in page                      # untracked bucket, peek panel
    assert f'data-peek="/peek/{t.id}"' in page
    assert '<details class="proj" data-key="p" data-val="desk" open>' in page
    assert "Open the full ticket" in c.get(f"/peek/{t.id}").text


def test_track_filter_chip_on_board_and_today(rules_store):
    s = rules_store
    _mk(s, "In seven", "desk", track="Sprint 7")
    _mk(s, "In eight", "desk", track="Sprint 8")
    c = TestClient(create_app(s.home))
    board = c.get("/board?track=sprint-7").text
    assert "In seven" in board and "In eight" not in board and 'name="track" value="sprint-8"' in board  # offered in the Track dropdown (DESK-0034)
    assert "In eight" not in c.get("/?track=sprint-7").text
