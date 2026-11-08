"""DESK-0044: people.yaml. Protects: every owner and assignee value resolves to a display name and colour, `main` is Patch, unknown
values still render, and the private file of external people is read only when it exists and never read by a test by accident."""

from fastapi.testclient import TestClient

from desk import ops, people
from desk.cli import main
from desk.ui.app import create_app


def test_built_in_team_when_the_store_has_no_file(store):
    pe = people.load(store.home)
    assert pe.display("main") == "Patch" and pe.display("bigd") == "Big D" and pe.display("james") == "James"
    assert pe.resolve("Patch") == "main" and pe.resolve("Big D") == "bigd" and pe.resolve("chaz") == "chaz"
    assert pe.resolve("jre") == "code:jre" and pe.display("code:jre").startswith("jre")  # a code session written the old way
    assert pe.colour("chaz").startswith("#")


def test_unknown_value_still_renders_with_a_neutral_colour(store):
    pe = people.load(store.home)
    assert pe.display("somebody-new") == "somebody-new" and pe.colour("somebody-new") == people.NEUTRAL and pe.get("somebody-new") is None


def test_store_file_wins_and_accepts_both_shapes(store):
    (store.home / "people.yaml").write_text("people:\n  - key: ann\n    name: Ann Lee\n    kind: coworker\n    colour: '#112233'\n"
                                            "  - {key: main, name: Patch, kind: agent}\n", encoding="utf-8")
    pe = people.load(store.home)
    assert pe.display("ann") == "Ann Lee" and pe.colour("ann") == "#112233" and pe.get("chaz") is None  # the file replaces the built-in team
    (store.home / "people.yaml").write_text("people:\n  zed: {name: Zed Z, kind: person}\n", encoding="utf-8")
    assert people.load(store.home).display("zed") == "Zed Z"  # the cache sees the edit


def test_private_file_adds_external_people_and_is_optional(store, tmp_path, monkeypatch):
    priv = tmp_path / "priv" / "people.yaml"
    monkeypatch.setenv("DESK_PRIVATE_PEOPLE", str(priv))
    assert people.load(store.home).get("legal-counsel") is None  # no file: nothing, no error
    priv.parent.mkdir()
    priv.write_text("people:\n  - key: legal-counsel\n    name: Counsel Name\n    note: not yet engaged\n", encoding="utf-8")
    pe = people.load(store.home)
    assert pe.display("legal-counsel") == "Counsel Name" and pe.get("legal-counsel").kind == "external" and pe.get("legal-counsel").private
    assert [label for label, _ in pe.groups()][-1] == "People outside the team"
    assert not (store.home / "people.yaml").exists()  # nothing is copied into the store


def test_tests_never_see_the_real_private_file(store):
    assert str(people.private_path()).endswith("people.yaml") and "jeby-private" not in str(people.private_path())


def test_ui_shows_display_names_and_colour_everywhere_an_owner_appears(store):
    t = ops.new(store, "Owned work", "jre", owner="main")
    ops.assign(store, t, "Big D")
    assert store.get(t.id).meta["assignee"] == "bigd"  # stored as the key
    c = TestClient(create_app(store.home))
    ticket = c.get(f"/ticket/{t.id}").text
    assert "Patch" in ticket and "Big D" in ticket and 'class="wdot"' in ticket
    assert "Patch" in c.get("/board?open=*&g=*").text
    assert "Patch" in c.get("/projects?p=jre&t=untracked").text
    assert "Big D" in c.get("/board").text  # the assignee chip


def test_unknown_owner_renders_as_written(store):
    ops.new(store, "Odd owner", "jre", owner="stranger")
    assert "stranger" in TestClient(create_app(store.home)).get("/board?open=*&g=*").text


def test_search_assignee_filter_takes_a_display_name(store):
    from desk import search

    t = ops.new(store, "Find me", "jre")
    ops.assign(store, t, "Patch")
    ix = search.Index(None, resolve=search.people_resolver(store))
    ix.refresh(store, force=True)
    assert [h.id for _, _, hs in ix.search("assignee:Patch").groups for h in hs] == [t.id]
    assert [h.id for _, _, hs in ix.search("assignee:main").groups for h in hs] == [t.id]


def test_cli_people_list_init_and_list_assignee_by_name(store, capsys):
    main(["--home", str(store.home), "people"])
    out = capsys.readouterr().out
    assert "built-in team" in out and "Patch" in out and "Code sessions" in out
    main(["--home", str(store.home), "people", "init"])
    assert (store.home / "people.yaml").is_file() and "Patch" in people.load(store.home).display("main")
    capsys.readouterr()
    t = ops.new(store, "Handed on", "jre")
    ops.assign(store, t, "Patch")
    main(["--home", str(store.home), "list", "--assignee", "Patch"])
    assert t.id in capsys.readouterr().out
