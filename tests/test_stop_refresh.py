"""DESK-0169: Ctrl+C stops drew-ui, `drew stop` ends the listener on the UI port, `drew refresh` stops, installs and starts again.
Protects: a server that kept running after Ctrl+C because an open event stream held it (James had to use Task Manager)."""

import asyncio
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from desk import version as ver
from desk.cli import main
from desk.ui import events as ev
from desk.ui.app import create_app

SRC = str(Path(__file__).resolve().parent.parent / "src")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_an_event_stream_ends_when_the_server_is_stopping():
    """Protects: SSE generators exit on shutdown, so uvicorn is not waiting on them."""
    flag = {"stop": False}

    async def run():
        seen = []
        gen = ev.stream(lambda: 1, None, poll=0.05, stopping=lambda: flag["stop"])
        seen.append(await gen.__anext__())  # retry: line
        flag["stop"] = True
        async for chunk in gen:
            seen.append(chunk)
        return seen

    assert asyncio.run(asyncio.wait_for(run(), 5)) == ["retry: 3000\n\n"]


def test_the_app_marks_itself_stopping_when_its_lifespan_ends(store):
    app = create_app(store.home)
    with TestClient(app):
        assert app.state.stopping is False
    assert app.state.stopping is True


@pytest.mark.skipif(sys.platform != "win32", reason="CTRL_BREAK_EVENT is the Windows way to signal a child process group")
def test_ctrl_c_stops_the_real_server_within_two_seconds_with_a_stream_open(store):
    """Protects: DESK-0169. Starts drew-ui's main() on a free port, opens an event stream, sends CTRL_BREAK (Ctrl+C for a child in its own
    process group) and times the exit. The process is stopped by its PID if it does not leave."""
    port = _free_port()
    env = {**{k: v for k, v in os.environ.items() if k != "DESK_UI_HOST"}, "DESK_HOME": str(store.home), "DESK_UI_PORT": str(port), "PYTHONPATH": SRC}
    proc = subprocess.Popen([sys.executable, "-c", "from desk.ui.app import main; main()"], env=env, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                c = socket.create_connection(("127.0.0.1", port), timeout=1)
                break
            except OSError:
                time.sleep(0.2)
        else:
            pytest.fail("the server did not start")
        c.sendall(b"GET /events HTTP/1.1\r\nHost: 127.0.0.1\r\nAccept: text/event-stream\r\n\r\n")
        time.sleep(1.0)
        assert b"200" in c.recv(200).split(b"\r\n")[0]  # the stream is open
        t0 = time.time()
        os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
        proc.wait(10)
        assert time.time() - t0 < 2.0
        assert not ver.listeners(port)
    finally:
        if proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)


def test_drew_stop_finds_and_ends_a_listener_and_exits_zero_when_nothing_listens(capsys):
    port = _free_port()
    code = f"import socket,time;s=socket.socket();s.bind(('127.0.0.1',{port}));s.listen();time.sleep(60)"
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            if ver.listeners(port):
                break
            time.sleep(0.2)
        pids = ver.listeners(port)
        assert len(pids) == 1  # the venv launcher may start the real python as a child: the listener is the one to stop
        main(["stop", "--port", str(port)])
        out = capsys.readouterr().out
        assert f"PID {pids[0]} stopped" in out and f"port {port}" in out
        proc.wait(10)
        assert not ver.listeners(port)
        main(["stop", "--port", str(port)])  # nothing listens now: exit 0, no SystemExit
        assert "nothing listens" in capsys.readouterr().out
    finally:
        if proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True) if sys.platform == "win32" else proc.kill()


def test_refresh_sequence_is_stop_then_install_then_start_then_version(store, monkeypatch, capsys, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='desk'\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(ver, "find_repo", lambda *a, **k: repo)
    monkeypatch.setattr(ver, "listeners", lambda port=None: [4242])
    monkeypatch.setattr(ver, "stop_pid", lambda pid: calls.append(("stop", pid)) or True)
    monkeypatch.setattr(ver, "on_windows", lambda: False)
    monkeypatch.setattr("shutil.which", lambda name: f"/bin/{name}")

    class Done:
        returncode = 0
        stdout = "Desk 0.2.0 · abc1234\n"

    def run(cmd, **kw):
        calls.append(("run", cmd[1:3] if "uv" in cmd[0] else cmd[1:]))
        return Done()

    monkeypatch.setattr("subprocess.run", run)
    monkeypatch.setattr("subprocess.Popen", lambda cmd, **kw: calls.append(("start", cmd)))
    monkeypatch.setattr("time.sleep", lambda s: None)
    main(["refresh"])
    out = capsys.readouterr().out
    assert [c[0] for c in calls] == ["stop", "run", "start", "run"]
    assert calls[0] == ("stop", 4242) and calls[1][1] == ["tool", "install"] and calls[2][1] == ["/bin/drew-ui"]
    assert out.rstrip().endswith("Desk 0.2.0 · abc1234")


def test_refresh_ref_installs_a_git_url_and_refuses_an_unknown_ref(store, monkeypatch, capsys, tmp_path):
    repo = tmp_path / "repo with space"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='desk'\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"], cwd=repo, check=True)
    subprocess.run(["git", "branch", "feature-x"], cwd=repo, check=True)
    assert ver.install_source(repo, "feature-x").startswith("git+file:///") and ver.install_source(repo, "feature-x").endswith("@feature-x")
    assert "%20" in ver.install_source(repo, "feature-x") and ver.install_source(repo) == str(repo)
    monkeypatch.setattr(ver, "find_repo", lambda *a, **k: repo)
    monkeypatch.setattr(ver, "listeners", lambda port=None: [])
    monkeypatch.setattr("shutil.which", lambda name: f"/bin/{name}")
    with pytest.raises(SystemExit):  # an unknown ref is refused before anything is stopped or installed (real git runs here)
        main(["refresh", "--ref", "no-such-ref"])
    assert "not a branch, tag or commit" in capsys.readouterr().err
    ran = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: ran.append(cmd) or type("R", (), {"returncode": 0, "stdout": "v\n"})())
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: None)
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(ver, "on_windows", lambda: False)
    main(["refresh", "--ref", "feature-x"])
    install = [c for c in ran if "install" in c][0]
    assert install[-1] == ver.install_source(repo, "feature-x") and "--force" in install


def test_windows_refresh_hands_the_install_to_a_detached_script(store, monkeypatch, capsys, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='desk'\n", encoding="utf-8")
    monkeypatch.setattr(ver, "find_repo", lambda *a, **k: repo)
    monkeypatch.setattr(ver, "listeners", lambda port=None: [])
    monkeypatch.setattr(ver, "on_windows", lambda: True)
    monkeypatch.setattr(ver, "in_uv_tools_dir", lambda: True)
    monkeypatch.setattr("shutil.which", lambda name: f"C:/tools/{name}.exe")
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    launched = []
    monkeypatch.setattr("subprocess.Popen", lambda cmd, **kw: launched.append(cmd))
    main(["refresh"])
    script = (tmp_path / "desk-refresh.cmd").read_text(encoding="utf-8")
    assert launched and launched[0][0] == "cmd.exe" and "tool install --force" in script and str(repo) in script
    assert "drew-ui" in script and "drew --version" in script and "desk-refresh.log" in capsys.readouterr().out
