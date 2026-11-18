"""DESK-0206: Esc closes the search palette. Protects: James opened it from the header search box, pressed Esc, and it stayed open
although its help line says "Esc closes". Cause: closing gave focus back to the box, and the box opens the palette when it gets focus."""

import pytest
from browser_harness import browser, drive

from desk import ops

SCRIPT = """<script>addEventListener('load',function(){
if(window.top!==window)return;
var out={},sleep=function(ms){return new Promise(function(r){setTimeout(r,ms);});},pal=function(){return document.getElementById('palette');},
 shown=function(){return !!pal()&&!pal().hidden;},key=function(el,k){el.dispatchEvent(new KeyboardEvent('keydown',{key:k,bubbles:true}));};
async function until(f,n){for(var i=0;i<(n||100)&&!f();i++){await sleep(100);}return f();}
(async function(){
 await sleep(300);var box=document.getElementById('search-open');
 /* 1: the header box, Esc in the input; the box gets its focus back and must not reopen the palette */
 box.focus();out.openedByBox=await until(shown);var inp=document.getElementById('pal-input');out.inputFocused=document.activeElement===inp;
 key(inp,'Escape');await sleep(400);out.closedFromInput=!shown();out.focusBackOnBox=document.activeElement===box;
 /* 2: clicking the box that already has focus opens it again */
 box.click();out.clickReopens=await until(shown);
 /* 3: Esc while focus is on a result link */
 inp.value='DESK-000';inp.dispatchEvent(new Event('input',{bubbles:true}));
 await until(function(){return document.querySelectorAll('#pal-list a.pal-item').length>=2;});
 var r=document.querySelector('#pal-list a.pal-item');r.focus();out.resultFocused=document.activeElement===r;var path=location.pathname,y=window.pageYOffset;
 key(r,'Escape');await sleep(400);out.closedFromResult=!shown();out.stayed=location.pathname===path&&window.pageYOffset===y;
 /* 4: the slash key opens from the page, Esc closes, focus goes back to where it was */
 box.blur();document.body.focus();key(document.body,'/');
out.slashOpens=await until(shown);
 key(document.getElementById('pal-input'),'Escape');await sleep(300);out.closedAfterSlash=!shown();
 /* 5: a click outside the box (on the dim backdrop) closes it */
 document.dispatchEvent(new KeyboardEvent('keydown',{key:'k',ctrlKey:true,bubbles:true}));out.ctrlK=await until(shown);
 pal().dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));await sleep(300);out.closedByClick=!shown();
 /* 6: a click inside the box does not close it */
 document.dispatchEvent(new KeyboardEvent('keydown',{key:'k',ctrlKey:true,bubbles:true}));await until(shown);
 document.getElementById('pal-input').dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));await sleep(200);out.insideStays=shown();
 key(document.getElementById('pal-input'),'Escape');
 fetch('/m',{method:'POST',body:JSON.stringify(out)});})();});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_esc_and_a_click_outside_close_the_palette_and_focus_comes_back(store):
    ops.new(store, "First thing", "desk")
    ops.new(store, "Second thing", "desk")
    got = drive(store, SCRIPT, "/board")
    assert got["openedByBox"] and got["inputFocused"], got
    assert got["closedFromInput"] and got["focusBackOnBox"], got
    assert got["clickReopens"], got
    assert got["resultFocused"] and got["closedFromResult"] and got["stayed"], got
    assert got["slashOpens"] and got["closedAfterSlash"], got
    assert got["ctrlK"] and got["closedByClick"] and got["insideStays"], got
