"""DESK-0189: the New ticket form. Protects: James can create a ticket from the browser; it is the same kind of file `drew new` writes
(next id, template sections), carries the four roles, passes validation, and a form that is not ready writes nothing."""

import re

from fastapi.testclient import TestClient

from desk import newform, rules
from desk.ui.app import create_app

GOOD = {"project": "desk", "title": "Approve the logo", "type": "task", "priority": "p1", "size": "xs", "owner": "main", "assignee": "chaz",
        "associated": ["em", "james"], "track": "Brand", "context": "James asked for a logo.", "outcome": "A logo is picked.",
        "acceptance": "One logo is chosen\n- Another line", "steps": "Open the folder and look"}


def _client(store):
    return TestClient(create_app(store.home))


def _token(c):
    return c.app.state.tokens.issue()


def test_form_renders_with_the_dropdowns_filled_from_the_store(store):
    page = _client(store).get("/new")
    assert page.status_code == 200
    for key in ("jre", "desk", "jeby-us"):  # project dropdown: the profiles
        assert f'<option value="{key}"' in page.text
    for key in ("james", "main", "chaz", "bigd", "code:jre"):  # people: James, Patch, coworkers, code sessions
        assert f'<option value="{key}"' in page.text
    assert 'name="assignee"' in page.text and 'name="associated"' in page.text and 'name="acceptance"' in page.text
    for t in ("feature", "bug", "decision"):
        assert f'<option value="{t}"' in page.text


def test_today_and_board_have_the_button(store):
    c = _client(store)
    for path in ("/", "/board"):
        assert re.search(r'<a class="btn ball" href="/new"[^>]*>New ticket</a>', c.get(path).text), path


def test_post_writes_a_valid_file_with_the_next_id_and_the_four_roles(store):
    c = _client(store)
    r = c.post("/new", data={**GOOD, "token": _token(c)}, follow_redirects=False)
    assert r.status_code == 303
    t = store.get("DESK-0001")
    assert r.headers["location"].startswith("/ticket/DESK-0001?done=new")
    assert t.path.name == "DESK-0001-approve-the-logo.md" and t.status == "inbox"
    assert (t.meta["creator"], t.meta["owner"], t.meta["assignee"], t.meta["associated"]) == ("james", "main", "chaz", ["em", "james"])
    assert t.meta["track"] == "Brand" and t.meta["priority"] == "p1" and t.meta["size"] == "xs" and t.meta["type"] == "task"
    assert t.section("Acceptance").strip() == "- [ ] One logo is chosen\n- [ ] Another line"
    assert t.section("How to validate").strip() == "- Open the folder and look" and t.section("Context").strip() == "James asked for a logo."
    assert "Commands" in t.sections and "Tests" in t.sections  # the template sections of `drew new`
    errors, _ = rules.validate_store(store)
    assert errors == []  # `drew validate`, unknown-to-the-validator fields included
    again = c.post("/new", data={**GOOD, "title": "Second one", "token": _token(c)}, follow_redirects=False)
    assert again.headers["location"].startswith("/ticket/DESK-0002")


def test_redirect_lands_on_the_ticket_and_says_it_was_created(store):
    c = _client(store)
    r = c.post("/new", data={**GOOD, "token": _token(c)}, follow_redirects=True)
    assert r.status_code == 200 and "DESK-0001" in r.text and "Approve the logo" in r.text and "Ticket created" in r.text


def test_creator_is_the_real_poster_whatever_an_old_act_as_cookie_says(store, monkeypatch):
    from desk import identity

    monkeypatch.setattr(identity, "os_user", lambda: "james")
    c = _client(store)
    c.cookies.set("desk_act_as", "chaz")
    from desk import people

    people.link_os_user(store.home, "james", "james")
    c.post("/new", data={**GOOD, "token": _token(c)}, follow_redirects=False)
    assert store.get("DESK-0001").meta["creator"] == "james"  # DESK-0261: the cookie is ignored


def test_missing_fields_say_so_in_plain_words_and_write_nothing(store):
    c = _client(store)
    before = sorted(p.name for p in store.ticket_paths())
    r = c.post("/new", data={"token": _token(c), "title": "", "project": "", "acceptance": "  \n "}, follow_redirects=False)
    assert r.status_code == 422
    assert "Give the ticket a title" in r.text and "Pick the project" in r.text and "at least one Acceptance line" in r.text
    assert "Fix the lines marked below" in r.text
    assert sorted(p.name for p in store.ticket_paths()) == before
    j = c.post("/new", data={"token": _token(c), "title": "Fix it", "project": "desk"}, headers={"accept": "application/json"})
    assert j.status_code == 422 and list(j.json()["errors"]) == ["acceptance"] and j.json()["token"]
    assert sorted(p.name for p in store.ticket_paths()) == before


def test_form_keeps_what_was_typed_when_it_is_refused(store):
    c = _client(store)
    r = c.post("/new", data={"token": _token(c), "title": "Fix the date", "project": "jre", "context": "Because it is wrong."})
    assert 'value="Fix the date"' in r.text and "Because it is wrong." in r.text and '<option value="jre" selected>' in r.text


def test_unknown_people_and_sprints_are_refused(store):
    errs = newform.check(store, {**GOOD, "assignee": "nobody", "associated": ["ghost"], "sprint": "sprint 99", "owner": "zed"})
    assert set(errs) == {"assignee", "associated", "sprint", "owner"}


def test_a_second_post_with_the_same_key_makes_no_second_ticket(store):
    c = _client(store)
    tok = _token(c)
    assert c.post("/new", data={**GOOD, "token": tok}, follow_redirects=False).status_code == 303
    r = c.post("/new", data={**GOOD, "token": tok}, follow_redirects=False)
    assert r.status_code == 409 and "already sent" in r.text
    assert [p.name for p in store.ticket_paths()] == ["DESK-0001-approve-the-logo.md"]


def test_json_answer_names_the_new_ticket(store):
    c = _client(store)
    j = c.post("/new", data={**GOOD, "token": _token(c)}, headers={"accept": "application/json"})
    body = j.json()
    assert j.status_code == 200 and body["ok"] and body["id"] == "DESK-0001" and body["url"].startswith("/ticket/DESK-0001")
    assert body["message"] == "Created DESK-0001: Approve the logo."


def test_no_validate_lines_keeps_the_template_hint(store):
    c = _client(store)
    c.post("/new", data={**GOOD, "steps": "", "token": _token(c)})
    assert "Open the folder" not in store.get("DESK-0001").section("How to validate")  # the template text stays


# -- in a real browser ---------------------------------------------------------------------------------------------------------

import pytest
from browser_harness import browser, drive

FLOW = """<script>addEventListener('load',function(){
if(window.top!==window)return;
var sleep=function(ms){return new Promise(function(r){setTimeout(r,ms);});};
async function until(f,n){for(var i=0;i<(n||100)&&!f();i++){await sleep(100);}return f();}
var out={};
(async function(){
 if(location.pathname==='/board'){  /* phone width: the form in a 390 px frame has no sideways scroll */
  var fr=document.createElement('iframe');fr.src='/new';fr.style.cssText='width:390px;height:900px;border:0';document.body.appendChild(fr);
  await until(function(){return fr.contentDocument&&fr.contentDocument.getElementById('newform');});await sleep(300);
  var d=fr.contentDocument.documentElement;out.phone={scrollW:d.scrollWidth,w:fr.contentWindow.innerWidth};
  out.smallText=Array.prototype.filter.call(fr.contentDocument.querySelectorAll('.nf-grid label, .nf-grid .muted, .ferr, .btn'),function(e){return parseFloat(fr.contentWindow.getComputedStyle(e).fontSize)<12;}).length;
  fetch('/m',{method:'POST',body:JSON.stringify(out)});return;}
 if(location.pathname==='/new'){
  await sleep(300);
  document.getElementById('nf-save').click();   /* empty: inline errors, nothing written */
  out.errShown=await until(function(){return document.getElementById('err-title').textContent.length>0;});
  out.errTitle=document.getElementById('err-title').textContent;out.errProject=document.getElementById('err-project').textContent;out.errAcc=document.getElementById('err-acceptance').textContent;
  out.msg=(document.querySelector('.dp-msg')||{}).textContent||'';
  out.formBox=!document.getElementById('form-error').hidden;
  document.getElementById('f-title').value='Fix the date on Today';document.getElementById('f-project').value='desk';
  document.getElementById('f-acceptance').value='The date is right';
  document.getElementById('nf-save').click();
  out.step=(document.querySelector('.dp')||{getAttribute:function(){return ''}}).getAttribute('data-step');
  sessionStorage.setItem('o',JSON.stringify(out));return;}
 if(location.pathname.indexOf('/ticket/')===0){var o=JSON.parse(sessionStorage.getItem('o')||'{}');o.landed=location.pathname;o.notice=Array.prototype.map.call(document.querySelectorAll('.notice'),function(n){return n.textContent;}).join(' | ');
  fetch('/m',{method:'POST',body:JSON.stringify(o)});}
})();});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_in_a_browser_errors_show_inline_then_save_lands_on_the_ticket(store):
    """Protects: the form posts in place with progress, writes the problem under the right box, and opens the new ticket on success."""
    got = drive(store, FLOW, "/new")
    assert got["errShown"] and "title" in got["errTitle"].lower() and "project" in got["errProject"].lower() and "Acceptance" in got["errAcc"], got
    assert got["formBox"] and got["msg"].startswith("Not saved"), got
    assert got["landed"].startswith("/ticket/DESK-0001") and "Ticket created" in got["notice"], got
    assert store.get("DESK-0001").meta["title"] == "Fix the date on Today"


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_form_fits_a_390_px_phone_with_no_text_under_12_px(store):
    got = drive(store, FLOW, "/board")
    assert got["phone"]["scrollW"] <= got["phone"]["w"], got
    assert got["smallText"] == 0, got
