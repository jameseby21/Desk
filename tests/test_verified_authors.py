"""DESK-0259: verified authors (DESK-0261: `--as` must now name the caller, so each run names its own actor in DESK_ACTOR). Protects: a comment is verified only when the actor's own token matches the stored hash; `--as chaz` without
the token is unverified; a session that already wrote as someone else and then writes as chaz is flagged; `drew validate --signoffs` lists
all three states. Tokens are made in a temporary keys folder and never written to the store."""

import pytest

from desk import ops
from desk.cli import main

SIGN = "Sign-off (security): fine"


@pytest.fixture
def keys(tmp_path, monkeypatch):
    monkeypatch.setenv("DESK_KEYS_DIR", str(tmp_path / "keys"))
    return tmp_path / "keys"


def _run(store, monkeypatch, args, **env):
    for k in ("DESK_ACTOR", "DESK_ACTOR_TOKEN", "DESK_INSTANCE"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    main(["--home", str(store.home), *args])


def _token(capsys, actor):
    main(["token", actor])
    return capsys.readouterr().out.splitlines()[1]


def _line(store, tid, n=-1):
    return ops.comment_lines(store.get(tid))[n]


def test_right_token_is_verified(store, keys, monkeypatch, capsys):
    t = ops.new(store, "Talk", "jre")
    tok = _token(capsys, "chaz")
    assert tok not in "".join(p.read_text() for p in keys.iterdir())  # only the hash is stored
    _run(store, monkeypatch, ["comment", t.id, SIGN, "--as", "chaz"], DESK_ACTOR="chaz", DESK_ACTOR_TOKEN=tok)
    assert "via=verified ok=1" in _line(store, t.id) and "sig=" in _line(store, t.id)
    assert tok not in (store.tickets_dir / next(store.tickets_dir.glob(f"{t.id}*"))).read_text(encoding="utf-8")


def test_no_token_is_unverified_and_wrong_actor_token_too(store, keys, monkeypatch, capsys):
    t = ops.new(store, "Talk", "jre")
    ptok = _token(capsys, "patch")
    _run(store, monkeypatch, ["comment", t.id, SIGN, "--as", "chaz"], DESK_ACTOR="chaz")
    assert "via=claimed ok=0" in _line(store, t.id)
    _run(store, monkeypatch, ["comment", t.id, "Sign-off (ux): other", "--as", "chaz"], DESK_ACTOR="chaz", DESK_ACTOR_TOKEN=ptok)
    assert "via=claimed ok=0" in _line(store, t.id) and "sig=" not in _line(store, t.id)


def test_session_that_wrote_as_patch_then_as_chaz_is_flagged(store, keys, monkeypatch):
    t = ops.new(store, "Talk", "jre")
    _run(store, monkeypatch, ["comment", t.id, "hello", "--as", "patch"], DESK_ACTOR="patch", DESK_INSTANCE="sess-1")
    assert "impersonation" not in _line(store, t.id)
    _run(store, monkeypatch, ["comment", t.id, SIGN, "--as", "chaz"], DESK_ACTOR="chaz", DESK_INSTANCE="sess-1")
    assert "impersonation=suspected" in _line(store, t.id)


def test_validate_signoffs_prints_all_three_states(store, keys, monkeypatch, capsys):
    t = ops.new(store, "Talk", "jre")
    tok = _token(capsys, "chaz")
    _run(store, monkeypatch, ["comment", t.id, "Sign-off (design): good", "--as", "chaz"], DESK_ACTOR="chaz", DESK_ACTOR_TOKEN=tok, DESK_INSTANCE="sess-c")
    _run(store, monkeypatch, ["comment", t.id, "Sign-off (legal): fine", "--as", "joe"], DESK_ACTOR="joe")
    _run(store, monkeypatch, ["comment", t.id, "note", "--as", "patch"], DESK_ACTOR="patch", DESK_INSTANCE="sess-p")
    _run(store, monkeypatch, ["comment", t.id, "Sign-off (money): ok", "--as", "big-d"], DESK_ACTOR="big-d", DESK_INSTANCE="sess-p")
    capsys.readouterr()
    with pytest.raises(SystemExit) as e:
        _run(store, monkeypatch, ["validate", "--signoffs"])
    out = capsys.readouterr().out
    assert e.value.code == 1
    assert "chaz  verified" in out and "joe  unverified" in out and "big-d  impersonation suspected" in out
    assert "3 sign-off(s), 1 verified, 2 not counted" in out
