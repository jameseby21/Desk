"""DESK-0285: Decide mode (/decide), batch approve from Needs me, inbox triage (/triage), the ask-me-once guard and delegations.
Every test runs on a throw-away store (tmp_path); the browser test uses the harness's own headless browser and ephemeral port."""

import datetime as dt
from urllib.parse import parse_qs, urlparse

import pytest
from browser_harness import browser, drive
from fastapi.testclient import TestClient

from desk import activity, decidemode, delegations, identity, lanes, ops, people
from desk.ui.app import create_app

HERE = "http://127.0.0.1:8799"  # Desk's own loopback address; a write from any other Host is refused (rule 80 item 10)
CHOOSE = "Options: A lime mark, it leaves the rotation; B a second candidate is drawn and rotates."


def _ask(store, title="Choose: is the lime mark final?", context=CHOOSE, report="", owner="james", status="ready", project="jre"):
    t = ops.new(store, title, project, "decision")
    t.set_section("Context", "Why this exists.\n\n" + context)
    if report:
        t.set_section("Report", report)
    t.meta["owner"] = owner
    t.meta["status"] = status
    store.save(t)
    return store.get(t.id)


def _review(store, title, report="Built and tested."):
    """A feature handed over for review: only the owner approves it, so it is an ask on Needs me."""
    t = ops.new(store, title, "jre", "feature")
    t.meta["size"] = "m"
    t.set_section("Report", report)
    t.meta["status"] = "review"
    store.save(t)
    return store.get(t.id)


def _owner(store, monkeypatch):
    """A browser on the owner's machine: the OS user is linked to the owner in people.yaml, so the identity is confirmed."""
    monkeypatch.setattr(identity, "os_user", lambda: "owner-login")
    people.link_os_user(store.home, "james", "owner-login")
    return TestClient(create_app(store.home), base_url=HERE)


def _someone_else(store, monkeypatch):
    monkeypatch.setattr(identity, "os_user", lambda: "helper-login")
    people.link_os_user(store.home, "chaz", "helper-login")
    return TestClient(create_app(store.home), base_url=HERE)


def _tok(c):
    return c.app.state.tokens.issue()


def _q(r) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(r.headers["location"]).query).items()}


# ---- the recommendation ----------------------------------------------------------------------------------------------------

def test_the_recommendation_is_read_from_where_workers_write_it(store):
    """Protects: Decide mode shows the worker's recommendation (Report, Comments, Context, options marked recommended) and its why."""
    t = _ask(store, report="Chaz recommends: A; it is the most-used mark and reads at 16 px.")
    rec = decidemode.recommendation(t)
    assert (rec.text, rec.why, rec.by, rec.option, rec.where) == ("A", "it is the most-used mark and reads at 16 px", "Chaz", 0, "Report")
    t2 = _ask(store, context=CHOOSE + "\n\nAsk James to choose. Recommendation: the second option.")
    assert decidemode.recommendation(t2).option == 1
    t3 = _ask(store, context="Options: value plus due (recommended); value only.")
    assert decidemode.recommendation(t3).option == 0 and decidemode.recommendation(t3).text == "value plus due"
    t4 = _ask(store, "Approve: the logo rule", "Plain.", report="Chaz recommends: approve; today these would be mislabeled.")
    assert decidemode.recommendation(t4).action == "approve"


def test_no_recommendation_is_said_and_never_invented(store, monkeypatch):
    """Protects: when no worker wrote a recommendation the page says so instead of making one up, and nothing is pre-selected."""
    t = _ask(store, context=CHOOSE + "\n\n- [ ] Recommendation in one paragraph")  # a checklist line is a task, not a recommendation
    assert decidemode.recommendation(t) is None
    page = _owner(store, monkeypatch).get("/decide").text
    assert "No recommendation recorded" in page and " checked" not in page and t.id in page


# ---- Decide mode -------------------------------------------------------------------------------------------------------------

def test_y_records_agree_with_the_recommendation_as_one_batch(store, monkeypatch):
    """Protects: the How to validate of DESK-0285, Y on the first item writes the owner's Decision line with the agree phrase, no typing."""
    t = _ask(store, report="Chaz recommends: A; it is the most-used mark.")
    c = _owner(store, monkeypatch)
    page = c.get("/decide").text
    assert 'value="0" checked' in page and "Recommended by" in page and "Why: it is the most-used mark" in page
    assert "Records this choice in the Decision section" in page  # what happens next, beside each option
    r = c.post("/decide/answer", data={"token": _tok(c), "id": t.id, "action": "approve", "option": "0", "words": ""}, follow_redirects=False)
    assert r.status_code == 303
    after = store.get(t.id)
    assert after.status == "approved"
    line = after.section("Decision").strip().splitlines()[-1]
    assert "· james · approved ·" in line and "Chose A: lime mark, it leaves the rotation. Agree with the recommendation." in line
    q = _q(r)
    assert q["undo"].startswith("b-") and t.id in q["msg"]
    batched = [e for e in activity.read(store.home, t.id) if e.batch]
    assert batched and {e.batch for e in batched} == {q["undo"]}


def test_agree_meets_the_minimum_reason_rule_for_an_approve_ask(store, monkeypatch):
    """Protects: 'Agree with the recommendation' is an accepted reason, so an approve needs no typed words when a recommendation exists."""
    t = _ask(store, "Approve: the logo rule", "Plain.", report="Chaz recommends: approve; it fixes the labels.")
    c = _owner(store, monkeypatch)
    c.post("/decide/answer", data={"token": _tok(c), "id": t.id, "action": "approve"}, follow_redirects=False)
    assert store.get(t.id).status == "approved" and "Agree with the recommendation: approve." in store.get(t.id).section("Decision")


def test_y_on_a_send_back_recommendation_sends_it_back_and_never_approves(store, monkeypatch):
    """Protects: agreeing with a 'send back' recommendation sends the ticket back; Y never approves what the worker said to send back."""
    t = _review(store, "Build the export")
    t.set_section("Report", "Built.\n\nChaz recommends: send back; the export drops the header row.")
    store.save(t)
    c = _owner(store, monkeypatch)
    page = c.get("/decide").text
    assert 'name="action" value="changes" data-dm-key="y"' in page and "Agree: send back" in page
    r = c.post("/decide/answer", data={"token": _tok(c), "id": t.id, "action": "approve", "words": ""}, follow_redirects=False)
    assert "send it back" in _q(r)["error"] and store.get(t.id).status == "review" and not store.get(t.id).section("Decision").strip()
    c.post("/decide/answer", data={"token": _tok(c), "id": t.id, "action": "changes", "words": ""}, follow_redirects=False)
    after = store.get(t.id)
    assert after.status == "changes_requested"
    assert "Agree with the recommendation: send back; the export drops the header row." in after.section("Decision")


def test_approve_with_no_recommendation_needs_the_owners_words(store, monkeypatch):
    """Protects: no canned approval is written in the owner's name; with nothing recommended, Y and batch approve need his own words."""
    t = _review(store, "Build the export")
    d = _ask(store, "Approve: the logo rule", "Plain.")
    c = _owner(store, monkeypatch)
    for x in (t, d):
        r = c.post("/decide/answer", data={"token": _tok(c), "id": x.id, "action": "approve", "words": ""}, follow_redirects=False)
        assert "say why you approve" in _q(r)["error"] and store.get(x.id).status == x.status and not store.get(x.id).section("Decision").strip()
    plan = decidemode.plan_batch(store, [t.id, d.id])
    assert [p.skip for p in plan] == ["No recommendation is recorded; open it and approve in your words."] * 2
    c.post("/decide/answer", data={"token": _tok(c), "id": t.id, "action": "approve", "words": "Checked the export myself."}, follow_redirects=False)
    assert store.get(t.id).status == "approved" and "Checked the export myself." in store.get(t.id).section("Decision")


def test_send_back_needs_a_reason_and_snooze_takes_it_off_for_a_week(store, monkeypatch):
    """Protects: N never sends back without words; S hides the ask for seven days and writes snooze_until (lanes contract field)."""
    t = _ask(store, "Approve: the logo rule", "Plain.")
    other = _ask(store, "Answer: which host name?", "Plain.")
    c = _owner(store, monkeypatch)
    r = c.post("/decide/answer", data={"token": _tok(c), "id": t.id, "action": "changes", "words": ""}, follow_redirects=False)
    assert "Say why" in _q(r)["error"] and store.get(t.id).status == "ready"
    c.post("/decide/answer", data={"token": _tok(c), "id": t.id, "action": "changes", "words": "Name the sizes first."}, follow_redirects=False)
    assert store.get(t.id).status == "changes_requested" and "Name the sizes first." in store.get(t.id).section("Decision")
    r = c.post("/decide/answer", data={"token": _tok(c), "id": other.id, "action": "snooze"}, follow_redirects=False)
    until = (dt.date.today() + dt.timedelta(days=7)).isoformat()
    assert str(store.get(other.id).meta["snooze_until"]) == until and lanes.is_snoozed(store.get(other.id))
    assert other.id not in [i.id for i in decidemode.queue(store)] and _q(r)["undo"].startswith("b-")


def test_an_answer_without_a_recommendation_needs_the_owners_words(store, monkeypatch):
    """Protects: Y on an Answer ask with nothing recommended refuses with a plain sentence instead of writing an empty decision."""
    t = _ask(store, "Answer: which host name?", "Plain.")
    c = _owner(store, monkeypatch)
    r = c.post("/decide/answer", data={"token": _tok(c), "id": t.id, "action": "approve"}, follow_redirects=False)
    assert "Type your answer first" in _q(r)["error"] and store.get(t.id).status == "ready"


def test_someone_who_is_not_the_owner_sees_it_read_only_and_cannot_post(store, monkeypatch):
    """Protects: decision 110, Decide mode records only under the viewer's own identity and only the owner decides."""
    t = _ask(store, report="Chaz recommends: A; reason.")
    c = _someone_else(store, monkeypatch)
    page = c.get("/decide").text
    assert "only james decides here" in page and "disabled" in page
    r = c.post("/decide/answer", data={"token": _tok(c), "id": t.id, "action": "approve", "option": "0"}, follow_redirects=False)
    assert r.status_code == 403 and store.get(t.id).status == "ready" and not store.get(t.id).section("Decision").strip()


def test_the_reply_box_refuses_a_confirmed_non_owner_on_the_shared_path(store, monkeypatch):
    """Protects: decision 110 on the one decision path, a confirmed non-owner can not write a Decision as the owner from the reply box."""
    t = _ask(store, "Approve: the logo rule", "Plain.")
    c = _someone_else(store, monkeypatch)
    for data in ({"action": "approve", "words": "Approved by someone else"}, {"reply": "1", "words": "Approved by someone else"}):
        c.post(f"/ticket/{t.id}/decide", data={"token": _tok(c), **data}, follow_redirects=False)
        assert store.get(t.id).status == "ready" and not store.get(t.id).section("Decision").strip()
    r = c.post(f"/ticket/{t.id}/decide", data={"token": _tok(c), "action": "approve", "words": "Approved by someone else"}, headers={"accept": "application/json"})
    assert "only james decides here" in r.json()["error"]
    with pytest.raises(decidemode.NotYours):  # the shared path itself refuses, whoever calls it
        decidemode.record(store, store.get(t.id), True, "Approved by someone else", viewer=identity.Actor("chaz", "thread", "", "ui", "os-user", True))
    assert store.get(t.id).status == "ready"


def test_the_owner_named_by_this_browser_decides_like_the_assumed_owner(store, monkeypatch):
    """Protects: one identity rule, the owner named by the browser's cookie decides as the assumed owner does; anyone else it names can not."""
    t = _ask(store, "Approve: the logo rule", "Plain.", report="Chaz recommends: approve; it is ready.")
    monkeypatch.setattr(identity, "os_user", lambda: "nobody-listed")
    c = TestClient(create_app(store.home), cookies={"desk_who": "chaz"}, base_url=HERE)
    r = c.post("/decide/answer", data={"token": _tok(c), "id": t.id, "action": "approve"}, follow_redirects=False)
    assert r.status_code == 403 and store.get(t.id).status == "ready"
    c = TestClient(create_app(store.home), cookies={"desk_who": "james"}, base_url=HERE)
    assert "disabled" not in c.get("/decide").text.split('id="dm-form"', 1)[1].split("</form>", 1)[0]
    r = c.post("/decide/answer", data={"token": _tok(c), "id": t.id, "action": "approve"}, follow_redirects=False)
    assert r.status_code == 303 and store.get(t.id).status == "approved"


def test_the_reply_box_and_decide_mode_share_one_decision_path(store, monkeypatch):
    """Protects: one path, not a copy; the ticket page's reply box and Decide mode both write through decidemode.record."""
    calls = []
    real = decidemode.record
    monkeypatch.setattr(decidemode, "record", lambda *a, **k: calls.append(a[1].id) or real(*a, **k))
    a, b = _ask(store), _ask(store, "Approve: the rule", "Plain.", report="Chaz recommends: approve; it is ready.")
    c = _owner(store, monkeypatch)
    c.post(f"/ticket/{a.id}/decide", data={"token": _tok(c), "reply": "1", "who": "james", "option": "1"}, follow_redirects=False)
    c.post("/decide/answer", data={"token": _tok(c), "id": b.id, "action": "approve"}, follow_redirects=False)
    assert calls == [a.id, b.id] and store.get(a.id).status == store.get(b.id).status == "approved"


def test_every_page_loads_the_d_key_script(store, monkeypatch):
    """Protects: key D opens Decide from any page (the script is in the base template, guarded so it never fires while typing)."""
    page = _owner(store, monkeypatch).get("/").text
    assert "/static/decide.js" in page and "/static/decide.css" in page


# ---- ask me once -------------------------------------------------------------------------------------------------------------

def test_the_guard_links_an_earlier_answer_that_looks_like_this_ask(store, monkeypatch):
    """Protects: the ask-me-once guard, the owner sees his earlier answer to a similar question before answering again."""
    old = _ask(store, "Choose: the logo colour for the brand page", "Options: lime; black.")
    ops.decide(store, old, True, "Lime, as the brand sheet says.", actor="james")
    unrelated = _ask(store, "Answer: which bank holds the reserve?", "Plain.")
    new = _ask(store, "Choose: logo colour for the shop page", "Options: lime; white.")
    hit = decidemode.similar_answer(store, store.get(new.id))
    assert hit is not None and hit.ticket.id == old.id and hit.quote == "Lime, as the brand sheet says."
    assert decidemode.similar_answer(store, store.get(unrelated.id)) is None
    page = _owner(store, monkeypatch).get(f"/decide?at={new.id}").text
    assert "You answered something like this." in page and f'href="/ticket/{old.id}#decision"' in page


# ---- batch approve -----------------------------------------------------------------------------------------------------------

def test_batch_approve_dry_run_then_one_batch_for_all(store, monkeypatch):
    """Protects: ticking two Needs me rows and Approve all approves both in one batch id (one Undo), after a dry run that writes nothing."""
    ok = "Built and tested.\n\nChaz recommends: approve; the checks pass."
    a, b = _review(store, "Build the export", ok), _review(store, "Build the import", ok)
    loner = _ask(store)  # a choose with no recommendation: left for the owner
    c = _owner(store, monkeypatch)
    dry = c.get(f"/decide/batch?ids={a.id}&ids={b.id}&ids={loner.id}&back=/").text
    assert "Approve 2 together?" in dry and "Left for you (1)" in dry and "No recommended option" in dry
    assert store.get(a.id).status == store.get(b.id).status == "review"  # the dry run wrote nothing
    r = c.post("/decide/batch", data={"token": _tok(c), "ids": [a.id, b.id], "back": "/"}, follow_redirects=False)
    q = _q(r)
    assert r.headers["location"].startswith("/?") and "Approved 2 tickets" in q["msg"]
    for t in (a, b):
        assert store.get(t.id).status == "approved"
        marked = [e for e in activity.read(store.home, t.id) if e.batch]
        assert marked and {e.batch for e in marked} == {q["undo"]}
    assert store.get(loner.id).status == "ready" and not [e for e in activity.read(store.home, loner.id) if e.batch]


def test_batch_back_never_leaves_desk(store, monkeypatch):
    """Protects: no open redirect, a back of //host or with a backslash falls back to / on the dry run's Cancel and after Approve."""
    ok = "Built and tested.\n\nChaz recommends: approve; the checks pass."
    a, b = _review(store, "Build the export", ok), _review(store, "Build the import", ok)
    c = _owner(store, monkeypatch)
    for bad in ("//evil.example/x", "/\\evil.example/x", "///evil.example"):
        page = c.get("/decide/batch", params={"ids": a.id, "back": bad}).text
        assert 'href="/">Cancel' in page and "evil.example" not in page
    r = c.post("/decide/batch", data={"token": _tok(c), "ids": [a.id], "back": "//evil.example/x"}, follow_redirects=False)
    assert r.headers["location"].startswith("/?") and store.get(a.id).status == "approved"
    r = c.post("/decide/batch", data={"token": _tok(c), "ids": [b.id], "back": "/board"}, follow_redirects=False)
    assert r.headers["location"].startswith("/board?")  # a plain local back still works


def test_a_write_from_a_foreign_host_is_refused(store, monkeypatch):
    """Protects: rule 80 item 10, Decide's writes answer only requests addressed to this computer (no DNS-rebinding batch approve)."""
    ok = "Built and tested.\n\nChaz recommends: approve; the checks pass."
    a = _review(store, "Build the export", ok)
    c = _owner(store, monkeypatch)
    for url, data in (("/decide/batch", {"ids": [a.id]}), ("/decide/answer", {"id": a.id, "action": "approve"}),
                      ("/decide/delegate", {"id": a.id, "who": "main", "pattern": "exports"}), ("/triage/place", {"id": a.id, "lane": "later"})):
        r = c.post(url, data={"token": _tok(c), **data}, headers={"host": "evil.example:8765"}, follow_redirects=False)
        assert r.status_code == 403 and "this computer" in r.text, url
    assert store.get(a.id).status == "review" and delegations.load(store.home) == [] and "lane" not in store.get(a.id).meta
    r = c.post("/decide/batch", data={"token": _tok(c), "ids": [a.id]}, headers={"host": "localhost:8765"}, follow_redirects=False)
    assert r.status_code == 303 and store.get(a.id).status == "approved"


# ---- inbox triage ------------------------------------------------------------------------------------------------------------

def _inbox(store, title):
    t = ops.new(store, title, "jre", "task")
    assert t.status == "inbox"
    return t


def test_triage_places_inbox_tickets_in_the_four_choices(store, monkeypatch):
    """Protects: /triage keys 1 to 4 set the lane field and move the ticket out of the inbox, one at a time."""
    a, b = _inbox(store, "Fix the footer"), _inbox(store, "Try a dark logo")
    c = _owner(store, monkeypatch)
    page = c.get("/triage").text
    assert "1 of 2" in page and all(f"<kbd>{k}</kbd> {w}" in page for k, w in (("1", "Now"), ("2", "Planned"), ("3", "Later"), ("4", "Park")))
    r = c.post("/triage/place", data={"token": _tok(c), "id": a.id, "lane": "later", "next": b.id}, follow_redirects=False)
    assert store.get(a.id).status == "triage" and lanes.lane_of(store.get(a.id)) == "later" and _q(r)["at"] == b.id
    c.post("/triage/place", data={"token": _tok(c), "id": b.id, "lane": "parked", "reason": "Not this season."}, follow_redirects=False)
    assert lanes.explicit_lane(store.get(b.id)) == "parked" and store.get(b.id).meta["park_reason"] == "Not this season."
    assert "The inbox is empty" in c.get("/triage").text


def test_merge_records_duplicate_of_and_closes_the_copy(store, monkeypatch):
    """Protects: M merges an inbox ticket into another id: duplicate_of, a link, a rejected line with the reason, a note on the target."""
    keep = _inbox(store, "Export the board as a sheet")
    copy = _inbox(store, "Board export to a sheet")
    c = _owner(store, monkeypatch)
    r = c.post("/triage/place", data={"token": _tok(c), "id": copy.id, "lane": "merge", "into": copy.id}, follow_redirects=False)
    assert "itself" in _q(r)["error"] and store.get(copy.id).status == "inbox"
    r = c.post("/triage/place", data={"token": _tok(c), "id": copy.id, "lane": "merge", "into": keep.id.lower()}, follow_redirects=False)
    gone = store.get(copy.id)
    assert gone.status == "rejected" and gone.meta["duplicate_of"] == keep.id and keep.id in gone.meta["related"]
    assert f"Merged into {keep.id}" in gone.section("Decision") and f"{copy.id} (" in store.get(keep.id).section("Comments")
    bid = _q(r)["undo"]
    assert {e.batch for e in activity.read(store.home, copy.id) if e.batch} == {bid} == {e.batch for e in activity.read(store.home, keep.id) if e.batch}


# ---- delegations ---------------------------------------------------------------------------------------------------------------

def test_a_delegation_is_written_to_the_store_file_shown_on_settings_and_removable(store, monkeypatch):
    """Protects: 'approve things like this for me' writes who, what, date and by whom to delegations.yaml; Settings lists it with Remove."""
    t = _ask(store, "Approve: alt text wording on cards", "Plain.")
    c = _owner(store, monkeypatch)
    r = c.post("/decide/delegate", data={"token": _tok(c), "id": t.id, "who": "main", "pattern": "alt text wording"}, follow_redirects=False)
    assert "may approve" in _q(r)["msg"]
    rows = delegations.load(store.home)
    assert rows == [{"who": "main", "pattern": "alt text wording", "project": "jre", "date": dt.date.today().isoformat(), "by": "james", "from": t.id}]
    assert (store.home / "delegations.yaml").read_text(encoding="utf-8").startswith("# Delegations")
    page = c.get("/settings").text
    assert "alt text wording" in page and 'action="/settings/delegations/remove"' in page
    c.post("/settings/delegations/remove", data={"token": _tok(c), "index": "0", "pattern": "alt text wording"}, follow_redirects=False)
    assert delegations.load(store.home) == [] and store.get(t.id).status == "ready"  # nothing was approved by saying so


def test_a_removed_delegation_can_be_put_back_and_both_writes_are_committed(store, monkeypatch):
    """Protects: reversible writes, Remove offers Put back (the same line returns as it was) and add, remove and put back go to git sync."""
    from desk.store import Store

    synced = []
    monkeypatch.setattr(Store, "sync_after", lambda self, message: synced.append(message))
    t = _ask(store, "Approve: alt text wording on cards", "Plain.")
    c = _owner(store, monkeypatch)
    c.post("/decide/delegate", data={"token": _tok(c), "id": t.id, "who": "main", "pattern": "alt text wording"}, follow_redirects=False)
    before = delegations.load(store.home)
    r = c.post("/settings/delegations/remove", data={"token": _tok(c), "index": "0", "pattern": "alt text wording"}, follow_redirects=False)
    assert delegations.load(store.home) == []
    page = c.get(r.headers["location"].split("#")[0]).text
    assert 'action="/settings/delegations/restore"' in page and "Put it back" in page
    key = _q(r)["put_back"]
    r = c.post("/settings/delegations/restore", data={"token": _tok(c), "key": key}, follow_redirects=False)
    assert delegations.load(store.home) == before and "Put back" in _q(r)["msg"]
    r = c.post("/settings/delegations/restore", data={"token": _tok(c), "key": key}, follow_redirects=False)
    assert delegations.load(store.home) == before  # a second Put back adds nothing
    assert [m.split(":")[0] for m in synced] == ["desk"] * 3 and all("delegation" in m for m in synced), synced


def test_someone_else_cannot_add_a_delegation(store, monkeypatch):
    """Protects: decision 110, only the owner can hand his approvals to someone."""
    t = _ask(store, "Approve: alt text wording", "Plain.")
    c = _someone_else(store, monkeypatch)
    r = c.post("/decide/delegate", data={"token": _tok(c), "id": t.id, "who": "chaz", "pattern": "anything"}, follow_redirects=False)
    assert r.status_code == 403 and delegations.load(store.home) == []


# ---- plain words ---------------------------------------------------------------------------------------------------------------

def test_the_new_screens_read_in_plain_words(store, monkeypatch):
    """Protects: DESK-0253 (rule 15), the words Desk itself puts on these screens read at grade 10 or below with no listed jargon."""
    import html as htmlmod
    import re

    from desk import plainwords

    t = _ask(store, "Approve: the rule", "Plain.")
    _inbox(store, "Fix the footer")
    c = _owner(store, monkeypatch)
    for url in ("/decide", "/triage", f"/decide/batch?ids={t.id}", "/settings"):
        page = c.get(url).text
        part = page[page.index('class="dm'):] if 'class="dm' in page else page[page.index('id="delegations"'):]
        part = part[:part.index("</main>")] if "</main>" in part else part
        words = htmlmod.unescape(re.sub(r"<[^>]+>", "\n", part))
        words = words.replace("Approve: the rule", "").replace("Fix the footer", "")  # the fixture titles are not Desk's words
        lines = [ln.strip() for ln in words.splitlines() if ln.strip() and not re.fullmatch(r"[A-Z]+-\d+|[\d\-: ]+|\W+", ln.strip())]
        text = "\n".join(ln if ln.endswith((".", "?", "!")) else ln + "." for ln in lines)
        g = plainwords.grade(text)
        assert (g is None or g <= plainwords.MAX_GRADE) and plainwords.jargon(text) == [], (url, g, plainwords.jargon(text))


# ---- in a real browser: keys only ----------------------------------------------------------------------------------------------

KEYS = """<script>addEventListener('load',function(){
var key=function(k){document.dispatchEvent(new KeyboardEvent('keydown',{key:k,bubbles:true}));};
var step=sessionStorage.getItem('dm-step')||'today';
if(location.pathname==='/'&&step==='today'){sessionStorage.setItem('dm-ticks',String(document.querySelectorAll('#needs-me .dm-tick').length));sessionStorage.setItem('dm-step','decide');key('d');return;}
if(location.pathname==='/decide'&&!location.search){sessionStorage.setItem('dm-step','sent');key('y');return;}
fetch('/m',{method:'POST',body:JSON.stringify({path:location.pathname,query:location.search,ticks:sessionStorage.getItem('dm-ticks'),step:step})});});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_keyboard_only_d_then_y_records_the_decision(store, monkeypatch):
    """Protects: /decide works with the keyboard alone, D from Today opens it and Y records the recommended answer."""
    t = _ask(store, report="Chaz recommends: B; it keeps the rotation fresh.")
    monkeypatch.setattr(identity, "os_user", lambda: "owner-login")
    people.link_os_user(store.home, "james", "owner-login")
    got = drive(store, KEYS, "/")
    assert got.get("path") == "/decide" and "undo=b-" in got.get("query", ""), got
    assert got["ticks"] == "1"  # the Needs me ask row on Today got its tick box
    assert store.get(t.id).status == "approved" and "Chose B: a second candidate is drawn and rotates. Agree with the recommendation." in store.get(t.id).section("Decision")


PHONE = """<script>addEventListener('load',function(){if(window!==top)return;
var paths=%s,out={},i=0;document.body.innerHTML='';
function next(){if(i>=paths.length){fetch('/m',{method:'POST',body:JSON.stringify(out)});return;}
 var p=paths[i++],f=document.createElement('iframe');f.style.cssText='width:390px;height:900px;border:0';
 f.onload=function(){setTimeout(function(){var d=f.contentDocument.documentElement;out[p]={scroll:d.scrollWidth,client:d.clientWidth,ticks:f.contentDocument.querySelectorAll('.dm-tick').length};f.remove();next();},300);};
 f.src=p;document.body.appendChild(f);}
next();});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_decide_triage_and_ticks_fit_a_390_px_phone(store, monkeypatch):
    """Protects: the phone width rule, no sideways scroll at 390 px on /decide, /triage, the batch dry run and Today with tick boxes."""
    t = _ask(store, "Choose: a long question title that has to wrap on a narrow phone screen without pushing sideways", report="Chaz recommends: A; it reads well.")
    _inbox(store, "A new inbox ticket with a long title that wraps on a phone")
    monkeypatch.setattr(identity, "os_user", lambda: "owner-login")
    people.link_os_user(store.home, "james", "owner-login")
    paths = ["/decide", "/triage", f"/decide/batch?ids={t.id}", "/"]
    import json

    got = drive(store, PHONE % json.dumps(paths), "/about")
    for p in paths:
        assert got[p]["scroll"] <= got[p]["client"] <= 390, (p, got[p])
    assert got["/"]["ticks"] >= 1
