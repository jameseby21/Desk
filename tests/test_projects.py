"""Project prefixes, aliases and retroactive renames: ids never change."""

import pytest
from fastapi.testclient import TestClient

from desk import ops
from desk.cli import main
from desk.rules import RuleError, validate_store
from desk.store import Store
from desk.ui.app import create_app


def profile(store, key, prefix=None, aliases=None, title=None):
    fm = [f"key: {key}"]
    if prefix:
        fm.append(f"prefix: {prefix}")
    if aliases is not None:
        fm.append("aliases: [" + ", ".join(aliases) + "]")
    (store.projects_dir / f"{key}.md").write_text("---\n" + "\n".join(fm) + f"\n---\n# {title or key}\n\nBody.\n", encoding="utf-8")


def test_alias_prefixes_are_valid_ids(store):
    """A ticket id may use the current prefix or the uppercased form of any alias (old key or old prefix)."""
    profile(store, "learninglabs", prefix="LEARN", aliases=["fraud", "FR"], title="learningLabs")
    assert store.key_for("learninglabs") == "LEARN"
    assert store.prefixes_for("learninglabs") == {"LEARN", "FRAUD", "FR"}
    t = ops.new(store, "Old fraud ticket", "learninglabs")
    assert t.id == "LEARN-0001"
    for old in ("FRAUD-0001", "FR-0007"):
        t.meta["id"] = old
        t.path = store.tickets_dir / f"{old}-old.md"
        store.save(t)
    t.meta["id"] = "NOPE-0001"
    with pytest.raises(ValueError, match="prefix"):
        store.save(t)
    errors, _ = validate_store(store)
    assert errors == []
    assert store.formerly(store.get("FR-0007")) == "FR"
    assert store.formerly(store.get("LEARN-0001")) is None
    assert store.next_id("learninglabs") == "LEARN-0002"  # new tickets always get the current prefix


def test_rename_project_keeps_ids_and_records_aliases(store):
    """rename-project moves the profile, appends old key and prefix to aliases, repoints tickets, bumps updated, comments."""
    profile(store, "fraud", prefix="FR", title="Fraud")
    a = ops.new(store, "Spot the pattern", "fraud")
    b = ops.new(store, "Second", "fraud")
    other = ops.new(store, "Unrelated", "jre")
    for t in (a, b):
        t.meta["updated"] = "2026-01-01"
        store.save(t, touch=False)

    path, changed = ops.rename_project(store, "fraud", "learninglabs", prefix="LEARN", name="learningLabs")
    assert path == store.projects_dir / "learninglabs.md" and not (store.projects_dir / "fraud.md").exists()
    meta, body = store.project_profile("learninglabs")
    assert meta["key"] == "learninglabs" and meta["prefix"] == "LEARN" and meta["aliases"] == ["fraud", "FR"]
    assert body.startswith("# learningLabs")
    assert store.project_title("learninglabs") == "learningLabs"

    assert sorted(t.id for t in changed) == ["FR-0001", "FR-0002"]
    a2 = store.get("FR-0001")
    assert a2.project == "learninglabs" and a2.path == a.path
    assert a2.meta["updated"] != "2026-01-01"
    assert "· desk · project renamed fraud -> learninglabs" in a2.section("Comments")
    assert store.get(other.id).project == "jre"
    assert store.formerly(a2) == "FR"
    assert ops.new(store, "Fresh", "learninglabs").id == "LEARN-0001"
    errors, _ = validate_store(store)
    assert errors == []


def test_profile_frontmatter_tolerates_hand_written_values(store):
    """`name: Algo (a repo): notes` is not strict YAML; the prefix and aliases are still read."""
    (store.projects_dir / "algo.md").write_text(
        "---\nkey: algo\nprefix: AL\nname: Algo (James's newest repo): just initialized\n"
        "aliases: [old-algo, OA]\nstatus: pending: James describes it\n---\n# Algo\n", encoding="utf-8")
    assert store.key_for("algo") == "AL"
    assert store.prefixes_for("algo") == {"AL", "OLD-ALGO", "OA"}
    meta, body = store.project_profile("algo")
    assert meta["status"] == "pending: James describes it" and body.startswith("# Algo")


def test_rename_project_refuses_existing_target(store):
    profile(store, "fraud", prefix="FR")
    with pytest.raises(RuleError, match="already exists"):
        ops.rename_project(store, "fraud", "jre")
    with pytest.raises(RuleError, match="No project"):
        ops.rename_project(store, "nothing", "something")
    assert (store.projects_dir / "fraud.md").exists()


def test_rename_shows_formerly_in_cli_and_ui(store, capsys):
    """list, board, show and every UI page say (formerly OLD) after a renamed project's old ids."""
    profile(store, "fraud", prefix="FR", title="Fraud")
    t = ops.new(store, "Spot the pattern", "fraud")
    main(["--home", str(store.home), "rename-project", "fraud", "learninglabs", "--prefix", "LEARN"])
    out = capsys.readouterr().out
    assert "1 ticket(s) now say project: learninglabs" in out

    main(["--home", str(store.home), "list"])
    assert "FR-0001 (formerly FR)" in capsys.readouterr().out
    main(["--home", str(store.home), "board"])
    out = capsys.readouterr().out
    assert "FR-0001 (formerly FR)" in out and "[learninglabs]" in out
    main(["--home", str(store.home), "show", "FR-0001"])
    out = capsys.readouterr().out
    assert out.startswith("# FR-0001 (formerly FR) · project learninglabs · Fraud")
    assert "project: learninglabs" in out

    ops.move(store, store.get(t.id), "ready", actor="main")
    ops.claim(store, store.get(t.id), "jre")  # Today lists what is in hand as rows (DESK-0040); a ready ticket is only counted there
    client = TestClient(create_app(store.home))
    assert "(formerly FR)" in client.get("/").text
    assert "(formerly FR)" in client.get("/board?open=*&g=*").text  # opened: the board starts at projects (DESK-0084)
    assert "(formerly FR)" in client.get("/ticket/FR-0001").text
    page = client.get("/projects").text
    assert "learninglabs" in page and page.count("fraud") == 1  # old key only as an alias
    assert "formerly fraud, FR" in page
