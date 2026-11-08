"""Version footer, doctor staleness, upgrade refusal."""

import pytest
from fastapi.testclient import TestClient

from desk import version as ver
from desk.cli import main
from desk.ui.app import create_app


def test_staleness_logic():
    assert ver.staleness("abc1234", "abc1234") == "current"
    assert ver.staleness("abc1234", "abc1234def") == "current"
    assert ver.staleness("abc1234", "fff9999", True) == "stale"
    assert ver.staleness("abc1234", "fff9999", False) == "stale"
    assert ver.staleness(ver.NO_GIT, "fff9999") == "stale"
    assert ver.staleness("abc1234", None) == "unknown"


def test_parse_netstat_and_lsof():
    out = ("  Proto  Local Address  Foreign Address  State  PID\n"
           "  TCP    127.0.0.1:8765  0.0.0.0:0  LISTENING  50244\n"
           "  TCP    127.0.0.1:18765  0.0.0.0:0  LISTENING  7\n"
           "  TCP    127.0.0.1:8765  127.0.0.1:5000  ESTABLISHED  99\n")
    assert ver.parse_netstat(out, 8765) == [50244]
    assert ver.parse_lsof("p123\np456\n") == [123, 456]


def test_doctor_exit_1_when_stale(store, tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='desk'\n")
    monkeypatch.setenv("DESK_REPO", str(repo))
    monkeypatch.setattr(ver, "build_commit", lambda: "aaaaaaa")
    monkeypatch.setattr(ver, "repo_head", lambda p: "bbbbbbb")
    monkeypatch.setattr(ver, "is_ancestor", lambda *a: True)
    monkeypatch.setattr(ver, "listeners", lambda port=None: [4242])
    with pytest.raises(SystemExit) as e:
        main(["--home", str(store.home), "doctor"])
    out = capsys.readouterr().out
    assert e.value.code == 1 and "OLDER than the repo" in out and "PID 4242" in out and str(repo) in out
    monkeypatch.setattr(ver, "repo_head", lambda p: "aaaaaaa")
    main(["--home", str(store.home), "doctor"])  # current: no exit


def test_upgrade_refuses_while_ui_runs(store, tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='desk'\n")
    monkeypatch.setattr(ver, "listeners", lambda port=None: [31337])
    ran = []
    monkeypatch.setattr("subprocess.run", lambda *a, **k: ran.append(a))
    with pytest.raises(SystemExit) as e:
        main(["--home", str(store.home), "upgrade", "--repo", str(repo)])
    err = capsys.readouterr().err
    assert e.value.code == 1 and "31337" in err and "stop" in err and not ran


def test_footer_shows_version(store):
    page = TestClient(create_app(store.home)).get("/").text
    assert ver.describe() in page and "Desk " in page
