"""DESK-0208: subtickets. James (2026-11-09): "Subtickets like 'have Chaz create an icon for this feature' need to be handled and
appropriately managed as well -- such that they are perfectly tracked and assigned/updated, but they don't clutter project boards
because they're part of a bigger main ticket."

Protects: `parent:` validation (missing, itself, a loop, a second level, a main ticket done with a subticket open); the board, the
Projects page, the company rows and Today's counts leaving subtickets out; the main ticket's card and row rolling them up; the people's
own views (Needs me, In hand, search, the coworker entry, `drew list --assignee`) listing them as "part of <ID>: <title>"; `drew sub`;
the New ticket form's Part of field; the done rule; and the new parts at a 390 px phone width."""

import datetime as dt
import re

import pytest
from fastapi.testclient import TestClient

from desk import ops, outbox, subtickets
from desk.cli import main
from desk.rules import RuleError, validate_store
from desk.ui.app import create_app

MAIN = "Company rows on the board"
ICON = "Icon for the company rows"
INPUT = "Provide the company names"
TODAY = dt.date.today().isoformat()


def run(store, *argv):
    main(["--home", str(store.home), *argv])


def _set(t, **meta):
    t.meta.update(meta)
    t.write()
    return t


def _approve(t):
    t.append_line("Decision", f'- {TODAY} · james · approved · "ship it"')
    return _set(t, status="approved")


def _fx(store):
    """A Desk feature with two subtickets: Chaz's icon (ready, due Nov 13) and James's input (in progress); a plain ticket beside them."""
    m = _set(ops.new(store, MAIN, "desk", "feature", assignee="main", priority="p1"), status="in_progress", track="Desk UI v1")
    icon = _set(ops.sub(store, m.id, ICON, assignee="chaz"), status="ready", review_by="2026-11-13", track="Desk UI v1")
    ask = _set(ops.sub(store, m.id, INPUT, assignee="james", owner="james"), status="in_progress", track="Desk UI v1")
    plain = _set(ops.new(store, "Plain ready ticket", "desk", "task", assignee="main"), status="ready", track="Desk UI v1")
    return m, icon, ask, plain


# -- the field and the validator ------------------------------------------------------------------------------------------------------

def test_validate_errors_on_a_missing_parent_itself_and_a_loop_and_warns_on_two_levels(store):
    m, icon, ask, _plain = _fx(store)
    errors, warnings = validate_store(store)
    assert not [e for e in errors if "parent" in e], errors
    lost = _set(ops.new(store, "Lost piece", "desk", "task", assignee="chaz"), parent="DESK-9999")
    selfish = ops.new(store, "Names itself", "desk", "task", assignee="chaz")
    _set(selfish, parent=selfish.id)
    a = ops.new(store, "Loop A", "desk", "task", assignee="chaz")
    b = _set(ops.new(store, "Loop B", "desk", "task", assignee="chaz"), parent=a.id)
    _set(a, parent=b.id)
    deep = _set(ops.new(store, "Two levels down", "desk", "task", assignee="chaz"), parent=icon.id)
    errors, warnings = validate_store(store)
    assert f"{lost.id}: parent `DESK-9999` does not exist" in errors
    assert f"{selfish.id}: parent is the ticket itself; a subticket names its main ticket" in errors
    loops = [e for e in errors if "parent chain loops back" in e]
    assert len(loops) == 1 and (f"{a.id} → {b.id} → {a.id}" in loops[0] or f"{b.id} → {a.id} → {b.id}" in loops[0]), loops
    assert any(w.startswith(f"{deep.id}: its main ticket {icon.id} is itself part of {m.id}; one level is enough") for w in warnings), warnings
    with pytest.raises(ValueError, match="parent `DESK-9999` does not exist"):  # a save checks it too, like depends_on
        store.save(store.get(lost.id))


def test_validate_warns_when_a_main_ticket_is_done_or_approved_with_a_subticket_open(store):
    m, icon, ask, _plain = _fx(store)
    _approve(store.get(m.id))
    _, warnings = validate_store(store)
    assert f"{m.id}: is approved but 2 subtickets are still open ({icon.id} ready, {ask.id} in_progress)" in warnings
    _set(store.get(m.id), status="done")
    _, warnings = validate_store(store)
    assert any(w.startswith(f"{m.id}: is done but 2 subtickets are still open") for w in warnings)


def test_parent_sits_after_related_in_the_file(store):
    m, icon, _ask, _plain = _fx(store)
    t = store.get(icon.id)
    t.meta["related"] = [m.id]
    text = t.render()
    assert text.index("related:") < text.index("parent:") < text.index("docs_to_update:")
    assert f"parent: {m.id}\n" in text


# -- drew sub ---------------------------------------------------------------------------------------------------------------------------

def test_drew_sub_writes_a_subticket_like_drew_new(store, capsys):
    m = ops.new(store, MAIN, "desk", "feature", assignee="main", priority="p1")
    run(store, "sub", m.id, ICON, "--assignee", "chaz")
    out = capsys.readouterr().out.splitlines()
    sub = [t for t in store.tickets() if t.meta.get("title") == ICON][0]
    assert out[0].endswith(sub.path.name) and out[1] == f"{sub.id} is part of {m.id}: {MAIN}"
    assert (sub.meta["parent"], sub.project, sub.meta["type"], sub.meta["priority"], sub.meta["assignee"], sub.status) == (m.id, "desk", "task", "p1", "chaz", "inbox")
    assert sub.validate_steps()  # the same template as drew new
    run(store, "sub", m.id.lower(), "Licence check in jRE", "--project", "jre", "--type", "research", "--priority", "p3")
    other = [t for t in store.tickets() if t.meta.get("title") == "Licence check in jRE"][0]
    assert (other.project, other.meta["type"], other.meta["priority"], other.meta["parent"]) == ("jre", "research", "p3", m.id)


@pytest.mark.parametrize("which, words", [("missing", "There is no ticket DESK-9999 to be part of"), ("sub", "is itself part of"),
                                          ("done", "takes no new subtickets")])
def test_drew_sub_refuses_in_plain_words(store, capsys, which, words):
    m, icon, _ask, _plain = _fx(store)
    if which == "done":
        _set(store.get(m.id), status="done")
    target = {"missing": "DESK-9999", "sub": icon.id, "done": m.id}[which]
    before = len(store.tickets())
    with pytest.raises(SystemExit):
        run(store, "sub", target, "Another piece", "--assignee", "chaz")
    assert words in capsys.readouterr().err
    assert len(store.tickets()) == before  # nothing written


# -- the boards and the counts leave subtickets out ------------------------------------------------------------------------------------

def test_board_and_company_rows_show_main_tickets_only_with_the_roll_up(store):
    m, icon, ask, plain = _fx(store)
    c = TestClient(create_app(store.home))
    top = c.get("/board").text
    assert re.search(r"1 project · 2 open · 1 claimed · 0 sent for review", top), top  # the main ticket and the plain one; not the two subtickets
    page = c.get("/board?company=unsorted&open=*&g=*").text
    cards = re.findall(r'<div class="cc-title" title="([^"]*)"', page)
    assert MAIN in cards and "Plain ready ticket" in cards and ICON not in cards and INPUT not in cards
    assert "Subtasks: 0 of 2 done" in page and f"Next: {ICON} · Chaz" in page  # Chaz's icon is ready and due first
    frag = c.get("/board/group?project=desk&slug=desk-ui-v1").text
    titles = re.findall(r'<div class="cc-title" title="([^"]*)"', frag)
    assert sorted(titles) == sorted([MAIN, "Plain ready ticket"]) and "Subtasks: 0 of 2 done" in frag
    more = c.get("/board/column?project=desk&slug=desk-ui-v1&col=in_progress&offset=0&frag=1").text
    assert re.findall(r'<div class="cc-title" title="([^"]*)"', more) == [MAIN]
    chaz = c.get("/board?company=unsorted&assignee=chaz").text  # the board's own filter is over main tickets too
    assert ICON not in re.findall(r'<div class="cc-title" title="([^"]*)"', chaz)


def test_projects_page_and_its_counts_leave_subtickets_out(store):
    m, icon, ask, plain = _fx(store)
    c = TestClient(create_app(store.home))
    page = c.get("/projects").text
    ids = re.findall(r'<details class="tk" data-key="k" data-val="([^"]+)"', page)
    assert m.id in ids and plain.id in ids and icon.id not in ids and ask.id not in ids
    assert '<span class="sub-roll">Subtasks: 0 of 2 done</span>' in page
    assert re.search(r'<span class="counts">2 open ·', page), page
    assert icon.id not in c.get("/projects?assignee=chaz").text


def test_today_counts_leave_subtickets_out_but_its_rows_list_them_labelled(store):
    m, icon, ask, plain = _fx(store)
    c = TestClient(create_app(store.home))
    page = c.get("/").text
    label = f"part of {m.id}: {MAIN}"
    assert re.search(r'status%3Aready">1 ready</a>', page), page  # the plain ticket; the icon is ready too but is a subticket
    assert "All open (2)" in page
    assert re.search(r'<option value="desk-ui-v1"[^>]*>Desk UI v1 \(2\)</option>', page)  # the Track dropdown counts main tickets
    hand = page.split('id="in-hand"')[1].split("</section>")[0]
    assert m.id in hand and INPUT not in hand  # In hand lists the main ticket; the subticket James holds is drawn once, under Needs me (2026-11-10 verifier)
    nm = page.split('id="needs-me"')[1].split("</section>")[0]
    assert INPUT in nm and f'<span class="nm-part">{label}</span>' in nm  # Needs me: James's own input is an ask


# -- the people's own views -----------------------------------------------------------------------------------------------------------

def test_assignee_views_list_subtickets_with_their_main_ticket(store, capsys):
    m, icon, ask, plain = _fx(store)
    label = f"part of {m.id}: {MAIN}"
    run(store, "list", "--assignee", "chaz")
    out = capsys.readouterr().out
    assert icon.id in out and f"{ICON} ({label})" in out
    run(store, "needs-me")
    assert f"{INPUT} ({label})" in capsys.readouterr().out
    c = TestClient(create_app(store.home))
    hits = [h for g in c.get("/search.json?q=icon").json()["groups"] for h in g["hits"]]
    assert [h["part_of"] for h in hits if h["id"] == icon.id] == [label]
    found = c.get(f"/search?q=parent:{m.id}").text
    assert icon.id in found and ask.id in found and f'<div class="part-line">{label}</div>' in found and "Plain ready ticket" not in found
    ops.assign(store, store.get(icon.id), "chaz", actor="main")  # handed to Chaz: the coworker entry names it
    lines = outbox.collect(store, "chaz", dt.datetime.now() - dt.timedelta(days=1))
    mine = [ln for ln in lines if ln.ticket.id == icon.id]
    assert mine and label in mine[0].text() and mine[0].part == label


def test_roll_up_counts_done_leaves_let_go_out_and_picks_the_next_by_priority(store):
    m, icon, ask, _plain = _fx(store)
    third = _set(ops.sub(store, m.id, "Urgent copy fix", assignee="em"), status="ready", priority="p0")
    fam = subtickets.Family(store.tickets())
    r = fam.rollup(store.get(m.id))
    assert (r.text, r.open, r.next.id, r.next_who) == ("Subtasks: 0 of 3 done", 3, third.id, "em")
    _set(store.get(third.id), status="done")
    _set(store.get(ask.id), status="rejected")
    r = subtickets.Family(store.tickets()).rollup(store.get(m.id))
    assert (r.text, r.open, r.next.id) == ("Subtasks: 1 of 2 done", 1, icon.id)
    assert subtickets.Family(store.tickets()).rollup(store.get(icon.id)) is None


# -- the done rule ------------------------------------------------------------------------------------------------------------------------

def test_a_main_ticket_cannot_be_done_while_a_subticket_is_open(store, capsys):
    m, icon, ask, _plain = _fx(store)
    _approve(store.get(m.id))
    words = (f"{m.id} cannot be done while 2 of its subtickets are still open: {icon.id} (ready, Chaz), {ask.id} (in progress, James). "
             "Finish them or let them go first.")
    with pytest.raises(RuleError) as e:
        ops.done(store, store.get(m.id))
    assert str(e.value) == words
    with pytest.raises(SystemExit):
        run(store, "done", m.id)
    assert words in capsys.readouterr().err
    c = TestClient(create_app(store.home))
    page = c.get(f"/ticket/{m.id}").text
    tok = re.search(r'action="/ticket/[^"]+/move".*?name="token" value="([^"]+)"', page, re.S).group(1)
    r = c.post(f"/ticket/{m.id}/move", data={"to": "done", "token": tok}, headers={"Accept": "application/json"})
    assert r.status_code == 422 and r.json()["error"] == words
    assert store.get(m.id).status == "approved"
    _approve(store.get(icon.id))
    ops.done(store, store.get(icon.id))
    with pytest.raises(RuleError, match=rf"while its subticket is still open: {ask.id} \(in progress, James\)\. Finish it or let it go first\."):
        ops.done(store, store.get(m.id))
    ops.reject(store, store.get(ask.id), "Not needed after all", actor="james")
    assert ops.done(store, store.get(m.id)).status == "done"


# -- the ticket pages and the New ticket form ------------------------------------------------------------------------------------------

def test_main_ticket_lists_its_subtasks_and_a_subticket_says_what_it_is_part_of(store):
    m, icon, ask, plain = _fx(store)
    c = TestClient(create_app(store.home))
    page = c.get(f"/ticket/{m.id}").text
    box = page.split('id="subtasks"')[1].split('id="acceptance"')[0]
    assert "Subtasks: 0 of 2 done" in box and f'href="/new?parent={m.id}"' in box and ">New subticket<" in box
    rows = re.findall(r'<li class="sub-row[^"]*">(.*?)</li>', box, re.S)
    assert [re.search(r"<code>([^<]+)</code>", r).group(1) for r in rows] == [icon.id, ask.id]
    assert "Chaz" in rows[0] and "review by 2026-11-13" in rows[0] and "no review date" in rows[1] and "James" in rows[1]
    sub = c.get(f"/ticket/{icon.id}").text
    assert re.search(rf'<p class="part-of" id="part-of">Part of <a href="/ticket/{m.id}">{m.id}: {MAIN}</a>', sub)
    assert 'id="subtasks"' not in sub  # one level: a subticket has no Subtasks list
    lone = c.get(f"/ticket/{plain.id}").text  # no subtickets: no card (the page is no taller), the button sits in the facts on the right
    assert 'id="subtasks"' not in lone and 'id="part-of"' not in lone
    assert re.search(rf'<div id="subtasks-none"><span class="label">Subtasks</span><div>None yet\. <a class="btn small" id="new-sub" href="/new\?parent={plain.id}"', lone)


def test_new_ticket_form_has_part_of_and_the_button_fills_it(store):
    m, icon, _ask, _plain = _fx(store)
    c = TestClient(create_app(store.home))
    form = c.get(f"/new?parent={m.id.lower()}").text
    assert re.search(rf'name="parent" id="f-parent" value="{m.id}"', form)
    assert re.search(r'<option value="desk" selected>', form) and re.search(r'<option value="task" selected>', form)
    assert f"Part of {m.id}: {MAIN}." in form
    tok = re.search(r'name="token" value="([^"]+)"', form).group(1)
    base = {"title": "Sketch the empty state", "project": "desk", "type": "task", "priority": "p1", "size": "s", "owner": "main", "assignee": "chaz",
            "acceptance": "The sketch is attached"}
    r = c.post("/new", data={**base, "token": tok, "parent": "DESK-9999"}, headers={"Accept": "application/json"})
    assert r.status_code == 422 and r.json()["errors"]["parent"].startswith("There is no ticket DESK-9999 to be part of")
    r = c.post("/new", data={**base, "token": r.json()["token"], "parent": icon.id}, headers={"Accept": "application/json"})
    assert r.status_code == 422 and "is itself part of" in r.json()["errors"]["parent"]
    r = c.post("/new", data={**base, "token": r.json()["token"], "parent": m.id}, headers={"Accept": "application/json"})
    assert r.status_code == 200, r.text
    made = store.get(r.json()["id"])
    assert made.meta["parent"] == m.id and made.meta["assignee"] == "chaz"
    assert "Subtasks: 0 of 3 done" in c.get(f"/ticket/{m.id}").text


# -- a real browser at phone width ---------------------------------------------------------------------------------------------------
from browser_harness import browser, drive  # noqa: E402

PHONE = """<script>addEventListener('load',function(){
if(window.top!==window)return;
var sleep=function(ms){return new Promise(function(r){setTimeout(r,ms);});},out={pages:[]};
(async function(){
 var urls=%s;
 for(var u=0;u<urls.length;u++){
  var fr=document.createElement('iframe');fr.src=urls[u][0];fr.style.cssText='width:390px;height:1400px;border:0';document.body.appendChild(fr);
  for(var i=0;i<100&&!(fr.contentDocument&&fr.contentDocument.querySelector(urls[u][1]));i++){await sleep(100);}await sleep(300);
  var d=fr.contentDocument,w=fr.contentWindow,res={url:urls[u][0],w:w.innerWidth,scrollW:d.documentElement.scrollWidth,found:0,wide:[],small:[]};
  d.querySelectorAll(urls[u][1]).forEach(function(box){res.found++;[box].concat(Array.prototype.slice.call(box.querySelectorAll('*'))).forEach(function(e){
    var r=e.getBoundingClientRect();if(r.width&&r.right>w.innerWidth+1)res.wide.push(e.tagName+'.'+e.className+' '+Math.round(r.right));
    if(e.children.length===0&&e.textContent.trim()&&parseFloat(w.getComputedStyle(e).fontSize)<12)res.small.push(e.tagName+'.'+e.className+' '+w.getComputedStyle(e).fontSize);});});
  out.pages.push(res);fr.remove();}
 fetch('/m',{method:'POST',body:JSON.stringify(out)});})();});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_subtasks_part_of_and_the_card_roll_up_fit_a_390_px_phone_with_nothing_under_12_px(store):
    """Protects: the Subtasks list, the Part of line, the board card's roll-up and Today's labelled rows at phone width: no sideways
    scroll, and no text in them under 12 px (James's floor, 2026-11-08), with long titles that have to wrap."""
    m, icon, ask, _plain = _fx(store)
    long = "Draw the icon for the company rows so that it reads at sixteen pixels and at the full size on the about page"
    _set(store.get(icon.id), title=long)
    _set(store.get(m.id), title=MAIN + " with a long title that has to wrap on a narrow phone screen without going sideways")
    urls = [[f"/ticket/{m.id}", "#subtasks"], [f"/ticket/{icon.id}", "#part-of"], ["/board?company=unsorted&open=*&g=*", ".cc-sub"],
            ["/", ".tr-part, .nm-part"]]
    got = drive(store, PHONE % repr(urls).replace("'", '"'), "/about")
    assert len(got.get("pages", [])) == 4, got
    for p in got["pages"]:
        assert p["found"] >= 1 and p["scrollW"] <= p["w"] and p["wide"] == [] and p["small"] == [], p
