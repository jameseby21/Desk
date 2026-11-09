"""DESK-0192: popup notifications. Protects: a person hears about a new ticket or a change on a ticket they hold a role on (James and Patch
hear about all of them) and never about someone else's; the popup names who did it; at most three show; mute hides everything; the
system notification waits for one click on Allow."""

import json

import pytest
from browser_harness import browser, drive
from fastapi.testclient import TestClient

from desk import identity, notify, ops, people
from desk.ui.app import create_app


_APPS: dict = {}


def _client(store, who=None):
    """A browser of its own on the one app for this store (one app means one feed, as in the real server)."""
    app = _APPS.setdefault(str(store.home), create_app(store.home))
    c = TestClient(app)
    if who:
        c.cookies.set("desk_who", who)
    return c


def _as(name, kind="thread", as_=""):
    return identity.CURRENT.set(identity.Actor(name, kind, "", "cli", "claimed", False, as_=as_))


def _new(store, title, actor="chaz", **meta):
    tok = _as(actor)
    try:
        t = ops.new(store, title, "desk")
        t.meta.update(meta)
        store.save(t)
    finally:
        identity.CURRENT.reset(tok)
    return t


def _change(store, tid, actor, **meta):
    tok = _as(actor)
    try:
        t = store.get(tid)
        t.meta.update(meta)
        store.save(t)
    finally:
        identity.CURRENT.reset(tok)


def _poll(c, since):
    return c.get("/notify/changes", params={"since": since} if since is not None else {}).json()


def test_first_look_only_says_where_the_feed_is_then_a_new_ticket_reaches_its_assignee(store):
    em, bri = _client(store, "em"), _client(store, "bri")
    base = _poll(em, None)
    assert base["items"] == [] and _poll(bri, None)["cursor"] == base["cursor"]
    t = _new(store, "Approve the logo", assignee="em", owner="main")
    got = _poll(em, base["cursor"])
    assert [(i["id"], i["who"], i["what"], i["href"]) for i in got["items"]] == [(t.id, "Chaz", "made this ticket", f"/ticket/{t.id}")]
    assert got["items"][0]["title"] == "Approve the logo"
    assert _poll(em, got["cursor"])["items"] == []  # nothing new since the last look


def test_a_ticket_the_viewer_holds_no_role_on_stays_quiet_unless_the_viewer_is_james_or_patch(store):
    clients = {n: _client(store, k) for n, k in (("em", "em"), ("bri", "bri"), ("patch", "main"), ("james", None), ("assoc", "joe"), ("owner", "lily"))}
    cursor = _poll(clients["em"], None)["cursor"]
    _new(store, "Fix the date", assignee="em", owner="lily", associated=["joe"])
    seen = {n: [i["id"] for i in _poll(c, cursor)["items"]] for n, c in clients.items()}
    assert seen["bri"] == []
    assert all(seen[n] == ["DESK-0001"] for n in ("em", "assoc", "owner", "patch", "james")), seen


def test_status_assignee_owner_decision_and_comment_changes_each_show_with_who_did_it(store):
    t = _new(store, "Fix the date", assignee="em", owner="main")
    em = _client(store, "em")
    c0 = _poll(em, None)["cursor"]
    _change(store, t.id, "bigd", status="triage")
    _poll(_client(store), c0)  # Desk looks after each change, as an open page does every few seconds
    _change(store, t.id, "main", assignee="joe")  # em is no longer the assignee and still hears it: a role held before the change counts
    _poll(_client(store), c0)
    _change(store, t.id, "james", owner="em")
    got = _poll(em, c0)
    assert [(i["who"], i["what"]) for i in got["items"]] == [("Big D", "moved to triage"), ("Patch", "assigned it"), ("James", "changed the owner")]
    tk = store.get(t.id)
    tk.append_line("Comments", "- 2026-11-09 chaz: hello")
    tk.append_line("Decision", "- 2026-11-09 james · approved · \"fine\"")
    tok = _as("chaz")
    try:
        store.save(tk)
    finally:
        identity.CURRENT.reset(tok)
    last = _poll(em, got["cursor"])["items"]
    assert len(last) == 1 and last[0]["who"] == "Chaz" and last[0]["what"] == "recorded a decision, added a comment"  # one line per ticket change


def test_the_popup_names_the_actor_by_the_identity_rule(store):
    pe = people.load(store.home)
    assert notify.who_text({"actor": "chaz", "kind": "thread"}, pe) == "Chaz"
    assert notify.who_text({"actor": "main", "kind": "session"}, pe) == "Patch"
    assert notify.who_text({"actor": "james", "kind": "person", "as": "chaz"}, pe) == "James (as Chaz)"
    assert notify.who_text({"actor": "code:jre", "kind": "session"}, pe) == "jre session"
    assert notify.who_text({"actor": "subagent:ab12", "kind": "subagent"}, pe) == "A Claude Code subagent"
    assert notify.who_text({"actor": "claude-code", "kind": "session"}, pe) == "A Claude Code session"
    assert notify.who_text(None, pe) == "Someone"


def test_a_person_is_not_told_about_their_own_change(store):
    t = _new(store, "Fix the date", assignee="em", owner="main")
    em = _client(store, "em")
    c0 = _poll(em, None)["cursor"]
    _change(store, t.id, "em", status="triage")
    assert _poll(em, c0)["items"] == []
    assert [i["what"] for i in _poll(_client(store, None), c0)["items"]] == ["moved to triage"]


def test_a_saved_ticket_with_no_watched_change_makes_no_popup(store):
    t = _new(store, "Fix the date", assignee="em", owner="main")
    james = _client(store)
    c0 = _poll(james, None)["cursor"]
    _change(store, t.id, "chaz", size="m")  # size is not one of the watched fields
    assert _poll(james, c0)["items"] == []


# -- the page: popups, mute and the system notification, in a real browser ----------------------------------------------------------

ITEM = {"id": "DESK-0100", "title": "Fix the date", "who": "Chaz", "what": "moved to review", "href": "/ticket/DESK-0100", "new": False}
HEAD = "<script>addEventListener('load',function(){if(window.top!==window)return;var sleep=function(ms){return new Promise(function(r){setTimeout(r,ms);});};"
SYSTEM_STUB = """
 window.__raised=[];window.Notification=function(t,o){window.__raised.push({t:t,b:o&&o.body});};
 window.Notification.permission='default';
 window.Notification.requestPermission=function(cb){window.__asked=(window.__asked||0)+1;window.Notification.permission='granted';if(cb)cb('granted');return Promise.resolve('granted');};
 document.hasFocus=function(){return false;};"""


def canned(batches):
    """Stand in for /notify/changes: the first look answers {cursor: 0}; every later look answers the next batch of items."""
    calls = {"n": 0}

    def intercept(scope):
        if scope["path"] != "/notify/changes":
            return None
        calls["n"] += 1
        first = b"since" not in scope["query_string"]
        items = [] if first else (batches.pop(0) if batches else [])
        body = json.dumps({"cursor": 0 if first else calls["n"], "items": items}).encode()

        async def handler(receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})

        return handler

    return intercept


def _items(n, start=1):
    return [{**ITEM, "id": f"DESK-{start + i:04d}", "seq": start + i, "title": f"Ticket {start + i}"} for i in range(n)]


def _run(store, script, batches, width=1400):
    return drive(store, HEAD + script + "});</script>", "/", intercept=canned(batches), width=width)


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_popup_shows_title_who_what_and_link_and_dismisses(store):
    got = _run(store, """(async function(){
 await sleep(400);await DeskNotify.poll();await DeskNotify.poll();await sleep(200);
 var t=document.querySelector('#toasts .toast');var out={n:document.querySelectorAll('#toasts .toast').length};
 out.text=t.textContent;out.href=t.querySelector('a').getAttribute('href');
 t.querySelector('.toast-x').click();out.after=document.querySelectorAll('#toasts .toast').length;
 fetch('/m',{method:'POST',body:JSON.stringify(out)});})();""", [_items(1, 100)])
    assert got["n"] == 1 and got["href"] == "/ticket/DESK-0100", got
    assert "DESK-0100" in got["text"] and "Ticket 100" in got["text"] and "Chaz moved to review" in got["text"], got
    assert got["after"] == 0, got


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_at_most_three_show_and_older_ones_fold_into_a_count(store):
    got = _run(store, """(async function(){
 await sleep(400);await DeskNotify.poll();await DeskNotify.poll();await sleep(200);
 var out={cards:document.querySelectorAll('#toasts .toast:not(.fold)').length,fold:(document.querySelector('#toasts .fold')||{textContent:''}).textContent,
          small:Array.prototype.filter.call(document.querySelectorAll('#toasts *, .notify-ctl *'),function(e){return parseFloat(getComputedStyle(e).fontSize)<12;}).length};
 fetch('/m',{method:'POST',body:JSON.stringify(out)});})();""", [_items(5)])
    assert got["cards"] == 3 and got["fold"].startswith("2 earlier changes"), got
    assert got["small"] == 0, got


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_mute_hides_every_popup_and_every_system_notification_and_is_remembered(store):
    got = _run(store, SYSTEM_STUB + """(async function(){
 await sleep(400);var out={};
 localStorage.setItem('desk-notify-system','1');
 document.getElementById('notify-mute').click();
 out.label=document.getElementById('notify-mute').textContent;out.stored=localStorage.getItem('desk-notify-mute');
 await DeskNotify.poll();await DeskNotify.poll();await sleep(200);
 out.shown=document.querySelectorAll('#toasts .toast').length;out.raised=window.__raised.length;
 fetch('/m',{method:'POST',body:JSON.stringify(out)});})();""", [_items(2)])
    assert got["label"] == "Unmute" and got["stored"] == "1", got
    assert got["shown"] == 0 and got["raised"] == 0, got


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_system_notification_is_off_until_one_click_on_allow_then_raised_with_the_same_words(store):
    got = _run(store, SYSTEM_STUB + """(async function(){
 await sleep(400);var out={before:document.getElementById('notify-allow').textContent};
 await DeskNotify.poll();await sleep(200);
 out.raisedBeforeAllow=window.__raised.length;out.popups=document.querySelectorAll('#toasts .toast').length;
 document.getElementById('notify-allow').click();await sleep(200);
 out.asked=window.__asked||0;out.after=document.getElementById('notify-allow').textContent;out.stored=localStorage.getItem('desk-notify-system');
 await DeskNotify.poll();await sleep(200);
 out.raised=window.__raised;
 fetch('/m',{method:'POST',body:JSON.stringify(out)});})();""", [_items(1, 1), _items(1, 2)])
    assert got["before"] == "Notify me" and got["raisedBeforeAllow"] == 0 and got["popups"] == 1, got
    assert got["asked"] == 1 and got["after"] == "Alerts on" and got["stored"] == "1", got
    assert got["raised"] == [{"t": "DESK-0002 Ticket 2", "b": "Chaz moved to review"}], got
