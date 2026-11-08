import pytest

from desk.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store.init(tmp_path / "home", owner="james")
    for key, title in (("jre", "jRE"), ("desk", "Desk"), ("jeby-us", "jeby.us")):
        (s.projects_dir / f"{key}.md").write_text(f"# {title}\n", encoding="utf-8")
    return s
