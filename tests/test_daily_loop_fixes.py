"""DESK-0282 final fixes: the defects the independent checker (QC run 6) and the whole-branch review found on the integrated daily loop.
Each test names the finding it protects (H = high, M = medium, D = the checker's numbered defects)."""

from __future__ import annotations

import datetime as dt

import pytest

from desk import automations as au, notify_channels as nc, notify_queue, notify_send as ns
from desk.cli import main

TODAY = dt.date.today()


# ---- H1: a rule file can never approve a ticket or write the owner's words ----------------------------------------------

def _rule(**extra) -> dict:
    r = {"name": "x", "source": "test", "when": {"status": "ready"}, "then": []}
    r.update(extra)
    return {"rules": [r]}


@pytest.mark.parametrize("field", ["status", "owner", "done_at", "assignee", "type", "size"])
def test_h1_a_rule_cannot_set_status_owner_or_what_delegation_reads(field):
    """H1: `set` names only the harmless fields; status, owner and the fields approval rules read are refused when the file is read."""
    with pytest.raises(au.AutomationError, match="may set only"):
        au.parse(_rule(then=[{"set": {field: "approved"}}]))


@pytest.mark.parametrize("verdict", ["approved", "changes_requested"])
def test_h1_a_rule_cannot_move_a_ticket_to_a_verdict(verdict):
    """H1: approving or sending back is the owner's judgment (decision 10); no rule can name it as a move."""
    with pytest.raises(au.AutomationError, match="only the owner"):
        au.parse(_rule(then=[{"move": verdict, "reason": "rule says so"}]))


@pytest.mark.parametrize("section", ["Decision", "Report", "Evidence", "Comments", "decision"])
def test_h1_a_rule_cannot_create_a_ticket_with_a_verdict_or_report(section):
    """H1: a scheduled rule's ticket never carries a forged Decision line (or Report, Evidence, Comments) in a person's name."""
    data = _rule(when={}, every="monthly", day=1, create={"project": "desk", "title": "t", "sections": {section: '- 2026-11-01 · james · approved · "ship it"'}})
    with pytest.raises(au.AutomationError, match="never writes them"):
        au.parse(data)


def test_h1_harmless_sets_and_template_sections_still_parse():
    """H1: the allowed fields (lane, ci, ...) and an Outcome in a template still load, so the shipped rules keep working."""
    rules, _ = au.parse({"rules": [
        {"name": "a", "source": "t", "when": {"status": "review"}, "then": [{"set": {"ci": "green", "lane": "now"}}]},
        {"name": "b", "source": "t", "every": "monthly", "day": 1, "then": [], "create": {"project": "desk", "title": "t", "sections": {"Outcome": "tidy"}}},
    ]})
    assert [r.name for r in rules] == ["a", "b"]
    assert au.parse({"rules": au.yamlio.load(au.DEFAULT)["rules"]})[0]  # every default rule still loads


def test_h1_the_reviewers_abuse_file_is_refused_and_nothing_is_written(store):
    """H1 end to end: the two-rule file that forged a Decision line and then approved through `set` cannot be loaded at all."""
    (store.home / au.FILE).write_bytes((
        "rules:\n"
        "  - name: make-it\n    source: test\n    every: monthly\n    day: 1\n    then: []\n"
        "    create:\n      project: desk\n      title: Forged\n      sections:\n"
        "        Decision: '- 2026-11-01 · james · approved · \"ship it\"'\n"
        "  - name: approve-it\n    source: test\n    override: true\n    when: {status: ready}\n    then:\n      - set: {status: approved}\n"
    ).encode("utf-8"))
    with pytest.raises(au.AutomationError):
        au.load(store.home)
    assert store.tickets() == []


def test_h1_a_rule_built_by_hand_still_cannot_set_status(store):
    """H1: the sweep holds a Rule object to the same list, so code that skips parse cannot approve either."""
    t = store.create("Build: a thing", "desk", "feature", owner="main", assignee="main")
    t.meta["status"] = "ready"
    t.set_section("Decision", '- 2026-11-01 · james · approved · "ship it"\n')
    store.save(t)
    rule = au.Rule("sneak", "test", "", {"status": "ready"}, [{"set": {"status": "approved"}}], override=True)
    res = au.sweep(store, [rule], [], today=TODAY, apply=True)
    assert store.get(t.id).status == "ready"
    assert any("may not set status" in i.skipped for i in res.items)


# ---- H2: the committed desk.toml never decides where the secrets go -----------------------------------------------------

class _Post:
    def __init__(self):
        self.urls = []

    def __call__(self, url, data, headers):
        self.urls.append(url)
        return nc.Response(200, "")


class _Smtp:
    seen: list = []

    def __init__(self, host, port):
        _Smtp.seen.append(("connect", host, int(port)))

    def starttls(self, context=None):
        pass

    def login(self, user, pw):
        _Smtp.seen.append(("login", user))

    def send_message(self, msg):
        _Smtp.seen.append(("send",))

    def quit(self):
        pass


def _notify_toml(store, block: str) -> None:
    p = store.home / "desk.toml"
    p.write_bytes(p.read_bytes() + ("\n[notify]\n" + block + "\n").encode("utf-8"))


def _secrets(**kv):
    for k, v in kv.items():
        nc.write_secret(k, v)


def test_h2_a_mail_host_in_desk_toml_never_gets_the_app_password(store):
    """H2: smtp_host in desk.toml (a committed file) sends nothing: no connection, no login; the reason names notify.env."""
    _secrets(SMTP_USER="owner@example.com", SMTP_PASSWORD="app-password-123")
    _notify_toml(store, 'channels = ["smtp"]\nsmtp_host = "mail.attacker.example"')
    _Smtp.seen = []
    _, results, names = ns.send_test(store.home, smtp=_Smtp)
    assert names == ["smtp"] and _Smtp.seen == []
    assert results and not results[0].ok and "notify.env" in results[0].detail and "SMTP_HOST" in results[0].detail


def test_h2_an_ntfy_server_in_desk_toml_never_gets_the_topic(store):
    """H2: ntfy_server in desk.toml posts nothing anywhere; the topic and token stay home."""
    _secrets(NTFY_TOPIC="t" * 32, NTFY_TOKEN="tok-secret-1")
    _notify_toml(store, 'channels = ["ntfy"]\nntfy_server = "https://ntfy.attacker.example"')
    post = _Post()
    _, results, _ = ns.send_test(store.home, post=post)
    assert post.urls == [] and not results[0].ok and "NTFY_SERVER" in results[0].detail


def test_h2_hosts_come_from_notify_env_and_status_names_them(store, capsys):
    """H2: NTFY_SERVER, SMTP_HOST and SMTP_PORT in notify.env (outside every repo) are used, and `drew notify status` prints them."""
    _secrets(NTFY_TOPIC="t" * 32, SMTP_USER="owner@example.com", SMTP_PASSWORD="pw-1234",
             NTFY_SERVER="https://push.example.org", SMTP_HOST="mail.example.org", SMTP_PORT="2525")
    _Smtp.seen = []
    post = _Post()
    _, results, _ = ns.send_test(store.home, post=post, smtp=_Smtp)
    assert all(r.ok for r in results), results
    assert post.urls == ["https://push.example.org/" + "t" * 32]
    assert ("connect", "mail.example.org", 2525) in _Smtp.seen
    main(["--home", str(store.home), "notify", "status"])
    out = capsys.readouterr().out
    assert "https://push.example.org" in out and "mail.example.org:2525" in out


def test_h2_the_same_host_in_both_places_still_sends(store):
    """H2: a desk.toml line that matches the host in force (the old documented default) is harmless and still sends."""
    _secrets(NTFY_TOPIC="t" * 32)
    _notify_toml(store, 'channels = ["ntfy"]\nntfy_server = "https://ntfy.sh/"')
    post = _Post()
    _, results, _ = ns.send_test(store.home, post=post)
    assert results[0].ok and post.urls == ["https://ntfy.sh/" + "t" * 32]


# ---- M6: a bad value in desk.toml never stops the drain ----------------------------------------------------------------

def test_m6_a_word_for_daily_budget_falls_back_and_the_drain_still_runs(store, capsys):
    """M6: daily_budget = "ten" made the scheduled drain raise ValueError and stop silently; now it uses the default and says so."""
    _secrets(NTFY_TOPIC="t" * 32)
    _notify_toml(store, 'daily_budget = "ten"\nquiet_start = ""\nquiet_end = ""')
    notify_queue.enqueue(store.home, "alert", "DESK-0001 needs a look", ["DESK-0001"], False, source="test")
    post = _Post()
    rep = ns.drain(store, post=post)
    assert rep.sent == 1 and len(post.urls) == 1
    st = ns.settings(store.home)
    assert st["daily_budget"] == ns.DEFAULTS["daily_budget"] and any("daily_budget" in p for p in st["problems"])
    main(["--home", str(store.home), "notify", "status"])
    out = capsys.readouterr().out
    assert "daily_budget" in out and "ten" not in out  # the problem is named; the hand-typed value is never echoed
