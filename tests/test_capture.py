"""DESK-0286: one-field capture, paste-to-split, file drop and the capture hotkey. Protects: a sentence becomes a valid inbox ticket with a
guessed project and type, in Later unless it says urgent; the MVP line is kept; the creator is the viewer; every write is one batch the
Undo toast can name; pasted text proposes one row per ask and only ticked rows become tickets; files of any type attach within the limit
and programs are refused; the hotkey command prints its plan and never installs anything in a dry run."""

from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from fastapi.testclient import TestClient

from desk import activity, capture, cli, cli_capture, ops, rules
from desk.ui.app import create_app

JSON = {"Accept": "application/json"}


def _client(store):
    return TestClient(create_app(store.home), base_url="http://127.0.0.1:8799")  # the capture routes answer this computer only


def _tok(c):
    return c.app.state.tokens.issue()


def _capture(c, text, **kw):
    return c.post("/capture", data={"text": text, "token": _tok(c), **kw}, headers=JSON)


def _batches(store, tid):
    return {e.batch for e in activity.read(store.home, tid)}


# ---- guessing --------------------------------------------------------------------------------------------------------------

def test_guess_reads_project_type_and_lane_from_the_words(store):
    """The sentence alone picks project (key, title or #key), type (bug words) and Now for urgent words."""
    g = capture.guess(store, "new: fix the login crash on jRE today")
    assert (g.project, g.named, g.type, g.lane) == ("jre", True, "bug", "now")
    assert g.title == "Fix the login crash on jRE today"
    g = capture.guess(store, "decide which colour the #jeby-us header gets")
    assert (g.project, g.type, g.lane) == ("jeby-us", "decision", "later")
    assert "#" not in g.title
    g = capture.guess(store, "try the brief page on the phone")
    assert g.named is False and g.project in store.projects() and g.type == "feature" and g.lane == "later"


def test_ticket_id_names_its_project_and_long_sentences_are_cut_at_a_word(store):
    """A ticket id wins over a plain word, and the title never passes the title limit."""
    g = capture.guess(store, "look into DESK-0042 again, the jre one was fine")
    assert g.project == "desk" and g.type == "research"
    long = "please " + " ".join(["something"] * 20)
    t = capture.title_for(long)
    assert len(t) <= rules.TITLE_MAX and not t.endswith(" ") and t.split()[-1] == "something"


def test_default_project_comes_from_desk_toml_when_nothing_is_named(store):
    """[capture] project in desk.toml is where an unnamed sentence lands."""
    toml = store.home / "desk.toml"
    toml.write_text(toml.read_text(encoding="utf-8") + "\n[capture]\nproject = \"jeby-us\"\n", encoding="utf-8")
    from desk.store import Store

    assert capture.guess(Store(store.home), "water the plants").project == "jeby-us"


# ---- the palette and /capture -----------------------------------------------------------------------------------------------

def test_capture_creates_a_valid_inbox_ticket_in_later_with_undo(store):
    """Acceptance 1: capture from the palette creates the ticket and answers with its id and an Undo batch."""
    c = _client(store)
    r = _capture(c, "new: try the brief page on the phone #desk", how="palette")
    assert r.status_code == 200, r.text
    j = r.json()
    t = store.get(j["id"])
    assert t.id == "DESK-0001" and t.status == "inbox" and t.meta["lane"] == "later" and t.project == "desk"
    assert t.meta["title"] == "Try the brief page on the phone" and t.meta["creator"] == "james"
    assert "palette" in str(t.meta["source"])
    assert rules.validate_store(store)[0] == []  # passes `drew validate`
    q = parse_qs(urlsplit(j["url"]).query)
    assert urlsplit(j["url"]).path == f"/ticket/{t.id}" and q["undo"] == [j["batch"]] and t.id in q["msg"][0]
    assert j["batch"].startswith("b-") and len(j["batch"]) == 14
    assert _batches(store, t.id) == {j["batch"]}  # every event of the capture carries the batch, so Undo takes it back as one


def test_urgent_words_put_the_capture_in_now_and_mvp_is_stored(store):
    """Urgent goes to Now; the optional MVP slice line becomes smallest_step."""
    c = _client(store)
    j = _capture(c, "the jre export is broken, need it asap", mvp="export one file").json()
    t = store.get(j["id"])
    assert t.meta["lane"] == "now" and t.meta["type"] == "bug" and t.project == "jre" and t.meta["smallest_step"] == "export one file"


def test_plain_form_post_redirects_to_the_ticket_with_undo_and_message(store):
    """The /capture page without script: a 303 to the new ticket carrying undo and msg for the toast."""
    c = _client(store)
    assert "MVP slice?" in c.get("/capture").text
    r = c.post("/capture", data={"text": "add a dark logo to desk", "token": _tok(c)}, follow_redirects=False)
    assert r.status_code == 303
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert q["undo"][0].startswith("b-") and "DESK-0001" in unquote(q["msg"][0])
    assert c.get(r.headers["location"]).status_code == 200


def test_a_spent_key_writes_nothing_and_an_empty_sentence_is_refused(store):
    """A double Enter cannot make two tickets; an empty box makes none."""
    c = _client(store)
    tok = _tok(c)
    assert c.post("/capture", data={"text": "one thing for desk", "token": tok}, headers=JSON).status_code == 200
    again = c.post("/capture", data={"text": "one thing for desk", "token": tok}, headers=JSON)
    assert again.status_code == 409 and again.json()["token"]
    empty = _capture(c, "   ")
    assert empty.status_code == 422
    assert [t.id for t in store.tickets()] == ["DESK-0001"]


def test_creator_is_the_viewer_and_cannot_be_claimed(store):
    """Decision 110: the creator is the verified viewer; a posted creator or act-as cookie changes nothing."""
    c = _client(store)
    c.cookies.set("desk_act_as", "chaz")
    j = c.post("/capture", data={"text": "a thing for desk", "creator": "chaz", "token": _tok(c)}, headers=JSON).json()
    assert store.get(j["id"]).meta["creator"] == "james"


def test_palette_script_offers_the_create_row(store):
    """The palette asks capture.js for a Create row; Enter on it creates instead of opening a search."""
    c = _client(store)
    page = c.get("/").text
    assert '<script src="/static/capture.js?v=' in page
    js = c.get("/static/search.js").text
    assert "DeskCapture.row(q, d.total)" in js and "DeskCapture.create(input.value)" in js and "DeskCapture.only(q)" in js
    cj = c.get("/static/capture.js").text
    assert "window.DeskCapture" in cj and '"/capture"' in cj and "MVP slice?" in cj
    g = c.get("/capture/guess.json", params={"q": "new: urgent fix for jre"}).json()
    assert (g["project"], g["type"], g["lane_word"], g["capture"]) == ("jre", "bug", "Now", True)


# ---- paste to split ----------------------------------------------------------------------------------------------------------

PASTE = """From: a partner
Subject: next week

Hi! Thanks for the call today.
- Can you send the jre invoice by Friday?
- We had a nice lunch.
Please fix the desk search box on the phone. The rest looks fine.
TODO: book the venue
[10:42] Partner: should we pick a new logo for jeby.us?
Can you send the jre invoice by Friday?
"""


def test_split_proposes_one_row_per_ask(store):
    """Acceptance 2, first half: one proposed row per ask, statements and repeats left out, polite lead-ins taken off."""
    rows = capture.split_asks(store, PASTE)
    titles = [r.title for r in rows]
    assert titles == ["Send the jre invoice by Friday", "Fix the desk search box on the phone", "Book the venue", "Pick a new logo for jeby.us"]
    assert [r.project for r in rows][:2] == ["jre", "desk"] and rows[3].project == "jeby-us"
    assert rows[1].type == "bug" and rows[3].type == "decision"


def test_only_ticked_rows_become_tickets_in_one_batch(store):
    """Acceptance 2: only confirmed rows are written, with edited titles, and all of them share one Undo batch."""
    c = _client(store)
    page = c.post("/capture/paste", data={"text": PASTE})
    assert page.status_code == 200 and page.text.count('name="pick"') == 4
    rows = capture.split_asks(store, PASTE)
    data = {"token": _tok(c), "pick": ["0", "1"], "title": [r.title for r in rows], "project": [r.project for r in rows],
            "type": [r.type for r in rows], "said": [r.said for r in rows]}
    data["title"][1] = "Fix the desk search box on small phones"
    r = c.post("/capture/paste/create", data=data, follow_redirects=False)
    assert r.status_code == 303
    made = sorted(t.id for t in store.tickets())
    assert made == ["DESK-0001", "JRE-0001"]
    assert store.get("DESK-0001").meta["title"] == "Fix the desk search box on small phones"
    assert store.get("JRE-0001").meta["title"] == "Send the jre invoice by Friday"
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert _batches(store, "DESK-0001") == _batches(store, "JRE-0001") == {q["undo"][0]}
    shown = c.get(r.headers["location"]).text
    assert "DESK-0001" in shown and "JRE-0001" in shown
    assert rules.validate_store(store)[0] == []


def test_nothing_ticked_writes_nothing(store):
    """A save with no row ticked creates no ticket and says so."""
    c = _client(store)
    r = c.post("/capture/paste/create", data={"token": _tok(c), "title": ["Send it"], "project": ["jre"], "type": ["task"], "said": ["Can you send it?"]})
    assert r.status_code == 422 and "Tick at least one row" in r.text and store.tickets() == []


def _paste_form(c, store, projects):
    rows = capture.split_asks(store, PASTE)[:len(projects)]
    return {"token": _tok(c), "pick": [str(i) for i in range(len(rows))], "title": [r.title for r in rows], "project": projects,
            "type": [r.type for r in rows], "said": [r.said for r in rows]}


def test_a_bad_row_among_good_ones_writes_nothing_and_keeps_the_rows(store):
    """Every ticked row is checked before any is written: one unknown project means no ticket at all, and the edited rows come back."""
    c = _client(store)
    data = _paste_form(c, store, ["jre", "nope"])
    data["title"][0] = "Send the edited invoice"
    r = c.post("/capture/paste/create", data=data, follow_redirects=False)
    assert r.status_code == 422 and store.tickets() == []
    assert "no project called nope" in r.text and "Nothing was saved" in r.text and "Undo takes them back" not in r.text
    assert 'value="Send the edited invoice"' in r.text and r.text.count('name="pick"') == 2  # the person's edits are not lost


def test_a_write_that_fails_partway_still_offers_undo_and_syncs(store, monkeypatch):
    """If a write fails after some tickets were made, the answer carries undo=<batch> for them and the store is synced."""
    from desk.store import Store
    synced = []
    monkeypatch.setattr(Store, "sync_after", lambda self, msg="": synced.append(msg))
    real, calls = capture.create, []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("The disk refused the second ticket.")
        return real(*a, **k)

    monkeypatch.setattr(capture, "create", flaky)
    c = _client(store)
    r = c.post("/capture/paste/create", data=_paste_form(c, store, ["jre", "desk"]), follow_redirects=False)
    assert r.status_code == 303, r.text
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert [t.id for t in store.tickets()] == ["JRE-0001"] and q["made"] == ["JRE-0001"]
    assert _batches(store, "JRE-0001") == {q["undo"][0]} and "Undo takes them back" in q["msg"][0]
    assert synced, "the ticket already written was not synced"


# ---- files ------------------------------------------------------------------------------------------------------------------

def test_drop_on_a_ticket_attaches_any_file_type(store):
    """Acceptance 3: a PDF or a text file (not only images) attaches to the ticket, with an Undo batch."""
    t = ops.new(store, "Contract review", "desk")
    c = _client(store)
    r = c.post(f"/ticket/{t.id}/drop", data={"token": _tok(c)}, files={"file": ("terms v2.pdf", b"%PDF-1.4 fake", "application/pdf")}, headers=JSON)
    assert r.status_code == 200, r.text
    j = r.json()
    saved = store.ticket_assets_dir(t.id) / j["name"]
    assert saved.read_bytes() == b"%PDF-1.4 fake"
    assert f"assets/{t.id}/{j['name']}" in store.get(t.id).section("Attachments")
    assert j["batch"] in _batches(store, t.id) and "undo=" in j["url"]


@pytest.mark.parametrize("name", ["setup.exe", "run.BAT", "x.cmd", "a.ps1", "b.vbs", "c.js", "d.msi", "e.scr", "f.com", "g.lnk"])
def test_executables_are_refused_by_extension(store, name):
    """Acceptance 3: programs and scripts are refused whatever their size, on a ticket and in a new capture."""
    t = ops.new(store, "Files", "desk")
    c = _client(store)
    r = c.post(f"/ticket/{t.id}/drop", data={"token": _tok(c)}, files={"file": (name, b"MZ", "application/octet-stream")}, headers=JSON)
    assert r.status_code == 415 and "programs and scripts" in r.json()["error"]
    r2 = c.post("/capture", data={"text": "look at this", "token": _tok(c)}, files={"file": (name, b"MZ", "application/octet-stream")}, headers=JSON)
    assert r2.status_code == 415
    assert not store.ticket_assets_dir(t.id).exists() and [x.id for x in store.tickets()] == [t.id]


@pytest.mark.parametrize("name", ["evil.exe.", "evil.exe-", "evil.exe;", "evil.bat..", "evil.ps1 .", "evil.ps1%", "EVIL.EXE .", "sub/evil.cmd."])
def test_trailing_junk_cannot_smuggle_a_program_past_the_refusal(store, name):
    """A name the saver would turn into a refused one (evil.exe. is stored as evil.exe) is refused on a ticket and in a new capture."""
    t = ops.new(store, "Files", "desk")
    c = _client(store)
    r = c.post(f"/ticket/{t.id}/drop", data={"token": _tok(c)}, files={"file": (name, b"MZ", "application/octet-stream")}, headers=JSON)
    assert r.status_code == 415 and "programs and scripts" in r.json()["error"]
    r2 = c.post("/capture", data={"text": "look at this", "token": _tok(c)}, files={"file": (name, b"MZ", "application/octet-stream")}, headers=JSON)
    assert r2.status_code == 415
    assert not store.ticket_assets_dir(t.id).exists() and [x.id for x in store.tickets()] == [t.id]
    assert capture.stored_name(name).lower().rsplit(".", 1)[-1] in ("exe", "bat", "ps1", "cmd")  # the name it would have been saved as


@pytest.mark.parametrize("headers", [{"host": "rebind.attacker.example:8765"},
                                     {"host": "rebind.attacker.example:8765", "origin": "http://rebind.attacker.example:8765"},
                                     {"host": "127.0.0.1:8765", "origin": "http://evil.example"},
                                     {"host": "127.0.0.1:8765", "origin": "null"}])
def test_capture_routes_answer_only_this_computer(store, headers):
    """DNS rebinding or a foreign page cannot mint a capture key or write a ticket: a non-local Host or Origin gets 403 and nothing is written."""
    t = ops.new(store, "Files", "desk")
    c = _client(store)
    k = c.get("/capture/token.json", headers=headers)
    assert k.status_code == 403 and "token" not in k.text
    assert c.get("/capture/guess.json?q=hello", headers=headers).status_code == 403
    r = c.post("/capture", data={"text": "made from another site", "token": _tok(c)}, headers={**JSON, **headers})
    d = c.post(f"/ticket/{t.id}/drop", data={"token": _tok(c)}, files={"file": ("a.txt", b"x", "text/plain")}, headers={**JSON, **headers})
    p = c.post("/capture/paste/create", data=_paste_form(c, store, ["jre"]), headers=headers)
    assert (r.status_code, d.status_code, p.status_code) == (403, 403, 403)
    assert [x.id for x in store.tickets()] == [t.id] and not store.ticket_assets_dir(t.id).exists()
    for local in ({"host": "localhost:8765"}, {"host": "[::1]:8765", "origin": "http://[::1]:8765"}, {"host": "127.0.0.1:8765", "origin": "http://127.0.0.1:8765"}):
        assert c.get("/capture/token.json", headers=local).status_code == 200, local


def test_a_refused_drop_without_script_shows_the_reason_on_the_ticket_page(store):
    """A drop sent as a plain form (no JSON) that is refused lands back on the ticket with the refusal shown, not silently."""
    t = ops.new(store, "Files", "desk")
    c = _client(store)
    r = c.post(f"/ticket/{t.id}/drop", data={"token": _tok(c)}, files={"file": ("setup.exe", b"MZ", "application/octet-stream")}, follow_redirects=False)
    assert r.status_code == 303 and urlsplit(r.headers["location"]).path == f"/ticket/{t.id}"
    shown = c.get(r.headers["location"]).text
    assert "Desk does not take .exe files" in shown and not store.ticket_assets_dir(t.id).exists()


def test_files_over_the_size_limit_are_refused(store):
    """Acceptance 3: the desk.toml [attachments] max_mb limit applies to dropped files."""
    toml = store.home / "desk.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace("max_mb = 10", "max_mb = 0.001"), encoding="utf-8")
    t = ops.new(store, "Big", "desk")
    c = _client(store)
    r = c.post(f"/ticket/{t.id}/drop", data={"token": _tok(c)}, files={"file": ("big.csv", b"x" * 5000, "text/csv")}, headers=JSON)
    assert r.status_code == 413 and "limit" in r.json()["error"]


def test_drop_on_a_page_makes_a_ticket_around_the_file(store):
    """Dropping a file outside a ticket: one capture makes the ticket and attaches the file, as one batch."""
    c = _client(store)
    r = c.post("/capture", data={"text": "", "how": "file drop", "token": _tok(c)}, files={"file": ("q3 numbers.xlsx", b"PK\x03\x04", "application/vnd.ms-excel")}, headers=JSON)
    assert r.status_code == 200, r.text
    j = r.json()
    t = store.get(j["id"])
    assert t.meta["title"] == "Look at q3 numbers.xlsx" and "file drop" in str(t.meta["source"])
    assert "q3-numbers.xlsx" in t.section("Attachments")
    assert _batches(store, t.id) == {j["batch"]}
    assert rules.validate_store(store)[0] == []


# ---- hotkey -----------------------------------------------------------------------------------------------------------------

def test_hotkey_dry_run_prints_the_shortcut_path_and_key_and_installs_nothing(store, capsys, monkeypatch, tmp_path):
    """Acceptance 4: the command prints what it will create; the dry run (the default) runs nothing."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setattr(cli_capture, "run", lambda *a, **k: pytest.fail("a dry run must not run PowerShell"))
    cli.main(["--home", str(store.home), "capture", "hotkey", "--dry-run", "--port", "8799"])
    out = capsys.readouterr().out
    link = tmp_path / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Desk capture.lnk"
    assert str(link) in out and "Ctrl+Alt+J" in out and "http://127.0.0.1:8799/capture" in out and "Dry run, nothing changed" in out
    cli.main(["--home", str(store.home), "capture", "hotkey"])  # no flag is a dry run too
    assert "Dry run" in capsys.readouterr().out
    assert not link.exists()


def test_hotkey_script_creates_with_ctrl_alt_j_and_uninstall_removes():
    """Acceptance 4: the generated PowerShell makes a .lnk with the key and the address, and --uninstall's removes that same file."""
    link = Path(r"C:\Users\someone\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Desk capture.lnk")
    inst = cli_capture.script("install", link, "http://127.0.0.1:8765/capture")
    assert "WScript.Shell" in inst and "CreateShortcut($link)" in inst and "$s.Hotkey = 'CTRL+ALT+J'" in inst
    assert "$s.Arguments = 'http://127.0.0.1:8765/capture'" in inst and f"$link = '{link}'" in inst and "$s.Save()" in inst
    un = cli_capture.script("uninstall", link, "http://127.0.0.1:8765/capture")
    assert f"$link = '{link}'" in un and "Remove-Item -LiteralPath $link" in un and "CreateShortcut" not in un
    odd = cli_capture.script("install", Path("C:/it's here/x.lnk"), "http://127.0.0.1:1/capture")
    assert "'C:\\it''s here\\x.lnk'" in odd or "'C:/it''s here/x.lnk'" in odd  # a quote in the path cannot break out of the string


def test_hotkey_uninstall_dry_run_names_the_removal(store, capsys, monkeypatch, tmp_path):
    """--uninstall --dry-run prints the removal plan and touches nothing."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(cli_capture, "run", lambda *a, **k: pytest.fail("a dry run must not run PowerShell"))
    cli.main(["--home", str(store.home), "capture", "hotkey", "--uninstall", "--dry-run"])
    out = capsys.readouterr().out
    assert "Remove the shortcut" in out and "Remove-Item" in out and "drew capture hotkey --uninstall" in out
