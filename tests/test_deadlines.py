"""DESK-0193: hard deadlines and Today's top row, Immediate Action Required."""

import datetime as dt

import pytest
from browser_harness import browser, run_in_browser  # noqa: F401
from fastapi.testclient import TestClient

from desk import deadlines, ops
from desk.cli import main
from desk.rules import validate_store
from desk.ui.app import create_app

TODAY = dt.date.today()  # the page and the CLI read the real clock


def _t(store, title, days=None, kind="hard", status="ready", project="desk", **meta):
    t = ops.new(store, title, project, "task")
    t.meta["status"] = status
    if days is not None:
        t.meta["deadline"] = (TODAY + dt.timedelta(days=days)).isoformat()
        t.meta["deadline_kind"] = kind
    t.meta.update(meta)
    if status == "review":
        t.set_section("Report", "Done.")
    if status == "rejected":
        t.set_section("Decision", "- 2026-11-09 · james · rejected · not needed")
    if status == "approved":
        t.set_section("Decision", "- 2026-11-09 · james · approved · yes, as written")
    store.save(t)
    return store.get(t.id)


def test_ranking_is_by_date_missed_first_and_ties_by_priority_then_id(store):
    """Protects: the row is a deadline queue; the most overdue ticket heads it."""
    a, b, c, d = _t(store, "In ten", 10), _t(store, "Missed 2", -2), _t(store, "Tomorrow", 1, priority="p2"), _t(store, "Tomorrow p0", 1, priority="p0")
    got = [t.meta["title"] for t in deadlines.immediate(store.tickets(), TODAY)]
    assert got == ["Missed 2", "Tomorrow p0", "Tomorrow", "In ten"], got


def test_soft_sprint_far_closed_and_approved_are_left_out(store):
    """Protects: features rarely have a hard date; a soft, sprint, distant, finished or approved ticket must not cry wolf."""
    _t(store, "Soft", 1, kind="soft"), _t(store, "Sprint", 1, kind="sprint"), _t(store, "Far", 15), _t(store, "Edge", 14)
    _t(store, "Approved", 1, status="approved", **{}), _t(store, "Plain")
    _t(store, "Rejected", -3, status="rejected")
    assert [t.meta["title"] for t in deadlines.immediate(store.tickets(), TODAY)] == ["Edge"]
    assert [t.meta["title"] for t in deadlines.immediate(store.tickets(), TODAY, days=20)] == ["Edge", "Far"]


def test_days_in_plain_words():
    assert [deadlines.words(n) for n in (3, 1, 0, -1, -2)] == ["due in 3 days", "due in 1 day", "due today", "missed by 1 day", "missed by 2 days"]


def test_validate_checks_the_shape(store):
    """Protects: `drew validate` refuses a deadline that is not a date, an unknown kind, and a date with no kind."""
    t = _t(store, "Shape")
    for meta, word in ((dict(deadline="soon", deadline_kind="hard"), "YYYY-MM-DD"), (dict(deadline="2026-11-12", deadline_kind="firm"), "not one of"),
                       (dict(deadline="2026-11-12"), "needs deadline_kind")):
        t.meta.pop("deadline", None), t.meta.pop("deadline_kind", None)
        t.meta.update(meta)
        t.write()  # straight to disk: Store.save would refuse it first
        assert word in " ".join(validate_store(store)[0]), meta
    t.meta.pop("deadline"), t.meta.pop("deadline_kind", None)
    t.write()
    assert not validate_store(store)[0]


def test_cli_deadline_list_due_and_new(store, capsys):
    home = str(store.home)
    t = _t(store, "Visit the specialist", 2, kind="soft")
    assert main(["--home", home, "deadline", t.id, "2099-01-01", "--hard"]) in (None, 0)
    assert store.get(t.id).meta["deadline_kind"] == "hard" and "deadline set to 2099-01-01 (hard)" in store.get(t.id).section("Comments")
    soon = (dt.date.today() + dt.timedelta(days=3)).isoformat()
    main(["--home", home, "deadline", t.id, soon, "--hard"])
    capsys.readouterr()
    main(["--home", home, "list", "--due", "14"])
    out = capsys.readouterr().out
    assert t.id in out and "due in 3 days" in out
    main(["--home", home, "deadline", t.id, "--clear"])
    capsys.readouterr()
    main(["--home", home, "list", "--due", "14"])
    assert "No hard deadline within 14 days" in capsys.readouterr().out and "deadline" not in store.get(t.id).meta
    with pytest.raises(SystemExit):
        main(["--home", home, "deadline", t.id, "next week", "--hard"])
    main(["--home", home, "new", "Pay the fee", "--project", "desk", "--deadline", soon])
    made = [x for x in store.tickets() if x.meta["title"] == "Pay the fee"][0]
    assert made.meta["deadline"] == soon and made.meta["deadline_kind"] == "hard"
    assert not validate_store(store)[0]


def _today(c):
    return c.get("/").text


def test_today_row_lists_missed_first_turns_red_and_caps_at_five(store, monkeypatch):
    """Protects: the top row shows title, owner, days left in plain words and company > project; five, then 'N more'; red when missed."""
    today = dt.date.today()
    rows = [_t(store, f"Task {i}", i) for i in range(1, 7)]
    c = TestClient(create_app(store.home))
    page = _today(c)
    top = page.split('id="immediate"')[1].split("</section>")[0]
    assert "Immediate Action Required" in top and top.split("<details")[0].count('class="tile imm-row') == 5 and top.count('class="tile imm-row') == 6 and "1 more" in top and "imm-missed" not in top
    assert "due in 1 day" in top and top.index("Task 1") < top.index("Task 5") and "Task 6" not in top.split("<details")[0]
    assert page.index('id="immediate"') < page.index('id="needs-you"')
    late = _t(store, "Late one", -2)
    top = _today(c).split('id="immediate"')[1].split("</section>")[0]
    assert top.index("Late one") < top.index("Task 1") and "missed by 2 days" in top and "imm-missed" in top
    assert "Desk" in top and "›" in top  # company > project
    assert rows and late


def test_today_row_empty_state_is_one_line(store):
    c = TestClient(create_app(store.home))
    page = _today(c)
    top = page.split('id="immediate"')[1].split("</section>")[0]
    assert "Nothing has a hard deadline in the next 14 days." in top and "imm-row" not in top and "<h2" not in top and top.count("<p") == 1


def test_ticket_page_shows_the_deadline_beside_the_status(store):
    t = _t(store, "Pay the fee", 3)
    page = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    assert "Hard deadline" in page and "due in 3 days" in page
    plain = _t(store, "No date")
    assert "deadline" not in TestClient(create_app(store.home)).get(f"/ticket/{plain.id}").text.lower().split("<main")[1].split("</h1>")[0]


GOOD = {"project": "desk", "title": "Pay the fee", "type": "task", "owner": "main", "acceptance": "The fee is paid"}


def test_new_ticket_form_offers_the_deadline_and_checks_it(store):
    c = TestClient(create_app(store.home))
    page = c.get("/new").text
    assert 'name="deadline"' in page and 'name="deadline_kind"' in page and "Hard: it cannot move" in page
    bad = c.post("/new", data={**GOOD, "deadline": "soon", "token": c.app.state.tokens.issue()}, headers={"accept": "application/json"})
    assert bad.status_code == 422 and "YYYY-MM-DD" in bad.json()["errors"]["deadline"] and not store.tickets()
    ok = c.post("/new", data={**GOOD, "deadline": "2026-11-20", "deadline_kind": "hard", "token": c.app.state.tokens.issue()}, follow_redirects=False)
    assert ok.status_code == 303
    t = store.tickets()[0]
    assert (t.meta["deadline"], t.meta["deadline_kind"]) == ("2026-11-20", "hard") and not validate_store(store)[0]


PHONE = """<script>addEventListener('load',function(){
if(window.top!==window||location.pathname!=='/board')return;
var sleep=function(ms){return new Promise(function(r){setTimeout(r,ms);});},out={};
(async function(){
 var fr=document.createElement('iframe');fr.src='/';fr.style.cssText='width:390px;height:1200px;border:0';document.body.appendChild(fr);
 for(var i=0;i<100&&!(fr.contentDocument&&fr.contentDocument.getElementById('immediate'));i++){await sleep(100);}await sleep(300);
 var d=fr.contentDocument,w=fr.contentWindow,sec=d.getElementById('immediate');
 out.scrollW=d.documentElement.scrollWidth;out.w=w.innerWidth;out.rows=sec.querySelectorAll('.imm-row').length;
 out.wide=Array.prototype.filter.call(sec.querySelectorAll('.imm-row'),function(r){return r.getBoundingClientRect().right>w.innerWidth+1;}).length;
 out.small=Array.prototype.filter.call(sec.querySelectorAll('*'),function(e){return e.children.length===0&&e.textContent.trim()&&parseFloat(w.getComputedStyle(e).fontSize)<12;}).length;
 out.red=w.getComputedStyle(sec.querySelector('.imm-row.late .tl-note')).backgroundColor;
 fetch('/m',{method:'POST',body:JSON.stringify(out)});})();});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_the_row_fits_a_390_px_phone_with_no_text_under_12_px(store):
    """Protects: a missed deadline row, five rows and the 'N more' fold at phone width: no sideways scroll, nothing under 12 px."""
    from browser_harness import drive

    for i in range(1, 6):
        _t(store, f"A fairly long ticket title number {i} that has to wrap on a narrow phone screen without going sideways", i)
    _t(store, "Late one with a title that is also long enough to wrap at phone width", -2)
    got = drive(store, PHONE, "/board")
    assert got["scrollW"] <= got["w"] and got["wide"] == 0 and got["rows"] == 6 and got["small"] == 0, got
    assert got["red"].startswith("rgb(215, 38, 46)"), got
