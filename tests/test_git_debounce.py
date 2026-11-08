"""[git] sync = true commits on an idle debounce or `drew sync`, not once per save."""

import subprocess
import time

import pytest

from desk import ops
from desk.store import Store


def git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture
def synced(tmp_path, store):
    """A store whose git repo pushes to a local bare remote, sync on, 30 s debounce."""
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", "-b", "main", str(remote))
    home = store.home
    git(home, "init", "-b", "main")
    git(home, "config", "user.email", "t@example.com")
    git(home, "config", "user.name", "T")
    git(home, "remote", "add", "origin", str(remote))
    (home / "desk.toml").write_text((home / "desk.toml").read_text(encoding="utf-8").replace("sync = false", "sync = true"), encoding="utf-8")
    git(home, "add", "-A")
    git(home, "commit", "-m", "start")
    git(home, "push", "-u", "origin", "main")
    Store.background_flush = False
    yield Store(home), remote
    Store.background_flush = False
    if Store._timer is not None:
        Store._timer.cancel()
        Store._timer = None


def commits(remote):
    return int(git(remote, "rev-list", "--count", "main"))


def save_one(s, i):
    t = ops.new(s, f"Imported ticket {i}", "jre")
    s.sync_after(f"desk: new {t.id}")


def test_hundred_ticket_import_makes_few_commits(synced):
    s, remote = synced
    for i in range(100):
        save_one(Store(s.home), i)
    assert commits(remote) == 1  # still only "start": everything is pending
    assert Store(s.home).pending()["count"] == 100
    assert Store(s.home).sync_now()[0] is True  # drew sync
    assert commits(remote) == 2
    assert len(git(remote, "ls-tree", "--name-only", "main", "tickets/").splitlines()) == 100
    assert Store(s.home).pending() is None
    assert "100 changes" in git(remote, "log", "-1", "--format=%s", "main")


def test_stop_mid_debounce_loses_nothing_and_next_start_commits(synced):
    s, remote = synced
    s.config["git"]["debounce_seconds"] = 0.3
    save_one(s, 1)
    assert commits(remote) == 1 and s.pending()
    s2 = Store(s.home)
    s2.config["git"]["debounce_seconds"] = 0.3
    s2.sync_before()  # a start inside the wait leaves it pending
    assert commits(remote) == 1 and s2.pending()
    time.sleep(0.45)
    s3 = Store(s.home)
    s3.config["git"]["debounce_seconds"] = 0.3
    s3.sync_before()  # a start after the idle time commits it
    assert commits(remote) == 2 and s3.pending() is None
    assert any("JRE" in n.upper() or n.startswith("tickets/") for n in git(remote, "ls-tree", "-r", "--name-only", "main").splitlines())


def test_background_timer_commits_after_idle(synced):
    s, remote = synced
    s.config["git"]["debounce_seconds"] = 0.3
    Store.background_flush = True
    for i in range(3):
        save_one(s, i)
        time.sleep(0.1)  # each save restarts the wait
    assert commits(remote) == 1
    deadline = time.time() + 5
    while commits(remote) == 1 and time.time() < deadline:
        time.sleep(0.1)
    assert commits(remote) == 2


def test_sync_false_stays_untouched(store):
    store.sync_after("desk: new X")
    assert store.pending() is None and not (store.home / ".git").exists()
