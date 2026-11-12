"""DESK-0248: ids reserved by open store pull requests. A fake gh stands in for the network."""
import json

import pytest

from desk import ops, reserved
from desk.cli import main


def fake_gh(monkeypatch, prs):
    monkeypatch.setattr(reserved, "_gh", lambda home: json.dumps(prs))


def run(store, *argv):
    main(["--home", str(store.home), *argv])


PR = [{"number": 7, "files": [{"path": "tickets/DESK-0240-a-thing.md"}, {"path": "docs/x.md"}]}]


def test_new_skips_an_id_an_open_pr_adds(store, monkeypatch, capsys):
    """Protects: two writers never allocate the same id (2026-11-10 collision of 17 ids)."""
    for _ in range(2):
        ops.new(store, "t", "desk")  # DESK-0001, 0002 on main
    fake_gh(monkeypatch, [{"number": 7, "files": [{"path": "tickets/DESK-0240-a-thing.md"}]}])
    run(store, "new", "Next one", "--project", "desk")
    assert "DESK-0241-" in capsys.readouterr().out


def test_no_gh_says_so_and_falls_back(store, monkeypatch, capsys):
    """Protects: without network, drew new still works from the local folder."""
    monkeypatch.setattr(reserved, "_gh", lambda home: None)
    run(store, "new", "Offline", "--project", "desk")
    cap = capsys.readouterr()
    assert "DESK-0001-" in cap.out and "open pull requests not read" in cap.err


def test_validate_prs_reports_a_collision_and_exits_1(store, monkeypatch, capsys):
    """Protects: a PR id that names a different ticket on main is reported with the PR number and both file names."""
    t = ops.new(store, "On main", "desk")
    fake_gh(monkeypatch, [{"number": 9, "files": [{"path": f"tickets/{t.id}-other-title.md"}]}])
    with pytest.raises(SystemExit) as e:
        run(store, "validate", "--prs")
    out = capsys.readouterr().out
    assert e.value.code == 1 and "PR #9" in out and f"{t.id}-other-title.md" in out and t.path.name in out


def test_validate_prs_clean_when_no_collision(store, monkeypatch, capsys):
    fake_gh(monkeypatch, PR)
    with pytest.raises(SystemExit) as e:
        run(store, "validate", "--prs")
    assert e.value.code == 0
