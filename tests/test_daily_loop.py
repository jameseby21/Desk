"""DESK-0282: the eleven daily-loop packages work as one product. Each test covers one seam between two or more packages:
Decide and the Undo toast, snooze on every page, automations and the brief through the notification sender, one settings page,
the key map on the new pages, and the origin guard outside the undo middleware. Every test runs on a throw-away store."""

import datetime as dt
import re
from urllib.parse import parse_qs, urlparse

import pytest
from browser_harness import browser, drive
from fastapi.testclient import TestClient

from desk import automations as au, brief, decidemode, identity, lanes, needs_me, notify_channels as nc, notify_queue, notify_send as ns, ops, people, plainwords, triage
from desk.ui.app import create_app

HERE = "http://127.0.0.1:8799"  # Desk's own loopback address; lane, capture and decide writes refuse any other Host
TODAY = dt.date.today()
PRIVATE = "Zorblax"  # a fictional private term, only in this test's denylist
FAKE_SCANNER = '''import re
from pathlib import Path
def load(path):
    p = Path(path)
    if not p.exists():
        return []
    return [(t, re.compile(r"(?<![\\w])" + re.escape(t) + r"(?![\\w])", re.I)) for t in p.read_text(encoding="utf-8").split() if t]
'''


def _review(store, title, report="Built and tested.\n\nChaz recommends: approve; the checks pass."):
    t = ops.new(store, title, "jre", "feature")
    t.meta["size"] = "m"
    t.set_section("Report", report)
    t.meta["status"] = "review"
    store.save(t)
    return store.get(t.id)


def _owner(store, monkeypatch):
    """A browser on the owner's machine (OS user linked to the owner), so Decide answers are allowed."""
    monkeypatch.setattr(identity, "os_user", lambda: "owner-login")
    people.link_os_user(store.home, "james", "owner-login")
    return TestClient(create_app(store.home), base_url=HERE)


def _q(location: str) -> dict:
    return {k: v for k, v in parse_qs(urlparse(location).query).items()}


@pytest.fixture
def sender(tmp_path, monkeypatch):
    """The notification sender with a fictional denylist: the phone channel is paired in a temp secrets file, sends are recorded."""
    scan = tmp_path / "scan" / "private_scan.py"
    scan.parent.mkdir()
    scan.write_bytes(FAKE_SCANNER.encode("utf-8"))
    (scan.parent / "deny.txt").write_bytes(f"{PRIVATE}\n".encode("utf-8"))
    monkeypatch.setenv("DESK_PRIVATE_SCAN", str(scan))
    monkeypatch.setenv("JEBY_DENYLIST", str(scan.parent / "deny.txt"))
    # no network: conftest sets DESK_NOTIFY_OFFLINE, so a real transport raises; every send below goes through `post`
    nc.write_secret("NTFY_TOPIC", "t" * 32)
    calls = []

    def post(url, data, headers):
        calls.append(data.decode("utf-8") if isinstance(data, bytes) else str(data))
        return nc.Response(200, '{"status":1}')

    return post, calls


# ---- Decide mode and the Undo toast (DESK-0285 + DESK-0284) ---------------------------------------------------------------------

def test_a_decide_answer_comes_back_with_one_undo_and_that_undo_puts_it_back(store, monkeypatch):
    """Protects the Decide/Undo seam: Y's redirect carries exactly one undo batch (the route's own, none added by the middleware),
    and posting that batch to DESK-0284's undo route puts the ticket back in review."""
    t = _review(store, "Build the export")
    c = _owner(store, monkeypatch)
    r = c.post("/decide/answer", data={"token": c.app.state.tokens.issue(), "id": t.id, "action": "approve", "words": ""}, follow_redirects=False)
    assert r.status_code == 303 and store.get(t.id).status == "approved"
    q = _q(r.headers["location"])
    assert len(q["undo"]) == 1 and len(q["msg"]) == 1 and q["undo"][0].startswith("b-") and t.id in q["msg"][0]
    u = c.post(f"/undo/batch/{q['undo'][0]}", headers={"Accept": "application/json", "X-Desk-Undo-Request": "1"})
    assert u.status_code == 200 and u.json()["ok"], u.text
    assert store.get(t.id).status == "review"


def test_the_undo_toast_says_the_sentence_once(store, monkeypatch):
    """Protects "no clutter": after a write, Decide, Sort the inbox and Automations keep the sentence only for a browser without
    script, because undo.js (on every page) shows it in the toast with Undo."""
    (store.projects_dir / "management.md").write_text("# Management\n", encoding="utf-8")
    au.init(store.home)  # the Automations page draws its message only when there are rules
    c = _owner(store, monkeypatch)
    for path in ("/decide", "/triage", "/automations"):
        page = c.get(path, params={"undo": "b-0123456789ab", "msg": "Sentence-for-the-toast"}).text
        assert "Sentence-for-the-toast" in page, path
        visible = re.sub(r"<noscript>.*?</noscript>", "", page, flags=re.S)
        assert "Sentence-for-the-toast" not in visible, path
        assert "undo.js" in page, path


# ---- snooze means one thing everywhere (DESK-0283 with 0285, 0288, 0289) -----------------------------------------------------

def test_a_snoozed_ticket_is_off_every_daily_loop_list_until_its_date(store, monkeypatch, sender):
    """Protects the lanes contract across packages: a snoozed ask is off Needs me, the brief (page and phone text), Decide mode and
    the board; a snoozed inbox ticket is off Sort the inbox; the ones not snoozed stay on each."""
    later = (TODAY + dt.timedelta(days=5)).isoformat()
    awake, asleep = _review(store, "Build the export"), _review(store, "Build the import")
    asleep.meta["snooze_until"] = later
    store.save(asleep)
    inbox_awake, inbox_asleep = ops.new(store, "Look at the logo", "jre", "task"), ops.new(store, "Look at the font", "jre", "task")
    inbox_asleep.meta["snooze_until"] = later
    store.save(inbox_asleep)
    assert lanes.is_snoozed(store.get(asleep.id)) and store.get(inbox_awake.id).status == "inbox"

    nm = [i.id for i in needs_me.build(store)]
    dm = [i.id for i in decidemode.queue(store)]
    bf = [d["id"] for d in brief.build_brief(store, TODAY)["decisions"]]
    tq = [t.id for t in triage.queue(store)]
    for name, ids in (("needs me", nm), ("decide", dm), ("brief", bf)):
        assert awake.id in ids and asleep.id not in ids, (name, ids)
    assert inbox_awake.id in tq and inbox_asleep.id not in tq, tq

    phone = ns.brief_note(store, TODAY).body
    assert awake.id in phone and asleep.id not in phone, phone

    c = _owner(store, monkeypatch)
    c.cookies.set("desk_show", "all")
    today_rows = re.sub(r'<details class="lane-snoozed">.*?</details>', "", c.get("/").text, flags=re.S)  # the lane bar names snoozed ones on purpose
    assert f"/ticket/{awake.id}" in today_rows and f"/ticket/{asleep.id}" not in today_rows
    page = c.get("/brief").text
    assert awake.id in page and asleep.id not in page
    board = re.sub(r"<[^>]+>", " ", c.get("/board").text)
    assert re.search(r"Needs me\s*·\s*1\b", board), "the board's Needs me count leaves the snoozed ask out"


# ---- automations and the brief reach the phone only through DESK-0289's sender ----------------------------------------------

def test_a_deadline_escalation_from_a_rule_is_sent_through_the_privacy_filter_budget_and_quiet_hours(store, monkeypatch, sender):
    """Protects the DESK-0292 to DESK-0289 seam: the hard-deadline rule queues an urgent alert, the sender withholds the private
    title but keeps the id, holds it in quiet hours, and folds it into the next morning brief; the rule itself sends nothing."""
    post, calls = sender
    (store.projects_dir / "management.md").write_text("# Management\n", encoding="utf-8")
    au.init(store.home)
    monkeypatch.setattr(au, "gh_run", lambda args, cwd=None: (127, "", "gh is not installed"))
    t = store.create(f"Build the {PRIVATE} export", "desk", "feature", owner="main", assignee="main")
    t.meta.update(status="ready", deadline=(TODAY + dt.timedelta(days=2)).isoformat(), deadline_kind="hard")
    store.save(t)
    rs, repos = au.load(store.home)
    au.sweep(store, [r for r in rs if r.name == "hard-deadline-two-days"], repos, today=TODAY, apply=True)
    q = notify_queue.pending(store.home)
    assert len(q) == 1 and q[0]["kind"] == "alert" and q[0]["urgent"] and q[0]["ids"] == [t.id] and not calls

    night = ns.drain(store, now=dt.datetime.combine(TODAY, dt.time(23, 0)), post=post)
    assert (night.sent, night.held, calls) == (0, 1, [])  # quiet hours hold even an urgent alert by default
    note, results, _ = ns.send_brief(store, now=dt.datetime.combine(TODAY + dt.timedelta(days=1), dt.time(7, 30)), post=post)
    assert results and all(r.ok for r in results)
    body = "\n".join(calls)
    assert t.id in body and PRIVATE not in body, body


def test_the_phone_brief_is_the_daily_brief(store, sender):
    """Protects the DESK-0288 to DESK-0289 seam: `drew notify brief` sends brief_text(build_brief(...)) through the filter: the
    same decisions in the same order as /brief, with titles scanned and nothing from a Report."""
    a, b = _review(store, "Build the export"), _review(store, f"Build the {PRIVATE} import", report="REPORT-SENTINEL. Chaz recommends: approve.")
    want = [d["id"] for d in brief.build_brief(store, TODAY)["decisions"]]
    body = ns.brief_note(store, TODAY).body
    assert body.splitlines()[0].startswith(f"Desk brief {TODAY.isoformat()}: {len(want)} decision")
    assert [ln[2:].split(":")[0] for ln in body.splitlines() if ln.startswith("- ")][:len(want)] == want
    assert PRIVATE not in body and "REPORT-SENTINEL" not in body and a.id in body and b.id in body


# ---- one settings page --------------------------------------------------------------------------------------------------------

def test_settings_shows_every_daily_loop_switch_once_in_plain_words(store, monkeypatch):
    """Protects one settings page: theme and type size, lanes on this device, Motivation, phone and email (quiet hours), and
    delegations each appear exactly once; the two new sections pass the plain-words check (DESK-0253)."""
    page = _owner(store, monkeypatch).get("/settings").text
    for anchor in ('id="display"', 'id="lanes"', 'id="motivation"', 'id="notify-settings"', 'id="delegations"'):
        assert page.count(anchor) == 1, anchor
    for words in ("Theme (this device)", "Type size (this device)", "Lanes (how soon", "Quiet hours", "Daily goal", "Things others may approve for you"):
        assert page.count(words) == 1, words
    assert "21:00 to 07:00" in page  # DESK-0289's default quiet hours, read from desk.toml [notify]
    for anchor, end in (('id="lanes"', "</form>"), ('id="notify-settings"', "</section>")):
        part = page[page.rindex("<", 0, page.index(anchor)):]
        text = re.sub(r"<[^>]+>", " ", part[:part.index(end)])
        g = plainwords.grade(text)
        assert (g is None or g <= plainwords.MAX_GRADE) and not plainwords.jargon(text), (anchor, g, plainwords.jargon(text))


def test_the_lanes_switch_on_settings_changes_today(store, monkeypatch):
    """Protects the settings lanes form: it posts to DESK-0283's one view route, comes back to Settings saying so, and Today then
    shows only the chosen lane; nothing in the store changes."""
    now_t = ops.new(store, "Fix the header", "jre", "task")
    now_t.meta.update(status="in_progress", owner="james", assignee="james", lane="now")
    store.save(now_t)
    later_t = ops.new(store, "Tidy the footer", "jre", "task")
    later_t.meta.update(status="in_progress", owner="james", assignee="james", lane="later")
    store.save(later_t)
    before = sorted(p.read_bytes() for p in store.tickets_dir.glob("*.md"))
    c = _owner(store, monkeypatch)
    r = c.post("/lanes/view", data={"lane": "now", "show": "", "next": "/settings?lanes=saved#lanes"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/settings?lanes=saved")
    assert "Saved for this device" in c.get(r.headers["location"]).text
    today = c.get("/").text
    assert f"/ticket/{now_t.id}" in today and f"/ticket/{later_t.id}" not in today
    assert sorted(p.read_bytes() for p in store.tickets_dir.glob("*.md")) == before


def test_a_bad_notify_value_in_desk_toml_never_breaks_settings(store, monkeypatch):
    """Protects /settings: a word where desk.toml [notify] wants a number gives one plain sentence in the Phone and email box,
    and every other section still draws."""
    p = store.home / "desk.toml"
    p.write_bytes(p.read_bytes() + b'\n[notify]\ndaily_budget = "lots"\n')
    r = _owner(store, monkeypatch).get("/settings")
    assert r.status_code == 200 and "could not read the [notify] settings" in r.text
    assert 'id="lanes"' in r.text and 'id="motivation"' in r.text and 'id="delegations"' in r.text


# ---- the origin guard stays outermost ---------------------------------------------------------------------------------------

def test_the_origin_guard_runs_before_every_other_middleware(store):
    """Protects rule 80 item 10 after DESK-0284 added its middleware: LocalOnly is still the outermost layer, so a foreign Host is
    refused before the undo batch, identity or any route is touched."""
    from desk.ui.origin_guard import LocalOnly

    app = create_app(store.home)
    assert app.user_middleware[0].cls is LocalOnly, [m.cls.__name__ for m in app.user_middleware]
    r = TestClient(app, base_url="http://evil.example").post("/decide/answer", data={"id": "JRE-0001", "action": "approve"})
    assert r.status_code == 403 and "x-desk-undo" not in r.headers


# ---- keys on the new pages (DESK-0290 with 0285, 0287, 0288, 0284) -------------------------------------------------------------

def test_the_key_map_reaches_every_daily_loop_page():
    """Protects the key map: g chords reach Decide, Sort the inbox, the brief, Changes and the Undo log, and the sheet lists D and Z."""
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "src" / "desk" / "ui" / "static" / "keys.js").read_text(encoding="utf-8")
    for path in ("/decide", "/triage", "/brief", "/changes", "/undo"):
        assert f'"{path}"' in js, path
    assert 'row(dl, ["D"]' in js and 'row(dl, ["Z"]' in js


KEYS_ON_DECIDE = """<script>addEventListener('load',function(){
var st=sessionStorage,sleep=function(ms){return new Promise(function(r){setTimeout(r,ms);});},
 key=function(k){document.body.dispatchEvent(new KeyboardEvent('keydown',{key:k,bubbles:true,cancelable:true}));},
 done=function(o){fetch('/m',{method:'POST',body:JSON.stringify(o)});};
(async function(){await sleep(300);
 if(location.pathname==='/triage'){done({help:st.getItem('help'),sheet:st.getItem('sheet'),landed:location.pathname});return;}
 key('?');var h=document.getElementById('dm-help'),s=document.getElementById('keys-sheet');
 st.setItem('help',String(!!h&&!h.hidden));st.setItem('sheet',String(!!s&&!s.hidden));
 key('Escape');key('g');key('i');await sleep(4000);done({help:st.getItem('help'),sheet:st.getItem('sheet'),landed:'stayed'});
})();});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_question_mark_on_decide_opens_its_own_keys_and_g_i_sorts_the_inbox(store):
    """Protects the key seam in a real browser: on /decide, ? opens Decide's own key list and not the global sheet (so Y can never
    approve behind an open sheet), and g then i goes to Sort the inbox."""
    got = drive(store, KEYS_ON_DECIDE, "/decide")
    assert got == {"help": "true", "sheet": "false", "landed": "/triage"}, got
