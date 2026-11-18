"""The CLI and UI call the same operations and respect the same rules."""

import json

import pytest
from fastapi.testclient import TestClient

from desk import ops
from desk.cli import main
from desk.ui.app import create_app


def run(store, *args):
    main(["--home", str(store.home), *args])


def test_cli_new_list_next_validate(store, capsys):
    run(store, "new", "--project", "jre", "--priority", "p1", "Install Em round 1")
    path = capsys.readouterr().out.strip()
    assert path.endswith("JRE-0001-install-em-round-1.md")
    run(store, "move", "JRE-0001", "--to", "ready", "--as", "main")
    capsys.readouterr()
    run(store, "next", "--project", "jre", "--json")
    payload = json.loads(capsys.readouterr().out)
    assert "JRE-0001" in payload["hookSpecificOutput"]["additionalContext"]
    run(store, "list")
    assert "JRE-0001" in capsys.readouterr().out
    with pytest.raises(SystemExit) as ex:
        run(store, "validate")
    assert ex.value.code == 0


def test_cli_refusal_exits_one(store, capsys):
    run(store, "new", "--project", "jre", "A slice")
    with pytest.raises(SystemExit) as ex:
        run(store, "claim", "JRE-0001")  # not ready yet
    assert ex.value.code == 1
    assert "not an allowed move" in capsys.readouterr().err


def test_ui_pages_and_decide(store):
    t = ops.new(store, "Ship it", "jre")
    ops.move(store, t, "ready", actor="main")
    ops.claim(store, t, "jre")
    ops.report(store, t, "Ran the build. 17 files.")
    client = TestClient(create_app(store.home))
    assert "Waiting for review" in client.get("/").text  # DESK-0040: Today is rows, plain headings
    assert "Ship it" in client.get("/board?company=unsorted").text  # DESK-0183: the top ticket shows one level below companies
    assert "jRE" in client.get("/projects").text
    page = client.get(f"/ticket/{t.id}").text
    assert "Approve" in page and "Ran the build" in page
    r = client.post(f"/ticket/{t.id}/decide", data={"token": client.app.state.tokens.issue(), "action": "approve", "words": "ok"}, follow_redirects=False)
    assert r.status_code == 303 and "error=" in r.headers["location"]
    client.post(f"/ticket/{t.id}/decide", data={"token": client.app.state.tokens.issue(), "action": "approve", "words": "Approve: ship it."}, follow_redirects=False)
    assert store.get(t.id).status == "approved"
    client.post(f"/ticket/{t.id}/comment", data={"token": client.app.state.tokens.issue(), "text": "Nice.", "who": "em"})
    assert "· em · Nice." in store.get(t.id).section("Comments")
