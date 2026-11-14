"""DESK-0261: nobody, on any path, can post as someone else.
Protects: the store owner's browser (which coworker threads drive) stamping a post as another person through the Act as menu or its
cookie; the command line accepting `--as <anyone>`; and a relayed word being recorded as if the relayed person had written it."""

import logging
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from desk import identity, ops, people
from desk.cli import main
from desk.ui.app import create_app

JSON = {"Accept": "application/json"}
BASE = Path(ops.__file__).parent / "ui" / "templates" / "base.html"


def _mk(store, title="Talk"):
    return ops.new(store, title, "jre")


def _owner_browser(store, monkeypatch):
    """A browser on the owner's machine: confirmed as the store owner, the one identity that used to be allowed to Act as."""
    monkeypatch.setattr(identity, "os_user", lambda: "jeby")
    people.link_os_user(store.home, "james", "jeby")
    return TestClient(create_app(store.home))


# -- web ------------------------------------------------------------------------------------

def test_a_desk_act_as_cookie_is_ignored_and_logged(store, monkeypatch, caplog):
    """Protects: a cookie left over from the old menu (or planted by a thread) turning a post into 'james (as lily)'."""
    t = _mk(store)
    c = _owner_browser(store, monkeypatch)
    c.cookies.set("desk_act_as", "lily")
    with caplog.at_level(logging.WARNING):
        r = c.post(f"/ticket/{t.id}/comment", data={"token": c.app.state.tokens.issue(), "text": "Cookie says Lily"}, headers=JSON)
    assert r.status_code == 200
    (line,) = store.get(t.id).section("Comments").splitlines()
    info = ops.comment_identity(line.split(" · ", 2)[2])
    assert "· james ·" in line and "(as" not in line and "as" not in info
    assert identity.read_log(store.home, t.id)[-1]["as"] == ""
    assert any("desk_act_as" in m and "ignored" in m for m in caplog.messages)


def test_the_act_as_route_is_gone(store, monkeypatch):
    c = _owner_browser(store, monkeypatch)
    r = c.post("/act-as", data={"token": c.app.state.tokens.issue(), "person": "lily"}, follow_redirects=False)
    assert r.status_code in (404, 405) and "desk_act_as" not in r.headers.get("set-cookie", "")


def test_the_act_as_control_is_absent_from_every_page_and_from_the_base_template(store, monkeypatch):
    t = _mk(store)
    c = _owner_browser(store, monkeypatch)
    for url in ("/", "/whoami", f"/ticket/{t.id}"):
        page = c.get(url).text
        assert not re.search(r"act[ -]?as|actas|/act-as", page, re.I), url
    src = BASE.read_text(encoding="utf-8")
    assert not re.search(r"act[ -]?as|actas|/act-as|show_private", src, re.I)


# -- command line ---------------------------------------------------------------------------

def test_as_another_person_is_refused_and_points_at_relaying(store, monkeypatch, capsys):
    t = _mk(store)
    monkeypatch.setenv("DESK_ACTOR", "patch")  # this session is Patch
    with pytest.raises(SystemExit) as e:
        main(["--home", str(store.home), "comment", t.id, "test", "--as", "lily"])
    assert e.value.code not in (0, None)
    assert "--relaying" in capsys.readouterr().err
    assert store.get(t.id).section("Comments").strip() == ""  # nothing was written


@pytest.mark.parametrize("cmd", [["refine", "Also this"], ["decide", "--approve", "Fine, go ahead."], ["move", "--to", "ready"], ["block", "--on", "lily"]])
def test_every_write_command_refuses_as_another_person(store, monkeypatch, capsys, cmd):
    t = _mk(store)
    monkeypatch.setenv("DESK_ACTOR", "patch")
    with pytest.raises(SystemExit) as e:
        main(["--home", str(store.home), cmd[0], t.id, *cmd[1:], "--as", "james"])
    assert e.value.code not in (0, None) and "--relaying" in capsys.readouterr().err
    assert identity.read_log(store.home, t.id) == []


def test_as_yourself_still_works_by_name_or_alias(store, monkeypatch):
    t = _mk(store)
    monkeypatch.setenv("DESK_ACTOR", "patch")
    main(["--home", str(store.home), "comment", t.id, "Me", "--as", "patch"])
    main(["--home", str(store.home), "comment", t.id, "Me again, by key", "--as", "main"])
    assert len(store.get(t.id).section("Comments").splitlines()) == 2


def test_relaying_decide_names_the_writer_and_quotes_the_person(store, monkeypatch):
    t = ops.new(store, "Choose: the rule", "jre", "decision")
    t.meta["status"] = "ready"
    store.save(t)
    monkeypatch.setenv("DESK_ACTOR", "patch")
    main(["--home", str(store.home), "decide", t.id, "--approve", "Option A.", "--relaying", "james"])
    got = store.get(t.id)
    assert got.status == "approved"
    (line,) = got.section("Decision").splitlines()
    assert re.fullmatch(r'- \d{4}-\d{2}-\d{2} · patch, relaying james\'s words of \d{4}-\d{2}-\d{2} · approved · "Option A\."', line), line
    assert "· james ·" not in line  # never stamped as the relayed person
    row = identity.read_log(store.home, t.id)[-1]
    assert row["actor"] == "patch" and row["relaying"] == "james"


def test_relaying_comment_reads_as_the_writer_relaying_and_never_as_the_person(store, monkeypatch):
    t = _mk(store)
    monkeypatch.setenv("DESK_ACTOR", "patch")
    main(["--home", str(store.home), "comment", t.id, "Ship it", "--relaying", "james"])
    (line,) = store.get(t.id).section("Comments").splitlines()
    who, rest = line.split(" · ", 2)[1], line.split(" · ", 2)[2]
    assert who == "patch" and rest.startswith("relaying james's words of ") and "Ship it" in rest
    assert ops.comment_identity(rest)["relaying"] == "james"
    assert not rest.lstrip().startswith("Sign-off")  # a relayed line is never a sign-off by the relayed person


def test_relaying_yourself_or_a_stranger_is_refused(store, monkeypatch, capsys):
    t = _mk(store)
    monkeypatch.setenv("DESK_ACTOR", "patch")
    for who in ("patch", "nobody-here"):
        with pytest.raises(SystemExit):
            main(["--home", str(store.home), "comment", t.id, "x", "--relaying", who])
    assert store.get(t.id).section("Comments").strip() == ""


def test_old_records_keep_their_stamps(store):
    """Protects: history. A line written by the old menu still parses and shows who it says."""
    info, text = identity.parse_tag("Approved <!-- by kind=person src=ui via=os-user ok=1 as=lily -->")
    assert info["as"] == "lily" and text == "Approved"


# -- review pass 3 (DESK-0261): the paths the first cut left open -----------------------------

def test_whoami_relinks_from_the_web_are_refused_once_the_os_user_is_linked(store, monkeypatch):
    """Protects: a page the owner's browser loads (coworker threads drive it) re-pointing the owner's OS user at another person, after
    which every write from this machine would read as that person, confirmed. Only the first run may link; a relink is the owner's
    edit of people.yaml on the command line."""
    c = _owner_browser(store, monkeypatch)  # first run done: jeby is james
    before = (store.home / "people.yaml").read_bytes()
    for data in ({"pick": "lily"}, {"new_name": "Someone Else", "new_kind": "person"}):
        r = c.post("/whoami", data={"token": c.app.state.tokens.issue(), **data}, follow_redirects=False)
        assert r.status_code == 403, data
        assert "desk_who" not in r.headers.get("set-cookie", "")
    assert (store.home / "people.yaml").read_bytes() == before
    assert identity.resolve_cli(store, None, env={}, user="jeby").name == "james"
    page = c.get("/whoami").text
    assert 'action="/whoami"' not in page and "people.yaml" in page  # no pick form once linked; the page says where a change is made


def _private_person(tmp_path, monkeypatch):
    priv = tmp_path / "priv" / "people.yaml"
    priv.parent.mkdir()
    priv.write_text("people:\n  - key: family-investor\n    name: Quentin Zzyzx-Fictional\n", encoding="utf-8")
    monkeypatch.setenv("DESK_PRIVATE_PEOPLE", str(priv))
    return "Quentin Zzyzx-Fictional"


def test_relaying_stores_the_key_and_a_private_name_never_reaches_a_ticket_or_the_write_log(store, monkeypatch, tmp_path):
    """Protects: rule 30. `--relaying <private person>` wrote that person's real name into a committed ticket file."""
    secret = _private_person(tmp_path, monkeypatch)
    t = _mk(store)
    monkeypatch.setenv("DESK_ACTOR", "patch")
    main(["--home", str(store.home), "comment", t.id, "Said in chat", "--relaying", "family-investor"])
    main(["--home", str(store.home), "refine", t.id, "Also this", "--relaying", "family-investor"])
    text = store.get(t.id).path.read_text(encoding="utf-8")
    assert "relaying family-investor's words of " in text
    assert secret not in text and "Quentin" not in text
    log = (store.home / ".desk" / identity.LOG).read_text(encoding="utf-8")
    assert secret not in log and "Quentin" not in log
    page = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    assert re.search(r"relaying family-investor(&#39;|')s words of", page)  # a private person stays a key on screen too (DESK-0254)


def test_relaying_shows_the_display_name_only_at_view_time(store, monkeypatch):
    t = _mk(store)
    monkeypatch.setenv("DESK_ACTOR", "patch")
    main(["--home", str(store.home), "comment", t.id, "Ship it", "--relaying", "james"])
    assert "relaying james's words of " in store.get(t.id).section("Comments")
    monkeypatch.delenv("DESK_ACTOR")
    page = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    assert "relaying James&#39;s words of " in page or "relaying James's words of " in page


def test_a_name_typed_in_the_who_field_is_a_marked_claim_and_never_a_sign_off(store, monkeypatch):
    """Protects: a fetch from the owner's browser posting 'Sign-off (security): fine' as bigd through the old `who` field."""
    from desk import signoffs

    t = _mk(store)
    c = _owner_browser(store, monkeypatch)
    r = c.post(f"/ticket/{t.id}/comment", data={"token": c.app.state.tokens.issue(), "text": "Sign-off (security): fine", "who": "bigd"}, headers=JSON)
    assert r.status_code == 200
    (line,) = store.get(t.id).section("Comments").splitlines()
    who, rest = line.split(" · ", 2)[1], line.split(" · ", 2)[2]
    assert who == "james"  # the real poster, never the typed name
    words = ops.split_comment_kind(rest)[1]
    assert words.startswith("claimed by bigd, unverified: ") and not words.startswith("Sign-off")
    info = ops.comment_identity(rest)
    assert info["ok"] == "0" and info["via"] == "claimed-in-request" and info["claimed"] == "bigd"
    assert [r for r in signoffs.rows(store) if r[3] == signoffs.VERIFIED] == []
    assert all(r[2] != "bigd" for r in signoffs.rows(store))
