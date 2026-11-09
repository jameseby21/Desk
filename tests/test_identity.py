"""DESK-0144: who is acting. Protects: every write records the actor with kind, instance, source and how the identity was established;
a name that is only claimed is shown as claimed; two Claude Code sessions writing one ticket are told apart; a subagent is never Patch or
James; the page has no author field; only the owner can Act as, and the record then reads 'james (as chaz)'."""

import json

import pytest
from fastapi.testclient import TestClient

from desk import identity, ops, people
from desk.cli import main
from desk.ui.app import create_app

JSON = {"Accept": "application/json"}


def _mk(store, title="Talk"):
    return ops.new(store, title, "jre")


def _env(**kw):
    return {k: v for k, v in kw.items()}


def _named(store, user="jeby", key="james"):
    people.link_os_user(store.home, key, user)
    return store


# -- resolution order -------------------------------------------------------------------------

def test_os_user_mapped_in_people_yaml_is_confirmed(store):
    _named(store)
    a = identity.resolve_cli(store, None, env={}, user="jeby", git="")
    assert (a.name, a.kind, a.source, a.how, a.confirmed) == ("james", "person", "cli", "os-user", True)
    assert a.short() == "person · cli · os-user · confirmed"


def test_git_user_is_the_next_step_and_unknown_users_are_only_claimed(store):
    a = identity.resolve_cli(store, None, env={}, user="nobody", git="James")  # matches the display name
    assert (a.name, a.how, a.confirmed) == ("james", "git-user", True)
    b = identity.resolve_cli(store, None, env={}, user="nobody", git="stranger")
    assert (b.name, b.how, b.confirmed) == ("nobody", "os-user-unlisted", False)


def test_a_typed_name_is_claimed_even_when_it_is_the_owner(store):
    _named(store)
    a = identity.resolve_cli(store, "patch", env={}, user="jeby", git="")
    assert (a.name, a.kind, a.how, a.confirmed) == ("patch", "session", "claimed", False)
    assert identity.resolve_cli(store, "chaz", env={}, user="jeby", git="").kind == "thread"


def test_a_claude_code_shell_is_not_james_and_two_sessions_differ(store):
    _named(store)
    one = identity.resolve_cli(store, None, env={"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "sess-aaa"}, user="jeby", git="")
    two = identity.resolve_cli(store, None, env={"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "sess-bbb"}, user="jeby", git="")
    assert one.kind == two.kind == "session" and one.name == "claude-code" and not one.confirmed
    assert one.instance == "sess-aaa" and two.instance == "sess-bbb" and one != two
    named = identity.resolve_cli(store, None, env={"DESK_ACTOR": "patch", "DESK_INSTANCE": "main-1", "CLAUDECODE": "1"}, user="jeby", git="")
    assert (named.name, named.kind, named.instance, named.how) == ("patch", "session", "main-1", "env-DESK_ACTOR")


def test_a_subagent_is_itself_with_its_parent_never_patch_or_james(store):
    a = identity.resolve_cli(store, "patch", env={"DESK_SUBAGENT_ID": "ag42", "DESK_PARENT_SESSION": "sess-aaa"}, user="jeby", git="")
    assert (a.name, a.kind, a.parent, a.confirmed) == ("subagent:ag42", "subagent", "sess-aaa", False)
    b = identity.resolve_cli(store, "james", env={"DESK_ACTOR_KIND": "subagent", "CLAUDE_CODE_SESSION_ID": "sess-ccc"}, user="jeby", git="")
    assert b.name == "subagent:sess-ccc" and b.parent == "sess-ccc" and b.kind == "subagent"


# -- what gets recorded -----------------------------------------------------------------------

def test_cli_comment_carries_the_tag_and_comments_lists_it(store, capsys, monkeypatch):
    t = _mk(store)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-aaa")
    monkeypatch.setenv("CLAUDECODE", "1")
    main(["--home", str(store.home), "comment", t.id, "From one session", "--as", "patch"])
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-bbb")
    main(["--home", str(store.home), "comment", t.id, "From the other session", "--as", "patch"])
    line1, line2 = [ln for ln in store.get(t.id).section("Comments").splitlines()]
    i1, i2 = ops.comment_identity(line1.split(" · ", 2)[2]), ops.comment_identity(line2.split(" · ", 2)[2])
    assert i1["inst"] == "sess-aaa" and i2["inst"] == "sess-bbb" and i1["ok"] == "0" and i1["kind"] == "session" and i1["src"] == "cli" and i1["via"] == "claimed"
    capsys.readouterr()
    main(["--home", str(store.home), "comments", "--json"])
    rows = json.loads(capsys.readouterr().out)
    assert [r["identity"]["inst"] for r in rows] == ["sess-aaa", "sess-bbb"] and rows[0]["text"] == "From one session"
    main(["--home", str(store.home), "comments"])
    assert "[session · cli · claimed · claimed]" in capsys.readouterr().out


def test_every_save_is_in_the_write_log_with_the_actor(store, monkeypatch):
    t = _mk(store)
    monkeypatch.setenv("DESK_SUBAGENT_ID", "ag42")
    monkeypatch.setenv("DESK_PARENT_SESSION", "sess-aaa")
    main(["--home", str(store.home), "comment", t.id, "From a subagent"])
    monkeypatch.delenv("DESK_SUBAGENT_ID")
    monkeypatch.delenv("DESK_PARENT_SESSION")
    main(["--home", str(store.home), "comment", t.id, "From a thread", "--as", "chaz"])
    rows = identity.read_log(store.home, t.id)
    assert [(r["actor"], r["kind"], r["source"], r["confirmed"]) for r in rows] == [("subagent:ag42", "subagent", "cli", False), ("chaz", "thread", "cli", False)]
    assert rows[0]["parent"] == "sess-aaa" and rows[0]["how"] == "agent-id" and rows[0]["ticket"] == t.id and rows[0]["ts"]


def test_old_comments_are_not_recorded_and_say_so(store, capsys):
    t = _mk(store)
    t.append_line("Comments", "- 2026-11-01 09:00 · james · An old one <!-- kind: Question -->")
    t.write()
    main(["--home", str(store.home), "comments", "--json"])
    (row,) = json.loads(capsys.readouterr().out)
    assert row["identity"] == {} and identity.describe({}) == "not recorded"
    page = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    assert "not recorded" in page


def test_whoami_prints_how_it_would_be_recorded(store, capsys, monkeypatch):
    main(["--home", str(store.home), "whoami", "--as", "patch"])
    out = capsys.readouterr().out
    assert out.startswith("patch: session · cli · claimed · claimed") and "a claim, not a check" in out
    monkeypatch.setenv("DESK_ACTOR", "chaz")
    main(["--home", str(store.home), "whoami", "--json"])
    d = json.loads(capsys.readouterr().out)
    assert d["actor"] == "chaz" and d["kind"] == "thread" and d["confirmed"] is False


# -- the UI -----------------------------------------------------------------------------------

def test_the_comment_form_has_no_author_field_and_records_the_resolved_person(store, monkeypatch):
    monkeypatch.setattr(identity, "os_user", lambda: "jeby")
    _named(store)
    t = _mk(store)
    c = TestClient(create_app(store.home))
    page = c.get(f"/ticket/{t.id}").text
    assert 'name="who"' not in page.split('id="composer"', 1)[1].split("</form>", 1)[0] and "posting as james" in page
    r = c.post(f"/ticket/{t.id}/comment", data={"token": c.app.state.tokens.issue(), "text": "Hello"}, headers=JSON)
    assert r.status_code == 200
    (line,) = store.get(t.id).section("Comments").splitlines()
    info = ops.comment_identity(line.split(" · ", 2)[2])
    assert "· james ·" in line and info["src"] == "ui" and info["via"] == "os-user" and info["ok"] == "1" and info["kind"] == "person" and info["inst"]
    assert "person · ui · os user · confirmed" in c.get(f"/ticket/{t.id}").text


def test_a_name_in_the_request_is_only_a_claim(store, monkeypatch):
    monkeypatch.setattr(identity, "os_user", lambda: "jeby")
    _named(store)
    t = _mk(store)
    c = TestClient(create_app(store.home))
    c.post(f"/ticket/{t.id}/comment", data={"token": c.app.state.tokens.issue(), "text": "Said by a script", "who": "em"})
    (line,) = store.get(t.id).section("Comments").splitlines()
    info = ops.comment_identity(line.split(" · ", 2)[2])
    assert "· em ·" in line and info["ok"] == "0" and info["via"] == "claimed-in-request"


def test_unnamed_machine_is_assumed_to_be_the_owner_and_shown_as_claimed_with_a_banner(store, monkeypatch):
    monkeypatch.setattr(identity, "os_user", lambda: "someone-new")
    t = _mk(store)
    c = TestClient(create_app(store.home))
    page = c.get(f"/ticket/{t.id}").text
    assert "Say who you are, once" in page and "/whoami" in page
    c.post(f"/ticket/{t.id}/comment", data={"token": c.app.state.tokens.issue(), "text": "First words"}, headers=JSON)
    (line,) = store.get(t.id).section("Comments").splitlines()
    info = ops.comment_identity(line.split(" · ", 2)[2])
    assert "· james ·" in line and info["ok"] == "0" and info["via"] == "assumed-owner"
    assert "Act as" not in page  # not confirmed, so no Act as


def test_first_run_naming_writes_people_yaml_once_and_stops_asking(store, monkeypatch):
    monkeypatch.setattr(identity, "os_user", lambda: "someone-new")
    c = TestClient(create_app(store.home))
    assert "Who are you?" in c.get("/whoami").text and "someone-new" in c.get("/whoami").text
    r = c.post("/whoami", data={"token": c.app.state.tokens.issue(), "pick": "james"}, follow_redirects=False)
    assert r.status_code == 303 and "desk_who=james" in r.headers.get("set-cookie", "")
    assert people.load(store.home).get("james").os_users == ["someone-new"] and (store.home / "people.yaml").is_file()
    assert "Say who you are, once" not in c.get("/").text  # never asked again
    t = _mk(store)
    c.post(f"/ticket/{t.id}/comment", data={"token": c.app.state.tokens.issue(), "text": "Named now"}, headers=JSON)
    info = ops.comment_identity(store.get(t.id).section("Comments").split(" · ", 2)[2])
    assert info["via"] == "os-user" and info["ok"] == "1"


def test_first_run_can_add_a_new_person(store, monkeypatch):
    monkeypatch.setattr(identity, "os_user", lambda: "ann")
    c = TestClient(create_app(store.home))
    c.post("/whoami", data={"token": c.app.state.tokens.issue(), "new_name": "Ann Lee", "new_kind": "person"}, follow_redirects=False)
    p = people.load(store.home).get("ann-lee")
    assert p and p.name == "Ann Lee" and p.os_users == ["ann"] and people.load(store.home).get("james")  # the rest of the team is kept
    bad = c.post("/whoami", data={"token": c.app.state.tokens.issue(), "pick": "nobody"}, follow_redirects=False)
    assert bad.status_code == 422


def test_act_as_is_owner_only_and_the_record_reads_james_as_chaz(store, monkeypatch):
    monkeypatch.setattr(identity, "os_user", lambda: "jeby")
    _named(store)
    t = _mk(store)
    c = TestClient(create_app(store.home))
    assert 'name="person"' in c.get("/").text  # the owner sees the menu
    r = c.post("/act-as", data={"token": c.app.state.tokens.issue(), "person": "chaz", "back": f"/ticket/{t.id}"}, follow_redirects=False)
    assert r.status_code == 303 and "desk_act_as=chaz" in r.headers["set-cookie"]
    c.post(f"/ticket/{t.id}/comment", data={"token": c.app.state.tokens.issue(), "text": "As Chaz"}, headers=JSON)
    (line,) = store.get(t.id).section("Comments").splitlines()
    info = ops.comment_identity(line.split(" · ", 2)[2])
    assert "· james (as chaz) ·" in line and info["as"] == "chaz" and info["ok"] == "1"
    assert "person · ui · os user · confirmed · as chaz" in c.get(f"/ticket/{t.id}").text
    assert identity.read_log(store.home, t.id)[-1]["as"] == "chaz"
    other = TestClient(create_app(store.home))
    other.cookies.set("desk_act_as", "chaz")
    monkeypatch.setattr(identity, "os_user", lambda: "someone-else")  # not the owner on this machine
    refused = other.post("/act-as", data={"token": other.app.state.tokens.issue(), "person": "chaz"}, follow_redirects=False)
    assert refused.status_code == 403
    other.post(f"/ticket/{t.id}/comment", data={"token": other.app.state.tokens.issue(), "text": "Not as Chaz"}, headers=JSON)
    assert "(as chaz)" not in store.get(t.id).section("Comments").splitlines()[-1]  # the cookie means nothing without the owner's identity


def test_every_form_in_the_new_screens_names_its_progress_steps(store):
    c = TestClient(create_app(store.home))
    assert 'data-progress="none"' in c.get("/whoami").text


def test_pytest_environment_does_not_leak_into_identity():
    import os

    assert "CLAUDECODE" not in os.environ and "CLAUDE_CODE_SESSION_ID" not in os.environ


@pytest.mark.parametrize("raw, expect", [("kind=person src=ui via=os-user ok=1 inst=pc as=chaz", "person · ui · os user · confirmed · as chaz"), ("kind=session src=cli via=claimed ok=0", "session · cli · claimed · claimed")])
def test_describe(raw, expect):
    assert identity.describe(identity.parse_tag(f"words <!-- by {raw} -->")[0]) == expect
