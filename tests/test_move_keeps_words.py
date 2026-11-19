"""DESK-0324 (p0, data loss): every word typed into a Move, Park, Decide or reason box lands on the ticket.

Protects: on 2026-11-17 a Move to Blocked saved the status and dropped the sentence typed beside it (the reason went to the rule
check and nowhere else). The words are now a dated comment (or the Decision line for approve, send back and reject) in the mover's
name, written in the same save as the status, so one event batch holds both. A move that cannot keep the words is refused with a
plain sentence, writes nothing, and the page gives the text back. Several methods per behaviour: the function (ops), the web route
(HTTP, form and JSON) and the command line, each read back from the ticket file or the event log, never from the call's own return."""

import re
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from desk import activity, lanes, ops, triage
from desk.cli import main
from desk.rules import RuleError
from desk.ui.app import create_app

JSON = {"Accept": "application/json"}
WORDS = "Waiting for the partner to send the sample file, not before Friday."


def _client(store):
    return TestClient(create_app(store.home), base_url="http://127.0.0.1:8799")


def _mk(store, title="Keep my words", status="ready", **meta):
    t = ops.new(store, title, "desk")
    t.meta.update({"status": status, "owner": "james", "assignee": "main", **meta})
    store.save(t)
    return store.get(t.id)


def _comments(store, tid):
    return [ln for ln in store.get(tid).section("Comments").splitlines() if ln.strip()]


def _events(store, tid):
    return activity.read(store.home, tid)


def _post_move(c, t, to, reason, js=True):
    data = {"token": c.app.state.tokens.issue(), "to": to, "reason": reason}
    return c.post(f"/ticket/{t.id}/move", data=data, headers=JSON if js else {}, follow_redirects=False)


# -- the behaviour: the words are kept, dated and named -----------------------------------------------------------------------

def test_ops_move_to_blocked_keeps_the_words_dated_and_named(store):
    """Method 1, the function: the sentence is a Comments line with a date, a time and the mover's name, and the status changed too."""
    t = _mk(store, blocked_on="DESK-0001")
    ops.move(store, t, "blocked", actor="james", words=WORDS)
    assert store.get(t.id).status == "blocked"
    (line,) = _comments(store, t.id)
    assert re.match(r"- \d{4}-\d{2}-\d{2} \d{2}:\d{2} · james · ", line) and WORDS in line


def test_web_move_to_blocked_keeps_the_words_on_the_ticket_page(store):
    """Method 2, the web route: Move to Blocked with a sentence in the box leaves the sentence in Comments, shown on the page."""
    t = _mk(store, blocked_on="DESK-0001")
    c = _client(store)
    r = _post_move(c, t, "blocked", WORDS)
    assert r.status_code == 200 and r.json()["ok"]
    page = c.get(f"/ticket/{t.id}").text
    assert WORDS in page and store.get(t.id).status == "blocked"


def test_cli_move_with_reason_keeps_the_words(store):
    """Method 3, the command line: `drew move --to blocked --reason ...` also keeps the sentence."""
    t = _mk(store, blocked_on="DESK-0001")
    main(["--home", str(store.home), "move", t.id, "--to", "blocked", "--reason", WORDS, "--as", "james"])
    assert store.get(t.id).status == "blocked"
    assert any(WORDS in ln for ln in _comments(store, t.id))


def test_the_words_and_the_status_are_one_event_batch(store):
    """Method 4, the event log: the comment event and the status event were written by the same save, under one batch id."""
    t = _mk(store, blocked_on="DESK-0001")
    _post_move(_client(store), t, "blocked", WORDS)
    evs = _events(store, t.id)
    status = [e for e in evs if e.field == "status" and e.new == "blocked"]
    said = [e for e in evs if e.change == "comment" and WORDS in (e.new + e.what)]
    assert len(status) == 1 and len(said) == 1, [(e.change, e.field, e.new, e.what) for e in evs]
    assert status[0].batch and status[0].batch == said[0].batch and status[0].ts == said[0].ts


def test_the_comment_carries_the_movers_verified_identity(store):
    """Method 5, identity: the line holds the identity tag the middleware resolved (kind, source, how it was established)."""
    t = _mk(store, blocked_on="DESK-0001")
    _post_move(_client(store), t, "blocked", WORDS)
    (line,) = _comments(store, t.id)
    assert "src=ui" in line and "ok=" in line


def test_every_status_a_person_can_pick_keeps_the_words(store):
    """Method 6: not only Blocked. In progress and unblock each leave the words."""
    c = _client(store)
    t = _mk(store, status="claimed", owner="james", branch="desk/x")
    assert _post_move(c, t, "in_progress", "starting now, after lunch").status_code == 200
    b = _mk(store, "Blocked one", status="blocked", blocked_on="DESK-0001", blocked_from="ready")
    assert _post_move(c, b, "unblock", "the sample file arrived").status_code == 200
    assert store.get(b.id).status == "ready"
    for tid, words in ((t.id, "starting now, after lunch"), (b.id, "the sample file arrived")):
        assert any(words in ln for ln in _comments(store, tid)), tid


def test_move_to_approved_or_rejected_writes_the_decision_line(store):
    """Method 7: the words that make a decision are the Decision line, not a comment, whichever way the move is made."""
    r = _mk(store, "Needs a no", status="triage")
    ops.move(store, r, "rejected", actor="james", words="Not this quarter, the numbers do not work.", reason="Not this quarter, the numbers do not work.")
    assert "Not this quarter" in store.get(r.id).section("Decision")
    a = _mk(store, "Needs a yes", status="in_progress")
    a.set_section("Report", "done")
    a.meta["status"] = "review"
    store.save(a)
    ops.move(store, store.get(a.id), "approved", actor="james", words="Yes, as written.", reason="Yes, as written.")
    assert "Yes, as written." in store.get(a.id).section("Decision")


# -- edge cases ---------------------------------------------------------------------------------------------------------------

def test_an_empty_or_blank_box_writes_no_comment(store):
    """Edge: nothing typed means nothing to keep; the move still happens and no empty comment line appears."""
    t = _mk(store, blocked_on="DESK-0001")
    assert _post_move(_client(store), t, "blocked", "   \n  ").status_code == 200
    assert store.get(t.id).status == "blocked" and _comments(store, t.id) == []


def test_quotes_newlines_and_markup_survive_word_for_word(store):
    """Edge: a double quote, an apostrophe, a line break and an HTML comment marker are kept as typed (the break becomes a space)."""
    t = _mk(store, blocked_on="DESK-0001")
    typed = 'He said "wait" and I\'m waiting\nuntil <!-- kind: Concern --> Friday & Monday'
    _post_move(_client(store), t, "blocked", typed)
    (line,) = _comments(store, t.id)
    assert 'He said "wait" and I\'m waiting until' in line and "Friday & Monday" in line
    assert re.findall(r"<!-- kind: (\w+) -->", line) == ["FYI"]  # only the kind marker Desk writes itself; the typed one cannot forge another
    assert ops.comment_lines(store.get(t.id))[0] == line


def test_the_same_words_typed_twice_are_kept_twice(store):
    """Edge: a duplicate check belongs to the comment box; a move never drops what the person typed."""
    t = _mk(store, blocked_on="DESK-0001", blocked_from="ready")
    c = _client(store)
    t.meta["status"] = "blocked"
    store.save(t)
    _post_move(c, store.get(t.id), "unblock", "checking again")
    t2 = store.get(t.id)
    t2.meta.update({"status": "blocked", "blocked_on": "DESK-0001", "blocked_from": "ready"})
    store.save(t2)
    _post_move(c, store.get(t.id), "unblock", "checking again")
    assert sum("checking again" in ln for ln in _comments(store, t.id)) == 2


def test_words_with_a_secret_are_refused_and_nothing_is_written(store):
    """Edge, rule 80: a secret-looking value is refused in plain words; the status stays and the text is handed back."""
    t = _mk(store, blocked_on="DESK-0001")
    secret = "my key is ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    r = _post_move(_client(store), t, "blocked", secret)
    assert r.status_code == 422 and r.json()["error"] and "token" in r.json()
    assert store.get(t.id).status == "ready" and _comments(store, t.id) == []


def test_a_move_that_cannot_happen_writes_no_words(store):
    """Edge: a move the rules refuse (no blocked_on) leaves the ticket and its comments untouched; the error says why."""
    t = _mk(store)  # no blocked_on
    r = _post_move(_client(store), t, "blocked", WORDS)
    assert r.status_code == 422 and "Blocked needs a name" in r.json()["error"]
    assert store.get(t.id).status == "ready" and _comments(store, t.id) == []
    with pytest.raises(RuleError):
        ops.move(store, t, "blocked", actor="james", words=WORDS)
    assert _comments(store, t.id) == []


def test_a_refused_move_without_script_gives_the_text_back_in_the_box(store):
    """A refused move keeps the typed text on screen: the redirect carries it and the page fills the reason box with it."""
    t = _mk(store)  # no blocked_on, so the move is refused
    c = _client(store)
    r = _post_move(c, t, "blocked", WORDS, js=False)
    assert r.status_code == 303
    loc = r.headers["location"]
    assert parse_qs(urlparse(loc).query).get("reason") == [WORDS]
    page = c.get(loc.split("#")[0]).text
    assert "Blocked needs a name" in page
    assert re.search(r'<input type="text" name="reason"[^>]*value="' + re.escape(WORDS) + '"', page)


def test_the_ticket_page_box_says_its_words_are_kept(store):
    """The label no longer says the box is only for rejecting: it says the words are written to the ticket."""
    t = _mk(store)
    page = _client(store).get(f"/ticket/{t.id}").text
    assert "needed to reject a ticket" not in page and "kept on the ticket as a comment" in page
    assert re.search(r"Your words[^<]*kept on the ticket", page)


# -- Park and Decide ------------------------------------------------------------------------------------------------------------

def test_park_keeps_the_reason_as_a_comment_from_the_lane_box(store):
    """Method 1 for Park: the lane route writes a dated comment as well as park_reason (which clears when the ticket leaves Parked)."""
    t = _mk(store)
    c = _client(store)
    tok = c.app.state.tokens.issue()
    r = c.post(f"/ticket/{t.id}/lane", data={"token": tok, "lane": "parked", "reason": "not this quarter, money is tight"}, headers=JSON)
    assert r.status_code == 200, r.text
    assert any("not this quarter, money is tight" in ln for ln in _comments(store, t.id))
    lanes.set_lane(store, store.get(t.id), "later")
    assert any("not this quarter, money is tight" in ln for ln in _comments(store, t.id))  # the words outlive the lane


def test_park_keeps_the_reason_from_the_command_line_and_triage(store):
    """Methods 2 and 3 for Park: `drew park` and the inbox triage Park both leave the sentence on the ticket."""
    a = _mk(store, "Park by command")
    main(["--home", str(store.home), "park", a.id, "later, when the partner answers"])
    assert any("later, when the partner answers" in ln for ln in _comments(store, a.id))
    b = _mk(store, "Park from triage", status="inbox")
    triage.place(store, b, "parked", actor="james", reason="parked until the new year, no budget")
    assert any("parked until the new year, no budget" in ln for ln in _comments(store, b.id))
    d = _mk(store, "Park with the default", status="inbox")
    triage.place(store, d, "parked", actor="james", reason="")
    assert _comments(store, d.id) == []  # Desk's own default sentence is not words a person typed


def test_decide_keeps_quotes_in_the_decision_line(store):
    """Decide: the words, quotes and all, are in the Decision line in the owner's name, on the one path every box uses."""
    t = _mk(store, "Choose a thing", status="ready", type="decision")
    c = _client(store)
    r = c.post(f"/ticket/{t.id}/decide", data={"token": c.app.state.tokens.issue(), "action": "approve", "words": 'Go with "B", it\'s cheaper.'}, headers=JSON)
    assert r.status_code == 200, r.text
    assert 'Go with "B", it\'s cheaper.' in store.get(t.id).section("Decision")
