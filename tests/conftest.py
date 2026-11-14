import pytest

from desk.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store.init(tmp_path / "home", owner="james")
    for key, title in (("jre", "jRE"), ("desk", "Desk"), ("jeby-us", "jeby.us")):
        (s.projects_dir / f"{key}.md").write_text(f"# {title}\n", encoding="utf-8")
    return s


@pytest.fixture(autouse=True)
def _tickets_get_validate_steps(monkeypatch):
    """Most tests are about other rules and hand tickets over for review; give new tickets real validate steps.
    Tests of the How to validate rule itself clear the section first."""
    from desk import store as store_mod

    real = store_mod.new_ticket

    def with_steps(*a, **k):
        t = real(*a, **k)
        t.sections["How to validate"] = "1. Run it and look.\n"
        t.sections["Commands"] = "- `uv run --no-sync pytest -q`\n"  # a ticket with its commands, so the tests of other rules are not about DESK-0177
        return t

    monkeypatch.setattr(store_mod, "new_ticket", with_steps)


@pytest.fixture(autouse=True)
def _no_private_people(tmp_path_factory, monkeypatch):
    """No test reads the real ~/.claude/jeby-private/people.yaml (private, DESK-0044); a test that wants one points the variable at its own file."""
    monkeypatch.setenv("DESK_PRIVATE_PEOPLE", str(tmp_path_factory.mktemp("noprivate") / "people.yaml"))


@pytest.fixture(autouse=True)
def _no_ambient_identity(monkeypatch):
    """The tests run inside a Claude Code session whose environment names it; nothing of that may leak into what a test records (DESK-0144)."""
    for var in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "DESK_ACTOR", "DESK_ACTOR_KIND", "DESK_INSTANCE", "DESK_SUBAGENT_ID", "DESK_PARENT_SESSION"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _testclient_host_is_allowed(monkeypatch):
    """TestClient's default Host is `testserver`; Desk answers only this computer's names and the bind address (DESK-0074), so tests name it as the bind address."""
    monkeypatch.setenv("DESK_UI_HOST", "testserver")
