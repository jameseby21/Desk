"""DESK-0286 in a real browser. Protects: Ctrl K, a `new:` sentence and Enter create an inbox ticket and open it with the Undo
parameters; a file dropped on a ticket page is attached and the page comes back with them; the /capture page fits a 390 px phone."""

from urllib.parse import parse_qs

import pytest
from browser_harness import browser, drive

from desk import ops

HEAD = """<script>addEventListener('load',function(){
var out={},sleep=function(ms){return new Promise(function(r){setTimeout(r,ms);});};
async function until(f,n){for(var i=0;i<(n||100)&&!f();i++){await sleep(100);}return f();}
function send(){fetch('/m',{method:'POST',body:JSON.stringify(out)});}
var loaded=new URL((performance.getEntriesByType('navigation')[0]||{}).name||location.href).search;/* the address as loaded: the Undo toast tidies undo= and msg= out of the bar */
(async function(){
 await sleep(300);
"""
TAIL = "})();});</script>"

PALETTE = HEAD + """
 if (loaded.indexOf('undo=') >= 0) { out.landed = location.pathname; out.query = loaded; send(); return; }
 document.dispatchEvent(new KeyboardEvent('keydown',{key:'k',ctrlKey:true,bubbles:true}));
 var inp=document.getElementById('pal-input');
 inp.value='new: try the brief page on the phone';inp.dispatchEvent(new Event('input',{bubbles:true}));
 out.row=await until(function(){var a=document.querySelector('#pal-list a.pal-create');return !!a&&a.classList.contains('sel');});
 out.mvp=!!document.getElementById('pal-mvp');
 inp.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
 await sleep(8000); out.stuck=document.getElementById('pal-status').textContent; send();
""" + TAIL

DROP = HEAD + """
 if (loaded.indexOf('undo=') >= 0) { out.landed = location.pathname; out.query = loaded; out.att = (document.getElementById('attachments')||document.body).textContent.indexOf('notes') >= 0; send(); return; }
 var dt=new DataTransfer(); dt.items.add(new File(['meeting notes'], 'notes.txt', {type:'text/plain'}));
 ['dragenter','dragover','drop'].forEach(function(t){document.querySelector('main').dispatchEvent(new DragEvent(t,{bubbles:true,cancelable:true,dataTransfer:dt}));});
 await sleep(8000); out.stuck=document.body.textContent.slice(-300); send();
""" + TAIL

PHONE = HEAD + """
 if (window !== window.top) return;  // the page inside the 390 px frame below runs this script too
 var f=document.createElement('iframe'); f.style.width='390px'; f.style.height='800px'; f.style.border='0'; f.src='/capture'; document.body.appendChild(f);
 await until(function(){try{return !!f.contentDocument.getElementById('cap-text');}catch(e){return false;}});
 await sleep(300);
 var d=f.contentDocument, i=d.getElementById('cap-text').getBoundingClientRect(), b=d.querySelector('#cap-form button').getBoundingClientRect();
 out.scroll=d.documentElement.scrollWidth; out.inner=f.contentWindow.innerWidth; out.inputRight=i.right; out.buttonRight=b.right; send();
""" + TAIL


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_palette_new_sentence_and_enter_opens_the_new_ticket_with_undo(store):
    """Acceptance 1 in a browser: Ctrl K, a new: sentence, Enter, and the new inbox ticket opens with undo and msg."""
    got = drive(store, PALETTE, "/")
    assert got.get("landed") == "/ticket/DESK-0001", got
    q = parse_qs(got["query"].lstrip("?"))
    assert q["undo"][0].startswith("b-") and "DESK-0001" in q["msg"][0]
    t = store.get("DESK-0001")
    assert t.status == "inbox" and t.meta["lane"] == "later" and t.meta["title"] == "Try the brief page on the phone"


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_a_file_dropped_on_a_ticket_page_is_attached(store):
    """Acceptance 3 in a browser: a non-image file dropped on a ticket page is attached and the page returns with undo."""
    t = ops.new(store, "Meeting follow-up", "desk")
    got = drive(store, DROP, f"/ticket/{t.id}")
    assert got.get("landed") == f"/ticket/{t.id}", got
    assert "undo=b-" in got["query"] and got["att"], got
    assert "notes.txt" in store.get(t.id).section("Attachments")


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_capture_page_fits_a_phone(store):
    """The /capture page the shortcut opens has no sideways scroll at 390 px."""
    got = drive(store, PHONE, "/capture")
    assert got["inner"] == 390 and got["scroll"] <= 390 and got["inputRight"] <= 390 and got["buttonRight"] <= 390, got
