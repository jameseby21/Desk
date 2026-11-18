"""DESK-0289: notifications on free channels, adapters for the best paid services, the privacy filter, budget and quiet hours.

No test reaches the network: the module transports are switched offline, sockets refuse to connect, and every adapter is driven
through a fake transport that records the request."""

from __future__ import annotations

import base64
import datetime as dt
import json
import re
import socket
import urllib.parse

import pytest

from desk import notify_channels as nc, notify_queue, notify_send as ns
from desk.cli import main

PRIVATE = "Zorblax"  # a fictional private term, only in the test denylist
FAKE_SCANNER = '''import re
from pathlib import Path
def load(path):
    p = Path(path)
    if not p.exists():
        return []
    return [(t, re.compile(r"(?<![\\w])" + re.escape(t) + r"(?![\\w])", re.I)) for t in p.read_text(encoding="utf-8").split() if t]
'''


@pytest.fixture(autouse=True)
def _offline(tmp_path, monkeypatch):
    """Every test: secrets in a temp file, a fictional denylist, transports offline, sockets refuse to connect."""
    monkeypatch.setenv("DESK_NOTIFY_ENV", str(tmp_path / "secrets" / "notify.env"))
    monkeypatch.setenv("DESK_NOTIFY_OFFLINE", "1")
    scan = tmp_path / "scan" / "private_scan.py"
    scan.parent.mkdir()
    scan.write_bytes(FAKE_SCANNER.encode("utf-8"))
    (scan.parent / "deny.txt").write_bytes(f"{PRIVATE}\n".encode("utf-8"))
    monkeypatch.setenv("DESK_PRIVATE_SCAN", str(scan))
    monkeypatch.setenv("JEBY_DENYLIST", str(scan.parent / "deny.txt"))

    def no_net(*a, **k):
        raise AssertionError("a test tried to reach the network")

    monkeypatch.setattr(socket.socket, "connect", no_net)
    monkeypatch.setattr(socket, "create_connection", no_net)


class FakePost:
    def __init__(self, status=200, body='{"status":1}'):
        self.calls, self.status, self.body = [], status, body

    def __call__(self, url, data, headers):
        self.calls.append({"url": url, "data": data, "headers": headers})
        return nc.Response(self.status, self.body)


class FakeSmtp:
    instances: list = []

    def __init__(self, host, port):
        self.host, self.port, self.steps, self.sent = host, port, [], []
        FakeSmtp.instances.append(self)

    def starttls(self, context=None):
        self.steps.append("starttls")
        self.context = context

    def login(self, user, pw):
        self.steps.append(("login", user, pw))

    def send_message(self, msg):
        self.sent.append(msg)

    def quit(self):
        self.steps.append("quit")


def _toml(store, block: str) -> None:
    p = store.home / "desk.toml"
    p.write_bytes(p.read_bytes() + ("\n[notify]\n" + block + "\n").encode("utf-8"))


def _ticket(store, title, context="", blocked_on="james"):
    t = store.create(title, "desk", "task", owner="james", assignee="main")
    t.meta["blocked_on"] = blocked_on
    if context:
        t.sections["Context"] = context + "\n"
        t.sections["Report"] = "REPORT-SENTINEL text from the worker\n"
    store.save(t)
    return t


def _pair_secrets():
    nc.write_secret("NTFY_TOPIC", "t" * 32)


# -- pairing and secrets ----------------------------------------------------------------------

def test_pair_makes_a_random_topic_in_the_secrets_file_and_keeps_it(store, capsys):
    """`drew notify pair` writes a 32-character random topic outside the repo, prints the phone steps, and does not replace it unasked."""
    main(["--home", str(store.home), "notify", "pair"])
    out = capsys.readouterr().out
    topic = nc.read_secrets()["NTFY_TOPIC"]
    assert re.fullmatch(r"[a-z0-9]{32}", topic)
    assert topic in out and "Install \"ntfy\"" in out and f"https://ntfy.sh/{topic}" in out
    main(["--home", str(store.home), "notify", "pair"])
    assert nc.read_secrets()["NTFY_TOPIC"] == topic
    main(["--home", str(store.home), "notify", "pair", "--new"])
    assert nc.read_secrets()["NTFY_TOPIC"] != topic


def test_notify_test_sends_one_line_to_each_channel_that_is_on(store):
    """`drew notify test` sends exactly one 'Desk test: notifications work' line per channel that is on, and --channel keeps it to one."""
    _pair_secrets()
    for k, v in (("SMTP_USER", "owner@example.com"), ("SMTP_PASSWORD", "pw")):
        nc.write_secret(k, v)
    FakeSmtp.instances.clear()
    post = FakePost()
    note, results, names = ns.send_test(store.home, post=post, smtp=FakeSmtp)
    assert names == ["ntfy", "smtp"] and [r.ok for r in results] == [True, True]
    assert len(post.calls) == 1 and post.calls[0]["url"] == "https://ntfy.sh/" + "t" * 32
    assert post.calls[0]["data"].decode("utf-8").startswith("Desk test: notifications work")
    assert len(FakeSmtp.instances) == 1 and FakeSmtp.instances[0].sent[0].get_content().startswith("Desk test: notifications work")
    FakeSmtp.instances.clear()
    post = FakePost()
    _, results, names = ns.send_test(store.home, "ntfy", post=post, smtp=FakeSmtp)
    assert names == ["ntfy"] and len(post.calls) == 1 and FakeSmtp.instances == []


def test_notify_test_on_a_channel_that_is_not_on_says_what_it_needs(store, capsys):
    """`drew notify test --channel smtp` with only the phone paired names the missing keys instead of saying nothing is paired."""
    _pair_secrets()
    with pytest.raises(SystemExit) as e:
        main(["--home", str(store.home), "notify", "test", "--channel", "smtp"])
    out = capsys.readouterr().out
    assert e.value.code == 1
    assert "smtp is not on" in out and "SMTP_USER" in out and "SMTP_PASSWORD" in out and "notify pair" not in out
    with pytest.raises(SystemExit):
        main(["--home", str(store.home), "notify", "test", "--channel", "pushover"])
    assert "not in [notify] channels" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["--home", str(store.home), "notify", "test", "--channel", "carrier-pigeon"])
    assert "There is no channel named carrier-pigeon" in capsys.readouterr().out


def test_secrets_file_inside_a_repository_is_refused(tmp_path):
    """Secrets never land inside a repository (rule 30): writing notify.env under a folder with .git raises."""
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    with pytest.raises(ValueError, match="outside every repository"):
        nc.write_secret("NTFY_TOPIC", "x", tmp_path / "repo" / "notify.env")
    assert not (tmp_path / "repo" / "notify.env").exists()


def test_status_masks_every_secret(store, capsys):
    """`drew notify status` names which keys are set but never prints a value."""
    nc.write_secret("NTFY_TOPIC", "secrettopicvalue1234567890abcdef")
    nc.write_secret("SMTP_PASSWORD", "app-password-value")
    main(["--home", str(store.home), "notify", "status"])
    out = capsys.readouterr().out
    assert "secrettopicvalue" not in out and "app-password-value" not in out
    assert "ntfy" in out and "1 channel(s) on: ntfy" in out
    main(["--home", str(store.home), "notify", "status", "--json"])
    assert "secrettopicvalue" not in capsys.readouterr().out


# -- the privacy filter -----------------------------------------------------------------------

def test_brief_dry_run_prints_ids_and_titles_never_context_or_report(store, capsys):
    """How to validate line 1: the dry run is the exact text, with no Context or Report text in it."""
    t = _ticket(store, "Pick the logo colour", context="CONTEXT-SENTINEL long private reasoning")
    main(["--home", str(store.home), "notify", "brief", "--dry-run"])
    out = capsys.readouterr().out
    assert t.id in out and "Pick the logo colour" in out
    assert "CONTEXT-SENTINEL" not in out and "REPORT-SENTINEL" not in out


def test_a_title_with_a_private_term_is_replaced_never_sent(store):
    """A title that hits the private-term scan becomes '(title withheld)'; the term is not in the message."""
    t = _ticket(store, f"Call {PRIVATE} about the lease")
    note = ns.brief_note(store, dt.date(2026, 11, 15))
    assert PRIVATE.lower() not in note.body.lower()
    assert f"{t.id}: {ns.WITHHELD}" in note.body


def test_without_the_private_scan_only_ids_are_sent(store, monkeypatch):
    """When the private-term scan cannot be loaded, titles are left out, ids go, and the message says why."""
    monkeypatch.setenv("DESK_PRIVATE_SCAN", str(store.home / "missing.py"))
    t = _ticket(store, "Harmless title here")
    note = ns.brief_note(store, dt.date(2026, 11, 15))
    assert t.id in note.body and "Harmless title here" not in note.body
    assert "Titles left out" in note.body


def test_ids_with_a_hyphenated_prefix_travel_whole(store):
    """A project key with a hyphen (prefix JEBY-US) keeps its whole id in the brief, a queued line and a withheld line, never '?' or 'US-0001'."""
    t = store.create("Pick the hosting plan", "jeby-us", "task", owner="james", assignee="main")
    t.meta["blocked_on"] = "james"
    store.save(t)
    assert t.id.startswith("JEBY-US-")
    note = ns.brief_note(store, dt.date(2026, 11, 15))
    assert f"{t.id}: Pick the hosting plan" in note.body and "?:" not in note.body
    scan = ns.Scanner([("x", re.compile(PRIVATE))], True)
    assert ns.item_line({"kind": "alert", "text": f"{PRIVATE} again", "ids": [t.id]}, scan, {}) == t.id
    assert ns.final_pass(f"- {t.id}: {PRIVATE} again", scan) == f"- {t.id} {ns.WITHHELD}"


def test_a_withheld_item_without_ids_never_echoes_its_kind_field():
    """A queued item whose text is withheld and has no ids goes as '1 note (text withheld)'; a free-text kind is not echoed."""
    scan = ns.Scanner([], True)
    line = ns.item_line({"kind": f"{PRIVATE} said", "text": "password = hunter2hunter2", "ids": []}, scan, {})
    assert line == "1 note (text withheld)"


def test_a_withheld_kind_is_named_only_from_the_fixed_list_even_without_the_scan():
    """With the private-term scan not ready, a writer-supplied kind that looks like a plain word is still not echoed; only known kinds are."""
    for ready in (False, True):
        scan = ns.Scanner([], ready)
        assert ns.item_line({"kind": f"{PRIVATE.lower()}-family", "text": "", "ids": []}, scan, {}) == "1 note (text withheld)"
        assert ns.item_line({"kind": "alert", "text": "", "ids": []}, scan, {}) == "1 alert (text withheld)"


def test_brief_fields_outside_the_allow_list_are_dropped():
    """The brief filter keeps ids, titles, asks, dates and counts; any other field (a note, a context) is dropped."""
    scan = ns.Scanner([], True)
    out = ns.clean_brief({"date": "2026-11-15", "decisions": [{"id": "DESK-0001", "title": "Ok", "context": "SECRET-CONTEXT"}],
                          "notes": "free text that should not travel", "counts": {"open": 3}}, scan)
    assert out == {"date": "2026-11-15", "decisions": [{"id": "DESK-0001", "title": "Ok"}], "counts": {"open": 3}}


def test_queued_text_with_a_private_term_or_a_secret_goes_as_ids(store):
    """A queued line that hits the private-term or secret scan is replaced by its ids; the term and the key are never posted."""
    _pair_secrets()
    t = _ticket(store, "Plain title")
    notify_queue.enqueue(store.home, "alert", f"{PRIVATE} called about it", [t.id])
    notify_queue.enqueue(store.home, "alert", "password = hunter2hunter2", [t.id])
    post = FakePost()
    rep = ns.drain(store, now=dt.datetime(2026, 11, 15, 12, 0), post=post)
    assert rep.sent == 2
    bodies = [c["data"].decode("utf-8") for c in post.calls]
    assert all(PRIVATE not in b and "hunter2" not in b for b in bodies)
    assert all(t.id in b for b in bodies)


# -- budget and quiet hours -------------------------------------------------------------------

def test_past_the_budget_items_fold_into_the_next_brief(store):
    """With daily_budget = 2, four queued items send two and hold two; the next brief carries the two and then clears them."""
    _toml(store, "daily_budget = 2\nquiet_start = \"\"")
    _pair_secrets()
    for i in range(4):
        notify_queue.enqueue(store.home, "alert", f"line {i}", [])
    post = FakePost()
    rep = ns.drain(store, now=dt.datetime(2026, 11, 15, 12, 0), post=post)
    assert (rep.sent, rep.held, len(post.calls)) == (2, 2, 2)
    assert notify_queue.pending(store.home) == []
    note, results, _ = ns.send_brief(store, now=dt.datetime(2026, 11, 16, 7, 30), post=post)
    assert "Held since the last brief: 2" in note.body and "line 2" in note.body and "line 3" in note.body
    assert results and all(r.ok for r in results)
    assert ns.load_state(store.home)["held"] == []


def test_quiet_hours_hold_even_urgent_items_unless_desk_toml_allows(store):
    """Inside quiet hours everything is held; an urgent item goes only when urgent_bypasses_quiet = true."""
    _pair_secrets()
    notify_queue.enqueue(store.home, "alert", "late urgent thing", [], urgent=True)
    post = FakePost()
    rep = ns.drain(store, now=dt.datetime(2026, 11, 15, 23, 0), post=post)
    assert (rep.sent, rep.held, post.calls) == (0, 1, [])
    _toml(store, "urgent_bypasses_quiet = true")
    notify_queue.enqueue(store.home, "alert", "second urgent thing", [], urgent=True)
    rep = ns.drain(store, now=dt.datetime(2026, 11, 15, 23, 5), post=post)
    assert rep.sent == 1 and post.calls[0]["headers"]["Priority"] == "high"


def test_quiet_hours_wrap_past_midnight():
    """21:00 to 07:00 is quiet at 23:00 and 06:59, not at 07:00 or noon; an empty start turns it off."""
    st = {"quiet_start": "21:00", "quiet_end": "07:00"}
    at = lambda h, m=0: dt.datetime(2026, 11, 15, h, m)  # noqa: E731
    assert ns.in_quiet_hours(at(23), st) and ns.in_quiet_hours(at(6, 59), st)
    assert not ns.in_quiet_hours(at(7), st) and not ns.in_quiet_hours(at(12), st)
    assert not ns.in_quiet_hours(at(23), {"quiet_start": "", "quiet_end": "07:00"})


def test_a_malformed_queue_line_is_skipped_and_never_blocks_the_rest(store, capsys):
    """One corrupt line in the queue is counted and stepped over; the good lines around it still send and status still works."""
    _pair_secrets()
    q = store.home / notify_queue.FILE
    notify_queue.enqueue(store.home, "alert", "first good line", [])
    with q.open("ab") as f:
        f.write(b'{"kind": "alert", "text": broken\n["not", "a", "dict"]\n')
    notify_queue.enqueue(store.home, "alert", "second good line", [])
    assert [i["text"] for i in notify_queue.pending(store.home)] == ["first good line", "second good line"]
    assert ns.status(store)["queued"] == 2 and ns.status(store)["unreadable"] == 2
    main(["--home", str(store.home), "notify", "status"])
    assert "2 unreadable" in capsys.readouterr().out
    post = FakePost()
    rep = ns.drain(store, now=dt.datetime(2026, 11, 15, 12, 0), post=post)
    assert (rep.sent, rep.skipped, len(post.calls)) == (2, 2, 2)
    assert notify_queue.pending(store.home) == [] and ns.status(store)["unreadable"] == 0
    with q.open("ab") as f:
        f.write(b"not json at all\n")
    rep = ns.drain(store, now=dt.datetime(2026, 11, 15, 12, 5), post=post)
    assert rep.skipped == 1 and "1 unreadable" in rep.reason
    assert ns.status(store)["unreadable"] == 0


def test_drain_with_no_channel_keeps_the_queue(store):
    """With nothing paired, drain sends nothing and leaves every item queued for later."""
    notify_queue.enqueue(store.home, "alert", "waiting", [])
    rep = ns.drain(store, now=dt.datetime(2026, 11, 15, 12, 0), post=FakePost())
    assert rep.sent == 0 and "No channel is on" in rep.reason
    assert len(notify_queue.pending(store.home)) == 1


def test_a_failed_send_is_kept_and_its_detail_never_shows_the_topic(store):
    """When every channel fails, the item is held for the brief, and the failure text has the secret masked."""
    _pair_secrets()

    def boom(url, data, headers):
        raise OSError(f"cannot reach {url}")

    notify_queue.enqueue(store.home, "alert", "will fail", [])
    rep = ns.drain(store, now=dt.datetime(2026, 11, 15, 12, 0), post=boom)
    assert rep.failed == 1 and len(ns.load_state(store.home)["held"]) == 1
    assert "t" * 32 not in " ".join(rep.lines)


# -- adapters, each with a fake transport -----------------------------------------------------

def test_ntfy_posts_the_body_to_the_topic_url():
    """ntfy: POST https://ntfy.sh/<topic>, body is the text, Title and Priority headers (docs.ntfy.sh/publish)."""
    post = FakePost()
    ch = next(c for c in nc.all_channels() if c.name == "ntfy")
    r = ch.send(nc.Note("Desk brief", "3 decisions"), {"NTFY_TOPIC": "abc"}, post=post)
    assert r.ok and post.calls[0]["url"] == "https://ntfy.sh/abc"
    assert post.calls[0]["data"] == b"3 decisions" and post.calls[0]["headers"]["Title"] == "Desk brief"


def test_smtp_uses_starttls_and_the_app_password():
    """smtp: STARTTLS on 587, login with the owner's address and app password, one message to SMTP_TO."""
    FakeSmtp.instances.clear()
    ch = next(c for c in nc.all_channels() if c.name == "smtp")
    sec = {"SMTP_USER": "owner@example.com", "SMTP_PASSWORD": "pw", "SMTP_TO": "phone@example.com"}
    r = ch.send(nc.Note("Desk brief", "body"), sec, smtp=FakeSmtp)
    s = FakeSmtp.instances[0]
    assert r.ok and (s.host, s.port) == ("smtp.gmail.com", 587)
    assert s.steps[:2] == ["starttls", ("login", "owner@example.com", "pw")]
    assert s.sent[0]["To"] == "phone@example.com" and s.sent[0]["Subject"] == "Desk brief"


def test_smtp_tls_verifies_the_server_certificate_and_hostname(monkeypatch):
    """The app password only travels over TLS that checks the certificate and hostname (no man-in-the-middle on STARTTLS or port 465)."""
    import ssl

    FakeSmtp.instances.clear()
    ch = next(c for c in nc.all_channels() if c.name == "smtp")
    ch.send(nc.Note("Desk", "body"), {"SMTP_USER": "owner@example.com", "SMTP_PASSWORD": "pw"}, smtp=FakeSmtp)
    ctx = FakeSmtp.instances[0].context
    assert ctx is not None and ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True
    seen = {}

    def fake_ssl(host, port, **kw):
        seen.update(kw)
        return FakeSmtp(host, port)

    monkeypatch.delenv("DESK_NOTIFY_OFFLINE")
    monkeypatch.setattr(nc.smtplib, "SMTP_SSL", fake_ssl)
    nc.smtp_connect("smtp.example.com", 465)
    ctx = seen.get("context")
    assert ctx is not None and ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True


def test_postmark_request_matches_its_api():
    """postmark: POST https://api.postmarkapp.com/email, X-Postmark-Server-Token, From/To/Subject/TextBody JSON."""
    post = FakePost()
    ch = next(c for c in nc.all_channels() if c.name == "postmark")
    r = ch.send(nc.Note("S", "B"), {"POSTMARK_TOKEN": "tok", "POSTMARK_FROM": "a@example.com", "POSTMARK_TO": "b@example.com"}, post=post)
    c = post.calls[0]
    assert r.ok and c["url"] == "https://api.postmarkapp.com/email" and c["headers"]["X-Postmark-Server-Token"] == "tok"
    assert json.loads(c["data"]) == {"From": "a@example.com", "To": "b@example.com", "Subject": "S", "TextBody": "B", "MessageStream": "outbound"}


def test_twilio_request_matches_its_api():
    """twilio: POST .../Accounts/<sid>/Messages.json with Basic auth and To, From, Body form fields."""
    post = FakePost(201, '{"sid":"SM1","status":"queued"}')
    ch = next(c for c in nc.all_channels() if c.name == "twilio")
    sec = {"TWILIO_ACCOUNT_SID": "AC1", "TWILIO_AUTH_TOKEN": "tok", "TWILIO_FROM": "+15550000001", "TWILIO_TO": "+15550000002"}
    r = ch.send(nc.Note("Desk", "hello"), sec, post=post)
    c = post.calls[0]
    assert r.ok and c["url"] == "https://api.twilio.com/2010-04-01/Accounts/AC1/Messages.json"
    assert c["headers"]["Authorization"] == "Basic " + base64.b64encode(b"AC1:tok").decode()
    assert urllib.parse.parse_qs(c["data"].decode()) == {"To": ["+15550000002"], "From": ["+15550000001"], "Body": ["Desk: hello"]}


def test_pushover_success_needs_status_1():
    """pushover: POST https://api.pushover.net/1/messages.json; HTTP 200 with status 0 is a failure, not a send."""
    ch = next(c for c in nc.all_channels() if c.name == "pushover")
    sec = {"PUSHOVER_TOKEN": "a", "PUSHOVER_USER": "u"}
    post = FakePost()
    assert ch.send(nc.Note("T", "M"), sec, post=post).ok
    assert post.calls[0]["url"] == "https://api.pushover.net/1/messages.json"
    assert urllib.parse.parse_qs(post.calls[0]["data"].decode())["message"] == ["M"]
    assert not ch.send(nc.Note("T", "M"), sec, post=FakePost(200, '{"status":0}')).ok


def test_paid_adapters_are_off_until_named_in_desk_toml(store):
    """With every key present, only the free channels are on by default; naming twilio in [notify] channels turns it on."""
    sec = {k: "x" for c in nc.all_channels() for k in c.needs}
    assert [c.name for c in ns.active_channels(ns.settings(store.home), sec)] == ["ntfy", "smtp"]
    _toml(store, 'channels = ["ntfy", "twilio"]')
    assert [c.name for c in ns.active_channels(ns.settings(store.home), sec)] == ["ntfy", "twilio"]


def test_the_real_transport_is_offline_in_tests():
    """The module transport refuses to run while DESK_NOTIFY_OFFLINE is set, so a test can never send a real message."""
    with pytest.raises(nc.Offline):
        nc.http_post("https://ntfy.sh/x", b"x", {})
    with pytest.raises(nc.Offline):
        nc.smtp_connect("smtp.gmail.com", 587)


# -- schedule ---------------------------------------------------------------------------------

def test_schedule_prints_the_commands_and_installs_nothing(store, capsys, monkeypatch):
    """`drew notify schedule --print` prints two schtasks lines; without --print it refuses; nothing is ever run."""
    import subprocess

    real, ran = subprocess.run, []

    def watch(cmd, *a, **k):
        ran.append(cmd)
        assert "schtasks" not in " ".join(map(str, cmd if isinstance(cmd, (list, tuple)) else [cmd])).lower(), "installed a task"
        return real(cmd, *a, **k)

    monkeypatch.setattr(subprocess, "run", watch)
    main(["--home", str(store.home), "notify", "schedule", "--print"])
    out = capsys.readouterr().out
    assert out.count("schtasks /Create") == 2 and "/SC DAILY /ST 07:30" in out and "/SC MINUTE /MO 15" in out
    with pytest.raises(SystemExit) as e:
        main(["--home", str(store.home), "notify", "schedule"])
    assert "never installs" in str(e.value)


def test_schedule_says_command_prompt_because_powershell_splits_the_quoted_task(store, capsys):
    """The schtasks lines use cmd.exe quoting, which PowerShell 5.1 breaks apart; the printed steps name the Command Prompt, never PowerShell."""
    main(["--home", str(store.home), "notify", "schedule", "--print"])
    head = capsys.readouterr().out.splitlines()[0]
    assert "Command Prompt" in head and "cmd" in head
    assert "Paste these into PowerShell" not in head and "PowerShell or" not in head


@pytest.mark.skipif(not hasattr(__import__("ctypes"), "windll"), reason="parses the line the way Windows does")
def test_schedule_task_run_value_is_one_argument_in_a_command_prompt(tmp_path):
    """Parsed by Windows' own rules (what schtasks receives from cmd.exe), /TR is one argument that names pythonw and the home, spaces included."""
    import ctypes
    from ctypes import wintypes

    exe = tmp_path / "with space" / "python.exe"
    home = tmp_path / "home dir"
    parse = ctypes.windll.shell32.CommandLineToArgvW
    parse.restype = ctypes.POINTER(wintypes.LPWSTR)
    for line in ns.schedule_commands(home, dict(ns.DEFAULTS), python=str(exe)):
        n = ctypes.c_int()
        argv = parse(line, ctypes.byref(n))
        args = [argv[i] for i in range(n.value)]
        ctypes.windll.kernel32.LocalFree(argv)
        tr = args[args.index("/TR") + 1]
        assert tr.startswith(f'"{exe}" -m desk.cli --home "{home}" notify ')
        assert args[-1] == tr
