"""DESK-0292: the automation rules engine. Each shipped rule on a fixture store; dry-run changes nothing; events are by automation
(kind rule) in one batch; hand-set values are kept; undo restores; nobody writes as automation."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from desk import activity, automations as au, identity, notify_queue, ops
from desk.cli import main

TODAY = dt.date.today()


@pytest.fixture
def home(store):
    (store.projects_dir / "management.md").write_text("# Management\n", encoding="utf-8")
    au.init(store.home)
    return store


@pytest.fixture
def no_gh(monkeypatch):
    monkeypatch.setattr(au, "gh_run", lambda args, cwd=None: (127, "", "gh is not installed"))


def gh_with(merged=(), open_=()):
    def run(args, cwd=None):
        state = args[args.index("--state") + 1]
        return 0, json.dumps(list(merged if state == "merged" else open_)), ""
    return run


def make(store, title="Build: a thing worth doing", status="ready", save=True, **meta):
    t = store.create(title, "desk", "feature", owner="main", assignee="main")
    t.meta["status"] = status
    t.meta.update(meta)
    if status in ("review", "in_progress", "approved", "done"):
        t.set_section("Report", "Built it; tests pass.\n")
    if status in ("approved", "done"):
        t.set_section("Decision", "- 2026-11-14 · james · approved · \"looks right\"\n")
    if save:
        store.save(t)
    return store.get(t.id)


def rules(store, *names):
    rs, repos = au.load(store.home)
    return [r for r in rs if not names or r.name in names], repos


def run(store, *names, today=TODAY, apply=True):
    rs, repos = rules(store, *names)
    return au.sweep(store, rs, repos, today=today, apply=apply)


def snapshot(store):
    files = {p.name: p.read_bytes() for p in store.tickets_dir.glob("*.md")}
    events = {p.name: p.read_bytes() for p in (store.home / "events").glob("*.jsonl")} if (store.home / "events").exists() else {}
    return files, events, notify_queue.pending(store.home)


# ---- the rules file ----------------------------------------------------------------------------

def test_init_writes_rules_each_with_a_source_and_refuses_to_overwrite(home):
    """Every shipped rule cites where it comes from; a second init never replaces James's edits."""
    rs, _ = au.load(home.home)
    assert len(rs) >= 10 and all(r.source and r.does for r in rs)
    with pytest.raises(au.AutomationError, match="already exists"):
        au.init(home.home)


def test_a_rule_that_names_who_writes_is_refused():
    """A rule can never write as a person: `as:` (or actor, by) in a rule makes the file unusable."""
    data = {"rules": [{"name": "x", "source": "test", "as": "james", "when": {}, "then": []}]}
    with pytest.raises(au.AutomationError, match="never as a person"):
        au.parse(data)


def test_a_rule_without_a_source_is_refused():
    """Each rule cites its source decision or rule."""
    with pytest.raises(au.AutomationError, match="needs a source"):
        au.parse({"rules": [{"name": "x", "when": {}, "then": []}]})


def test_nobody_can_claim_the_automation_name_on_the_command_line(home):
    """`--as automation` is refused, so no person's change is ever shown as a rule's (decision 110)."""
    with pytest.raises(SystemExit, match="automation runner"):
        identity.resolve_cli(home, "automation", env={})


# ---- dry run -----------------------------------------------------------------------------------

def test_dry_run_changes_nothing_and_says_what_and_why(home, no_gh):
    """Dry run reports each rule's would-be changes and writes no ticket, event or notification."""
    parent = make(home, status="in_progress")
    kid =make(home, "Task: the piece of the parent", status="ready", parent=parent.id)
    kid.meta["status"] = "rejected"
    kid.set_section("Decision", "- 2026-11-14 · james · rejected · \"not needed\"\n")
    home.save(kid)
    done_kid = make(home, "Task: the done piece", status="ready", parent=parent.id)
    done_kid.meta["status"] = "done"
    done_kid.set_section("Report", "Done.\n")
    done_kid.set_section("Decision", "- 2026-11-14 · james · approved · \"ok\"\n")
    home.save(done_kid)
    before = snapshot(home)
    res = run(home, apply=False)
    assert snapshot(home) == before
    item = next(i for i in res.items if i.rule == "subtickets-done-parent-to-review")
    assert item.ticket == parent.id and "subtickets are closed" in item.why and item.changes == ["status: in_progress → review"]
    assert res.summary().startswith("Dry run:") and "nothing was changed" in res.summary()


# ---- each shipped rule -------------------------------------------------------------------------

def _family(home):
    parent = make(home, status="in_progress")
    k = make(home, "Task: the only piece", status="ready", parent=parent.id)
    k.meta["status"] = "done"
    k.set_section("Report", "Done.\n")
    k.set_section("Decision", "- 2026-11-14 · james · approved · \"ok\"\n")
    home.save(k)
    return parent


def test_subtickets_done_moves_parent_to_review_as_automation_in_one_batch(home, no_gh):
    """The parent moves; every event is by automation with kind rule, carries the run's batch and names the rule."""
    parent = _family(home)
    res = run(home, "subtickets-done-parent-to-review")
    assert home.get(parent.id).status == "review"
    evs = [e for e in activity.read(home.home, parent.id) if e.batch]
    assert evs and all(e.actor == "automation" and e.kind == "rule" and e.batch == res.batch for e in evs)
    assert all("(rule subtickets-done-parent-to-review)" in e.what for e in evs)
    assert res.batch.startswith("b-") and "Undo: drew auto undo" in res.summary()


def test_merged_pr_closes_an_approved_ticket_with_the_link(home, monkeypatch):
    """Idea 47: a merged pull request whose title starts with the id moves the approved ticket to done, PR link in Decision."""
    t = make(home, status="approved")
    url = "https://example.invalid/pr/12"
    monkeypatch.setattr(au, "gh_run", gh_with(merged=[{"number": 12, "title": f"{t.id}: build it", "url": url, "mergedAt": "x"}]))
    run(home, "merged-pr-closes-ticket")
    done = home.get(t.id)
    assert done.status == "done" and url in done.section("Decision")


def test_merged_pr_before_approval_is_flagged_and_queued_not_closed(home, monkeypatch):
    """A rule never skips approval: the ticket stays in review, gets pr-merged, and the owner is told through the queue."""
    t = make(home, status="review")
    monkeypatch.setattr(au, "gh_run", gh_with(merged=[{"number": 3, "title": f"{t.id} fix", "url": "https://example.invalid/3"}]))
    run(home, "merged-pr-waits-for-approval", "merged-pr-closes-ticket")
    after = home.get(t.id)
    assert after.status == "review" and "pr-merged" in after.tags
    q = notify_queue.pending(home.home)
    assert [x["ids"] for x in q] == [[t.id]] and q[0]["source"] == "automation:merged-pr-waits-for-approval"


def test_ci_state_is_written_on_review_tickets(home, monkeypatch):
    """Idea 48: a red check on the open pull request shows as ci: red on the review ticket."""
    t = make(home, status="review")
    pr = {"number": 4, "title": f"{t.id}: thing", "url": "https://example.invalid/4",
          "statusCheckRollup": [{"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"}]}
    monkeypatch.setattr(au, "gh_run", gh_with(open_=[pr]))
    run(home, "ci-state-on-review")
    assert home.get(t.id).meta["ci"] == "red" and home.get(t.id).meta["ci_pr"] == pr["url"]


def test_gh_missing_skips_pull_request_rules_with_a_plain_note(home, no_gh):
    """No gh: the pull request rules do nothing and say why; the others still run."""
    parent = _family(home)
    res = run(home)
    assert any("gh is not installed" in n for n in res.notes)
    assert home.get(parent.id).status == "review"


def test_hard_deadline_two_days_out_tags_lanes_and_queues_an_urgent_note(home, no_gh):
    """Idea 57: two days before a hard deadline the ticket is tagged, put in Now, and an urgent notification is queued."""
    t = make(home, deadline=(TODAY + dt.timedelta(days=2)).isoformat(), deadline_kind="hard")
    far = make(home, "Build: a later thing", deadline=(TODAY + dt.timedelta(days=9)).isoformat(), deadline_kind="hard")
    run(home, "hard-deadline-two-days")
    near = home.get(t.id)
    assert "deadline-near" in near.tags and near.meta["lane"] == "now"
    assert "deadline-near" not in home.get(far.id).tags
    q = notify_queue.pending(home.home)
    assert len(q) == 1 and q[0]["urgent"] is True and q[0]["ids"] == [t.id]


def test_a_lane_set_by_hand_is_kept(home, no_gh):
    """A rule never changes what a person set by hand: a lane chosen by a person stays, and the run says it kept it."""
    t = make(home, deadline=(TODAY + dt.timedelta(days=1)).isoformat(), deadline_kind="hard")
    t.meta["lane"] = "later"
    home.save(t)  # a person's save (no automation actor)
    res = run(home, "hard-deadline-two-days")
    after = home.get(t.id)
    assert after.meta["lane"] == "later" and "deadline-near" in after.tags
    assert "kept lane later (set by hand)" in res.items[0].changes


def test_repeated_ask_overrides_priority_because_the_rule_says_so(home, no_gh):
    """Decision 60: a second ask makes the ticket p0 with emergency even though priority was set by hand (override: true)."""
    t = make(home, priority="p2")
    t.append_line("Comments", "- 2026-11-13 09:00 · james · please ship the export page today for the client <!-- kind: Question -->")
    t.append_line("Comments", "- 2026-11-14 10:00 · james · ship the export page today for the client please <!-- kind: Question -->")
    home.save(t)
    run(home, "repeated-ask-emergency")
    after = home.get(t.id)
    assert after.meta["priority"] == "p0" and "emergency" in after.tags


def test_stale_label_then_close_with_a_reason_and_owner_tickets_are_kept(home, no_gh):
    """DESK-0199: first pass labels, a second pass a stale-period later closes with the reason; the owner's tickets only get the label."""
    t = make(home)
    mine = make(home, "Build: the owner's own thing")
    mine.meta["owner"] = "james"
    home.save(mine)
    later = TODAY + dt.timedelta(days=home.stale_days + 2)
    run(home, "stale-label", "stale-close", today=later)
    assert "stale" in home.get(t.id).tags and home.get(t.id).status == "ready"
    run(home, "stale-label", "stale-close", today=later + dt.timedelta(days=home.stale_days + 2))
    closed = home.get(t.id)
    assert closed.status == "rejected" and "labelled stale" in closed.section("Decision")
    assert home.get(mine.id).status == "ready" and "stale" in home.get(mine.id).tags


def test_stale_close_never_rejects_a_ticket_waiting_on_the_owner_or_in_progress(home, no_gh):
    """Rule 10: a ticket blocked on the owner, claimed or in progress is labelled stale but never auto-closed."""
    waiting = make(home, "Build: waits on the owner's answer")
    ops.block(home, waiting, "james", "needs the owner's choice")
    working = make(home, "Build: someone is on it", status="in_progress")
    claimed = make(home, "Build: someone took it", status="claimed")
    later = TODAY + dt.timedelta(days=home.stale_days + 2)
    run(home, "stale-label", "stale-close", today=later)
    run(home, "stale-label", "stale-close", today=later + dt.timedelta(days=home.stale_days + 2))
    for t, status in ((waiting, "blocked"), (working, "in_progress"), (claimed, "claimed")):
        after = home.get(t.id)
        assert after.status == status and "stale" in after.tags, (t.id, after.status)


def test_unblock_when_the_blocker_closes(home, no_gh):
    """A ticket blocked on another ticket returns to where it was when that ticket closes."""
    blocker = make(home, "Build: the blocker")
    t = make(home)
    ops.block(home, t, blocker.id, "waits for the blocker")
    ops.reject(home, home.get(blocker.id), "not needed after all")
    run(home, "unblock-when-blocker-closes")
    assert home.get(t.id).status == "ready"


def test_weekly_review_is_created_once_per_week(home, no_gh):
    """Decision 24 and idea 56: the week's ticket review appears on the first sweep from Monday, and only once."""
    monday = TODAY - dt.timedelta(days=TODAY.weekday())
    run(home, "weekly-review-monday", today=monday)
    run(home, "weekly-review-monday", today=monday + dt.timedelta(days=2))
    reviews = [t for t in home.tickets() if "review-week" in t.tags]
    assert len(reviews) == 1 and reviews[0].project == "management"


def test_recurring_template_creates_one_ticket_per_period(home, no_gh):
    """Idea 55: an `every: monthly` rule creates its ticket from the template once a month, with the period in the title."""
    rs, repos = rules(home, "monthly-recurring-example")
    rs[0].enabled = True
    first = dt.date(TODAY.year, TODAY.month, 2)
    au.sweep(home, rs, repos, today=first, apply=True)
    au.sweep(home, rs, repos, today=first + dt.timedelta(days=3), apply=True)
    made = [t for t in home.tickets() if str(t.meta.get("recurring", "")).startswith("monthly-recurring-example@")]
    assert len(made) == 1 and f"{first:%Y-%m}" in made[0].meta["title"] and made[0].meta["assignee"] == "main"


# ---- undo --------------------------------------------------------------------------------------

def test_undo_restores_every_ticket_the_run_changed(home, no_gh):
    """One run undoes as one: the moved parent is back in progress and a created ticket is closed with the reason."""
    parent = _family(home)
    before = parent.path.read_bytes()
    monday = TODAY - dt.timedelta(days=TODAY.weekday())
    res = run(home, "subtickets-done-parent-to-review", "weekly-review-monday", today=monday)
    assert home.get(parent.id).status == "review"
    done, kept = au.undo(home, res.batch)
    assert not kept and parent.id in done
    assert home.get(parent.id).status == "in_progress"
    assert home.get(parent.id).path.read_bytes() == before
    assert all(t.status == "rejected" for t in home.tickets() if "review-week" in t.tags)
    with pytest.raises(au.AutomationError, match="already undone"):
        au.undo(home, res.batch)


def test_undo_leaves_a_ticket_someone_changed_after_the_run(home, no_gh):
    """Undo never loses later work: a ticket edited after the run is named and left alone."""
    parent = _family(home)
    res = run(home, "subtickets-done-parent-to-review")
    t = home.get(parent.id)
    t.append_line("Comments", "- 2026-11-15 12:00 · james · reviewed it <!-- kind: FYI -->")
    home.save(t)
    done, kept = au.undo(home, res.batch)
    assert not done and kept and parent.id in kept[0]
    assert home.get(parent.id).status == "review"


# ---- the commands ------------------------------------------------------------------------------

def test_cli_list_explain_dry_run_and_run(home, no_gh, capsys):
    """`drew auto list|explain|dry-run|run` print plain lines; run shows progress and a summary with the undo command."""
    parent = _family(home)
    main(["--home", str(home.home), "auto", "list"])
    out = capsys.readouterr().out
    assert "merged-pr-closes-ticket" in out and "source: " in out
    main(["--home", str(home.home), "auto", "explain", "subtickets-done-parent-to-review"])
    out = capsys.readouterr().out
    assert "Source: idea 49" in out and parent.id in out
    main(["--home", str(home.home), "auto", "dry-run"])
    out = capsys.readouterr().out
    assert "would: status: in_progress → review" in out and "nothing was changed" in out
    assert home.get(parent.id).status == "in_progress"
    main(["--home", str(home.home), "auto", "run"])
    cap = capsys.readouterr()
    assert "of" in cap.err and "rules" in cap.err and "[" in cap.err
    assert "Done:" in cap.out and "drew auto undo b-" in cap.out
    assert home.get(parent.id).status == "review"


def test_cli_init_refuses_to_overwrite(home, capsys):
    """`drew auto init` on a store that has the file exits 1 and writes nothing."""
    text = au.path(home.home).read_bytes()
    with pytest.raises(SystemExit) as e:
        main(["--home", str(home.home), "auto", "init"])
    assert e.value.code == 1 and au.path(home.home).read_bytes() == text


# ---- the page ----------------------------------------------------------------------------------

def test_page_shows_rules_and_run_now_writes_as_automation_with_undo_params(home, no_gh, monkeypatch):
    """The page answers first (rules on, tickets today); Run now changes as automation and redirects with undo= and msg= (DESK-0284)."""
    import re

    from fastapi.testclient import TestClient

    from desk import people
    from desk.ui.app import create_app

    monkeypatch.setattr(identity, "os_user", lambda: "jeby")
    people.link_os_user(home.home, "james", "jeby")
    parent = _family(home)
    c = TestClient(create_app(home.home))
    page = c.get("/automations").text
    assert re.search(r"rules are on. Today they would change <b>[1-9][0-9]*</b> ticket", page) and f"/ticket/{parent.id}" in page
    tok = re.search(r'action="/automations/run"[^>]*>\s*<input type="hidden" name="token" value="([^"]+)"', page).group(1)
    r = c.post("/automations/run", data={"token": tok}, follow_redirects=False)
    assert r.status_code == 303 and "undo=b-" in r.headers["location"] and "msg=Done" in r.headers["location"]
    assert home.get(parent.id).status == "review"
    evs = [e for e in activity.read(home.home, parent.id) if e.batch]
    assert evs and {e.actor for e in evs} == {"automation"} and {e.kind for e in evs} == {"rule"}
    again = c.post("/automations/run", data={"token": tok}, follow_redirects=False)
    assert "works+once" in again.headers["location"]


def test_page_without_a_rules_file_says_how_to_start(store):
    """No automations.yaml: the page says so in one sentence and names `drew auto init`."""
    from fastapi.testclient import TestClient

    from desk.ui.app import create_app

    page = TestClient(create_app(store.home)).get("/automations").text
    assert "drew auto init" in page


def test_a_move_the_lifecycle_rules_refuse_is_held_back_and_nothing_is_written(home, no_gh):
    """Rules go through rules.check_transition: a parent with no Report is not moved to review, and the run says why."""
    parent = _family(home)
    t = home.get(parent.id)
    t.set_section("Report", "")
    home.save(t)
    before = home.get(parent.id).path.read_bytes()
    res = run(home, "subtickets-done-parent-to-review")
    assert home.get(parent.id).path.read_bytes() == before
    assert res.items[0].skipped.startswith("not moved to review") and not res.changed
