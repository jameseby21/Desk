"""DESK-0284: undo. Protects: field, section, comment and decision changes revert by writing new events (the log only grows); a batch
reverts as one, newest first, or not at all; a change made since is never overwritten silently; every web write hands back an Undo;
/undo lists the week's changes with Undo; the ticket page says who changed each field."""

import datetime as dt
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from desk import activity, identity, ops, undo
from desk.cli import main
from desk.ui.app import create_app


def run(store, *argv):
    main(["--home", str(store.home), *argv])


def as_(name, kind="person"):
    return identity.CURRENT.set(identity.Actor(name, kind, "t1", "cli", "claimed", False))


def lines(store, tid):
    return activity.path(store.home, tid).read_text(encoding="utf-8").splitlines()


def set_field(store, tid, **kw):
    t = store.get(tid)
    t.meta.update(kw)
    store.save(t)


def test_drew_undo_puts_a_field_back_and_the_log_only_grows(store, capsys):
    """`drew undo ID` reverts the last change through a new event; every earlier line of the log is byte for byte unchanged."""
    t = ops.new(store, "Undo a field", "desk")
    set_field(store, t.id, priority="p0")
    before = lines(store, t.id)
    run(store, "undo", t.id)
    assert store.get(t.id).meta["priority"] == "p2"
    after = lines(store, t.id)
    assert after[:len(before)] == before and len(after) == len(before) + 1
    last = activity.read(store.home, t.id)[-1]
    assert last.field == "priority" and last.new == "p2" and last.what.endswith(f"· undoes #{len(before)}")
    assert re.fullmatch(r"b-[0-9a-f]{12}", last.batch)
    assert "Undone:" in capsys.readouterr().out
    assert activity.verify(store.home, t.id) == []


def test_a_long_section_comes_back_whole(store):
    """A section's full old text is restored from the undo snapshot, not the one-line value the log keeps."""
    t = ops.new(store, "Undo a section", "desk")
    long_text = "First version of the context. " * 12
    t = store.get(t.id)
    t.set_section("Context", long_text)
    store.save(t)
    t = store.get(t.id)
    t.set_section("Context", "Replaced by mistake.")
    store.save(t)
    seq = activity.read(store.home, t.id)[-1].seq
    res = undo.revert_event(store, t.id, seq)
    assert store.get(t.id).section("Context").strip() == long_text.strip()
    assert res.reverted[0].seq == seq and res.written and all(e.batch == res.batch for e in res.written)


def test_a_comment_is_withdrawn_never_deleted(store):
    """Reverting a comment appends a dated line that names it; the comment's text stays in the file."""
    t = ops.new(store, "Undo a comment", "desk")
    ops.comment(store, store.get(t.id), "Ship it on Friday", actor="james")
    seq = activity.read(store.home, t.id)[-1].seq
    undo.revert_event(store, t.id, seq)
    text = store.get(t.id).section("Comments")
    assert "Ship it on Friday" in text.splitlines()[0]
    assert re.search(r'· Withdrawn: "Ship it on Friday" \(the comment of', text.splitlines()[-1])
    with pytest.raises(undo.Refused, match="already undone"):
        undo.revert_event(store, t.id, seq)


def test_a_decision_is_reverted_by_a_new_decision_line_that_names_it(store):
    """Idea 60: undoing Approve moves the status back and adds a Decision line naming the old one; the old line stays."""
    t = ops.new(store, "Decide me", "desk", owner="james")
    t = store.get(t.id)
    t.meta["status"] = "review"
    t.set_section("Report", "Built it.")
    store.save(t)
    with activity.batch("b-aaaaaaaaaaaa"):
        ops.decide(store, store.get(t.id), approve=True, reason="Looks right to me", actor="james")
    assert store.get(t.id).status == "approved"
    res = undo.revert_batch(store, "b-aaaaaaaaaaaa")
    got = store.get(t.id)
    assert got.status == "review"
    dec = got.section("Decision").splitlines()
    assert '"Looks right to me"' in dec[0] and "approved" in dec[0]
    assert "· reverted ·" in dec[-1] and "Looks right to me" in dec[-1]
    assert res.batch != "b-aaaaaaaaaaaa" and {e.batch for e in res.written} == {res.batch}


def test_a_batch_reverts_as_one_across_tickets_newest_first(store):
    """Every change of one batch, on two tickets, comes back in one undo; the redo (an undo of the undo) puts them forward again."""
    a = ops.new(store, "Batch A", "desk")
    b = ops.new(store, "Batch B", "desk")
    with activity.batch("b-bbbbbbbbbbbb"):
        set_field(store, a.id, priority="p1")
        set_field(store, a.id, priority="p0")
        set_field(store, b.id, size="m")
    res = undo.revert_batch(store, "b-bbbbbbbbbbbb")
    assert store.get(a.id).meta["priority"] == "p2" and store.get(b.id).meta["size"] == "s"
    assert len(res.reverted) == 3 and res.message == "Undone: 3 changes on 2 tickets."
    undo.revert_batch(store, res.batch)
    assert store.get(a.id).meta["priority"] == "p0" and store.get(b.id).meta["size"] == "m"


def test_a_change_made_since_is_refused_with_who_made_it_unless_forced(store):
    """Undo never silently overwrites someone else's later change: the refusal names them, and nothing in the batch is written."""
    t = ops.new(store, "Contested", "desk")
    tok = as_("patch", "session")
    try:
        with activity.batch("b-cccccccccccc"):
            set_field(store, t.id, priority="p1")
            set_field(store, t.id, size="m")
    finally:
        identity.CURRENT.reset(tok)
    tok = as_("chaz")
    try:
        set_field(store, t.id, priority="p3")
    finally:
        identity.CURRENT.reset(tok)
    n = len(lines(store, t.id))
    with pytest.raises(undo.Refused) as e:
        undo.revert_batch(store, "b-cccccccccccc")
    assert e.value.conflict and "priority was changed since by Chaz (Coworker) at " in str(e.value)
    assert len(lines(store, t.id)) == n and store.get(t.id).meta["size"] == "m"  # all or nothing
    undo.revert_batch(store, "b-cccccccccccc", force=True)
    assert store.get(t.id).meta["priority"] == "p2" and store.get(t.id).meta["size"] == "s"


def test_dry_run_counts_and_writes_nothing(store, capsys):
    """`drew undo --dry-run` names what would be undone, with the count, and leaves the log as it was."""
    t = ops.new(store, "Dry", "desk")
    set_field(store, t.id, priority="p0")
    n = len(lines(store, t.id))
    run(store, "undo", t.id, "--dry-run")
    out = capsys.readouterr().out
    assert "would undo" in out and "1 of 1 change would be undone" in out
    assert len(lines(store, t.id)) == n and store.get(t.id).meta["priority"] == "p0"


def test_every_web_write_hands_back_an_undo_that_works(store):
    """A form post redirects with undo=<batch>&msg=<sentence>; an in-place post carries the X-Desk-Undo headers; Undo restores."""
    t = ops.new(store, "Web undo", "desk")
    c = TestClient(create_app(store.home))
    tok = c.app.state.tokens
    r = c.post(f"/ticket/{t.id}/move", data={"to": "triage", "token": tok.issue()}, follow_redirects=False)
    assert r.status_code == 303
    q = parse_qs(urlsplit(r.headers["location"]).query)
    batch = q["undo"][0]
    assert re.fullmatch(r"b-[0-9a-f]{12}", batch) and q["msg"][0] == f"{t.id} status is now triage."
    assert store.get(t.id).status == "triage"
    r = c.post(f"/ticket/{t.id}/comment", data={"text": "In place", "token": tok.issue()}, headers={"Accept": "application/json"})
    assert r.status_code == 200 and r.headers["x-desk-undo"].startswith("b-") and "Comment" in r.headers["x-desk-undo-msg"]
    r = c.post(f"/undo/batch/{batch}", headers={"Accept": "application/json", "X-Desk-Undo-Request": "1"})
    assert r.status_code == 200 and r.json()["ok"] and r.json()["batch"].startswith("b-")
    assert store.get(t.id).status == "inbox"
    r = c.get(f"/ticket/{t.id}")
    assert "status: triage → inbox · undoes #" in r.text


def test_an_undo_without_a_page_key_or_the_header_is_refused(store):
    """Another site cannot post an undo: without the header or a one-time key nothing is written."""
    t = ops.new(store, "Guarded", "desk")
    set_field(store, t.id, priority="p0")
    seq = activity.read(store.home, t.id)[-1].seq
    c = TestClient(create_app(store.home))
    r = c.post(f"/undo/event/{t.id}/{seq}", headers={"Accept": "application/json"})
    assert r.status_code == 403 and store.get(t.id).meta["priority"] == "p0"
    r = c.post(f"/undo/event/{t.id}/{seq}", data={"token": c.app.state.tokens.issue(), "next": "/undo"}, follow_redirects=False)
    assert r.status_code == 303 and "undo=b-" in r.headers["location"] and store.get(t.id).meta["priority"] == "p2"


def test_a_conflict_on_the_page_offers_undo_anyway(store):
    """A refused undo on /undo comes back with the reason and an Undo anyway button that forces it."""
    t = ops.new(store, "Page conflict", "desk")
    set_field(store, t.id, priority="p1")
    seq = activity.read(store.home, t.id)[-1].seq
    set_field(store, t.id, priority="p3")
    c = TestClient(create_app(store.home))
    r = c.post(f"/undo/event/{t.id}/{seq}", data={"token": c.app.state.tokens.issue()})
    assert r.status_code == 200 and "was changed since" in r.text and "Undo anyway" in r.text and f'action="/undo/event/{t.id}/{seq}"' in r.text
    r = c.post(f"/undo/event/{t.id}/{seq}", data={"token": c.app.state.tokens.issue()}, headers={"Accept": "application/json"})
    assert r.status_code == 409 and r.json()["conflict"] is True


def test_the_undo_page_lists_this_weeks_changes_with_undo_and_filters_by_who(store):
    """/undo shows each change with an Undo button, one Undo all per batch, and a Who filter."""
    a = ops.new(store, "Listed one", "desk")
    tok = as_("chaz")
    try:
        with activity.batch("b-dddddddddddd"):
            set_field(store, a.id, priority="p1")
            set_field(store, a.id, size="m")
    finally:
        identity.CURRENT.reset(tok)
    c = TestClient(create_app(store.home))
    page = c.get("/undo").text
    assert "Undo log" in page and 'action="/undo/batch/b-dddddddddddd"' in page and "Undo all 2" in page
    assert "priority: p2 → p1" in page and f'action="/undo/event/{a.id}/' in page
    assert '<option value="chaz"' in page
    only = c.get("/undo?actor=nobody").text
    assert "priority: p2 → p1" not in only and "No changes in the last seven days" in only


def test_the_ticket_page_says_who_changed_each_field_including_a_rule(store):
    """The 'who did this' chip names the person, agent or rule behind the newest change of each field."""
    t = ops.new(store, "Who did this", "desk")
    activity.append(store.home, [activity.Event(t.id, dt.datetime.now().replace(microsecond=0).isoformat(), "auto-triage", "rule", "cli",
                                                "field", "priority", "p2", "p1", "priority: p2 → p1")])
    page = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    chip = re.search(r'<span class="chip who-chip" data-kind="rule"[^>]*>(.*?)</span>', page, re.S)
    assert chip and "<b>priority</b> p1" in chip.group(1) and "auto-triage · Rule" in chip.group(1)
    assert f'href="/undo?ticket={t.id}"' in page


def test_the_toast_script_is_on_every_page(store):
    """Every page loads undo.js, which exposes deskToast and deskConfirmCount for the other packages."""
    c = TestClient(create_app(store.home))
    page = c.get("/").text
    src = re.search(r'src="(/static/undo\.js[^"]*)"', page).group(1)
    js = c.get(src).text
    assert "window.deskToast" in js and "window.deskConfirmCount" in js and "X-Desk-Undo-Request" in js
    assert c.get("/static/undo.css").status_code == 200


def test_creating_a_ticket_on_the_page_can_be_undone(store):
    """Undo of New ticket never deletes the file: the ticket is set aside as rejected with a Decision line saying why."""
    c = TestClient(create_app(store.home))
    r = c.post("/new", data={"token": c.app.state.tokens.issue(), "project": "desk", "title": "Made by mistake", "type": "task", "priority": "p2",
                             "size": "s", "owner": "james", "assignee": "james", "outcome": "Nothing.", "acceptance": "- [ ] nothing"}, follow_redirects=False)
    assert r.status_code == 303, r.text[:500]
    tid = urlsplit(r.headers["location"]).path.rsplit("/", 1)[-1]
    batch = parse_qs(urlsplit(r.headers["location"]).query)["undo"][0]
    r = c.post(f"/undo/batch/{batch}", headers={"Accept": "application/json", "X-Desk-Undo-Request": "1"})
    assert r.status_code == 200, r.text
    got = store.get(tid)
    assert got.status == "rejected" and "created by mistake" in got.section("Decision") and got.path.is_file()


def test_the_toast_shows_at_phone_width_and_z_undoes(store):
    """In a real browser at 390 px: the toast from ?undo= fits the screen, Z undoes, and the next toast offers Redo."""
    from browser_harness import browser, drive

    if not browser():
        pytest.skip("no headless Edge or Chrome on this machine")
    t = ops.new(store, "Browser undo", "desk")
    with activity.batch("b-eeeeeeeeeeee"):
        set_field(store, t.id, priority="p0")
    script = """<script>
window.addEventListener("load", function () { setTimeout(function () {
  var t = document.querySelector(".undo-toast"), r = t ? t.getBoundingClientRect() : null;
  var out = {toast: !!t, text: t ? t.textContent : "", left: r ? r.left : -1, right: r ? r.right : 0, vw: document.documentElement.clientWidth, addr: location.search};
  document.dispatchEvent(new KeyboardEvent("keydown", {key: "z", bubbles: true}));
  var n = 0, iv = setInterval(function () { var t2 = document.querySelector(".undo-toast"); n++;
    if ((t2 && /Undone/.test(t2.textContent)) || n > 80) { clearInterval(iv); out.after = t2 ? t2.textContent : "";
      fetch("/m", {method: "POST", body: JSON.stringify(out)}); } }, 100);
}, 400); });
</script>"""
    got = drive(store, script, f"/ticket/{t.id}?undo=b-eeeeeeeeeeee&msg=Priority%20saved.", width=390)
    assert got["toast"] and got["text"].startswith("Priority saved.") and "Undo" in got["text"], got
    assert got["left"] >= 0 and got["right"] <= got["vw"], got
    assert "undo=" not in got["addr"], got  # the address is cleaned, so a reload does not show it again
    assert got["after"].startswith("Undone") and "Redo" in got["after"], got
    assert store.get(t.id).meta["priority"] == "p2"
