"""DESK-0322: `drew comments --author james` lists every word he wrote in Desk, not only Comments lines: Not accepted reasons (with the
acceptance line), Decision lines (Approve, Send back, Decide mode, Move to approved or rejected), Park reasons, and a yes/no answer a session
recorded as `Decided by James (...)`. Three methods per behaviour: the library on files written by hand, the library on files written by the
real operations, and the command line; plus edge cases (a quote inside a reason, a decision with no time, a line in the mark's own minute)."""

import datetime as dt
import json

from desk import activity, comments, ops
from desk.cli import main


def _ticket(store, title="A ticket", *, acceptance="", decision="", comments_text="", updated="2026-11-17"):
    t = ops.new(store, title, "desk")
    if acceptance:
        t.set_section("Acceptance", acceptance)
    if decision:
        t.set_section("Decision", decision)
    if comments_text:
        t.set_section("Comments", comments_text)
    store.save(t)
    t = store.get(t.id)
    t.meta["updated"] = updated  # save() stamps today; the fixture needs a chosen date
    store.save(t, touch=False)
    return t


OK_DECISION = '- 2026-11-17 · james · approved · "ok"\n'


def _json(store, capsys, *args):
    capsys.readouterr()
    main(["--home", str(store.home), "comments", "--json", *args])
    return json.loads(capsys.readouterr().out)


NA = ('- [!] PR 11 shows Merged.  <!-- by james 2026-11-17T20:07 -->\n'
      '  > Not accepted (must fix): You didn\'t link them in here, "out of principle" I can\'t accept.\n')


# -- Not accepted reasons -------------------------------------------------------------------------------------------------------------

def test_not_accepted_reason_is_listed_with_its_acceptance_line_by_the_file(store):
    """Method 1 (the file as written): the reason, the line it is about, who and when come from the Acceptance section."""
    t = _ticket(store, acceptance=NA + "- [x] Another line\n")
    [r] = comments.query(store, author="james")
    assert (r.ticket_id, r.when, r.author) == (t.id, dt.datetime(2026, 11, 17, 20, 7), "james")
    assert "out of principle" in r.text and "PR 11 shows Merged." in r.text  # the quote inside the reason survives; the line is shown
    assert r.kind.startswith("Not accepted") and r.source == "not accepted"


def test_not_accepted_reason_pressed_through_the_real_operation_is_listed(store):
    """Method 2 (the real write): the box a person presses on the page writes the same lines the listing reads."""
    t = _ticket(store, acceptance="- [ ] It works\n- [ ] It is fast\n")
    ops.toggle_check(store, t.id, "Acceptance", 1, "!", actor="james", reason="Too slow on my phone")
    rows = comments.query(store, author="james")
    assert [r.text for r in rows] == ["Not accepted (must fix): Too slow on my phone  [Acceptance: It is fast]"]
    assert comments.query(store, author="chaz") == []  # a reason is the pressing person's, nobody else's


def test_not_accepted_reason_is_listed_by_the_command_line(store, capsys):
    """Method 3 (the command): `drew comments --author james --since 2026-11-17` prints it with ticket, time and text."""
    t = _ticket(store, acceptance=NA)
    capsys.readouterr()
    assert main(["--home", str(store.home), "comments", "--author", "james", "--since", "2026-11-17"]) in (None, 0)
    out = capsys.readouterr().out
    assert t.id in out and "2026-11-17 20:07" in out and "out of principle" in out and "1 comment(s)" in out


def test_not_accepted_line_whose_by_tag_carries_a_note_is_still_his(store):
    """Edge, from the real store (JEBYUS-0077): a worker added a note inside the tag, `<!-- by james 2026-11-17T20:07; links added by patch -->`;
    the person and time are still the ones who pressed the box, and the hidden note is not part of the line shown."""
    body = ("- [!] PR 11 shows Merged (link added).  <!-- by james 2026-11-17T20:07; links and times added by Patch 2026-11-17 -->\n"
            "  > Not accepted (must fix): You did not link them in here.\n")
    t =_ticket(store, acceptance=body)
    [r] = comments.query(store, author="james")
    assert (r.ticket_id, r.when) == (t.id, dt.datetime(2026, 11, 17, 20, 7))
    assert "links and times" not in r.text and "PR 11 shows Merged (link added)." in r.text


def test_a_cleared_not_accepted_box_lists_nothing(store):
    """Edge: a line that went back to open or done no longer holds a reason."""
    _ticket(store, acceptance="- [x] done line\n- [ ] open line\n")
    assert comments.query(store, author="james") == []


# -- Decision lines (Approve, Send back, Decide mode, yes/no through ops.decide, Move to approved) ----------------------------------

def test_decision_line_with_no_time_is_listed_by_the_file(store):
    """Method 1: a Decision line has a date and no time; it is listed on that day and `--since` that day keeps it."""
    t = _ticket(store, decision='- 2026-11-17 · james · approved · "Value and Priority could be the 2, both in a 1-3 rating"\n')
    [r] = comments.query(store, author="james", since=dt.datetime(2026, 11, 17, 20, 0))  # a day-only line is not cut by the hour
    assert (r.ticket_id, r.kind, r.date_only) == (t.id, "Decision: approved", True)
    assert r.text == "Value and Priority could be the 2, both in a 1-3 rating"
    assert comments.query(store, author="james", since=dt.datetime(2026, 11, 18)) == []


def test_decision_written_by_decide_and_by_a_move_is_listed(store):
    """Method 2: `ops.decide` (Approve, Send back, Decide mode, yes/no) and `ops.move` with words write the lines the listing reads, quotes kept."""
    a = ops.new(store, "Choose", "desk", "decision")
    a.meta.update({"status": "review", "owner": "james"})
    a.set_section("Report", "Ready for the decision.")
    store.save(a)
    ops.decide(store, store.get(a.id), False, 'I said "no more" of this', actor="james")
    b = ops.new(store, "Reject me", "desk")
    b.meta.update({"status": "review", "owner": "james"})
    b.set_section("Report", "Ready.")
    store.save(b)
    ops.move(store, store.get(b.id), "rejected", actor="james", reason="Not wanted", words="Not wanted", by="james")
    rows = comments.query(store, author="james")
    assert sorted((r.ticket_id, r.kind, r.text) for r in rows) == [(a.id, "Decision: changes_requested", 'I said "no more" of this'),
                                                                  (b.id, "Decision: rejected", "Not wanted")]


def test_decision_is_listed_by_the_command_line_and_in_json(store, capsys):
    """Method 3: the command prints day-only lines without an invented time, and --json says where the words live."""
    t = _ticket(store, decision='- 2026-11-17 · james · changes_requested · "I ALREADY added detail"\n')
    capsys.readouterr()
    main(["--home", str(store.home), "comments", "--author", "james", "--since", "2026-11-17"])
    out = capsys.readouterr().out
    assert f"{t.id}  2026-11-17  james" in out and "00:00" not in out and "I ALREADY added detail" in out
    [row] = _json(store, capsys, "--author", "james")
    assert (row["when"], row["source"], row["ticket"]) == ("2026-11-17", "decision", t.id)


def test_decision_by_someone_else_is_not_james(store):
    """Edge: a rule's `desk` line in Decision (a split, a merge) is not his words."""
    _ticket(store, decision="- 2026-11-17 · desk · merged · abc123\n")
    assert comments.query(store, author="james") == []
    assert [r.kind for r in comments.query(store, author="desk")] == ["Decision"]


# -- Park reasons and Move reasons ----------------------------------------------------------------------------------------------------

def test_park_reason_from_the_activity_log_is_listed(store):
    """Park reasons written before DESK-0324 live only as a `park_reason` change in the activity log."""
    t = _ticket(store)
    activity.append(store.home, [activity.Event(ticket=t.id, ts="2026-11-17T20:10:00", actor="james", kind="owner", source="ui", change="field",
                                                field="park_reason", new="Waiting for the LLC", what="park_reason: empty -> Waiting for the LLC")])
    [r] = comments.query(store, author="james")
    assert (r.kind, r.text, r.when) == ("Park reason", "Waiting for the LLC", dt.datetime(2026, 11, 17, 20, 10))


def test_park_reason_kept_as_a_comment_is_listed_once(store):
    """Edge: since DESK-0324 the same words are also a Comments line; the log's copy must not make a second row."""
    t = _ticket(store, comments_text="- 2026-11-17 20:10 · james · moved to parked: Waiting for the LLC <!-- kind: FYI -->")
    activity.append(store.home, [activity.Event(ticket=t.id, ts="2026-11-17T20:10:00", actor="james", kind="owner", source="ui", change="field",
                                                field="park_reason", new="Waiting for the LLC")])
    assert len(comments.query(store, author="james")) == 1


def test_move_reason_written_by_move_is_listed(store):
    """A Move to Blocked keeps the words as a comment in the mover's name (DESK-0324); it is listed with the other words."""
    t = ops.new(store, "Blocked thing", "desk")
    t.meta.update({"status": "ready", "owner": "james", "assignee": "main", "blocked_on": "the bank"})
    store.save(t)
    ops.move(store, store.get(t.id), "blocked", actor="james", reason="Waiting on the bank", words="Waiting on the bank", by="james")
    assert [r.text for r in comments.query(store, author="james")] == ["moved to blocked: Waiting on the bank"]


# -- yes/no answers a session recorded -----------------------------------------------------------------------------------------------

def test_relayed_yes_no_answer_is_listed_under_the_person_and_marked_as_a_relay(store, capsys):
    """DESK-0238: `Decided by James (...)` written by a session is his answer, listed under him, and says who wrote it (no one acts as anyone)."""
    t = _ticket(store, comments_text="- 2026-11-17 20:23 · desk · Decided by James (2026-11-17, yes/no D5 re-asked): B, keep both. <!-- kind: FYI -->")
    [r] = comments.query(store, author="james")
    assert (r.ticket_id, r.kind, r.source) == (t.id, "Answer (relayed by desk)", "relayed answer")
    assert "B, keep both" in r.text
    [row] = _json(store, capsys, "--author", "james")
    assert row["source"] == "relayed answer"
    assert [x.author for x in comments.query(store, author="desk")] == ["desk"]  # the writer's own line is still the writer's


# -- --unanswered and --since apply to all of them ----------------------------------------------------------------------------------

def test_unanswered_applies_to_every_kind(store):
    """A later line by patch answers a Not accepted reason, as it does a comment; a ticket that moved on a later day answers all of its lines;
    a day-only Decision line stays open beside a same-day patch line (its time is unknown, and better listed twice than lost)."""
    open_t = _ticket(store, "Open", acceptance=NA, decision=OK_DECISION)
    answered = _ticket(store, "Answered", acceptance=NA, comments_text="- 2026-11-17 21:00 · patch · Done, see the report. <!-- kind: FYI -->")
    day_only = _ticket(store, "Day only", decision=OK_DECISION,
                       comments_text="- 2026-11-17 21:00 · patch · Done. <!-- kind: FYI -->")
    moved = _ticket(store, "Moved on", acceptance=NA, decision=OK_DECISION, updated="2026-11-19")
    rows = comments.query(store, author="james", unanswered=True)
    assert sorted(r.ticket_id for r in rows) == sorted([open_t.id, open_t.id, day_only.id])
    assert answered.id not in {r.ticket_id for r in rows} and moved.id not in {r.ticket_id for r in rows}


def test_a_line_in_the_marks_own_minute_is_listed_again_not_lost(store):
    """Edge: comments are timed to the minute and a mark to the second; a comment at 20:07 and a mark at 20:07:30 must not drop the comment."""
    t = _ticket(store, comments_text="- 2026-11-17 20:07 · james · please look at this")
    rows = comments.query(store, author="james", since=dt.datetime(2026, 11, 17, 20, 7, 30))
    assert [r.ticket_id for r in rows] == [t.id]
    assert comments.query(store, author="james", since=dt.datetime(2026, 11, 17, 20, 8)) == []


def test_old_comment_only_behaviour_is_unchanged(store):
    """Regression: a plain comment is still listed once with its kind, as before."""
    _ticket(store, comments_text="- 2026-11-08 03:01 · james · why is this like that? <!-- kind: Question -->")
    [r] = comments.query(store, author="james")
    assert (r.kind, r.text, r.source, r.date_only) == ("Question", "why is this like that?", "comment", False)
