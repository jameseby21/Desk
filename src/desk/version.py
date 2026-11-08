"""Which Desk is this? Version, build commit, the repo it came from, and who is listening on the UI port."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__, yamlio

NO_GIT = "no git"
DEFAULT_PORT = 8765
_PKG_DIR = Path(__file__).resolve().parent


def package_version() -> str:
    try:
        import warnings
        from importlib.metadata import version

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return version("desk") or __version__
    except Exception:  # not installed (running from a checkout)
        return __version__


def _git(repo: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    out = r.stdout.strip()
    return out if r.returncode == 0 and out else None


def build_commit() -> str:
    """The commit this code was built from: `_build.py` (written when the wheel is built), else the
    git checkout the package sits in, else 'no git'."""
    try:
        from . import _build  # type: ignore[attr-defined]

        if getattr(_build, "COMMIT", ""):
            return str(_build.COMMIT)
    except ImportError:
        pass
    if (_PKG_DIR.parent.parent / ".git").exists():
        return _git(_PKG_DIR, "rev-parse", "--short", "HEAD") or NO_GIT
    return NO_GIT


def describe() -> str:
    return f"Desk {package_version()} · {build_commit()}"


# -- the repo ------------------------------------------------------------------

def find_repo(explicit: str | os.PathLike | None = None, home: Path | None = None) -> Path | None:
    """--repo, then DESK_REPO, then the `desk/` directory next to the store's desk.toml."""
    cands = [explicit, os.environ.get("DESK_REPO")]
    if home is not None:
        cands.append(Path(home) / "desk")
    for c in cands:
        if c and (Path(c) / "pyproject.toml").is_file():
            return Path(c).resolve()
    return None


def repo_head(repo: Path) -> str | None:
    return _git(repo, "rev-parse", "--short", "HEAD")


def is_ancestor(repo: Path, older: str, newer: str) -> bool | None:
    try:
        r = subprocess.run(["git", "merge-base", "--is-ancestor", older, newer], cwd=repo, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return {0: True, 1: False}.get(r.returncode)


def staleness(installed: str, head: str | None, ancestor: bool | None = None) -> str:
    """'current', 'stale' (installed code is older than, or differs from, the repo) or 'unknown'.

    `ancestor` says whether the installed commit is an ancestor of the repo HEAD, when git can tell."""
    if not head:
        return "unknown"
    if installed == NO_GIT or not installed:
        return "stale"  # cannot show it matches the repo, so refresh
    if installed == head or head.startswith(installed) or installed.startswith(head):
        return "current"
    return "stale"  # different commit: older (ancestor) or off-branch, either way not what the repo has


# -- processes on the UI port ---------------------------------------------------

def ui_port() -> int:
    try:
        return int(os.environ.get("DESK_UI_PORT", DEFAULT_PORT))
    except ValueError:
        return DEFAULT_PORT


def parse_netstat(text: str, port: int) -> list[int]:
    """PIDs of LISTENING sockets on `port` from `netstat -ano` output."""
    pids: list[int] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper().startswith("TCP") and parts[3].upper() == "LISTENING":
            if parts[1].rsplit(":", 1)[-1] == str(port) and parts[4].isdigit() and int(parts[4]) not in pids:
                pids.append(int(parts[4]))
    return pids


def parse_lsof(text: str) -> list[int]:
    return sorted({int(m.group(1)) for m in re.finditer(r"^p(\d+)$", text, re.M)})


def listeners(port: int | None = None) -> list[int]:
    port = port or ui_port()
    try:
        if sys.platform == "win32":
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=20).stdout
            return parse_netstat(out, port)
        if shutil.which("lsof"):
            out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"], capture_output=True, text=True, timeout=20).stdout
            return parse_lsof(out)
    except (OSError, subprocess.SubprocessError):
        pass
    return []


def on_windows() -> bool:
    return sys.platform == "win32"


def in_uv_tools_dir() -> bool:
    """True when this Python lives in uv's tool directory (<...>/uv/tools/<name>/...): the exe that is running is then one that
    `uv tool install --force` deletes, and Windows will not delete a running exe."""
    tools = os.environ.get("UV_TOOL_DIR")
    me = Path(sys.executable).resolve()
    if tools and me.is_relative_to(Path(tools).resolve()):
        return True
    parts = [p.lower() for p in me.parts]
    return any(a == "uv" and b == "tools" for a, b in zip(parts, parts[1:]))


def restore_command(repo: Path) -> str:
    return f'uv tool install --force --reinstall "{repo}"'


def upgrade_script(uv: str, repo: Path, log: str) -> str:
    """The .cmd a detached process runs after drew.exe has exited: wait, reinstall, report the version, and on failure write the
    one command that restores Desk. Everything it prints goes to `log`."""
    q = lambda v: str(v).replace("%", "%%")
    return "\r\n".join([
        "@echo off",
        "%SystemRoot%\\System32\\ping.exe -n 3 127.0.0.1 >nul",  # about 2 s; timeout.exe refuses to run without a console
        f'echo Desk upgrade started %date% %time% > "{q(log)}"',
        f'"{q(uv)}" tool install --force "{q(repo)}" >> "{q(log)}" 2>&1',
        "if errorlevel 1 goto failed",
        f'drew --version >> "{q(log)}" 2>&1',
        f'echo Upgrade finished. >> "{q(log)}"',
        "exit /b 0",
        ":failed",
        f'echo Upgrade failed. To restore Desk run: {q(restore_command(repo))} >> "{q(log)}"',
        "exit /b 1",
        "",
    ])


def stop_hint(pid: int) -> str:
    return f"taskkill /PID {pid} /T /F" if sys.platform == "win32" else f"kill {pid}"


# -- doctor / upgrade -------------------------------------------------------------

def doctor_report(home: Path | None, repo_arg: str | None = None, *, pids: list[int] | None = None) -> tuple[list[str], bool]:
    """(lines to print, stale?)."""
    installed = build_commit()
    lines = [f"installed version: {package_version()}", f"installed commit:  {installed}"]
    repo = find_repo(repo_arg, home)
    stale = False
    if repo:
        head = repo_head(repo)
        lines += [f"repo path:         {repo}", f"repo HEAD:         {head or NO_GIT}"]
        anc = is_ancestor(repo, installed, head) if head and installed != NO_GIT else None
        state = staleness(installed, head, anc)
        stale = state == "stale"
        lines.append("installed code:    " + {"current": "up to date with the repo", "stale": "OLDER than the repo (or cannot be matched): run `drew upgrade`",
                                              "unknown": "unknown (repo has no git HEAD)"}[state])
    else:
        lines.append("repo path:         not found (set DESK_REPO, or keep the repo at <store>/desk)")
    if repo:
        lines.append(f"if drew-ui says No module named 'desk': {restore_command(repo)}")
    lines.append(f"YAML loader:       {yamlio.LOADER}")
    lines.append(f"tool path:         {shutil.which('drew') or sys.argv[0]}")
    lines.append(f"store path:        {home if home else 'not found'}")
    port = ui_port()
    found = listeners(port) if pids is None else pids
    lines.append(f"UI port {port}:      " + (", ".join(f"PID {p}" for p in found) + " listening" if found else "nothing listening"))
    return lines, stale
