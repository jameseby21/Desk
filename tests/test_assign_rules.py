"""DESK-0168: auto-assignment rules. Protects: a workflow maps to a person (decision to james, security to bigd ...), two disagreeing rules
leave the assignee blank and warn, a rule never overrides an assignee someone set, and the migration fills blanks only."""

import pytest
from fastapi.testclient import TestClient

from desk import assign_rules as ar, ops, people
from desk.cli import main
from desk.ui.app import create_app


def _mk(store, title, project="jre", **meta):
    t = ops.new(store, title, project)
    t.meta.update(meta)
    t.write()
    return store.get(t.id)


def _decide(store, t):
    rules, _ = ar.load(store.home)
    return ar.decide(t, rules, people.load(store.home))


@pytest.mark.parametrize("meta, project, who, rule", [
    ({"type": "decision"}, "jre", "james", "human review"),
    ({}, "brand", "chaz", "brand"),
    ({"tags": ["design"]}, "jre", "chaz", "design tag"),
    ({}, "security", "bigd", "security project"),
    ({"tags": ["security"], "type": "task"}, "jre", "bigd", "security tag"),
    ({"type": "research"}, "jre", "em", "research"),
    ({"tags": ["infrastructure"], "type": "task"}, "jre", "lily", "tech and product"),
    ({"tags": ["calendar"], "type": "task"}, "jre", "bri", "admin"),
    ({"tags": ["legal"], "type": "task"}, "jre", "joe", "legal and finance"),
    ({"type": "feature"}, "jre", "main", "build"),
    ({"type": "bug"}, "desk", "main", "build"),
    ({"type": "feature", "owner": "jre"}, "jre", "code:jre", "project session"),
])
def test_each_mapping(store, meta, project, who, rule):
    if project not in store.projects():
        (store.projects_dir / f"{project}.md").write_text(f"# {project}\n", encoding="utf-8")
    out = _decide(store, _mk(store, "Some work", project, **meta))
    assert (out.state, out.assignee, out.rule) == ("filled", who, rule)


def test_title_words_human_review_and_design_review(store):
    assert _decide(store, _mk(store, "Human review of the deck", type="task")).assignee == "james"
    assert _decide(store, _mk(store, "Design review of the board", type="task")).assignee == "chaz"
    assert _decide(store, _mk(store, "Redesigned thing", type="task")).state == "none"  # whole words only


def test_two_rules_that_disagree_leave_it_blank_and_warn(store):
    t = _mk(store, "Security research", type="research", tags=["security"])
    out = _decide(store, t)
    assert out.state == "conflict" and "bigd" in out.warning and "em" in out.warning


def test_two_rules_that_agree_on_one_person_are_one_answer(store):
    t = _mk(store, "Human review: approve", type="decision")
    out = _decide(store, t)
    assert len(out.matches) == 2 and out.assignee == "james"  # the type rule and the title rule, one person


def test_no_rule_matches_leaves_it_alone(store):
    assert _decide(store, _mk(store, "Odd", type="task")).state == "none"


def test_drew_new_fills_and_warns_and_never_overrides(store, capsys):
    (store.projects_dir / "security.md").write_text("# Security\n", encoding="utf-8")
    main(["--home", str(store.home), "new", "Rotate things", "--project", "security", "--type", "task"])
    err = capsys.readouterr().err
    assert "assignee bigd (rule security project)" in err
    t = [x for x in store.tickets() if x.meta["title"] == "Rotate things"][0]
    assert t.meta["assignee"] == "bigd" and t.meta["assigned_by"] == "rule:security project" and t.meta["assigned_at"]
    main(["--home", str(store.home), "new", "Decide a thing", "--project", "jre", "--type", "decision"])
    assert [x for x in store.tickets() if x.meta["title"] == "Decide a thing"][0].meta["assignee"] == "james"
    main(["--home", str(store.home), "new", "Chaz decides", "--project", "jre", "--type", "decision", "--assignee", "chaz"])
    assert [x for x in store.tickets() if x.meta["title"] == "Chaz decides"][0].meta["assignee"] == "chaz"  # given: never overridden
    capsys.readouterr()
    main(["--home", str(store.home), "new", "Both ways", "--project", "security", "--type", "research"])
    both = [x for x in store.tickets() if x.meta["title"] == "Both ways"][0]
    assert "assignee" not in both.meta and "two rules disagree" in capsys.readouterr().err


def test_a_store_assign_yaml_replaces_the_built_in_table(store):
    (store.home / "assign.yaml").write_text("rules:\n  - name: everything to em\n    assign: Em\n    when: {}\n", encoding="utf-8")
    rules, source = ar.load(store.home)
    assert [r.name for r in rules] == ["everything to em"] and source.endswith("assign.yaml")
    assert _decide(store, _mk(store, "Anything")).assignee == "em"  # a display name resolves to the key
    (store.home / "assign.yaml").write_text("rules:\n  - name: bad\n    when: {colour: red}\n    assign: em\n", encoding="utf-8")
    with pytest.raises(ar.RulesError):
        ar.load(store.home)


def test_seed_round_trips(store):
    main(["--home", str(store.home), "assign-rules", "init"])
    rules, source = ar.load(store.home)
    assert source.endswith("assign.yaml") and [(r.name, r.assign, r.fallback, r.when) for r in rules] == [
        (r["name"], r["assign"], bool(r.get("fallback")), r["when"]) for r in ar.DEFAULT_RULES]


def test_migrate_dry_run_then_apply_fills_blanks_only_and_keeps_updated(store, capsys):
    blank = _mk(store, "Blank decision", type="decision", updated="2026-10-01")
    set_ = _mk(store, "Chaz has it", type="decision", assignee="chaz", updated="2026-10-01")
    done = _mk(store, "Old decision", type="decision", status="done", updated="2026-10-01")
    unmatched = _mk(store, "Odd", type="task", updated="2026-10-01")
    before = {t.id: t.path.read_text(encoding="utf-8") for t in (blank, set_, done, unmatched)}
    main(["--home", str(store.home), "migrate", "assignee", "--dry-run"])
    out = capsys.readouterr().out
    assert blank.id in out and "human review" in out and set_.id not in out and done.id not in out and "Dry run: nothing written" in out
    assert {t.id: t.path.read_text(encoding="utf-8") for t in (blank, set_, done, unmatched)} == before
    main(["--home", str(store.home), "migrate", "assignee", "--apply"])
    assert "Filled 1 ticket(s)" in capsys.readouterr().out
    got = store.get(blank.id)
    assert got.meta["assignee"] == "james" and got.meta["assigned_by"] == "rule:human review" and str(got.meta["updated"]) == "2026-10-01"
    assert store.get(set_.id).meta["assignee"] == "chaz" and store.get(set_.id).path.read_text(encoding="utf-8") == before[set_.id]
    assert "assignee" not in store.get(unmatched.id).meta and store.get(done.id).path.read_text(encoding="utf-8") == before[done.id]
    main(["--home", str(store.home), "migrate", "assignee", "--apply"])
    assert "Filled 0 ticket(s)" in capsys.readouterr().out  # twice writes nothing the second time


def test_migrate_replaces_an_empty_assignee_line(store, capsys):
    t = _mk(store, "Empty line", type="decision", assignee="")
    assert "assignee:" in t.path.read_text(encoding="utf-8")
    main(["--home", str(store.home), "migrate", "assignee", "--apply"])
    text = store.get(t.id).path.read_text(encoding="utf-8")
    assert text.count("assignee:") == 1 and store.get(t.id).meta["assignee"] == "james"


def test_ticket_page_says_assigned_by_rule_until_a_person_changes_it(store):
    t = _mk(store, "Rule filled", type="decision")
    ar.apply_new(store, store.get(t.id))
    c = TestClient(create_app(store.home))
    assert "assigned by rule <b>human review</b> until a person changes it" in c.get(f"/ticket/{t.id}").text
    ops.assign(store, store.get(t.id), "chaz", actor="james")
    page = c.get(f"/ticket/{t.id}").text
    assert "assigned by rule" not in page and "assigned by" in page and store.get(t.id).meta["assigned_by"] == "james"
