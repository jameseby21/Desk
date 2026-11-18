"""Project profiles are parsed once per version of the file; an edit shows on the next request."""

from fastapi.testclient import TestClient

from desk import ops, store as store_mod
from desk.ui.app import create_app


def test_profile_is_parsed_once_until_the_file_changes(store, monkeypatch):
    calls = []
    real = store_mod.split_profile
    monkeypatch.setattr(store_mod, "split_profile", lambda text: (calls.append(1), real(text))[1])
    (store.projects_dir / "jre.md").write_text("---\nprefix: JR\naliases: [OLD]\nbrand: {accent: \"#111111\"}\n---\n# jRE\n", encoding="utf-8")
    t = ops.new(store, "A card", "jre")
    for _ in range(3):
        store.brand_for("jre"), store.project_title("jre"), store.key_for("jre"), store.aliases_for("jre"), store.prefixes_for("jre"), store.formerly(t)
    assert len(calls) == 1
    assert store.key_for("jre") == "JR" and store.prefixes_for("jre") == {"JR", "OLD"} and store.brand_for("jre")["accent"] == "#111111"
    (store.projects_dir / "jre.md").write_text("---\nprefix: JRX\n---\n# jRE renamed\n", encoding="utf-8")
    assert store.key_for("jre") == "JRX" and store.project_title("jre") == "jRE renamed" and store.brand_for("jre") is None
    assert store.aliases_for("jre") == [] and len(calls) == 2


def test_edit_shows_on_the_next_request(store):
    (store.projects_dir / "jre.md").write_text("---\nbrand: {accent: \"#111111\"}\n---\n# jRE\n", encoding="utf-8")
    ops.new(store, "A card", "jre")
    c = TestClient(create_app(store.home))
    first = c.get("/board?company=unsorted").text  # DESK-0183: project titles show one level below companies
    assert "jRE" in first
    (store.projects_dir / "jre.md").write_text("---\nbrand: {accent: \"#222222\"}\n---\n# Renamed Project Title\n", encoding="utf-8")
    second = c.get("/board?company=unsorted").text
    assert "Renamed Project Title" in second and "Renamed Project Title" not in first


def test_new_and_removed_profiles_are_seen(store):
    assert store.project_title("fresh") == "fresh" and store.brand_for("fresh") is None
    (store.projects_dir / "fresh.md").write_text("# Fresh One\n", encoding="utf-8")
    assert store.project_title("fresh") == "Fresh One"
    (store.projects_dir / "fresh.md").unlink()
    assert store.project_title("fresh") == "fresh"
