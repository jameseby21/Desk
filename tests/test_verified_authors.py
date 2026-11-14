"""DESK-0259, DESK-0262: verified authors. Protects: a comment is verified only when it is signed with the actor's own SSH private key and
checks against the public key Desk stores; `--as chaz` without that key is unverified; a session that already wrote as someone else and
then writes as chaz is flagged; `drew validate --signoffs` lists all three states; only the owner at their own terminal can issue or
rotate a key (a session cannot); nothing in the key directory can forge a verified line. Keys are made in temporary folders."""

import json
import shutil

import pytest

from desk import identity, ops, people, signoffs
from desk.cli import main

SIGN = "Sign-off (security): fine"
pytestmark = pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="ssh-keygen (OpenSSH) is needed to sign and verify")
_ENV = ("DESK_ACTOR", "DESK_ACTOR_KEY", "DESK_INSTANCE")


@pytest.fixture
def keys(tmp_path, monkeypatch):
    monkeypatch.setenv("DESK_KEYS_DIR", str(tmp_path / "keys"))
    return tmp_path / "keys"


@pytest.fixture
def owner(store, monkeypatch):
    """This machine's OS user is the store owner (james), as at the owner's own terminal."""
    people.link_os_user(store.home, "james", "jeby")
    monkeypatch.setattr(identity, "os_user", lambda: "jeby")
    return store


def _run(store, monkeypatch, args, **env):
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    main(["--home", str(store.home), *args])


def _key(store, monkeypatch, tmp_path, actor, *more):
    """The owner issues `actor` a key pair; returns the private key path."""
    out = tmp_path / "private" / actor
    _run(store, monkeypatch, ["token", actor, "--out", str(out), *more])
    return str(out)


def _line(store, tid, n=-1):
    return ops.comment_lines(store.get(tid))[n]


def test_right_key_is_verified_and_only_public_keys_are_stored(owner, keys, monkeypatch, tmp_path):
    store = owner
    t = ops.new(store, "Talk", "jre")
    priv = _key(store, monkeypatch, tmp_path, "chaz")
    assert [p.name for p in keys.iterdir()] == ["chaz.pub"]
    assert "PRIVATE KEY" not in (keys / "chaz.pub").read_text() and "PRIVATE KEY" in open(priv).read()
    _run(store, monkeypatch, ["comment", t.id, SIGN, "--as", "chaz"], DESK_ACTOR="chaz", DESK_ACTOR_KEY=priv)
    assert "via=verified ok=1" in _line(store, t.id) and "sig=" in _line(store, t.id)
    assert signoffs.rows(store)[-1][2:] == ("chaz", signoffs.VERIFIED)


def test_no_key_is_unverified_and_wrong_actor_key_too(owner, keys, monkeypatch, tmp_path):
    store = owner
    t = ops.new(store, "Talk", "jre")
    ppriv = _key(store, monkeypatch, tmp_path, "patch")
    _run(store, monkeypatch, ["comment", t.id, SIGN, "--as", "chaz"])
    assert "via=claimed ok=0" in _line(store, t.id)
    _run(store, monkeypatch, ["comment", t.id, "Sign-off (ux): other", "--as", "chaz"], DESK_ACTOR_KEY=ppriv)
    assert "via=claimed ok=0" in _line(store, t.id) and "sig=" not in _line(store, t.id)


def test_session_that_wrote_as_patch_then_as_chaz_is_flagged(store, keys, monkeypatch):
    t = ops.new(store, "Talk", "jre")
    _run(store, monkeypatch, ["comment", t.id, "hello", "--as", "patch"], DESK_INSTANCE="sess-1")
    assert "impersonation" not in _line(store, t.id)
    _run(store, monkeypatch, ["comment", t.id, SIGN, "--as", "chaz"], DESK_INSTANCE="sess-1")
    assert "impersonation=suspected" in _line(store, t.id)


def test_validate_signoffs_prints_all_three_states(owner, keys, monkeypatch, capsys, tmp_path):
    store = owner
    t = ops.new(store, "Talk", "jre")
    priv = _key(store, monkeypatch, tmp_path, "chaz")
    _run(store, monkeypatch, ["comment", t.id, "Sign-off (design): good", "--as", "chaz"], DESK_ACTOR="chaz", DESK_ACTOR_KEY=priv, DESK_INSTANCE="sess-c")
    _run(store, monkeypatch, ["comment", t.id, "Sign-off (legal): fine", "--as", "joe"])
    _run(store, monkeypatch, ["comment", t.id, "note", "--as", "patch"], DESK_INSTANCE="sess-p")
    _run(store, monkeypatch, ["comment", t.id, "Sign-off (money): ok", "--as", "big-d"], DESK_INSTANCE="sess-p")
    capsys.readouterr()
    with pytest.raises(SystemExit) as e:
        _run(store, monkeypatch, ["validate", "--signoffs"])
    out = capsys.readouterr().out
    assert e.value.code == 1
    assert "chaz  verified" in out and "joe  unverified" in out and "big-d  impersonation suspected" in out
    assert "3 sign-off(s), 1 verified, 2 not counted" in out


# -- DESK-0262 review findings (pass 3, desk-pr14) --------------------------------------------------------

@pytest.mark.parametrize("session_env", [{"DESK_ACTOR": "patch"}, {"CLAUDECODE": "1"}])
def test_a_session_cannot_rotate_another_actors_key(owner, keys, monkeypatch, tmp_path, session_env):
    """Protects: a session (DESK_ACTOR=patch, or any Claude Code shell) cannot issue or rotate chaz's key; non-zero exit, nothing written."""
    store = owner
    _key(store, monkeypatch, tmp_path, "chaz")
    before = {p.name: p.read_bytes() for p in keys.iterdir()}
    for k, v in session_env.items():
        monkeypatch.setenv(k, v)
    out = tmp_path / "stolen"
    with pytest.raises(SystemExit) as e:
        main(["--home", str(store.home), "token", "chaz", "--rotate", "--out", str(out)])
    assert e.value.code not in (0, None)
    assert {p.name: p.read_bytes() for p in keys.iterdir()} == before
    assert not out.exists()
    with pytest.raises(SystemExit):
        main(["--home", str(store.home), "token", "joe", "--out", str(tmp_path / "joe")])  # a first issue is refused too
    assert not (keys / "joe.pub").exists()


def test_a_non_owner_os_user_cannot_issue(store, keys, monkeypatch, tmp_path):
    """Protects: an OS user who is not mapped to the owner in people.yaml is refused."""
    monkeypatch.setattr(identity, "os_user", lambda: "someone-else")
    with pytest.raises(SystemExit) as e:
        main(["--home", str(store.home), "token", "chaz", "--out", str(tmp_path / "chaz")])
    assert e.value.code not in (0, None) and not (keys / "chaz.pub").exists()


def test_every_issue_and_rotation_is_logged(owner, keys, monkeypatch, tmp_path):
    store = owner
    _key(store, monkeypatch, tmp_path, "chaz")
    out2 = tmp_path / "private" / "chaz2"
    _run(store, monkeypatch, ["token", "chaz", "--rotate", "--out", str(out2)])
    rows = [r for r in identity.read_log(store.home) if r.get("event", "").startswith("key-")]
    assert [(r["event"], r["for"], r["actor"], r["how"]) for r in rows] == [("key-issue", "chaz", "james", "os-user"), ("key-rotate", "chaz", "james", "os-user")]
    assert all(r["fingerprint"].startswith("SHA256:") for r in rows)
    assert json.dumps(rows).count("PRIVATE") == 0


def test_a_line_forged_from_the_key_directory_does_not_verify(owner, keys, monkeypatch, tmp_path):
    """Protects: nothing Desk stores can sign; a sign-off built from every file in the key directory is not counted as verified."""
    import hashlib
    import hmac
    import subprocess

    store = owner
    t = ops.new(store, "Talk", "jre")
    _key(store, monkeypatch, tmp_path, "chaz")
    when, words = "2026-11-13 10:00", "Sign-off (money): ok"
    sigs = []
    for f in keys.iterdir():
        k = f.read_text(encoding="utf-8").strip()
        sigs.append(hmac.new(k.encode(), f"chaz\n{t.id}\n{when}\n{words}".encode(), hashlib.sha256).hexdigest()[:32])  # the old scheme
        r = subprocess.run([shutil.which("ssh-keygen"), "-Y", "sign", "-f", str(f), "-n", identity.NAMESPACE],
                           input=f"chaz\n{t.id}\n{when}\n{words}".encode(), capture_output=True)
        sigs.append("".join(ln for ln in r.stdout.decode(errors="replace").splitlines() if not ln.startswith("-----")))
    for sig in sigs:
        rest = f"{words} <!-- by kind=thread src=cli via=verified ok=1 sig={sig} --> <!-- kind: FYI -->"
        assert signoffs.state_of(t.id, when, "chaz", rest) != signoffs.VERIFIED
