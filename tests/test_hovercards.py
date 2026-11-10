"""DESK-0085: links everywhere. Ticket ids and document paths are links wherever a ticket or document shows them (dependencies and
Docs to update included), a hover card shows what is behind each, a document link carries Edit in VS Code, and nothing outside the
store opens."""

import pytest
from browser_harness import browser, drive
from fastapi.testclient import TestClient

from desk import ops
from desk.ui.app import create_app


@pytest.fixture
def linked(store):
    (store.home / "docs").mkdir()
    (store.home / "docs" / "EDITIONS.md").write_text("# Editions\n\nFree and paid editions of every product, and how they relate.\n\n## Free clients\nMore.\n", encoding="utf-8")
    (store.home / "standards" / "rules").mkdir(parents=True)
    (store.home / "standards" / "rules" / "90-test-first.md").write_text("# Test first\n\nTests come before the code.\n", encoding="utf-8")
    dep = ops.new(store, "The dependency", "jre")
    dep.set_section("Context", "Why the dependency exists: it unblocks the rest.\n\nSecond paragraph.")
    dep.set_section("Acceptance", "- [x] one\n- [ ] two")
    store.save(dep)
    t = ops.new(store, "The linker", "jre")
    t.meta["depends_on"] = [dep.id]
    t.meta["docs_to_update"] = ["docs/EDITIONS.md", "docs/NOT-THERE.md"]
    t.set_section("Context", f"See {dep.id}, docs/EDITIONS.md#free-clients and standards/rules/90-test-first.md for the rule.")
    store.save(t)
    return t, dep


def _c(store):
    return TestClient(create_app(store.home))


def test_ids_docs_and_standards_in_the_body_are_links_with_an_edit_link(store, linked):
    t, dep = linked
    page = _c(store).get(f"/ticket/{t.id}").text
    assert f'href="/ticket/{dep.id}"' in page and 'class="tid"' in page
    assert 'href="/doc/docs/EDITIONS.md#free-clients"' in page and 'href="/doc/standards/rules/90-test-first.md"' in page
    assert page.count('title="Edit in VS Code"') >= 2  # each document link carries the way into the editor


def test_waits_on_and_docs_to_update_in_the_sidebar_are_links(store, linked):
    t, dep = linked
    page = _c(store).get(f"/ticket/{t.id}").text
    side = page[page.index("<aside>"):]
    assert f'<a class="tid" href="/ticket/{dep.id}"' in side  # Waits on
    assert '<a class="docref" href="/doc/docs/EDITIONS.md">docs/EDITIONS.md</a>' in side and "vscode://file/" in side
    assert "<code>docs/NOT-THERE.md</code>" in side  # a path that does not exist stays plain


def test_ticket_card_has_status_title_summary_and_acceptance(store, linked):
    t, dep = linked
    r = _c(store).get(f"/card/ticket/{dep.id}")
    assert r.status_code == 200
    for needle in (dep.id, "Inbox", "jre", "The dependency", "Why the dependency exists", "1 of 2 accepted"):
        assert needle in r.text, needle
    assert "Second paragraph" not in r.text  # one line of summary, not the whole Context
    assert _c(store).get("/card/ticket/ZZZ-9999").status_code == 404


def test_doc_card_has_title_path_first_paragraph_and_both_ways_in(store, linked):
    r = _c(store).get("/card/doc/docs/EDITIONS.md")
    assert r.status_code == 200
    for needle in ("Editions", "docs/EDITIONS.md", "Free and paid editions of every product", 'href="/doc/docs/EDITIONS.md"', "Edit in VS Code", "vscode://file/"):
        assert needle in r.text, needle
    assert "More." not in r.text


@pytest.mark.parametrize("bad", ["../x.md", "docs/../../x.md", "nope/none.md", "jeby-private/a.md", "docs/EDITIONS.txt", ".desk/x.md"])
def test_cards_are_path_safe(store, linked, bad):
    assert _c(store).get("/card/doc/" + bad).status_code == 404


def test_cards_hide_what_the_profanity_filter_hides(store):
    t = ops.new(store, "Fucking mess", "jre")
    store.save(t)
    assert "Fucking" not in _c(store).get(f"/card/ticket/{t.id}").text


HOVER = """<script>addEventListener('load',function(){if(!document.querySelector('a.tid'))return;
var out={},sleep=function(ms){return new Promise(function(r){setTimeout(r,ms);});};
(async function(){
 var a=document.querySelector('.md a.tid'),d=document.querySelector('.md a.docref');
 a.dispatchEvent(new MouseEvent('mouseover',{bubbles:true}));
 for(var i=0;i<40&&!(document.getElementById('hcard')&&!document.getElementById('hcard').hidden);i++){await sleep(100);}
 var c=document.getElementById('hcard');out.shown=!!c&&!c.hidden;out.text=c?c.textContent:'';out.described=a.getAttribute('aria-describedby');
 a.dispatchEvent(new MouseEvent('mouseout',{bubbles:true}));
 for(var j=0;j<20&&!c.hidden;j++){await sleep(100);}out.hidden=c.hidden;
 d.dispatchEvent(new MouseEvent('mouseover',{bubbles:true}));
 for(var k=0;k<40&&(c.hidden||!/Editions/.test(c.textContent));k++){await sleep(100);}out.docText=c.textContent;
 document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));out.escaped=c.hidden;
 fetch('/m',{method:'POST',body:JSON.stringify(out)});})();});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_hovering_an_id_and_a_document_link_shows_a_card_in_a_real_browser(store, linked):
    t, dep = linked
    got = drive(store, HOVER, f"/ticket/{t.id}")
    assert got["shown"] and dep.id in got["text"] and "The dependency" in got["text"] and "Inbox" in got["text"]  # the style's one word for the state (DESK-0037) and got["described"] == "hcard", got
    assert got["hidden"] and "Editions" in got["docText"] and "Edit in VS Code" in got["docText"] and got["escaped"], got
