"""DESK-0154 (Chaz F1, F3, F5) and DESK-0155 (F2): one flag component for a masked comment, a Not accepted line and a rejected ticket,
the same in Plain, Dogs and Cat; a masked word keeps the word's own width, so Show and Hide move nothing and store nothing.

Server-rendered structure and the stylesheet always run; the layout measurements run in a headless Edge or Chrome when one is
installed (skipped otherwise)."""

import datetime as dt
import re

import pytest
from fastapi.testclient import TestClient

from browser_harness import browser, drive
from desk import ops, style as styles, words
from desk.ui.app import STATIC, create_app

WORD = "fucking"


def _flagged(store):
    """A ticket with a masked comment and a Not accepted line, and a rejected one with a Context."""
    t = ops.new(store, "Flags", "jre")
    t.set_section("Acceptance", "- [ ] one\n- [!] two  <!-- by james 2026-11-08T03:41 -->\n  > Not accepted (must fix): The README lists 12 commands; intake and board are missing, and the board command's flags are out of date.")
    store.save(t)
    ops.comment(store, store.get(t.id), f"This checkbox looks {WORD} wrong. Two boxes, right side.", actor="james")
    r = ops.new(store, "Duplicate", "jre")
    r.set_section("Context", "The body stays readable.")
    store.save(r)
    ops.reject(store, store.get(r.id), "Duplicate of DESK-0082; the fix there covers it.", actor="james")
    return t, store.get(r.id)


def _page(store, tid, style="plain"):
    styles.save(store.home, style, "drew")
    html = TestClient(create_app(store.home)).get(f"/ticket/{tid}").text
    return re.sub(r'((?:name="token" value|data-token)=")[^"]*', r"X", html)  # one-time keys differ on every request


def _states_as_plain(html, style):
    """Each style has its own word per state (DESK-0037: Dogs say Sniffed, Thrown, Fetching); F5 is about the flag component, so those
    words are read as the Plain ones before pages of two styles are compared."""
    for st, word in words.TABLES[style].items():
        if word != words.PLAIN[st]:
            html = re.sub(rf"\b{re.escape(word)}\b", words.PLAIN[st], html)
    return html


def test_masked_word_is_a_span_that_carries_the_word_only_encoded(store):
    """Protects F2: the page never carries the plain word; the mask span has the stars as its fallback and the word in data-w."""
    t, _ = _flagged(store)
    page = _page(store, t.id)
    assert WORD not in page and re.search(r'<span class="mask"[^>]*data-w="[A-Za-z0-9+/=]+"[^>]*>f\*{5}g</span>', page)
    assert WORD in store.get(t.id).path.read_text(encoding="utf-8")  # the file keeps the original text


def test_masked_comment_has_the_chip_one_reason_line_and_show(store):
    t, _ = _flagged(store)
    page = _page(store, t.id)
    li = re.search(r'<li class="flagged masked">.*?</li>', page, re.S).group(0)
    assert '<span class="flag">! Masked</span>' in li and "Hidden by the language filter: 1 word." in li
    assert 'data-act="mask-toggle"' in li and ">Show<" in li
    assert "/static/flags.js" in page and TestClient(create_app(store.home)).get("/static/flags.js").status_code == 200


def test_not_accepted_line_uses_the_flag_chip_with_the_severity_and_more_in_place(store):
    t, _ = _flagged(store)
    page = _page(store, t.id)
    r = re.search(r'<div class="nr-reason".*?</div>', page, re.S).group(0)
    assert '<span class="flag sev">Must fix</span>' in r and 'class="why"' in r and 'data-act="more"' in r
    assert '<p class="more" hidden>The README lists 12 commands' in r  # the full reason is already in the page; More only shows it


def test_rejected_ticket_note_sits_above_the_header_and_the_body_is_unchanged(store):
    _, rej = _flagged(store)
    page = _page(store, rej.id)
    note = re.search(r'<div class="rejected-note flagged".*?</div>', page, re.S).group(0)
    today = dt.date.today()
    assert "&#10005; Rejected" in note and "James" in note and f"{today.strftime('%b')} {today.day}" in note
    assert "Duplicate of DESK-0082; the fix there covers it." in note
    assert page.index('id="rejected-note"') < page.index("<h1")
    assert "The body stays readable." in page


def test_the_three_styles_differ_only_in_the_empty_state(store):
    """Protects F5: every flagged page is byte-identical in Plain, Dogs and Cat (once each style's own state words, DESK-0037, are read
    as Plain); a page with nothing flagged differs by the one node."""
    t, rej = _flagged(store)
    clean = ops.new(store, "Clean", "jre")
    for tid in (t.id, rej.id):
        pages = {s: _states_as_plain(_page(store, tid, s), s) for s in ("plain", "dogs", "cat")}
        assert pages["plain"] == pages["dogs"] == pages["cat"]
        assert "Nothing flagged" not in pages["plain"]
    pages = {s: _states_as_plain(_page(store, clean.id, s), s) for s in ("plain", "dogs", "cat")}
    node = re.compile(r'<span class="flag-empty" title="[^"]*">[^<]*</span>')
    assert len({node.search(p).group(0) for p in pages.values()}) == 3
    assert "Nothing flagged" in pages["plain"] and "Nothing to growl at" in pages["dogs"] and "Nothing hissed at" in pages["cat"]
    assert len({node.sub("", p) for p in pages.values()}) == 1


def test_stylesheet_has_the_one_red_the_rule_and_the_20px_line():
    css = (STATIC / "desk.css").read_text(encoding="utf-8")
    assert "#C21B24" in css and "border-left:3px solid #C21B24".replace(" ", "") in css.replace(" ", "")
    assert re.search(r"\.reason,\.nr-reason \.why\{[^}]*line-height:20px;height:20px;[^}]*text-overflow:ellipsis", css)
    assert re.search(r"\.mask\[data-ready\]\{color:transparent", css)


SCRIPT = """<script>addEventListener('load',function(){
var out={},sleep=function(ms){return new Promise(function(r){setTimeout(r,ms);});};
function snap(root){return Array.prototype.map.call(root.querySelectorAll('*'),function(e){var r=e.getBoundingClientRect();return [e.tagName,r.left,r.top,r.width,r.height].join(',');}).join('|');}
(async function(){
 await sleep(400);
 var box=document.querySelector('[data-comments]'),m=box.querySelector('.mask'),btn=box.querySelector('[data-act=mask-toggle]');
 out.ready=m.hasAttribute('data-ready');out.text=m.textContent;
 var before=snap(document.body),w0=m.getBoundingClientRect().width;
 btn.click();await sleep(100);out.shownClass=m.classList.contains('shown');out.afterShow=snap(document.body)===before;out.wShow=m.getBoundingClientRect().width-w0;
 btn.click();await sleep(100);out.afterHide=snap(document.body)===before;
 m.click();await sleep(100);out.afterWordClick=snap(document.body)===before&&m.classList.contains('shown');
 var r=document.querySelector('.nr-reason'),why=r.querySelector('.why'),more=r.querySelector('[data-act=more]'),y=window.scrollY,top=why.getBoundingClientRect().top;
 out.reasonH=why.getBoundingClientRect().height;out.blockH=r.getBoundingClientRect().height;out.ellipsis=why.scrollWidth>why.clientWidth;
 more.click();await sleep(100);
 out.moreOpen=!r.querySelector('.more').hidden;out.sameTop=why.getBoundingClientRect().top===top;out.sameScroll=window.scrollY===y;out.btn=more.textContent;
 var flag=document.querySelector('.nr-reason .flag'),cs=getComputedStyle(flag);out.flagBg=cs.backgroundColor;out.flagFg=cs.color;
 out.rule=getComputedStyle(r).borderLeftWidth+' '+getComputedStyle(r).borderLeftColor;
 fetch('/m',{method:'POST',body:JSON.stringify(out)});})();});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_in_a_real_browser_show_hide_and_more_move_nothing(store):
    """Protects F1 and F2: bounding boxes of every element are equal before Show, after Show and after Hide; the reason line is 20 px;
    More opens in place without scrolling or moving the line."""
    t, _ = _flagged(store)
    out = drive(store, SCRIPT, f"/ticket/{t.id}")
    assert out["ready"] and out["text"] == WORD
    assert out["shownClass"] and out["afterShow"] and out["afterHide"] and out["afterWordClick"] and out["wShow"] == 0
    assert abs(out["reasonH"] - 20) < 0.6 and out["blockH"] <= 21 and out["ellipsis"]
    assert out["moreOpen"] and out["sameTop"] and out["sameScroll"] and out["btn"] == "Less"
    assert out["flagBg"] == "rgb(194, 27, 36)" and out["flagFg"] == "rgb(255, 255, 255)" and out["rule"] == "3px rgb(194, 27, 36)"
    assert WORD in store.get(t.id).path.read_text(encoding="utf-8")  # nothing was stored
