"""DESK-0123 (Chaz R7): every post ends in a visible Posted or a visible failure that keeps the draft.

Success: "Posted" in a polite live region, the new comment inserted and highlighted for 2 s, the box cleared.
Failure (non-2xx, or no answer in 10 s): "Couldn't post: Desk didn't answer. Your text is still here.", the draft kept, the button
reads Try again. Real server on a free port with a stub in front of it, headless Edge or Chrome (Playwright is not installed)."""

import pytest
from browser_harness import browser, drive

from desk import ops

SENTENCE = "Couldn't post: Desk didn't answer. Your text is still here."
HEAD = """<script>addEventListener('load',function(){if(!document.getElementById('composer'))return;
var out={},form=document.getElementById('composer'),ta=document.getElementById('comment-text'),btn=form.querySelector('.post-btn'),st=document.getElementById('post-status'),
sleep=function(ms){return new Promise(function(r){setTimeout(r,ms);});},
until=async function(f,ms){for(var i=0;i<ms/50&&!f();i++){await sleep(50);}return f();},
items=function(){return document.querySelectorAll('[data-comments] li').length;};
(async function(){
"""
TAIL = "\n fetch('/m',{method:'POST',body:JSON.stringify(out)});})();});</script>"


def _boom(scope):
    async def handler(receive, send):
        await send({"type": "http.response.start", "status": 500, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b'{"detail":"boom"}'})
    return handler if scope["path"].endswith("/comment") and scope["method"] == "POST" else None


def _lines(store, t):
    return [ln for ln in store.get(t.id).section("Comments").splitlines() if ln.strip()]


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_a_server_error_keeps_the_draft_and_offers_try_again(store):
    """Protects: a 500 shows the sentence, keeps the text, unlocks the box, and the button says Try again (and keeps saying it)."""
    t = ops.new(store, "Talk", "jre")
    script = HEAD + """
 ta.value='Keep this draft';btn.click();
 await until(function(){return /Couldn/.test(st.textContent);},4000);
 out.status=st.textContent.trim();out.draft=ta.value;out.label=btn.textContent.trim();out.unlocked=!ta.readOnly&&!btn.disabled;
 out.marked=form.classList.contains('failed');out.items=items();
 btn.click();await until(function(){return /Posting/.test(st.textContent)||/Couldn/.test(st.textContent);},2000);
 await until(function(){return !btn.disabled;},3000);out.again=st.textContent.trim();out.draftAfter=ta.value;""" + TAIL
    got = drive(store, script, f"/ticket/{t.id}", intercept=_boom)
    assert got["status"] == SENTENCE and got["draft"] == "Keep this draft" and got["label"] == "Try again"
    assert got["unlocked"] and got["marked"] and got["items"] == 0
    assert got["again"] == SENTENCE and got["draftAfter"] == "Keep this draft"
    assert not _lines(store, t)


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_a_three_second_answer_still_posts_then_highlights_for_two_seconds(store):
    """Protects: a slow 200 is success, not failure (the limit is 10 s). The comment is added, marked fresh for 2 s, and the box clears."""
    t = ops.new(store, "Talk", "jre")
    script = HEAD + """
 ta.value='Slow but sure';var t0=Date.now();btn.click();await sleep(1000);
 out.during=st.textContent.trim();out.duringLabel=btn.textContent.trim();
 await until(function(){return /Posted/.test(st.textContent);},9000);
 out.waited=Date.now()-t0;out.status=st.textContent.trim();out.cleared=ta.value==='';out.items=items();
 var li=document.querySelector('[data-comments] li:last-child');out.fresh=!!li&&li.classList.contains('fresh');out.text=li?li.textContent:'';
 out.polite=document.getElementById('post-status').getAttribute('aria-live');
 await sleep(2600);out.freshGone=!li.classList.contains('fresh');
 out.label=btn.textContent.trim();""" + TAIL
    got = drive(store, script, f"/ticket/{t.id}", intercept=lambda scope: 3.0 if scope["path"].endswith("/comment") else None, timeout=40)
    assert got["during"] == "Posting…" and got["duringLabel"] == "Posting…"
    assert got["waited"] >= 2800 and got["status"] == "Posted" and got["cleared"] and got["items"] == 1
    assert got["fresh"] and "Slow but sure" in got["text"] and got["freshGone"] and got["polite"] == "polite" and got["label"] == "Post"
    assert len(_lines(store, t)) == 1


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_no_answer_in_time_is_a_failure_and_try_again_does_not_double_post(store):
    """Protects: silence past the limit counts as failure with the draft kept; Desk did store the comment late, and Try again (same
    key) returns that comment instead of adding a second. The limit is data-timeout-ms (10000 in the page; 1000 here to keep the test short)."""
    t = ops.new(store, "Talk", "jre")
    seen = {"n": 0}

    def late_once(scope):
        if scope["path"].endswith("/comment") and scope["method"] == "POST":
            seen["n"] += 1
            return 2.5 if seen["n"] == 1 else None
        return None

    script = HEAD + """
 out.limit=form.getAttribute('data-timeout-ms');form.setAttribute('data-timeout-ms','1000');
 ta.value='Draft kept on silence';btn.click();
 await until(function(){return /Couldn/.test(st.textContent);},4000);
 out.status=st.textContent.trim();out.draft=ta.value;out.label=btn.textContent.trim();out.unlocked=!ta.readOnly&&!btn.disabled;
 await sleep(2500);   // Desk finishes the first request after we gave up
 btn.click();await until(function(){return /Posted/.test(st.textContent);},4000);
 out.after=st.textContent.trim();out.items=items();out.cleared=ta.value==='';""" + TAIL
    got = drive(store, script, f"/ticket/{t.id}", intercept=late_once, timeout=40)
    assert got["limit"] == "10000"
    assert got["status"] == SENTENCE and got["draft"] == "Draft kept on silence" and got["label"] == "Try again" and got["unlocked"]
    assert got["after"] == "Posted" and got["items"] == 1 and got["cleared"]
    assert len(_lines(store, t)) == 1 and seen["n"] == 2
