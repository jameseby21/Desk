"""DESK-0205: "Send answer" and "Approve" failed with 'Desk hit an error and did not finish' on James's real decision text.

Cause: both boxes have a submit button named `action`, and in a browser `form.action` then returns that button, not the address, so the
script posted to '/ticket/<id>/[object HTMLButtonElement]' and got 405. The Choose box (buttons named `option`) was fine, which is why the
older browser test passed. These tests post the exact text through a real browser and check the error state says what went wrong."""

import json
import re
from pathlib import Path

import pytest
from browser_harness import browser, drive
from fastapi.testclient import TestClient

from desk import ops
from desk.ui.app import create_app

# The words James typed (two paragraphs, straight double quotes, a double hyphen), from DESK-0010's Decision section.
JAMES = ('As of now, Desk will be completely free in terms of software. The "paid" version of Desk is thusly only a service that comes along '
         'with Desk, like maybe customization/maintenance from my end or something like that.\n\nRegardless though, MY SPECIFIC tickets will never '
         'see the light of day -- they are extremely private and should be such as default for any forker or cloner or user.')
STATIC = Path(__file__).resolve().parent.parent / "src" / "desk" / "ui" / "static"


def _question(store, kind="Answer"):
    t = ops.new(store, f"{kind}: public Desk, private tickets", "desk", "decision")
    t.set_section("Context", "Why this exists.")
    t.set_section("Report", "Options laid out.")
    t.meta["owner"] = "james"
    t.meta["status"] = "review"
    store.save(t)
    return t


def _script(select, button, text, wait="Decision recorded"):
    js = r"""addEventListener('load',function(){var out={xhr:[]},os=XMLHttpRequest.prototype.send,sleep=function(ms){return new Promise(function(r){setTimeout(r,ms);});};
XMLHttpRequest.prototype.send=function(){var x=this;x.addEventListener('loadend',function(){out.xhr.push([x.status,x.responseURL])});return os.apply(this,arguments)};
(async function(){try{
 var ta=document.querySelector(SEL);out.found=!!ta;if(ta){ta.value=TEXT;document.querySelector(BTN).click();
 for(var i=0;i<80&&!/WAIT/.test(document.body.textContent);i++){await sleep(100);}
await sleep(300);out.said=(document.body.textContent.match(/WAIT|did not finish[^.]*\./)||[''])[0];
 var d=document.querySelector('.dp.bad');out.failed=!!d;out.sentence=d?d.parentNode.textContent:'';var c=document.querySelector('.dp.bad button.copy');out.copy=c?c.getAttribute('data-copy'):'';}
}catch(e){out.err=String(e)}
fetch('/m',{method:'POST',body:JSON.stringify(out)});})();});""".replace("WAIT", wait)
    return "<script>var SEL=%s,BTN=%s,TEXT=%s;%s</script>" % (json.dumps(select), json.dumps(button), json.dumps(text), js)


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
@pytest.mark.parametrize("box", ["answer", "your_call"])
def test_james_text_goes_through_both_boxes_in_a_real_browser(store, box):
    """Protects: James's decision on DESK-0010 was lost twice because the form's own 'action' button hid its address from the script."""
    t = _question(store)
    sel, btn = (("#reply textarea[name=words]", "#reply button[name=action]") if box == "answer"
                else ("aside form textarea[name=words]", "aside form button[value=approve]"))
    got = drive(store, _script(sel, btn, JAMES), f"/ticket/{t.id}")
    assert got["found"] and not got.get("err") and not got["failed"], got
    assert all(s == 200 for s, _ in got["xhr"][:1]) and got["xhr"][0][1].endswith(f"/ticket/{t.id}/decide"), got
    done = store.get(t.id)
    assert done.status == "approved"
    decision = done.section("Decision")
    assert "completely free in terms of software" in decision and 'The "paid" version' in decision and "light of day -- they are extremely private" in decision


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_a_failed_post_says_what_failed_and_offers_a_copy_button(store):
    """Protects: the error state read only 'hit an error', so the bug could not be filed from it (DESK-0205, DESK-0082)."""
    t = _question(store)

    def refuse(scope):
        async def handler(receive, send):
            await send({"type": "http.response.start", "status": 405, "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": b'{"detail":"Method Not Allowed"}'})
        return handler if scope["path"].endswith("/decide") and scope["method"] == "POST" else None

    got = drive(store, _script("#reply textarea[name=words]", "#reply button[name=action]", "Yes, as written.", wait="did not finish"), f"/ticket/{t.id}", intercept=refuse)
    assert got["failed"] and "405" in got["sentence"] and "Method Not Allowed" in got["sentence"], got
    assert f"POST /ticket/{t.id}/decide" in got["copy"] and "405" in got["copy"] and "Method Not Allowed" in got["copy"], got
    assert store.get(t.id).status == "review"


def test_a_script_never_reads_form_action_as_a_property():
    """Protects: `form.action` is the address only when no field is named 'action'; use getAttribute('action') (DESK-0205)."""
    bad = [f"{p.name}:{n}" for p in STATIC.glob("*.js") if p.name != "mermaid.min.js"
           for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if re.search(r"\b(?:f|form)\.action\b", line)]
    assert not bad, bad


def test_an_unhandled_error_on_a_script_post_comes_back_as_plain_words(store):
    """Protects: the progress bar showed a bare 'hit an error' because a 500 came back as an HTML page; the script wants the exception's words."""
    app = create_app(store.home)

    @app.post("/boom")
    def boom():
        raise ValueError("kaboom 7")

    c = TestClient(app, raise_server_exceptions=False)
    r = c.post("/boom", headers={"Accept": "application/json"})
    assert r.status_code == 500 and "ValueError" in r.json()["error"] and "kaboom 7" in r.json()["error"]
    assert "Desk hit a bug" in c.post("/boom").text  # a plain page still gets the bug page
