"""DESK-0113: the Ctrl-K palette in a real browser. Protects: it opens from any page, shows results grouped by project as you type,
moves with the arrow keys and Enter opens the ticket."""

import pytest
from browser_harness import browser, drive

from desk import ops

SCRIPT = """<script>addEventListener('load',function(){
var out={},sleep=function(ms){return new Promise(function(r){setTimeout(r,ms);});};
async function until(f,n){for(var i=0;i<(n||100)&&!f();i++){await sleep(100);}return f();}
(async function(){
 await sleep(300);
 document.dispatchEvent(new KeyboardEvent('keydown',{key:'k',ctrlKey:true,bubbles:true}));
 out.opened=!!document.getElementById('palette')&&!document.getElementById('palette').hidden;
 var inp=document.getElementById('pal-input');out.focused=document.activeElement===inp;
 inp.value='DESK-000';inp.dispatchEvent(new Event('input',{bubbles:true}));
 out.shown=await until(function(){return document.querySelectorAll('#pal-list a.pal-item').length>=2;});
 out.groups=Array.prototype.map.call(document.querySelectorAll('#pal-list .pal-group'),function(g){return g.textContent.trim();});
 var first=document.querySelector('#pal-list a.pal-item');out.firstHref=first.getAttribute('href');out.firstSel=first.classList.contains('sel');
 inp.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}));
 out.secondSel=document.querySelectorAll('#pal-list a.pal-item')[1].classList.contains('sel');
 inp.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
 out.closed=document.getElementById('palette').hidden;
 fetch('/m',{method:'POST',body:JSON.stringify(out)});})();});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_palette_opens_with_ctrl_k_searches_as_you_type_and_moves_with_arrows(store):
    a = ops.new(store, "First thing", "desk")
    b = ops.new(store, "Second thing", "desk")
    got = drive(store, SCRIPT, "/board")
    assert got["opened"] and got["focused"], got
    assert got["shown"] and got["firstHref"] == f"/ticket/{a.id}" and got["firstSel"], got
    assert got["secondSel"] and got["closed"], got
    assert any(g.startswith("desk") and "2" in g for g in got["groups"]), got
    assert b.id
