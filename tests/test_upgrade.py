"""drew upgrade on Windows hands the install to a detached script instead of running uv from the exe uv is about to replace (DESK-0132)."""

import subprocess
import tempfile

import pytest

from desk import version as ver
from desk.cli import main


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "my repo"
    r.mkdir()
    (r / "pyproject.toml").write_text("[project]\nname='desk'\n")
    return r


def _quiet(monkeypatch):
    monkeypatch.setattr(ver, "listeners", lambda port=None: [])
    monkeypatch.setattr("shutil.which", lambda name: "C:/tools/uv.exe" if name == "uv" else None)


def test_windows_tool_install_detaches_and_never_runs_uv_in_process(store, repo, monkeypatch, capsys, tmp_path):
    """Protects: running `uv tool install --force` from drew.exe itself deleted the install (James, 2026-11-08)."""
    _quiet(monkeypatch)
    monkeypatch.setattr(ver, "on_windows", lambda: True)
    monkeypatch.setattr(ver, "in_uv_tools_dir", lambda: True)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    ran, spawned = [], []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append(a))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: spawned.append((a, k)))
    main(["--home", str(store.home), "upgrade", "--repo", str(repo)])
    out = capsys.readouterr().out
    assert not ran, "uv must not be run in-process"
    assert len(spawned) == 1
    args, kw = spawned[0]
    assert kw["creationflags"] == getattr(subprocess, "DETACHED_PROCESS", 0x8) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
    assert kw["close_fds"] is True
    script = (tmp_path / "desk-upgrade.cmd").read_text()
    assert "tool install --force" in script and str(repo) in script and "C:/tools/uv.exe" in script
    assert "drew --version" in script
    assert "--reinstall" in script  # the recovery command is printed if uv fails
    assert "handed" in out.lower() and "drew doctor" in out


def test_script_text_waits_runs_uv_then_checks_version(repo):
    text = ver.upgrade_script("C:/tools/uv.exe", repo, "C:/tmp/up.log")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert "ping.exe -n 3" in lines[1]  # about 2 s; timeout.exe refuses to run with no console
    assert any("tool install --force" in ln for ln in lines)
    assert text.index("tool install --force") < text.index("drew --version")
    assert "No module named" not in text  # the log names the fix, not the symptom


def test_script_escapes_percent_in_paths(tmp_path):
    text = ver.upgrade_script("uv", tmp_path / "100%", "log")
    assert "100%%" in text


def test_non_windows_still_runs_uv_directly(store, repo, monkeypatch, capsys):
    _quiet(monkeypatch)
    monkeypatch.setattr(ver, "on_windows", lambda: False)
    ran = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (ran.append(a[0]), subprocess.CompletedProcess(a[0], 0, stdout="Desk 0.2.0 · abc", stderr=""))[1])
    main(["--home", str(store.home), "upgrade", "--repo", str(repo)])
    assert ran and ran[0][:4] == ["C:/tools/uv.exe", "tool", "install", "--force"]
    assert "Upgraded" in capsys.readouterr().out


def test_doctor_names_the_recovery_command(store, repo, monkeypatch, capsys):
    monkeypatch.setenv("DESK_REPO", str(repo))
    monkeypatch.setattr(ver, "build_commit", lambda: "aaaaaaa")
    monkeypatch.setattr(ver, "repo_head", lambda p: "aaaaaaa")
    monkeypatch.setattr(ver, "listeners", lambda port=None: [])
    main(["--home", str(store.home), "doctor"])
    out = capsys.readouterr().out
    assert "No module named" in out and "--reinstall" in out and str(repo) in out


def test_quickstart_has_the_recovery_line():
    from pathlib import Path

    qs = (Path(__file__).resolve().parent.parent / "docs" / "QUICKSTART.md").read_text(encoding="utf-8")
    assert "No module named" in qs and "--reinstall" in qs
