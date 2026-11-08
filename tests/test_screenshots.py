"""DESK-0124 (Chaz R8): pasted or dropped screenshots become evidence. PNG, JPEG, WebP or GIF up to 10 MB, saved to
tickets/assets/<ID>/ with a timestamped name; a thumbnail with a remove button; an optional "Evidence for line N" written into both
the comment and the line; the same paste works in the Not accepted reason; other types are refused with a message naming the types."""

import base64
import re

import pytest
from browser_harness import browser, drive
from fastapi.testclient import TestClient

from desk import ops
from desk.ui.app import create_app

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000") + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32
JSON = {"Accept": "application/json"}
STAMPED = r"\d{4}-\d{2}-\d{2}-\d{4}-"
TYPES = "PNG, JPEG, WebP or GIF"


def _ticket(store, acc="- [ ] one\n- [ ] two\n- [ ] three"):
    t = ops.new(store, "Shots", "jre")
    t.set_section("Acceptance", acc)
    store.save(t)
    return t


def _up(c, t, name, data, ctype="image/png"):
    return c.post(f"/ticket/{t.id}/attach", data={"token": c.app.state.tokens.issue()}, files={"file": (name, data, ctype)}, headers=JSON)


def test_the_limit_is_ten_megabytes_by_default(store):
    assert store.max_attach_mb == 10
    assert "max_mb = 10" in (store.home / "desk.toml").read_text(encoding="utf-8")


@pytest.mark.parametrize("name,data,ctype,ext", [("shot.png", PNG, "image/png", "png"), ("x.jpg", JPEG, "image/jpeg", "jpg"),
                                                 ("x.gif", GIF, "image/gif", "gif"), ("x.webp", WEBP, "image/webp", "webp")])
def test_the_four_types_are_saved_with_a_timestamped_name(store, name, data, ctype, ext):
    t = _ticket(store)
    c = TestClient(create_app(store.home))
    r = _up(c, t, name, data, ctype)
    assert r.status_code == 200
    j = r.json()
    assert re.fullmatch(rf"{STAMPED}{name.split('.')[0]}\.{ext}", j["name"]) and j["rel"] == f"assets/{t.id}/{j['name']}"
    assert (store.ticket_assets_dir(t.id) / j["name"]).read_bytes() == data


def test_a_pasted_screenshot_with_a_generic_name_gets_the_word_screenshot(store):
    t = _ticket(store)
    c = TestClient(create_app(store.home))
    for generic in ("image.png", "blob.png", "screenshot.png"):
        assert re.fullmatch(rf"{STAMPED}screenshot(-\d+)?\.png", _up(c, t, generic, PNG).json()["name"]), generic


def test_twelve_megabytes_and_other_types_are_refused_in_words(store):
    t = _ticket(store)
    c = TestClient(create_app(store.home))
    big = _up(c, t, "big.png", PNG + b"\x00" * (12 * 1024 * 1024))
    assert big.status_code == 413 and "10 MB" in big.json()["error"] and TYPES in big.json()["error"]
    for name, data, ctype in (("notes.pdf", b"%PDF-1.4 hello", "application/pdf"), ("a.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml"),
                              ("fake.png", b"not really a png", "image/png")):
        r = _up(c, t, name, data, ctype)
        assert r.status_code == 415 and TYPES in r.json()["error"] and r.json()["token"], name
    assert not store.ticket_assets_dir(t.id).exists() or not list(store.ticket_assets_dir(t.id).iterdir())


def _remove(c, t, name):
    return c.post(f"/ticket/{t.id}/attach/remove", data={"token": c.app.state.tokens.issue(), "name": name}, headers=JSON)


def test_remove_deletes_the_file_and_its_attachments_line_but_not_a_used_one(store):
    t = _ticket(store)
    c = TestClient(create_app(store.home))
    name = _up(c, t, "a.png", PNG).json()["name"]
    assert name in store.get(t.id).section("Attachments")
    r = _remove(c, t, name)
    assert r.status_code == 200 and r.json()["token"] and not (store.ticket_assets_dir(t.id) / name).exists()
    assert name not in store.get(t.id).section("Attachments")
    kept = _up(c, t, "b.png", PNG).json()["name"]  # a screenshot a posted comment uses stays
    ops.comment(store, store.get(t.id), f"see ![b](assets/{t.id}/{kept})", actor="james")
    r = _remove(c, t, kept)
    assert r.status_code == 409 and "already used" in r.json()["error"] and (store.ticket_assets_dir(t.id) / kept).exists()
    for bad in ("../../desk.toml", "nope.png", ""):
        assert _remove(c, t, bad).status_code in (400, 404, 422)


def _comment(c, t, key, text="Look at this", **extra):
    return c.post(f"/ticket/{t.id}/comment", data={"token": key, "text": text, **extra}, headers=JSON)


def test_evidence_for_line_is_written_into_the_comment_and_the_line(store):
    """Protects: pick line 2 and post: the comment carries the image and "Evidence for line 2", the line gets `> Evidence:`, and the
    page shows a count of 1."""
    t = _ticket(store)
    c = TestClient(create_app(store.home))
    name = _up(c, t, "run.png", PNG).json()["name"]
    r = _comment(c, t, c.app.state.tokens.issue(), evidence=name, line="2")
    assert r.status_code == 200
    comment = ops.comment_lines(store.get(t.id))[0]
    assert f"](assets/{t.id}/{name})" in comment and "(Evidence for line 2)" in comment and "Look at this" in comment
    acc = store.get(t.id).sections["Acceptance"].split("\n")
    assert acc[:2] == ["- [ ] one", "- [ ] two"] and acc[2] == f"  > Evidence: assets/{t.id}/{name}" and acc[3] == "- [ ] three"
    page = c.get(f"/ticket/{t.id}").text.split("Acceptance (this")[1].split('id="done-means"')[0]
    assert page.count("1 screenshot<") == 1
    assert "1 screenshot" in r.json()["check_html"] and "0 of 3 done" in r.json()["summary_html"]


def test_without_a_line_only_the_comment_carries_the_screenshot(store):
    t = _ticket(store)
    c = TestClient(create_app(store.home))
    name = _up(c, t, "run.png", PNG).json()["name"]
    assert _comment(c, t, c.app.state.tokens.issue(), evidence=name).status_code == 200
    assert "Evidence for line" not in ops.comment_lines(store.get(t.id))[0] and "> Evidence" not in store.get(t.id).sections["Acceptance"]
    assert f"assets/{t.id}/{name}" in ops.comment_lines(store.get(t.id))[0]


def test_a_bad_line_or_an_unknown_file_is_refused_and_nothing_is_written(store):
    t = _ticket(store)
    c = TestClient(create_app(store.home))
    name = _up(c, t, "run.png", PNG).json()["name"]
    for extra in ({"evidence": name, "line": "9"}, {"evidence": name, "line": "0"}, {"evidence": name, "line": "x"}, {"evidence": "ghost.png"}, {"evidence": "../x.png"}):
        assert _comment(c, t, c.app.state.tokens.issue(), **extra).status_code == 422, extra
    assert not ops.comment_lines(store.get(t.id)) and "Evidence" not in store.get(t.id).sections["Acceptance"]


def test_the_same_paste_works_in_the_not_accepted_reason(store):
    t = _ticket(store)
    c = TestClient(create_app(store.home))
    name = _up(c, t, "proof.png", PNG).json()["name"]
    data = {"section": "Acceptance", "index": 0, "state": "!", "reason": "It is wrong, see the picture", "severity": "should", "evidence": [name]}
    r = c.post(f"/ticket/{t.id}/check", data={"token": c.app.state.tokens.issue(), **data}, headers=JSON)
    assert r.status_code == 200
    lines = store.get(t.id).sections["Acceptance"].split("\n")
    assert lines[1] == "  > Not accepted (should): It is wrong, see the picture" and lines[2] == f"  > Evidence: assets/{t.id}/{name}"
    assert "1 screenshot" in r.json()["html"]
    r = c.post(f"/ticket/{t.id}/check", data={"token": c.app.state.tokens.issue(), **data, "reason": "Still wrong", "evidence": ["ghost.png"]}, headers=JSON)
    assert r.status_code == 422


def test_page_has_the_thumbnail_strip_the_line_picker_and_the_script(store):
    t = _ticket(store)
    c = TestClient(create_app(store.home))
    form = re.search(r'<form[^>]*id="composer".*?</form>', c.get(f"/ticket/{t.id}").text, re.S).group(0)
    assert 'id="shots"' in form and '<select name="line"' in form and "Evidence for" in form
    assert re.findall(r"<option[^>]*>([^<]*)</option>", form) == ["No line", "Line 1: one", "Line 2: two", "Line 3: three"]
    js = c.get("/static/shots.js").text
    assert 'addEventListener("paste"' in js and 'addEventListener("drop"' in js


LIVE = """<script>addEventListener('load',function(){if(!document.getElementById('composer'))return;
var out={},form=document.getElementById('composer'),ta=document.getElementById('comment-text'),btn=form.querySelector('.post-btn'),st=document.getElementById('post-status'),
strip=document.getElementById('shots'),pick=form.querySelector('select[name=line]'),note=document.getElementById('attach-note'),
sleep=function(ms){return new Promise(function(r){setTimeout(r,ms);});},
until=async function(f,ms){for(var i=0;i<ms/50&&!f();i++){await sleep(50);}return f();},
file=function(b64,name,type){var bin=atob(b64),a=new Uint8Array(bin.length);for(var i=0;i<a.length;i++)a[i]=bin.charCodeAt(i);return new File([a],name,{type:type});},
paste=function(el,f){var dt=new DataTransfer();dt.items.add(f);el.dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}));},
drop=function(el,f){var dt=new DataTransfer();dt.items.add(f);el.dispatchEvent(new DragEvent('drop',{dataTransfer:dt,bubbles:true,cancelable:true}));},
shots=function(){return strip.querySelectorAll('.shot').length;},acc='[data-check-section=Acceptance]';
(async function(){
 var PNG='__PNG__';
 out.pickHiddenAtFirst=document.getElementById('evline').hidden;
 paste(ta,file(PNG,'image.png','image/png'));await until(function(){return shots()===1;},5000);
 out.thumb=shots();out.hasRemove=!!strip.querySelector('.shot-x');out.pickShown=!document.getElementById('evline').hidden;
 paste(ta,file(PNG,'second.png','image/png'));await until(function(){return shots()===2;},5000);out.two=shots();
 strip.querySelectorAll('.shot-x')[1].click();await until(function(){return shots()===1;},5000);out.afterRemove=shots();
 pick.value='2';ta.value='Evidence attached';btn.click();
 await until(function(){return /Posted/.test(st.textContent);},6000);
 out.status=st.textContent.trim();out.shotsCleared=shots()===0&&strip.hidden;out.pickHidden=document.getElementById('evline').hidden;
 var c=document.querySelector(acc+' li:nth-child(2) .ev-count');out.count=c?c.textContent.trim():'';
 out.commentImage=!!document.querySelector('[data-comments] a.thumb img');out.commentText=document.querySelector('[data-comments]').textContent;
 drop(ta,file(btoa('%PDF-1.4 hello'),'notes.pdf','application/pdf'));await until(function(){return note.classList.contains('err');},5000);
 out.refusal=note.textContent.trim();
 var li=document.querySelectorAll(acc+' li.task-list-item')[2];li.querySelector('[data-act=no]').click();
 var rt=li.querySelector('.nr-panel textarea');paste(rt,file(PNG,'proof.png','image/png'));
 await until(function(){return li.querySelectorAll('.nr-panel .shot').length===1;},5000);out.panelThumb=li.querySelectorAll('.nr-panel .shot').length;
 rt.value='The picture shows it is wrong';li.querySelector('.nr-panel button[value="!"]').click();
 await until(function(){return !!document.querySelector(acc+' .nr-reason');},6000);
 var r=document.querySelector(acc+' .nr-reason');out.reason=r?r.textContent:'';
 fetch('/m',{method:'POST',body:JSON.stringify(out)});})();});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_paste_pick_a_line_post_and_the_same_paste_in_the_reason_in_a_real_browser(store):
    """Protects the whole flow in a real page: paste, thumbnail, remove, pick line 2, post, evidence count on the line, a refused
    .pdf drop with its message, and a paste inside the Not accepted reason. Real server on a free port, headless browser."""
    t = _ticket(store)
    got = drive(store, LIVE.replace("__PNG__", base64.b64encode(PNG).decode()), f"/ticket/{t.id}", timeout=60)
    assert got["pickHiddenAtFirst"] and got["thumb"] == 1 and got["hasRemove"] and got["pickShown"] and got["two"] == 2 and got["afterRemove"] == 1
    assert got["status"] == "Posted" and got["shotsCleared"] and got["pickHidden"]
    assert got["count"] == "1 screenshot" and got["commentImage"] and "Evidence for line 2" in got["commentText"] and "Evidence attached" in got["commentText"]
    assert "PNG, JPEG, WebP or GIF" in got["refusal"]
    assert got["panelThumb"] == 1 and "The picture shows it is wrong" in got["reason"] and "1 screenshot" in got["reason"]
    acc = store.get(t.id).sections["Acceptance"].split("\n")
    assert acc[0] == "- [ ] one" and acc[1] == "- [ ] two" and acc[2].startswith("  > Evidence: assets/") and acc[3].startswith("- [!] three")
    assert acc[4] == "  > Not accepted (must fix): The picture shows it is wrong" and acc[5].startswith("  > Evidence: assets/")
    files = sorted(p.name for p in store.ticket_assets_dir(t.id).iterdir())
    assert len(files) == 2 and all(re.match(STAMPED, f) for f in files)  # the removed one is gone; the pasted-then-removed second is not on disk
    assert not any(f.endswith(".pdf") for f in files)
