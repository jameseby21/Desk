"""Doc-path links, the read-only /doc viewer, glossary popovers, [[Term]] and Mentioned-in backlinks."""

import pytest
from fastapi.testclient import TestClient

from desk import docs, ops
from desk.ui.app import create_app


@pytest.fixture
def doc_store(store):
    (store.home / "docs").mkdir()
    (store.home / "docs" / "EDITIONS.md").write_text("# Editions\n\n## Free edition\nText here.\n", encoding="utf-8")
    (store.home / "glossary.md").write_text("# Glossary\n\n## Ask\nWhat a ticket wants from you.\n\n## Proof\nA run that shows it works.\n", encoding="utf-8")
    (store.home / "secret.txt").write_text("nope", encoding="utf-8")
    return store


def _client(store):
    return TestClient(create_app(store.home))


def test_doc_path_links_and_opens(doc_store):
    t = ops.new(doc_store, "Linker", "jre")
    t.set_section("Context", "See docs/EDITIONS.md#free-edition for it, and docs/MISSING.md too, and `docs/EDITIONS.md`.")
    doc_store.save(t)
    c = _client(doc_store)
    page = c.get(f"/ticket/{t.id}").text
    assert 'href="/doc/docs/EDITIONS.md#free-edition"' in page and 'href="/doc/docs/EDITIONS.md"' in page
    assert 'href="/doc/docs/MISSING.md"' not in page and "docs/MISSING.md" in page
    r = c.get("/doc/docs/EDITIONS.md")
    assert r.status_code == 200 and 'id="free-edition"' in r.text and "Text here." in r.text


@pytest.mark.parametrize("bad", ["../secret.txt", "docs/../../x.md", "..%2fsecret.md", "secret.txt", "docs/nothere.md", "C:/Windows/win.md",
                                 "jeby-private/x.md", "other/EDITIONS.md", ".desk/x.md", "docs//EDITIONS.md/.."])
def test_traversal_and_strays_refused(doc_store, bad):
    assert _client(doc_store).get("/doc/" + bad).status_code == 404


def test_resolve_doc_is_store_only(doc_store, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("x", encoding="utf-8")
    assert docs.resolve_doc(doc_store.home, str(outside)) is None
    assert docs.resolve_doc(doc_store.home, "docs/EDITIONS.md") is not None
    assert docs.resolve_doc(doc_store.home, "glossary.md") is not None


def test_glossary_popover_once_and_wiki_link(doc_store):
    t = ops.new(doc_store, "Terms", "jre")
    t.set_section("Context", "An ask is here. Another ask later. Also [[Proof]] and a Proof word.")
    t.set_section("Outcome", "The ask again, in another section.")
    doc_store.save(t)
    page = _client(doc_store).get(f"/ticket/{t.id}").text
    assert page.count('<span class="gloss"') == 2 and page.count("A run that shows it works.") == 1  # ask and Proof, once each
    assert page.count("What a ticket wants from you.") == 1
    assert 'href="/doc/glossary.md#proof"' in page
    r = _client(doc_store).get("/doc/glossary.md")
    assert r.status_code == 200 and 'id="ask"' in r.text and '<span class="gloss"' not in r.text


def test_wiki_link_always_links(doc_store):
    t = ops.new(doc_store, "Wiki", "jre")
    t.set_section("Context", "[[Ask]] one. [[Ask]] two.")
    doc_store.save(t)
    page = _client(doc_store).get(f"/ticket/{t.id}").text
    assert page.count('href="/doc/glossary.md#ask"') == 2


def test_mentioned_in_lists_the_right_tickets(doc_store):
    a = ops.new(doc_store, "Target", "jre")
    b = ops.new(doc_store, "Mentions target", "jre")
    c = ops.new(doc_store, "Unrelated", "jre")
    d = ops.new(doc_store, "Mentions doc", "jre")
    b.set_section("Context", f"Blocked by {a.id} until later.")
    d.set_section("Report", "Read projects/jre.md and docs/EDITIONS.md.")
    for x in (b, d):
        doc_store.save(x)
    client = _client(doc_store)
    page = client.get(f"/ticket/{a.id}").text
    mentioned = page.split("Mentioned in")[1]
    assert b.id in mentioned and c.id not in mentioned and d.id not in mentioned
    assert "No other ticket mentions" in client.get(f"/ticket/{c.id}").text
    proj = client.get("/doc/projects/jre.md").text.split("Mentioned in")[1]
    assert d.id in proj and b.id not in proj
    assert d.id in client.get("/doc/docs/EDITIONS.md").text.split("Mentioned in")[1]


@pytest.fixture
def repo_store(doc_store):
    """A store with a project repo folder (spaces in its name, like a submodule) and its own doc-ownership map."""
    repo = doc_store.home / "jeby Real Estate"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "README.md").write_text("# Docs\n\n## Documentation tree\nx\n", encoding="utf-8")
    (repo / "docs" / "ARCH.md").write_text("# Arch\n\n## Layers\nx\n\n## Lot\nA lot is a parcel.\n", encoding="utf-8")
    (repo / "docs" / "doc-ownership.yaml").write_text(
        '- {glob: "backend/**/*.py", doc: "docs/ARCH.md#layers", primary: true}\n- {glob: "backend/api/{a,b}.py", doc: "docs/README.md", primary: true}\n', encoding="utf-8")
    prof = doc_store.projects_dir / "jre.md"
    prof.write_text("---\nkey: jre\n---\n# jRE\n\n| | |\n| --- | --- |\n| Repo | jeby Real Estate/ → github.com/x/y |\n", encoding="utf-8")
    return doc_store


def test_any_store_folder_and_edit_in_vscode(repo_store):
    t = ops.new(repo_store, "Spaced", "jre")
    t.set_section("Context", "Read [arch](<jeby Real Estate/docs/ARCH.md#layers>) and `jeby Real Estate/docs/ARCH.md`.")
    repo_store.save(t)
    c = _client(repo_store)
    page = c.get(f"/ticket/{t.id}").text
    assert 'href="/doc/jeby%20Real%20Estate/docs/ARCH.md#layers"' in page
    assert page.count("Edit in VS Code") >= 2 and 'href="vscode://file/' in page and "jeby%20Real%20Estate/docs/ARCH.md" in page
    r = c.get("/doc/jeby Real Estate/docs/ARCH.md")
    assert r.status_code == 200 and "A lot is a parcel." in r.text and "vscode://file/" in r.text
    assert c.get("/doc/jeby Real Estate/../secret.md").status_code == 404


def test_owning_doc_and_documentation_index(repo_store):
    t = ops.new(repo_store, "Owned", "jre")
    t.meta["docs_to_update"] = ["backend/app/x.py", "docs/ARCH.md#layers"]
    t.meta["source"] = "jeby Real Estate/docs/README.md#documentation-tree"
    t.set_section("Context", "Touches backend/api/a.py too.")
    repo_store.save(t)
    c = _client(repo_store)
    page = c.get(f"/ticket/{t.id}").text
    assert "Owning docs" in page and "jeby%20Real%20Estate/docs/ARCH.md#layers" in page and "jeby%20Real%20Estate/docs/README.md" in page
    assert 'class="docref" href="/doc/jeby%20Real%20Estate/docs/README.md#documentation-tree"' in page  # source linked
    prof = c.get("/doc/projects/jre.md").text
    assert "Documentation index" in prof and "jeby%20Real%20Estate/docs/README.md" in prof


def test_validate_warns_on_dead_doc_link(repo_store):
    from desk.rules import validate_store

    t = ops.new(repo_store, "Dead", "jre")
    t.set_section("Context", "See docs/EDITIONS.md, docs/GONE.md, and projects/<key>.md.")
    t.meta["source"] = "docs/ALSO_GONE.md#x"
    repo_store.save(t)
    _, warnings = validate_store(repo_store)
    joined = "\n".join(warnings)
    assert "docs/GONE.md" in joined and "docs/ALSO_GONE.md#x" in joined and "docs/EDITIONS.md" not in joined and "<key>" not in joined


def test_project_term_links_to_project_doc(repo_store):
    (repo_store.home / "glossary.md").write_text(
        "# Glossary\n\n## Ask\nWhat a ticket wants.\n\n## Lot\nSee [jRE architecture](jeby Real Estate/docs/ARCH.md#lot).\n", encoding="utf-8")
    t = ops.new(repo_store, "Lots", "jre")
    t.set_section("Context", "Each lot is checked. See [[Lot]].")
    repo_store.save(t)
    page = _client(repo_store).get(f"/ticket/{t.id}").text
    assert page.count("jeby%20Real%20Estate/docs/ARCH.md#lot") >= 2 and "/doc/glossary.md#lot" not in page


def test_resolver_prefers_root_then_repo(repo_store):
    # docs/EDITIONS.md exists at the store root; docs/ARCH.md only inside the project's repo folder
    assert docs.resolve_for(repo_store, "jre", "docs/EDITIONS.md") == ("docs/EDITIONS.md", ["docs/EDITIONS.md", "jeby Real Estate/docs/EDITIONS.md"])
    res, tried = docs.resolve_for(repo_store, "jre", "docs/ARCH.md#layers")
    assert res == "jeby Real Estate/docs/ARCH.md#layers" and tried[0] == "docs/ARCH.md"
    assert docs.resolve_for(repo_store, "algo", "docs/ARCH.md")[0] is None  # another project's repo does not apply


def test_repo_key_and_known_folder(repo_store):
    (repo_store.home / "Algo" / "docs").mkdir(parents=True)
    (repo_store.home / "Algo" / "docs" / "ROADMAP.md").write_text("# Roadmap\n", encoding="utf-8")
    assert docs.resolve_for(repo_store, "algo", "docs/ROADMAP.md")[0] == "Algo/docs/ROADMAP.md"  # known folder, no profile
    (repo_store.home / "other" / "docs").mkdir(parents=True)
    (repo_store.home / "other" / "docs" / "X.md").write_text("# X\n", encoding="utf-8")
    (repo_store.projects_dir / "jre.md").write_text("---\nkey: jre\nrepo: other\n---\n# jRE\n", encoding="utf-8")
    assert docs.resolve_for(repo_store, "jre", "docs/X.md")[0] == "other/docs/X.md"


def test_repo_relative_link_renders_and_multi_source(repo_store):
    from desk.rules import validate_store

    t = ops.new(repo_store, "Backfilled", "jre")
    t.meta["source"] = "docs/ARCH.md#lot; docs/README.md, docs/NOWHERE.md"
    t.set_section("Context", "See docs/ARCH.md#layers.")
    repo_store.save(t)
    page = _client(repo_store).get(f"/ticket/{t.id}").text
    assert 'href="/doc/jeby%20Real%20Estate/docs/ARCH.md#layers"' in page
    assert 'href="/doc/jeby%20Real%20Estate/docs/ARCH.md#lot"' in page and 'href="/doc/jeby%20Real%20Estate/docs/README.md"' in page
    _, warnings = validate_store(repo_store)
    mine = [w for w in warnings if w.startswith(t.id)]
    assert len(mine) == 1 and "docs/NOWHERE.md" in mine[0] and "tried: docs/NOWHERE.md, jeby Real Estate/docs/NOWHERE.md" in mine[0]
