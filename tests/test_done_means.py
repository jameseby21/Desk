"""DESK-0093: Done means comes from the Definition of Done, by reference."""

from fastapi.testclient import TestClient

from desk import accept_migrate, docs, ops
from desk.cli import main
from desk.ui.app import create_app


def _dod_files(store):
    d = store.home / "standards" / "definition-of-done"
    d.mkdir(parents=True)
    (d / "core.md").write_text("# core\n\n- [ ] CORE-MARKER the claim names what was and was not run.\n", encoding="utf-8")
    (d / "ui.md").write_text("# ui\n\n- [ ] UI-MARKER keyboard and phone width checked.\n", encoding="utf-8")


def test_two_blocks_and_modules_rendered_from_standards_files(store):
    _dod_files(store)
    t = ops.new(store, "Look", "desk", dod=["core", "ui"])
    t.set_section("Acceptance", "- [ ] The widget glows")
    store.save(t)
    c = TestClient(create_app(store.home))
    page = c.get(f"/ticket/{t.id}").text
    assert "Acceptance (this ticket's outcome)" in page and "Done means" in page and "core and ui checklists" in page and "2 checks" in page  # one folded line (DESK-0175)
    assert "CORE-MARKER" in page and "UI-MARKER" in page and 'href="/doc/standards/definition-of-done/ui.md"' in page
    assert "CORE-MARKER" not in store.get(t.id).render()  # by reference: nothing copied into the ticket
    (store.home / "standards" / "definition-of-done" / "ui.md").write_text("# ui\n\n- [ ] CHANGED-MARKER\n", encoding="utf-8")
    assert "CHANGED-MARKER" in c.get(f"/ticket/{t.id}").text  # read on every request


def test_missing_module_says_so(store):
    _dod_files(store)
    t = ops.new(store, "Look", "desk", dod=["nope"])
    assert "No file at standards/definition-of-done/nope.md" in TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text


def test_profile_dod_doc_overrides_from_the_repo(store):
    _dod_files(store)
    (store.projects_dir / "jre.md").write_text("---\nkey: jre\nprefix: JRE\ndod_doc: docs/guides/DEFINITION_OF_DONE.md\n---\n# jRE\n\n| Repo | `jeby Real Estate` |\n", encoding="utf-8")
    g = store.home / "jeby Real Estate" / "docs" / "guides"
    g.mkdir(parents=True)
    (g / "DEFINITION_OF_DONE.md").write_text("# Definition of Done\n\nJRE-OWN-DOD runtime-verified evidence.\n", encoding="utf-8")
    t = ops.new(store, "Listing", "jre", dod=["core"])
    page = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    assert "JRE-OWN-DOD" in page and "CORE-MARKER" not in page and 'href="/doc/jeby%20Real%20Estate/docs/guides/DEFINITION_OF_DONE.md"' in page
    assert docs.dod_sources(store, t)["kind"] == "project"


def test_bad_dod_doc_warns_and_falls_back(store):
    _dod_files(store)
    (store.projects_dir / "jre.md").write_text("---\nkey: jre\ndod_doc: docs/nowhere.md\n---\n# jRE\n", encoding="utf-8")
    t = ops.new(store, "Listing", "jre", dod=["core"])
    page = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    assert "no such file" in page and "CORE-MARKER" in page


def test_migrate_acceptance_dry_run_then_apply_keeps_specific_lines(store, capsys):
    t = ops.new(store, "Decide", "desk")
    t.set_section("Acceptance", "- [ ] Evidence attached (screenshot or recording)\n- [x] Works in light and dark\n- [ ] The invoice total matches\n"
                                "- [ ] Decision recorded in DECISIONS.md\n- [ ] Decision recorded by 2026-12-15\n- [ ] Answer recorded in `notes/x.md`\n"
                                "```\n- [ ] Reduced motion respected\n```\n")
    store.save(t, touch=False)
    before = t.path.read_bytes()
    main(["--home", str(store.home), "migrate", "acceptance", "--dry-run"])
    out = capsys.readouterr().out
    assert "3 boilerplate lines in 1 tickets" in out and t.path.read_bytes() == before
    main(["--home", str(store.home), "migrate", "acceptance", "--apply"])
    acc = store.get(t.id).section("Acceptance")
    assert acc.splitlines()[0] == "- [ ] The invoice total matches" and "Decision recorded by 2026-12-15" in acc and "Answer recorded in `notes/x.md`" in acc
    assert "Reduced motion respected" in acc  # inside a fence: untouched
    assert "Evidence attached" not in acc and "Works in light" not in acc and "DECISIONS.md" not in acc
    assert accept_migrate.plan(store.tickets()) == []
