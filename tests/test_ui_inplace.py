"""DESK-0141: no form moves the viewport. Every form posts in place (a fragment or a sentence, not a redirect) when the request asks
for JSON, and the no-JavaScript fallback redirects back to the same anchor. The browser test measures the scroll position."""

import re

import pytest
from browser_harness import browser, drive
from fastapi.testclient import TestClient

from desk import ops
from desk.ui.app import create_app

JSON = {"Accept": "application/json"}


def _client(store):
    return TestClient(create_app(store.home))


def _review(store, title="Rev"):
    t = ops.new(store, title, "jre")
    t.set_section("Report", "Did it.")
    t.meta["status"] = "review"
    store.save(t)
    return t


def _key(c):
    return c.app.state.tokens.issue()


def test_comment_without_script_redirects_to_the_comments_anchor(store):
    """Protects: the plain form post lands at #comments, not at the top of the page (James, DESK-0082)."""
    t = ops.new(store, "Talk", "jre")
    c = _client(store)
    r = c.post(f"/ticket/{t.id}/comment", data={"token": _key(c), "text": "Hello there"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("#comments") and "done=comment" in r.headers["location"]
    bad = c.post(f"/ticket/{t.id}/comment", data={"token": _key(c), "text": "  "}, follow_redirects=False)
    assert bad.headers["location"].endswith("#comments") and "error=" in bad.headers["location"]


def test_other_forms_without_script_redirect_to_their_anchor(store):
    t = _review(store)
    c = _client(store)
    r = c.post(f"/ticket/{t.id}/decide", data={"token": _key(c), "action": "approve", "words": "Ship it."}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("#decision")
    u = ops.new(store, "Move me", "jre")
    r = c.post(f"/ticket/{u.id}/move", data={"token": _key(c), "to": "triage", "reason": ""}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("#decision")
    v = ops.new(store, "Checks", "jre")
    v.set_section("Acceptance", "- [ ] one")
    store.save(v)
    r = c.post(f"/ticket/{v.id}/check", data={"token": _key(c), "section": "Acceptance", "index": 0, "state": "x"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("#acceptance")


def test_decide_move_settings_answer_in_json_not_a_redirect(store):
    """Protects: with the fetch header the server answers with a sentence and a status the page can show, never a 303."""
    t = _review(store)
    c = _client(store)
    r = c.post(f"/ticket/{t.id}/decide", data={"token": _key(c), "action": "approve", "words": "Ship it."}, headers=JSON, follow_redirects=False)
    assert r.status_code == 200 and r.json()["ok"] and "Decision recorded" in r.json()["message"]
    assert store.get(t.id).status == "approved"
    u = ops.new(store, "Move me", "jre")
    r = c.post(f"/ticket/{u.id}/move", data={"token": _key(c), "to": "triage", "reason": ""}, headers=JSON, follow_redirects=False)
    assert r.status_code == 200 and r.json()["ok"] and "triage" in r.json()["message"]
    r = c.post("/settings", data={"name": "plain", "dog": "drew", "profanity": "on", "autorefresh": "on"}, headers=JSON, follow_redirects=False)
    assert r.status_code == 200 and r.json()["ok"] and "Saved" in r.json()["message"]


def test_a_refusal_in_json_is_a_sentence_with_a_fresh_key(store):
    """Protects: a refused decision keeps the person's words and the form can be sent again (the spent key is replaced)."""
    t = _review(store)
    c = _client(store)
    r = c.post(f"/ticket/{t.id}/decide", data={"token": _key(c), "action": "approve", "words": "no"}, headers=JSON)
    j = r.json()
    assert r.status_code == 422 and "four characters" in j["error"] and j["token"]
    r2 = c.post(f"/ticket/{t.id}/decide", data={"token": j["token"], "action": "approve", "words": "Ship it, really."}, headers=JSON)
    assert r2.status_code == 200
    spent = c.post(f"/ticket/{t.id}/decide", data={"token": j["token"], "action": "approve", "words": "Again please."}, headers=JSON)
    assert spent.status_code == 409 and "already sent" in spent.json()["error"]


def test_attach_in_json_returns_the_attachment_list(store):
    t = ops.new(store, "Pics", "jre")
    c = _client(store)
    png = bytes.fromhex("89504e470d0a1a0a") + b"\0" * 32
    r = c.post(f"/ticket/{t.id}/attach", data={"token": _key(c)}, files={"file": ("shot.png", png, "image/png")}, headers=JSON, follow_redirects=False)
    j = r.json()
    assert r.status_code == 200 and j["message"] == "Image attached." and j["name"] in j["attachments_html"]


def test_every_page_loads_the_progress_component_and_keeps_its_anchors(store):
    t = ops.new(store, "Anchors", "jre")
    page = _client(store).get(f"/ticket/{t.id}").text
    assert '<script src="/static/progress.js">' in page
    for anchor in ("comments", "acceptance", "validate", "decision", "attachments"):
        assert f'id="{anchor}"' in page


LIVE = """<script>addEventListener('load',function(){if(!document.getElementById('composer'))return;
var out={},sleep=function(ms){return new Promise(function(r){setTimeout(r,ms);});},y=function(){return Math.round(window.pageYOffset);};
async function until(f,n){for(var i=0;i<(n||100)&&!f();i++){await sleep(100);}return f();}
(async function(){
 document.getElementById('comments').scrollIntoView();await sleep(200);
 out.start=y();out.tall=document.documentElement.scrollHeight-innerHeight;
 /* 1. a comment */
 document.getElementById('comment-text').value='Scroll test comment';document.querySelector('#composer .post-btn').click();
 out.posted=await until(function(){return /Posted/.test(document.getElementById('post-status').textContent);});
 out.afterComment=y();
 /* 2. a check box */
 var done=document.querySelector('#acceptance form.chk [data-act=done]');done.click();
 out.checked=await until(function(){return /1 of \\d+ done/.test((document.getElementById('chk-summary')||{}).textContent||'');});
 out.afterCheck=y();
 /* 3. the move form (the ticket changes status, the page is swapped in place) */
 var mv=document.querySelector('form[action$="/move"]');mv.querySelector('select[name=to]').value='triage';
 mv.querySelector('button[type=submit]').click();
 out.moved=await until(function(){return /Moved to triage/.test(document.body.textContent);});
 out.afterMove=y();out.sameDocument=!!document.getElementById('composer')&&window.__alive===1;
 out.draftKept=document.getElementById('comment-text').value==='';
 fetch('/m',{method:'POST',body:JSON.stringify(out)});})();});window.__alive=1;</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_posting_a_comment_a_check_and_a_move_keeps_the_scroll_position_in_a_real_browser(store):
    """Protects: James was sent to the top of the page after every submit. Measures pageYOffset before and after each form."""
    t = ops.new(store, "Tall page", "jre")
    t.set_section("Acceptance", "\n".join(f"- [ ] line {i} " + "word " * 30 for i in range(40)))
    t.set_section("Context", "\n\n".join("A paragraph of context. " * 20 for _ in range(10)))
    store.save(t)
    got = drive(store, LIVE, f"/ticket/{t.id}")
    assert got["tall"] > 400 and got["start"] > 200, got  # the page is tall and we are well down it
    assert got["posted"] and got["afterComment"] == got["start"], got
    assert got["checked"] and got["afterCheck"] == got["start"], got
    assert got["moved"] and got["afterMove"] == got["start"] and got["sameDocument"], got
    assert "Scroll test comment" in "".join(store.get(t.id).section("Comments").splitlines())
    assert store.get(t.id).status == "triage"
    assert re.search(r"x\]", store.get(t.id).section("Acceptance"))
