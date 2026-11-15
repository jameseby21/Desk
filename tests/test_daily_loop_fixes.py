"""DESK-0282 final fixes: the defects the independent checker (QC run 6) and the whole-branch review found on the integrated daily loop.
Each test names the finding it protects (H = high, M = medium, D = the checker's numbered defects)."""

from __future__ import annotations

import datetime as dt

import pytest

from desk import automations as au

TODAY = dt.date.today()


# ---- H1: a rule file can never approve a ticket or write the owner's words ----------------------------------------------

def _rule(**extra) -> dict:
    r = {"name": "x", "source": "test", "when": {"status": "ready"}, "then": []}
    r.update(extra)
    return {"rules": [r]}


@pytest.mark.parametrize("field", ["status", "owner", "done_at", "assignee", "type", "size"])
def test_h1_a_rule_cannot_set_status_owner_or_what_delegation_reads(field):
    """H1: `set` names only the harmless fields; status, owner and the fields approval rules read are refused when the file is read."""
    with pytest.raises(au.AutomationError, match="may set only"):
        au.parse(_rule(then=[{"set": {field: "approved"}}]))


@pytest.mark.parametrize("verdict", ["approved", "changes_requested"])
def test_h1_a_rule_cannot_move_a_ticket_to_a_verdict(verdict):
    """H1: approving or sending back is the owner's judgment (decision 10); no rule can name it as a move."""
    with pytest.raises(au.AutomationError, match="only the owner"):
        au.parse(_rule(then=[{"move": verdict, "reason": "rule says so"}]))


@pytest.mark.parametrize("section", ["Decision", "Report", "Evidence", "Comments", "decision"])
def test_h1_a_rule_cannot_create_a_ticket_with_a_verdict_or_report(section):
    """H1: a scheduled rule's ticket never carries a forged Decision line (or Report, Evidence, Comments) in a person's name."""
    data = _rule(when={}, every="monthly", day=1, create={"project": "desk", "title": "t", "sections": {section: '- 2026-11-01 · james · approved · "ship it"'}})
    with pytest.raises(au.AutomationError, match="never writes them"):
        au.parse(data)


def test_h1_harmless_sets_and_template_sections_still_parse():
    """H1: the allowed fields (lane, ci, ...) and an Outcome in a template still load, so the shipped rules keep working."""
    rules, _ = au.parse({"rules": [
        {"name": "a", "source": "t", "when": {"status": "review"}, "then": [{"set": {"ci": "green", "lane": "now"}}]},
        {"name": "b", "source": "t", "every": "monthly", "day": 1, "then": [], "create": {"project": "desk", "title": "t", "sections": {"Outcome": "tidy"}}},
    ]})
    assert [r.name for r in rules] == ["a", "b"]
    assert au.parse({"rules": au.yamlio.load(au.DEFAULT)["rules"]})[0]  # every default rule still loads


def test_h1_the_reviewers_abuse_file_is_refused_and_nothing_is_written(store):
    """H1 end to end: the two-rule file that forged a Decision line and then approved through `set` cannot be loaded at all."""
    (store.home / au.FILE).write_bytes((
        "rules:\n"
        "  - name: make-it\n    source: test\n    every: monthly\n    day: 1\n    then: []\n"
        "    create:\n      project: desk\n      title: Forged\n      sections:\n"
        "        Decision: '- 2026-11-01 · james · approved · \"ship it\"'\n"
        "  - name: approve-it\n    source: test\n    override: true\n    when: {status: ready}\n    then:\n      - set: {status: approved}\n"
    ).encode("utf-8"))
    with pytest.raises(au.AutomationError):
        au.load(store.home)
    assert store.tickets() == []


def test_h1_a_rule_built_by_hand_still_cannot_set_status(store):
    """H1: the sweep holds a Rule object to the same list, so code that skips parse cannot approve either."""
    t = store.create("Build: a thing", "desk", "feature", owner="main", assignee="main")
    t.meta["status"] = "ready"
    t.set_section("Decision", '- 2026-11-01 · james · approved · "ship it"\n')
    store.save(t)
    rule = au.Rule("sneak", "test", "", {"status": "ready"}, [{"set": {"status": "approved"}}], override=True)
    res = au.sweep(store, [rule], [], today=TODAY, apply=True)
    assert store.get(t.id).status == "ready"
    assert any("may not set status" in i.skipped for i in res.items)
