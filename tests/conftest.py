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
        return t

    monkeypatch.setattr(store_mod, "new_ticket", with_steps)


@pytest.fixture(autouse=True)
def _no_private_people(tmp_path_factory, monkeypatch):
    """No test reads the real ~/.claude/jeby-private/people.yaml (private, DESK-0044); a test that wants one points the variable at its own file."""
    monkeypatch.setenv("DESK_PRIVATE_PEOPLE", str(tmp_path_factory.mktemp("noprivate") / "people.yaml"))
