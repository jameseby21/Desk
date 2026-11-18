"""DESK-0308: Ctrl+C stops drew-ui within two seconds on Windows, however it was started (James, 2026-11-17, second ask).

The cause found: a console process started by `drew refresh` (a CREATE_NEW_PROCESS_GROUP script that opens drew-ui with `start`) or from
an agent's shell inherits Windows' "ignore Ctrl+C" flag, so Ctrl+C in that window never reached Python; and a request stuck in a worker
thread (non-daemon) could hold the process after uvicorn returned. Three methods: unit (main's order of calls, daemon threads), a real
console Ctrl+C against the real server in a new hidden console, and an in-process SIGINT with a request that never finishes."""

import http.client
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import desk
from desk import ops
from desk.store import Store
from desk.ui import app as appmod

SRC = str(Path(desk.__file__).resolve().parent.parent)
WIN = sys.platform == "win32"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_listening(port: int, proc: subprocess.Popen, seconds: float = 30) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise AssertionError(f"server exited before listening (code {proc.returncode})")
        try:
            socket.create_connection(("127.0.0.1", port), 0.3).close()
            return
        except OSError:
            time.sleep(0.1)
    raise AssertionError(f"nothing listened on {port} within {seconds}s")


def _env(store: Store, port: int) -> dict:
    env = dict(os.environ, DESK_HOME=str(store.home), DESK_UI_PORT=str(port), PYTHONPATH=SRC, DESK_NOTIFY_OFFLINE="1")
    env.pop("PYTHONHOME", None)
    return env


def _kill(proc: subprocess.Popen) -> None:
    """Last resort for a server the test could not stop: by its own PID and its children only."""
    if proc.poll() is None:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"] if WIN else ["kill", "-9", str(proc.pid)], capture_output=True)
        proc.wait(10)


# --- 1. unit: main() turns Ctrl+C back on before it serves; every thread Desk starts is a daemon -------------------------------------

def test_main_turns_ctrl_c_back_on_before_serving(store, monkeypatch, capsys):
    """Protects the fix for the inherited 'ignore Ctrl+C' flag: main() must clear it before the server starts listening."""
    calls = []
    monkeypatch.setenv("DESK_HOME", str(store.home))
    monkeypatch.setenv("DESK_UI_PORT", "1")
    monkeypatch.setattr(appmod, "allow_ctrl_c", lambda: calls.append("allow_ctrl_c") or True, raising=False)
    monkeypatch.setattr(appmod, "serve", lambda app, port: calls.append(("serve", port)), raising=False)
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: calls.append("uvicorn.run"))
    appmod.main()
    assert calls == ["allow_ctrl_c", ("serve", 1)]


def test_stop_deadline_is_a_daemon_that_prints_and_exits(capsys):
    """Protects the last resort: two seconds after Ctrl+C the process ends with one plain line, from a thread that never holds exit."""
    ended = []
    appmod._said_stopped.clear()
    t = appmod.stop_deadline(0.05, end=ended.append)
    assert t.daemon
    t.join(2)
    assert ended == [0]
    assert capsys.readouterr().out.strip().splitlines()[-1] == "Desk stopped."


def test_threads_desk_starts_are_daemons(store, monkeypatch):
    """No thread Desk starts may keep the interpreter alive after the server returns (the store's commit timer, the deadline)."""
    (store.home / "desk.toml").write_text((store.home / "desk.toml").read_text(encoding="utf-8").replace("sync = false", "sync = true"), encoding="utf-8")
    s = Store(store.home)
    monkeypatch.setattr(Store, "background_flush", True)
    s.sync_after("desk: test")
    try:
        assert Store._timer is not None and Store._timer.daemon
    finally:
        Store._timer.cancel()
    t = appmod.stop_deadline(3600, end=lambda code: None)
    assert t.daemon


# --- 2. integration: the real server in a new hidden console, a real console Ctrl+C ------------------------------------------------

# A stand-in for the window drew-ui runs in. "inherited": the flag `drew refresh` and agent shells pass down (Ctrl+C ignored);
# "terminal": an ordinary shell (Ctrl+C on for the child, the shell itself waits, as cmd.exe does).
_WRAP = """import ctypes, signal, subprocess, sys
k = ctypes.windll.kernel32
if sys.argv[1] == "inherited":
    k.SetConsoleCtrlHandler(None, True)
else:
    k.SetConsoleCtrlHandler(None, False)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
sys.exit(subprocess.call(sys.argv[2:]))
"""

# Sends CTRL_C_EVENT to the console of the given PID, from a process with no console of its own (never to the test's console).
_CTRL_C = """import ctypes, sys, time
k = ctypes.windll.kernel32
k.FreeConsole()
if not k.AttachConsole(int(sys.argv[1])):
    sys.exit(2)
k.SetConsoleCtrlHandler(None, True)
ok = k.GenerateConsoleCtrlEvent(0, 0)
time.sleep(0.1)
k.FreeConsole()
sys.exit(0 if ok else 3)
"""


def _hold_events(port: int, opened: threading.Event) -> None:
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        c.request("GET", "/events")
        r = c.getresponse()
        opened.set()
        while r.read1(100):
            pass
    except Exception:
        pass
    finally:
        opened.set()


@pytest.mark.skipif(not WIN, reason="Windows console Ctrl+C (CTRL_C_EVENT); other systems get SIGINT, covered by the next test")
@pytest.mark.parametrize("window,client,presses", [
    ("inherited", True, 1),   # James's case: the window `drew refresh` opens, a page with its live stream open
    ("inherited", False, 1),  # Ctrl+C before the first request
    ("inherited", True, 2),   # Ctrl+C twice
    ("terminal", True, 1),    # an ordinary terminal, a client still connected
    ("terminal", False, 1),   # an ordinary terminal, no clients
])
def test_console_ctrl_c_stops_the_real_server_within_three_seconds(store, window, client, presses):
    """Protects DESK-0308 end to end: the real drew-ui entry point, a real console Ctrl+C, the process gone within 3 s with code 0."""
    for i in range(3):
        ops.new(store, f"Ticket {i}", "desk")
    port = _free_port()
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # hidden: never a window on the owner's screen
    cmd = [sys.executable, "-I", "-c", _WRAP, window, sys.executable, "-X", "utf8", "-c", "from desk.ui.app import main; main()"]
    proc = subprocess.Popen(cmd, env=_env(store, port), creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=si)
    try:
        _wait_listening(port, proc)
        if client:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
            c.request("GET", "/")
            assert c.getresponse().status == 200
            opened = threading.Event()
            threading.Thread(target=_hold_events, args=(port, opened), daemon=True).start()
            assert opened.wait(10)
            time.sleep(0.3)
        start = None
        for i in range(presses):
            h = subprocess.run([sys.executable, "-I", "-c", _CTRL_C, str(proc.pid)], creationflags=subprocess.DETACHED_PROCESS, timeout=20)
            assert h.returncode == 0, f"could not send Ctrl+C (helper exit {h.returncode})"
            start = start or time.monotonic()
            if i + 1 < presses:
                time.sleep(0.3)
        try:
            code = proc.wait(3)
        except subprocess.TimeoutExpired:
            pytest.fail(f"drew-ui ({window} window, client={client}, presses={presses}) still running 3 s after Ctrl+C")
        took = time.monotonic() - start
        assert code == 0, f"exit code {code}"
        assert took < 3, f"took {took:.2f}s"
    finally:
        _kill(proc)


# --- 3. in-process SIGINT while a request never finishes: the deadline ends the process --------------------------------------------

_HANG = """import signal, sys, threading, time, http.client, socket
from pathlib import Path
import uvicorn
from desk.ui import app as appmod
port = int(sys.argv[2])
app = appmod.create_app(Path(sys.argv[1]))

@app.get("/hang-for-test")
def hang():
    time.sleep(60)  # a request stuck in a worker thread (a slow git, a locked file): it must not hold Ctrl+C
    return {}

def poke():
    while True:
        try:
            socket.create_connection(("127.0.0.1", port), 0.3).close(); break
        except OSError:
            time.sleep(0.1)
    def call():
        try:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=90); c.request("GET", "/hang-for-test"); c.getresponse().read()
        except Exception:
            pass
    threading.Thread(target=call, daemon=True).start()
    time.sleep(0.8)
    print("SIGINT", flush=True)
    signal.raise_signal(signal.SIGINT)

threading.Thread(target=poke, daemon=True).start()
serve = getattr(appmod, "serve", None)
if serve:
    serve(app, port)
else:  # the server as main() ran it before DESK-0308
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", timeout_graceful_shutdown=1)
"""


def test_sigint_with_a_stuck_request_still_stops_within_three_seconds(store):
    """Protects the last-resort deadline: a request that never finishes holds a non-daemon worker thread, which used to keep the
    process alive after uvicorn returned; now the process ends within 3 s of SIGINT and says "Desk stopped."."""
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-X", "utf8", "-c", _HANG, str(store.home), str(port)], env=_env(store, port),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    lines, start = [], [None]

    def read():
        for line in proc.stdout:
            lines.append(line.rstrip())
            if line.strip() == "SIGINT":
                start[0] = time.monotonic()

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        end = time.monotonic() + 30
        while start[0] is None and time.monotonic() < end and proc.poll() is None:
            time.sleep(0.05)
        assert start[0] is not None, f"the script never sent SIGINT: {lines}"
        try:
            code = proc.wait(3)
        except subprocess.TimeoutExpired:
            pytest.fail(f"still running 3 s after SIGINT with a stuck request; output: {lines}")
        took = time.monotonic() - start[0]
        reader.join(2)
        assert code == 0 and took < 3, (code, took, lines)
        assert lines and lines[-1] == "Desk stopped.", lines
    finally:
        _kill(proc)
