"""DESK-0122 (Chaz R6): Post is single-shot. One key per post, the server stores at most one comment per key, and sending a key
again returns the comment that is already there. In the page the first click disables the button, shows a spinner and "Posting…",
and locks the text."""

import re
import threading

import pytest
from browser_harness import browser, drive
from fastapi.testclient import TestClient

from desk import ops
from desk.ui.app import create_app

JSON = {"Accept": "application/json"}


def _ticket(store):
    return ops.new(store, "Talk", "jre")


def _post(c, t, key, text="Hello there", **extra):
    return c.post(f"/ticket/{t.id}/comment", data={"token": key, "text": text, **extra}, headers=JSON)


def _lines(store, t):
    return [ln for ln in store.get(t.id).section("Comments").splitlines() if ln.strip()]


def test_resending_a_key_returns_the_existing_comment(store):
    """Protects: a retry (the first answer was lost) neither stores a second comment nor fails."""
    t = _ticket(store)
    c = TestClient(create_app(store.home))
    key = c.app.state.tokens.issue()
    r1, r2 = _post(c, t, key), _post(c, t, key)
    assert r1.status_code == r2.status_code == 200
    j1, j2 = r1.json(), r2.json()
    assert j1["repeat"] is False and j2["repeat"] is True
    assert j1["line"] == j2["line"] and j1["index"] == j2["index"] == 0 and "Hello there" in j1["line"]
    assert len(_lines(store, t)) == 1
    assert j2["token"] and j2["token"] != key  # a fresh key for the next post


def test_three_requests_at_once_with_one_key_store_one_comment(store):
    t = _ticket(store)
    c = TestClient(create_app(store.home))
    key = c.app.state.tokens.issue()
    out = []
    ts = [threading.Thread(target=lambda: out.append(_post(c, t, key))) for _ in range(3)]
    [x.start() for x in ts]
    [x.join() for x in ts]
    assert len(out) == 3 and all(r.status_code == 200 for r in out)
    assert sorted(r.json()["repeat"] for r in out) == [False, True, True]
    assert len(_lines(store, t)) == 1


def test_a_failed_post_does_not_use_up_the_key(store):
    """Protects: Try again works. An empty comment is refused (422) and the same key is still good for the real text."""
    t = _ticket(store)
    c = TestClient(create_app(store.home))
    key = c.app.state.tokens.issue()
    bad = _post(c, t, key, text="   ")
    assert bad.status_code == 422 and bad.json()["error"] and not _lines(store, t)
    assert _post(c, t, key).status_code == 200 and len(_lines(store, t)) == 1


def test_an_unknown_or_foreign_key_is_refused_in_words(store):
    t, other = _ticket(store), _ticket(store)
    c = TestClient(create_app(store.home))
    key = c.app.state.tokens.issue()
    assert _post(c, t, key).status_code == 200
    r = c.post(f"/ticket/{other.id}/comment", data={"token": key, "text": "Hello there"}, headers=JSON)  # that key belongs to another ticket
    assert r.status_code == 409 and "reload" in r.json()["error"].lower() and not _lines(store, other)
    assert _post(c, t, "not-a-key").status_code == 409


def test_the_form_post_without_script_is_still_once_only(store):
    t = _ticket(store)
    c = TestClient(create_app(store.home))
    key = c.app.state.tokens.issue()
    assert c.post(f"/ticket/{t.id}/comment", data={"token": key, "text": "Plain"}, follow_redirects=False).status_code == 303
    assert c.post(f"/ticket/{t.id}/comment", data={"token": key, "text": "Plain"}).status_code == 409
    assert len(_lines(store, t)) == 1


def test_the_page_carries_one_key_and_the_posting_words(store):
    t = _ticket(store)
    c = TestClient(create_app(store.home))
    page = c.get(f"/ticket/{t.id}").text
    form = re.search(r'<form[^>]*id="composer".*?</form>', page, re.S).group(0)
    assert 'data-timeout-ms="10000"' in form and 'data-posting="Posting…"' in form
    assert re.search(r'name="token" value="[^"]+"', form) and ">Post</button>" in form
    assert 'id="post-status"' in form and 'aria-live="polite"' in form
    assert '<script src="/static/composer.js?v=' in page


LIVE = """<script>addEventListener('load',function(){if(!document.getElementById('composer'))return;window.__alive=1;
var out={},ta=document.getElementById('comment-text'),btn=document.querySelector('#composer .post-btn'),st=document.getElementById('post-status'),
sleep=function(ms){return new Promise(function(r){setTimeout(r,ms);});};
(async function(){
 ta.value='Three clicks, one comment';
 btn.click();btn.click();setTimeout(function(){btn.click();},100);btn.click();   // four clicks inside 300 ms
 out.label=btn.textContent.trim();out.disabled=btn.disabled||btn.getAttribute('aria-disabled')==='true';
 out.spinner=!!btn.querySelector('.spin');out.locked=ta.readOnly;out.statusWhilePosting=st.textContent.trim();
 for(var i=0;i<80&&!/Posted/.test(st.textContent);i++){await sleep(100);}
 out.statusAfter=st.textContent.trim();out.cleared=ta.value==='';out.unlocked=!ta.readOnly&&!btn.disabled;
 out.shown=(document.querySelector('[data-comments]').textContent.match(/Three clicks, one comment/g)||[]).length;
 out.sameDocument=window.__alive===1;
 fetch('/m',{method:'POST',body:JSON.stringify(out)});})();});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_four_clicks_in_300_ms_save_one_comment_in_a_real_browser(store):
    """Protects: the first click turns the button into a spinner reading Posting…, locks the text and ignores the rest; one comment is
    stored; then Posted shows, the comment appears, the box clears. Real server on a free port, headless browser (no Playwright here)."""
    t = _ticket(store)
    slow = lambda scope: 0.8 if scope["path"].endswith("/comment") else None  # noqa: E731  # the answer takes 0.8 s, so all clicks land while it is out
    got = drive(store, LIVE, f"/ticket/{t.id}", intercept=slow)
    assert got["label"] == "Posting…" and got["disabled"] and got["spinner"] and got["locked"] and got["statusWhilePosting"] == "Posting…"
    assert got["statusAfter"] == "Posted" and got["cleared"] and got["unlocked"] and got["shown"] == 1 and got["sameDocument"]
    assert len(_lines(store, t)) == 1 and "Three clicks, one comment" in _lines(store, t)[0]
