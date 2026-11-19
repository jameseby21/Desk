"""DESK-0325 (second check, 2026-11-19): the Move box warns for EVERY move the server would refuse, not only Blocked, Rejected and Parked.

Protects: the page said "Ready to move." and enabled Move for ready -> claimed, in_progress, review and done, for blocked -> ready, and for
approved on a task ticket; the server then refused each of them. The false "Ready to move." was worse than silence. Now the page gets each
option's refusal from the rule the server uses (`ops.move_refusal`, run when the page is drawn with the fields filled), shows that sentence
beside a grey Move, and the sentences are plain words that name `drew` (the name `desk` opens Display Settings on Windows).
Each behaviour is checked three ways: the rule itself (ops), the web route (the page's markup and the server's own 422), and a real headless
browser driving the page's own script."""

import re
from html.parser import HTMLParser

import pytest
from browser_harness import browser, drive
from fastapi.testclient import TestClient

from desk import ops
from desk.rules import STATUSES, RuleError
from desk.ui.app import create_app

JSON = {"Accept": "application/json"}
FILLER = "Typed words that are long enough."  # what the page assumes the person will type, so only the move itself is judged


def _client(store):
    return TestClient(create_app(store.home), base_url="http://127.0.0.1:8799")


def _mk(store, status="ready", type_="task", **meta):
    t = ops.new(store, "Move me", "desk", type_)
    t.meta.update({"status": status, "owner": "james", "assignee": "main", **meta})
    if status in ("done", "approved", "rejected", "changes_requested"):
        word = "approved" if status in ("done", "approved") else status
        t.append_line("Decision", f'- 2026-11-18 · james · {word} · "written by the test"')
    if status == "review":
        t.set_section("Report", "Built it.")
    store.save(t)
    return store.get(t.id)


class _Options(HTMLParser):
    def __init__(self):
        super().__init__()
        self.found = {}
        self.inside = False

    def handle_starttag(self, tag, attrs):
        if tag == "select" and dict(attrs).get("class") == "mv-to":
            self.inside = True
        elif tag == "option" and self.inside:
            a = dict(attrs)
            self.found[a.get("value", "")] = a.get("data-block")

    def handle_endtag(self, tag):
        if tag == "select":
            self.inside = False


def _page_blocks(c, tid) -> dict:
    """{option value: the sentence the page carries for it, or None} read from the Move box's own markup."""
    p = _Options()
    p.feed(c.get(f"/ticket/{tid}").text)
    return {k: v for k, v in p.found.items() if k}


def _post(c, tid, to, reason=FILLER, waiting_on=FILLER):
    data = {"token": c.app.state.tokens.issue(), "to": to, "reason": reason, "waiting_on": waiting_on}
    return c.post(f"/ticket/{tid}/move", data=data, headers=JSON, follow_redirects=False)


def _rule(store, t, to):
    return ops.move_refusal(store, t, to, actor=store.owner, reason=FILLER, waiting_on=FILLER)


# -- behaviour 1: the rule says what each move would be refused for -----------------------------------------------------------------

@pytest.mark.parametrize("to", ["claimed", "in_progress", "review", "done", "approved", "changes_requested"])
def test_rule_refuses_every_move_a_ready_task_ticket_cannot_make(store, to):
    """Method 1, the rule: for a ready task ticket each of these gets a sentence (never None), and it is the sentence the real move gives."""
    t = _mk(store)
    said = _rule(store, t, to)
    assert said, to
    with pytest.raises((RuleError, ValueError)) as e:
        ops.move_any(store, store.get(t.id), to, actor=store.owner, reason=FILLER, waiting_on=FILLER)
    assert str(e.value) == said


@pytest.mark.parametrize("to", ["triage", "blocked", "rejected", "parked"])
def test_rule_lets_a_ready_task_ticket_make_the_moves_that_are_allowed(store, to):
    """Edge: the moves a ready ticket may make give no refusal once the typed fields are filled."""
    t = _mk(store)
    assert _rule(store, t, to) is None


def test_every_status_pair_of_a_ready_task_ticket_agrees_with_the_real_move(store):
    """Method 2, a sweep: for every status the rule's answer and the real move's answer are the same (a sentence, or both go through)."""
    for to in STATUSES:
        t = _mk(store)
        said = _rule(store, t, to)
        try:
            ops.move_any(store, store.get(t.id), to, actor=store.owner, reason=FILLER, waiting_on=FILLER)
            real = None
        except (RuleError, ValueError) as e:
            real = str(e)
        assert said == real, (to, said, real)


def test_a_blocked_ticket_is_told_to_unblock_first_in_words_that_name_drew(store):
    """Edge: blocked -> ready or in_progress says to unblock first, names `drew unblock <id>`, and unblock itself goes through."""
    t = _mk(store, status="blocked", blocked_on="DESK-0001", blocked_from="ready")
    for to in ("ready", "in_progress", "claimed"):
        said = _rule(store, t, to)
        assert said and f"drew unblock {t.id}" in said, (to, said)
    assert _rule(store, t, "unblock") is None and _rule(store, t, "rejected") is None


def test_approved_on_a_task_ticket_is_refused_and_on_a_decision_ticket_it_is_not(store):
    """Edge: a decision ticket is answered where it stands; a task ticket is not, and the refusal says so."""
    assert _rule(store, _mk(store), "approved")
    assert _rule(store, _mk(store, type_="decision"), "approved") is None


def test_refusal_sentences_are_plain_words_that_name_drew_never_desk(store):
    """Method 3, the wording: no arrows, no `desk` command (it opens Display Settings), no 'is not an allowed move', and no placeholder in
    angle brackets."""
    sentences = []
    for status in ("inbox", "triage", "ready", "claimed", "in_progress", "review", "changes_requested", "approved", "blocked", "done", "rejected"):
        t = _mk(store, status=status, **({"blocked_on": "DESK-0001", "blocked_from": "ready"} if status == "blocked" else {}))
        sentences += [s for to in list(STATUSES) + ["unblock", "parked"] if (s := _rule(store, t, to))]
    assert len(sentences) > 40
    for s in sentences:
        assert "→" not in s and "->" not in s, s
        assert "not an allowed move" not in s, s
        assert not re.search(r"\bdesk\b", s), s  # lower-case `desk` is the command that opens Display Settings; the tool is drew
        assert "<" not in s, s


# -- behaviour 2: the web page carries the same sentence beside each option ------------------------------------------------------------

def test_page_markup_carries_the_rules_sentence_for_every_option(store):
    """Method 1, the markup: each option of a ready task ticket's Move box has data-block equal to the rule's sentence, or none when allowed."""
    t = _mk(store)
    blocks = _page_blocks(_client(store), t.id)
    assert set(blocks) >= set(STATUSES) - {"ready"}
    for to, got in blocks.items():
        assert got == _rule(store, t, to), to
    assert blocks["claimed"] and blocks["in_progress"] and blocks["review"] and blocks["done"] and not blocks["triage"]


def test_page_sentence_is_what_the_server_answers_when_the_move_is_sent_anyway(store):
    """Method 2, the web route: for every option the server's own 422 says the page's sentence, word for word; allowed ones return 200."""
    c = _client(store)
    checked = 0
    for to in STATUSES:
        t = _mk(store)
        if to == t.status:
            continue
        shown = _page_blocks(c, t.id)[to]
        r = _post(c, t.id, to)
        if shown:
            assert r.status_code == 422 and r.json()["error"] == shown, (to, shown, r.text)
        else:
            assert r.status_code == 200, (to, r.text)
        checked += 1
    assert checked == len(STATUSES) - 1


def test_blocked_and_decision_tickets_have_their_own_sentences_on_the_page(store):
    """Edge: a blocked ticket's page shows 'drew unblock' beside ready, none beside unblock; a decision ticket shows none beside approved."""
    c = _client(store)
    b = _mk(store, status="blocked", blocked_on="DESK-0001", blocked_from="ready")
    blocks = _page_blocks(c, b.id)
    assert "drew unblock" in (blocks["ready"] or "") and "drew unblock" in (blocks["in_progress"] or "")
    assert not blocks["unblock"] and not blocks["rejected"]
    d = _mk(store, type_="decision")
    assert not _page_blocks(c, d.id)["approved"]


def test_a_closed_ticket_blocks_every_status_option(store):
    """Edge: a done ticket does not move; every status option carries a sentence, so Move is never enabled for one (Parked is a lane, not a
    status: the server lets it through, so the page does not claim otherwise)."""
    t = _mk(store, status="done")
    c = _client(store)
    blocks = _page_blocks(c, t.id)
    assert blocks and all(v for k, v in blocks.items() if k != "parked"), blocks
    assert not blocks["parked"] and _post(c, t.id, "parked", FILLER, "").status_code == 200


# -- behaviour 3: in a real browser, Move is grey with that sentence beside it ---------------------------------------------------------

_SCRIPT = """<script>window.addEventListener("load", function () {
  var f = document.querySelector("form.mv-form"), sel = f.querySelector(".mv-to"), go = f.querySelector(".mv-go");
  var wait = f.querySelector(".mv-wait input"), words = f.querySelector(".mv-words input"), why = f.querySelector(".mv-why");
  function type(el, v) { el.value = v; el.dispatchEvent(new Event("input", { bubbles: true })); }
  var out = {};
  Array.prototype.forEach.call(sel.options, function (o) {
    if (!o.value) return;
    sel.value = o.value; sel.dispatchEvent(new Event("change", { bubbles: true }));
    type(wait, "DESK-0100"); type(words, "Typed words that are long enough.");
    out[o.value] = { off: go.disabled, why: why.textContent, block: o.getAttribute("data-block") };
  });
  fetch("/m", { method: "POST", body: JSON.stringify(out) });
});</script>"""


@pytest.mark.skipif(browser() is None, reason="no headless browser on this machine")
def test_in_a_real_browser_move_is_grey_with_the_rules_sentence_for_each_refused_move(store):
    """Method 3, the page's own script in a real browser, with the fields filled: for a ready task ticket Move is grey and the line beside it
    is the rule's sentence for claimed, in_progress, review, done, approved and changes_requested; it is on for triage, blocked, rejected, parked."""
    t = _mk(store)
    got = drive(store, _SCRIPT, f"/ticket/{t.id}")
    for to in ("claimed", "in_progress", "review", "done", "approved", "changes_requested"):
        assert got[to]["off"] and got[to]["why"] == _rule(store, t, to), (to, got[to])
    for to in ("triage", "blocked", "rejected", "parked"):
        assert not got[to]["off"] and got[to]["why"] == "Ready to move.", (to, got[to])


@pytest.mark.skipif(browser() is None, reason="no headless browser on this machine")
def test_in_a_real_browser_a_blocked_ticket_greys_everything_but_unblock_and_let_go(store):
    """Edge, real browser: on a blocked ticket Move is grey beside ready, in_progress and claimed with the 'drew unblock' sentence; unblock,
    Rejected and Parked (with the fields filled) are on."""
    t = _mk(store, status="blocked", blocked_on="DESK-0001", blocked_from="ready")
    got = drive(store, _SCRIPT, f"/ticket/{t.id}")
    for to in ("ready", "in_progress", "claimed"):
        assert got[to]["off"] and f"drew unblock {t.id}" in got[to]["why"], (to, got[to])
    for to in ("unblock", "rejected", "parked"):
        assert not got[to]["off"], (to, got[to])


@pytest.mark.skipif(browser() is None, reason="no headless browser on this machine")
def test_in_a_real_browser_approved_on_a_task_ticket_is_grey_and_on_a_decision_ticket_it_is_on(store):
    """Edge, real browser: the approved option is grey with the rule's sentence on a task ticket and enabled on a decision ticket."""
    task, dec = _mk(store), _mk(store, type_="decision")
    assert drive(store, _SCRIPT, f"/ticket/{task.id}")["approved"]["off"]
    assert not drive(store, _SCRIPT, f"/ticket/{dec.id}")["approved"]["off"]
