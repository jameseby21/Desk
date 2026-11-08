"""DESK-0176: the header is Desk, the navigation, a search box focused by the / key, and the user. The store path and the owner line moved
to Settings. Protects: the folder path James saw on every page. Nothing is lost: /settings shows the path, the owner and who Desk
thinks you are."""

import re

import pytest
from browser_harness import browser, drive
from fastapi.testclient import TestClient

from desk import ops
from desk.ui.app import create_app

PAGES = ["/", "/board", "/projects", "/about", "/settings", "/search?q=x"]


def _header(html):
    return re.search(r"<header>.*?</header>", html, re.S).group(0)


def test_the_header_has_no_store_path_or_owner_line(store):
    t = ops.new(store, "One", "desk")
    cl = TestClient(create_app(store.home))
    for url in PAGES + [f"/ticket/{t.id}"]:
        head = _header(cl.get(url).text)
        assert str(store.home) not in head and "owner james" not in head and "you:" not in head and 'class="home"' not in head, url
    head = _header(cl.get("/").text)
    assert "Today" in head and "Board" in head and "Projects" in head


def test_the_header_has_a_search_box_and_the_user(store):
    head = _header(TestClient(create_app(store.home)).get("/").text)
    assert 'id="search-open"' in head and 'placeholder="Find a ticket or project"' in head and re.search(r"<kbd[^>]*>/</kbd>", head)
    m = re.search(r'<a class="user" href="/settings"[^>]*>(.*?)</a>', head, re.S)
    assert m and "james" in m.group(1).lower() and 'class="uinit"' in m.group(1)


def test_settings_shows_the_store_path_the_owner_and_who_you_are(store):
    page = TestClient(create_app(store.home)).get("/settings").text
    body = page.split("</header>")[1]
    assert "This store" in body and str(store.home) in body
    assert re.search(r"Owner[^<]*</[^>]+>\s*<[^>]+>\s*james", body) or "owner james" in body.lower() or ">james<" in body
    assert "You are" in body or "you:" in body.lower()


def test_about_and_settings_are_still_reachable_from_every_page(store):
    page = TestClient(create_app(store.home)).get("/board").text
    assert 'href="/settings"' in page and 'href="/about"' in page


SLASH = """<script>addEventListener('load',function(){
var out={},sleep=function(ms){return new Promise(function(r){setTimeout(r,ms);});};
(async function(){
 await sleep(300);
 document.dispatchEvent(new KeyboardEvent('keydown',{key:'/',bubbles:true}));await sleep(200);
 var p=document.getElementById('palette');out.opened=!!p&&!p.hidden;out.focus=document.activeElement&&document.activeElement.id;
 document.getElementById('pal-input').dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
 var ta=document.createElement('textarea');document.body.appendChild(ta);ta.focus();
 ta.dispatchEvent(new KeyboardEvent('keydown',{key:'/',bubbles:true}));await sleep(100);out.typingSafe=document.getElementById('palette').hidden;
 fetch('/m',{method:'POST',body:JSON.stringify(out)});})();});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_the_slash_key_focuses_search_but_not_while_typing_in_a_field(store):
    ops.new(store, "One", "desk")
    got = drive(store, SLASH, "/board")
    assert got["opened"] and got["focus"] == "pal-input", got
    assert got["typingSafe"], got
