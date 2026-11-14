"""DESK-0138: James can decide, approve, answer or provide from the ticket page. The reply box is drawn from the ticket's type and
the Options line of its Context; submitting calls the same code as `drew decide`, as james, and the ticket moves, even from ready."""

import pytest
from browser_harness import browser, drive
from fastapi.testclient import TestClient

from desk import asks, ops
from desk.cli import main
from desk.rules import RuleError
from desk.ui.app import create_app

JSON = {"Accept": "application/json"}
CHOOSE = "Options: A opt-in button per item, nothing automatic; B automatic links through a small server; C both: A free, B paid."


def _ask(store, title="Choose: news links", context=CHOOSE, owner="james", status="ready", project="jre"):
    t = ops.new(store, title, project, "decision")
    t.set_section("Context", "Why this exists.\n\n" + context)
    t.meta["owner"] = owner
    t.meta["status"] = status
    store.save(t)
    return t


def _post(c, t, **data):
    return c.post(f"/ticket/{t.id}/decide", data={"token": c.app.state.tokens.issue(), "reply": "1", "who": "james", **data}, headers=JSON)


def test_options_and_kind_are_read_from_the_ticket(store):
    t = _ask(store)
    a = asks.ask_of(t)
    assert a["kind"] == "choose" and [x[0] for x in a["options"]] == ["A", "B", "C"]
    assert a["options"][2][1] == "both: A free, B paid"
    assert asks.parse_options("Options: yes; new names.") == [("yes", "yes"), ("new names", "new names")]
    assert asks.parse_options("Options were: A one; B two.") == []  # history, not a question
    assert asks.ask_of(_ask(store, "Approve: the logo rule", "Plain context."))["kind"] == "approve"
    assert asks.ask_of(_ask(store, "Answer: keep the names", "Plain context."))["kind"] == "answer"
    assert asks.ask_of(_ask(store, "Provide: the domain", "Plain context."))["kind"] == "provide"
    assert asks.ask_of(_ask(store, "Choose: which", "No options line here."))["kind"] == "answer"  # nothing to choose between
    assert asks.ask_of(ops.new(store, "Build it", "jre", "feature")) is None


def test_a_decision_ticket_can_be_decided_from_ready_but_a_feature_cannot(store):
    """Protects: Patch had to move CONS-0046 to review first; `drew decide` refused 'ready -> approved'."""
    t = _ask(store)
    done = ops.decide(store, t, True, "Option C, the free report offers both.", actor="james")
    assert done.status == "approved" and "Option C" in store.get(t.id).section("Decision")
    f = ops.new(store, "Build it", "jre", "feature")
    f.meta["status"] = "ready"
    store.save(f)
    with pytest.raises(RuleError, match="not an allowed move"):
        ops.decide(store, store.get(f.id), True, "Ship it, please.", actor="james")


def test_drew_decide_works_from_ready_on_a_decision_ticket(store, capsys, monkeypatch):
    monkeypatch.setenv("DESK_ACTOR", "james")  # DESK-0261: `--as` must be the caller
    t = _ask(store)
    main(["--home", str(store.home), "decide", t.id, "--approve", "Option A, nothing automatic.", "--as", "james"])
    assert store.get(t.id).status == "approved"


def test_the_page_draws_the_box_for_each_kind(store):
    c = TestClient(create_app(store.home))
    page = c.get(f"/ticket/{_ask(store).id}").text
    assert 'id="reply"' in page and page.count('name="option" value="') >= 4  # three options plus the Enter key's own
    assert "<b>A</b> opt-in button per item" in page and 'name="words"' in page and "Your answer, as james" in page
    assert "Send answer" not in page
    ap = c.get(f"/ticket/{_ask(store, 'Approve: the rule', 'Plain.').id}").text
    assert ">Approve</button>" in ap and ">Send back</button>" in ap and 'id="reply"' in ap
    an = c.get(f"/ticket/{_ask(store, 'Answer: which name', 'Plain.').id}").text
    assert "Send answer" in an and "<textarea" in an
    pr = c.get(f"/ticket/{_ask(store, 'Provide: the domain', 'Plain.').id}").text
    assert "Send it" in pr and "never a password or key" in pr


def test_no_box_for_someone_elses_ticket_or_a_closed_one_or_a_feature(store):
    c = TestClient(create_app(store.home))
    assert 'id="reply"' not in c.get(f"/ticket/{_ask(store, owner='chaz').id}").text
    assert 'id="reply"' not in c.get(f"/ticket/{_ask(store, status="in_progress").id}").text
    assert 'id="reply"' not in c.get(f"/ticket/{ops.new(store, 'Build it', 'jre', 'feature').id}").text


def test_choosing_an_option_records_the_words_as_james_and_moves_the_ticket(store):
    """Protects: James could not answer CONS-0046 in Desk (his comment of 2026-11-08 04:37)."""
    t = _ask(store)
    r = _post(TestClient(create_app(store.home)), t, option="2", words="Free tier only at first.")
    assert r.status_code == 200 and r.json()["ok"], r.text
    got = store.get(t.id)
    line = got.section("Decision")
    assert got.status == "approved" and "· james · approved ·" in line
    assert "Chose C: both: A free, B paid. Free tier only at first." in line


def test_free_words_alone_also_answer(store):
    t = _ask(store)
    r = _post(TestClient(create_app(store.home)), t, words="None of these; ask the lawyer first.")
    assert r.status_code == 200 and "ask the lawyer first" in store.get(t.id).section("Decision")
    assert _post(TestClient(create_app(store.home)), _ask(store), words="").status_code == 422  # nothing picked, nothing said


def test_approve_and_send_back_for_an_approve_ask(store):
    c = TestClient(create_app(store.home))
    a = _ask(store, "Approve: the rule", "Plain.")
    assert _post(c, a, action="approve", words="Approved as written.").status_code == 200 and store.get(a.id).status == "approved"
    b = _ask(store, "Approve: the other rule", "Plain.")
    assert _post(c, b, action="changes", words="Send back: say who pays.").status_code == 200 and store.get(b.id).status == "changes_requested"


def test_a_non_owner_gets_403_and_nothing_is_recorded(store):
    t = _ask(store)
    c = TestClient(create_app(store.home))
    r = _post(c, t, option="0", who="chaz")
    assert r.status_code == 403 and "Only james" in r.json()["error"] and r.json()["token"]
    assert store.get(t.id).status == "ready" and not store.get(t.id).section("Decision").strip()
    other = _ask(store, owner="chaz")  # a ticket james does not hold
    assert _post(c, other, option="0").status_code == 403 and store.get(other.id).status == "ready"
    plain = c.post(f"/ticket/{t.id}/decide", data={"token": c.app.state.tokens.issue(), "reply": "1", "who": "chaz", "words": "Sure thing."})
    assert plain.status_code == 403


def test_a_secret_looking_value_is_refused_and_nothing_is_saved(store):
    """Protects: rule 80, reply boxes refuse secret-looking values."""
    t = _ask(store, "Provide: the key", "Plain.")
    c = TestClient(create_app(store.home))
    for secret in ("password = hunter2hunter2", "sk-abcdefghijklmnopqrstuvwxyz0123", "-----BEGIN RSA PRIVATE KEY-----"):
        r = _post(c, t, words=secret)
        assert r.status_code == 422 and "looks like" in r.json()["error"] and r.json()["token"], secret
    assert store.get(t.id).status == "ready" and not store.get(t.id).section("Decision").strip()
    ok = _post(c, t, words="The key lives in the password manager, entry jeby-api.")
    assert ok.status_code == 200


def test_the_plain_form_post_redirects_to_the_decision_anchor(store):
    t = _ask(store)
    c = TestClient(create_app(store.home))
    r = c.post(f"/ticket/{t.id}/decide", data={"token": c.app.state.tokens.issue(), "reply": "1", "who": "james", "option": "1"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("#decision") and store.get(t.id).status == "approved"


LIVE = """<script>addEventListener('load',function(){if(!document.getElementById('reply'))return;
var out={},sleep=function(ms){return new Promise(function(r){setTimeout(r,ms);});},y=function(){return Math.round(window.pageYOffset);};
(async function(){
 document.getElementById('decision').scrollIntoView();await sleep(200);out.start=y();
 var btns=document.querySelectorAll('#reply .opts button');out.options=btns.length;
 document.querySelector('#reply input[name=words]').value='Free tier first.';
 btns[2].click();
 for(var i=0;i<100&&!/Chose C/.test(document.getElementById('decision').textContent);i++){await sleep(100);}
 out.recorded=/Chose C/.test(document.getElementById('decision').textContent);out.after=y();
 out.chip=(document.body.textContent.match(/approved/g)||[]).length>0;out.boxGone=!document.getElementById('reply');
 fetch('/m',{method:'POST',body:JSON.stringify(out)});})();});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_choosing_in_a_real_browser_fills_decision_without_moving_the_page(store):
    """Protects: the page does not jump and the Decision section shows the words at once (how James validates CONS-0046)."""
    t = _ask(store)
    t.set_section("Context", "Why this exists.\n\n" + CHOOSE + "\n\n" + "\n\n".join("More context paragraph. " * 25 for _ in range(12)))
    t.set_section("Comments", "\n".join(f"- 2026-11-08 09:{i:02d} · chaz · Comment number {i} with enough words to take a line or two." for i in range(40)))  # the page goes on below the Decision
    store.save(t)
    got = drive(store, LIVE, f"/ticket/{t.id}")
    assert got["options"] == 3 and got["start"] > 200 and got["recorded"] and got["after"] == got["start"] and got["boxGone"], got
    assert "Chose C" in store.get(t.id).section("Decision") and store.get(t.id).status == "approved"
