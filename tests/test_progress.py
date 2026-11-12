"""DESK-0152: one app-wide progress component (static/progress.js). Every form names its steps; the bar advances on real events (the
request going out, the server working, the answer back), shows elapsed time after 2 s, and ends in a sentence a person reads."""

import re
from pathlib import Path

import pytest
from browser_harness import browser, drive, run_in_browser
from fastapi.testclient import TestClient

from desk import ops
from desk.ui.app import create_app

UI = Path(__file__).resolve().parent.parent / "src" / "desk" / "ui"
JS = (UI / "static" / "progress.js").read_text(encoding="utf-8")


def test_every_form_in_the_app_names_its_progress_steps():
    """Protects: a form added without the component (James: 'an app-wide rule'). Every <form in the templates, the Markdown renderer
    and the app's own HTML carries data-progress ('Sending,Saving,Done', or 'none' on the standalone error page)."""
    files = list((UI / "templates").glob("*.html")) + [UI / "markdown.py", UI / "app.py"]
    seen = 0
    for f in files:
        for m in re.finditer(r"<form\b[^>]*>", f.read_text(encoding="utf-8")):
            seen += 1
            assert "data-progress" in m.group(0), f"{f.name}: {m.group(0)[:90]}"
    assert seen >= 6


def test_every_page_with_a_form_loads_the_component(store):
    t = ops.new(store, "Pages", "jre")
    c = TestClient(create_app(store.home))
    for path in ("/settings", f"/ticket/{t.id}", "/board", "/"):
        assert '<script src="/static/progress.js?v=' in c.get(path).text, path


def test_the_component_has_steps_a_count_mode_elapsed_time_and_a_result_sentence():
    text = JS
    for needle in ("DEFAULT_STEPS", "Sending", "Saving", "Done", "Progress.prototype.count", "ELAPSED_AFTER_MS = 2000",
                   "Progress.prototype.done", "Progress.prototype.fail", "upload.onload", "role\", \"progressbar"):
        assert needle in text, needle


UNIT = """<script>addEventListener('load',function(){
var out={},host=document.getElementById('h'),sleep=function(ms){return new Promise(function(r){setTimeout(r,ms);});};
(async function(){
 var p=DeskProgress.start(host,{steps:['Sending','Saving','Done']});
 out.first=host.querySelector('.dp-step').textContent;out.firstStep=host.querySelector('.dp').getAttribute('data-step');
 var w0=parseFloat(host.querySelector('.dp-bar i').style.width);
 p.step('Saving');out.second=host.querySelector('.dp-step').textContent;var w1=parseFloat(host.querySelector('.dp-bar i').style.width);
 out.advances=w1>w0;out.noTimeYet=host.querySelector('.dp-time').textContent==='';
 await sleep(2700);out.timeAfter2s=host.querySelector('.dp-time').textContent;
 p.done('Posted.');
 out.doneLabel=host.querySelector('.dp-step').textContent;out.doneWidth=host.querySelector('.dp-bar i').style.width;out.sentence=host.querySelector('.dp-msg').textContent;
 out.okClass=host.querySelector('.dp').classList.contains('ok');out.timeCleared=host.querySelector('.dp-time').textContent==='';
 out.role=host.querySelector('.dp-bar').getAttribute('role');
 var q=DeskProgress.start(host,{});q.count(3,12,'Moving');out.count=host.querySelector('.dp-step').textContent;out.countWidth=host.querySelector('.dp-bar i').style.width;
 out.one=host.querySelectorAll('.dp').length;
 q.fail('Could not reach Desk. Nothing was changed. Try again.');out.failClass=host.querySelector('.dp').classList.contains('bad');out.failSentence=host.querySelector('.dp-msg').textContent;
 /* scroll restore after an auto-refresh */
 out.hasReload=typeof DeskProgress.reload==='function';
 fetch('/m',{method:'POST',body:JSON.stringify(out)});})();});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_the_component_in_a_real_browser():
    """Protects: steps named in words, a bar that moves with them, a count mode, elapsed time only after 2 s, one component per spot,
    and a plain sentence for success or failure."""
    css = (UI / "static" / "desk.css").read_text(encoding="utf-8")
    html = f"<!doctype html><meta charset=utf-8><style>{css}</style><body><div id=h></div><script>{JS}</script>{UNIT}"
    got = run_in_browser(html, 800)
    assert got["first"] == "Sending…" and got["firstStep"] == "Sending" and got["second"] == "Saving…" and got["advances"] and got["noTimeYet"], got
    assert re.fullmatch(r"· [2-3] s", got["timeAfter2s"]), got
    assert got["doneLabel"] == "Done" and got["doneWidth"] == "100%" and got["sentence"] == "Posted." and got["okClass"] and got["timeCleared"], got
    assert got["role"] == "progressbar" and got["count"] == "Moving 3 of 12" and got["countWidth"] == "25%" and got["one"] == 1, got
    assert got["failClass"] and got["failSentence"].startswith("Could not reach Desk") and got["hasReload"], got


RESTORE = """<script>addEventListener('load',function(){
 var out={y:Math.round(window.pageYOffset)};fetch('/m',{method:'POST',body:JSON.stringify(out)});});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_auto_refresh_comes_back_to_the_same_place():
    """Protects: DESK-0097 auto-refresh sent the page back to the top. The position is kept for the reload and restored on load."""
    pre = "<script>sessionStorage.setItem('desk-scroll:/page.html', '600');</script>"
    html = f"<!doctype html><meta charset=utf-8><body style='margin:0'>{pre}<div style='height:4000px'>tall</div><script>{JS}</script>{RESTORE}"
    got = run_in_browser(html, 800)
    assert got["y"] == 600, got


LIVE = """<script>addEventListener('load',function(){if(!document.getElementById('composer'))return;
var out={seen:[],times:[]},ta=document.getElementById('comment-text'),btn=document.querySelector('#composer .post-btn'),st=document.getElementById('post-status'),
sleep=function(ms){return new Promise(function(r){setTimeout(r,ms);});};
(async function(){
 ta.value='Progress test comment';btn.click();
 for(var i=0;i<140&&!/Posted/.test(st.textContent);i++){
  var d=document.querySelector('#composer .dp');if(d){var s=d.getAttribute('data-step');if(out.seen[out.seen.length-1]!==s)out.seen.push(s);
   var t=d.querySelector('.dp-time').textContent;if(t)out.times.push(t);}
  await sleep(50);}
 var d=document.querySelector('#composer .dp');out.final=d.getAttribute('data-step');out.okClass=d.classList.contains('ok');out.status=st.textContent.trim();
 fetch('/m',{method:'POST',body:JSON.stringify(out)});})();});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_a_comment_post_shows_sending_then_saving_then_done_with_elapsed_time(store):
    """Protects: the steps follow the real request (going out, then the server working, then the answer), and a post that takes more
    than 2 s shows how long it has been."""
    t = ops.new(store, "Talk", "jre")
    got = drive(store, LIVE, f"/ticket/{t.id}", intercept=lambda s: 2.6 if s["path"].endswith("/comment") and s["method"] == "POST" else None, timeout=40)
    assert got["seen"][:2] == ["Sending", "Saving"] and got["final"] == "done" and got["okClass"] and got["status"] == "Posted", got
    assert any(re.fullmatch(r"· [2-3] s", x) for x in got["times"]), got


FAIL = """<script>addEventListener('load',function(){if(!document.getElementById('composer'))return;
var out={},ta=document.getElementById('comment-text'),btn=document.querySelector('#composer .post-btn'),st=document.getElementById('post-status'),
sleep=function(ms){return new Promise(function(r){setTimeout(r,ms);});};
(async function(){
 ta.value='Keep me';btn.click();
 for(var i=0;i<80&&!/Couldn/.test(st.textContent);i++){await sleep(50);}
 var d=document.querySelector('#composer .dp');out.bad=d.classList.contains('bad');out.step=d.getAttribute('data-step');out.sentence=st.textContent.trim();out.draft=ta.value;
 out.barStopped=d.querySelector('.dp-time').textContent==='';
 fetch('/m',{method:'POST',body:JSON.stringify(out)});})();});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_a_failed_post_stops_the_bar_and_says_the_draft_is_kept(store):
    t = ops.new(store, "Talk", "jre")

    def boom(scope):
        async def handler(receive, send):
            await send({"type": "http.response.start", "status": 500, "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": b"{}"})
        return handler if scope["path"].endswith("/comment") and scope["method"] == "POST" else None

    got = drive(store, FAIL, f"/ticket/{t.id}", intercept=boom)
    assert got["bad"] and got["step"] == "failed" and "Your text is still here" in got["sentence"] and got["draft"] == "Keep me" and got["barStopped"], got
