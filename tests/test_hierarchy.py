"""DESK-0181 (refined 2026-11-13 by the owner's hierarchy decision, MGMT-0052) and DESK-0251 (levels as data).

Protects: the board and Today group by the company named by `company:` in each project profile (`area:` is the fallback), the company
names live in desk.toml [hierarchy] and nowhere in code, `drew migrate hierarchy` is a dry run unless told to write and `--undo` gives
back every file byte for byte, and a company rename leaves zero broken references."""

import hashlib
import re
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from desk import boardview as bv, companies, hierarchy, ops
from desk.cli import main
from desk.store import Store
from desk.ui.app import create_app

DOCS = Path(__file__).resolve().parents[1] / "docs" / "hierarchy"
TOML = """
[hierarchy]
levels = ["company", "project", "track", "ticket", "subticket"]

[hierarchy.companies]
jeby = "jeby"
ja-insights = "JA Insights"
jeby-real-estate = "jeby Real Estate"
jebylabs = "jebyLabs"
unassigned = "Unassigned"
"""
COMPANIES_YAML = """companies:
  jeby: jeby
  ja-insights: JA Insights
  jeby-real-estate: jeby Real Estate
  jebylabs: jebyLabs
  unassigned: Unassigned
default: unassigned
projects:
  jre: jeby-real-estate
  desk: jebylabs
  jeby-us: jeby
  consulting: ja-insights
"""
VOCAB_YAML = """tracks: [Feature, Bug fix, Debugging, Optimization, Security, Docs, Design, Research, Funding, Release, Decision, Operations]
map:
  Desk UI v1: Feature
  Backlog: from_type
  Decisions and asks: Decision
from_type:
  bug: Bug fix
  feature: Feature
  task: Operations
"""


def _profile(store, key, title, area=None, company=None, eol="\n"):
    lines = ["---", f"key: {key}", f"name: {title}"] + ([f"area: {area}"] if area else []) + ([f"company: {company}"] if company else []) + ["---", f"# {title}", ""]
    (store.projects_dir / f"{key}.md").write_bytes(eol.join(lines).encode("utf-8"))


def _mk(store, title, project, **meta):
    t = ops.new(store, title, project)
    t.meta.update(meta)
    t.write()
    return t


def _hierarchy(store):
    p = store.home / "desk.toml"
    p.write_text(p.read_text(encoding="utf-8") + TOML, encoding="utf-8")
    return Store(store.home)


def _company_rows(page):
    return re.findall(r'<a class="bcomp"[^>]*>.*?<b[^>]*>([^<]*)</b>', page, flags=re.S)


# -- display: company first, area second -----------------------------------------------------------------------------------------

def test_company_line_wins_over_area_and_names_come_from_desk_toml(store):
    _profile(store, "jre", "jRE", area="Client work (JA Insights)", company="jeby-real-estate")
    _profile(store, "desk", "Desk", area="jebyLabs (free and open tools)", company="jebylabs")
    _profile(store, "jeby-us", "jeby.us", area="Personal")  # no company: line yet, so its area is the company (the fallback)
    s = _hierarchy(store)
    assert companies.of(s, "jre") == "jeby Real Estate" and companies.of(s, "desk") == "jebyLabs"
    assert companies.of(s, "jeby-us") == "Personal"
    assert hierarchy.levels(s) == ["company", "project", "track", "ticket", "subticket"]


def test_levels_and_names_default_when_desk_toml_has_no_hierarchy(store):
    _profile(store, "jre", "jRE", company="jeby-real-estate")
    assert hierarchy.levels(store) == list(hierarchy.LEVELS)
    assert companies.of(store, "jre") == "jeby-real-estate"  # an undeclared key shows as written, and validate says so


def test_validate_errors_on_a_company_key_desk_toml_does_not_declare(store, capsys):
    _profile(store, "jre", "jRE", company="jeby-realestate")
    _hierarchy(store)
    with pytest.raises(SystemExit) as ex:
        main(["--home", str(store.home), "validate"])
    out = capsys.readouterr().out
    assert ex.value.code == 1 and "projects/jre.md: company `jeby-realestate` is not declared in desk.toml [hierarchy.companies]" in out


def test_board_opens_at_the_companies_from_the_company_line(store):
    _profile(store, "jre", "jRE", area="Client work (JA Insights)", company="jeby-real-estate")
    _profile(store, "desk", "Desk", area="jebyLabs (free and open tools)", company="jebylabs")
    _profile(store, "jeby-us", "jeby.us", area="Personal", company="jeby")
    s = _hierarchy(store)
    _mk(s, "Value a house", "jre", status="ready", priority="p1")
    _mk(s, "Board rows", "desk", status="ready")
    _mk(s, "Site copy", "jeby-us", status="ready")
    cl = TestClient(create_app(s.home))
    page = cl.get("/board").text
    assert sorted(_company_rows(page)) == ["jeby", "jeby Real Estate", "jebyLabs"]
    assert "Client work" not in page and "Personal" not in page  # area: stays in the file but no longer heads the board
    inside = cl.get("/board?company=jeby-real-estate").text
    assert re.findall(r'<details class="bproj"[^>]*data-val="([^"]+)"', inside) == ["jre"] and ">jeby Real Estate</a>" in inside
    today = cl.get("/").text
    assert ">jeby Real Estate (1)</option>" in today


def test_ticket_page_list_and_show_name_the_company(store, capsys):
    _profile(store, "jre", "jRE", area="Client work (JA Insights)", company="jeby-real-estate")
    s = _hierarchy(store)
    t = _mk(s, "Value a house", "jre", track="Feature", legacy_track="Backlog")
    page = TestClient(create_app(s.home)).get(f"/ticket/{t.id}").text
    assert re.search(r'<span class="label">Company</span><div><a href="/board\?company=jeby-real-estate">jeby Real Estate</a>', page)
    assert re.search(r'<span class="label">Track</span><div>Feature <span class="muted">\(was Backlog\)</span>', page)
    main(["--home", str(s.home), "list", "--project", "jre"])
    out = capsys.readouterr().out
    assert "company" in out.splitlines()[0] and re.search(rf"{t.id}.*jeby Real Estate", out)
    main(["--home", str(s.home), "show", t.id])
    out = capsys.readouterr().out
    assert out.startswith(f"# {t.id} · company jeby Real Estate · project jre · jRE\n") and "project: jre" in out


# -- the migration: dry run, apply, undo byte for byte ---------------------------------------------------------------------------

def _tree(home: Path) -> dict[str, str]:
    return {str(p.relative_to(home)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(home.rglob("*")) if p.is_file() and ".desk" not in p.parts}


def _migration_store(store):
    _profile(store, "jre", "jRE", area="Client work (JA Insights)")
    _profile(store, "desk", "Desk", area="jebyLabs (free and open tools)", eol="\r\n")  # a CRLF profile keeps its line endings
    (store.projects_dir / "jeby-us.md").write_text("# jeby.us\n", encoding="utf-8")  # a profile with no frontmatter at all
    _profile(store, "loose", "Loose", area="Personal")  # no row in the map: the default company
    a = _mk(store, "Board rows", "desk", track="Desk UI v1", type="feature")
    b = _mk(store, "Fix a crash", "jre", track="Backlog", type="bug")
    c = _mk(store, "Pick one", "jre", track="Decisions and asks", type="decision")
    d = _mk(store, "Odd one", "desk", track="Something new", type="task")  # not in the map: left alone, listed
    e = _mk(store, "Already new", "desk", track="Feature", type="feature")  # already in the vocabulary: left alone
    f = _mk(store, "No track", "jre", type="task")
    raw = c.path.read_bytes().replace(b"\n", b"\r\n")  # a CRLF ticket
    c.path.write_bytes(raw)
    q = _mk(store, "Quoted", "jre", track="Backlog", type="task")
    q.path.write_bytes(q.path.read_bytes().replace(b"\ntrack: Backlog\n", b"\ntrack: 'Backlog'\n"))
    assert b"track: 'Backlog'" in q.path.read_bytes()
    (store.home / "tracks").mkdir(exist_ok=True)
    (store.home / "tracks" / "companies.yaml").write_text(COMPANIES_YAML, encoding="utf-8")
    (store.home / "tracks" / "vocabulary.yaml").write_text(VOCAB_YAML, encoding="utf-8")
    return {"a": a.id, "b": b.id, "c": c.id, "d": d.id, "e": e.id, "f": f.id, "q": q.id}


def test_dry_run_writes_nothing_and_lists_the_plan(store, capsys):
    ids = _migration_store(store)
    before = _tree(store.home)
    main(["--home", str(store.home), "migrate", "hierarchy", "--dry-run"])
    out = capsys.readouterr().out
    assert _tree(store.home) == before
    assert re.search(r"jre\s+jeby-real-estate\s+jeby Real Estate", out) and re.search(r"loose\s+unassigned\s+Unassigned", out)
    assert re.search(rf"{ids['a']}\s+Desk UI v1\s+Feature", out) and re.search(rf"{ids['b']}\s+Backlog\s+Bug fix", out)
    assert "Something new" in out and "not in the vocabulary map" in out
    assert "Dry run: nothing written." in out


def test_apply_then_undo_restores_every_file_byte_for_byte(store, capsys):
    ids = _migration_store(store)
    before = _tree(store.home)
    toml_before = (store.home / "desk.toml").read_bytes()
    main(["--home", str(store.home), "migrate", "hierarchy", "--apply"])
    capsys.readouterr()
    s = Store(store.home)
    assert companies.of(s, "jre") == "jeby Real Estate" and companies.of(s, "desk") == "jebyLabs" and companies.of(s, "loose") == "Unassigned"
    assert companies.of(s, "jeby-us") == "jeby"
    desk_profile = (s.projects_dir / "desk.md").read_bytes()
    assert b"area: jebyLabs (free and open tools)\r\ncompany: jebylabs\r\n" in desk_profile  # area kept, company beside it, CRLF kept
    by = {k: s.get(v) for k, v in ids.items()}
    assert by["a"].meta["track"] == "Feature" and by["a"].meta["legacy_track"] == "Desk UI v1"
    assert by["b"].meta["track"] == "Bug fix" and by["b"].meta["legacy_track"] == "Backlog"
    assert by["c"].meta["track"] == "Decision" and b"\r\nlegacy_track: Decisions and asks\r\n" in by["c"].path.read_bytes()
    assert by["q"].meta["track"] == "Operations" and b"\nlegacy_track: 'Backlog'\n" in by["q"].path.read_bytes()  # the old text verbatim
    assert by["d"].meta["track"] == "Something new" and "legacy_track" not in by["d"].meta
    assert "legacy_track" not in by["e"].meta and not by["f"].meta.get("track") and "legacy_track" not in by["f"].meta
    assert hierarchy.levels(s) == ["company", "project", "track", "ticket", "subticket"]
    after = _tree(store.home)
    main(["--home", str(store.home), "migrate", "hierarchy", "--apply"])  # twice writes nothing the second time
    assert "0 profile(s) and 0 ticket(s) written" in capsys.readouterr().out and _tree(store.home) == after
    main(["--home", str(store.home), "migrate", "hierarchy", "--undo", "--dry-run"])
    assert _tree(store.home) == after
    main(["--home", str(store.home), "migrate", "hierarchy", "--undo"])
    out = capsys.readouterr().out
    assert "restored" in out
    assert (store.home / "desk.toml").read_bytes() == toml_before
    assert _tree(store.home) == before


def test_undo_restores_a_store_whose_desk_toml_had_no_trailing_newline(store, capsys):
    _migration_store(store)
    p = store.home / "desk.toml"
    p.write_bytes(p.read_bytes().rstrip(b"\r\n"))
    before = _tree(store.home)
    main(["--home", str(store.home), "migrate", "hierarchy", "--apply"])
    main(["--home", str(store.home), "migrate", "hierarchy", "--undo"])
    assert _tree(store.home) == before


# -- renames: one line, zero broken references -----------------------------------------------------------------------------------

def test_a_company_rename_leaves_zero_broken_references(store, tmp_path, capsys):
    _migration_store(store)
    main(["--home", str(store.home), "migrate", "hierarchy", "--apply"])
    copy = tmp_path / "copy"
    shutil.copytree(store.home, copy)
    # 1. the shown name: one line in desk.toml
    toml = copy / "desk.toml"
    text = toml.read_text(encoding="utf-8")
    assert text.count('jebylabs = "jebyLabs"') == 1
    toml.write_text(text.replace('jebylabs = "jebyLabs"', 'jebylabs = "jeby Labs"'), encoding="utf-8")
    s = Store(copy)
    assert companies.of(s, "desk") == "jeby Labs"
    # 2. the key itself: desk.toml and the profiles that name it; no ticket names a company, so no ticket changes
    tickets_before = _tree(copy / "tickets")
    toml.write_text(toml.read_text(encoding="utf-8").replace("jebylabs = ", "labs = "), encoding="utf-8")
    for p in (copy / "projects").glob("*.md"):
        p.write_bytes(p.read_bytes().replace(b"company: jebylabs", b"company: labs"))
    s = Store(copy)
    assert companies.of(s, "desk") == "jeby Labs" and _tree(copy / "tickets") == tickets_before
    capsys.readouterr()
    with pytest.raises(SystemExit) as ex:
        main(["--home", str(copy), "validate"])
    out = capsys.readouterr().out
    assert ex.value.code == 0 and "error:" not in out, out
    rows = bv.build(s, s.tickets())
    assert sum(c.total for c in bv.companies(s, rows)) == len(s.tickets())  # every ticket still lands under exactly one company
    assert all(hierarchy.company_key(s, k) in hierarchy.company_names(s) for k in s.projects())


# -- the owner-editable files in docs/hierarchy ----------------------------------------------------------------------------------

def test_the_shipped_map_and_vocabulary_are_consistent():
    cmap = hierarchy.load_companies(DOCS / "companies.yaml")
    vocab = hierarchy.load_vocabulary(DOCS / "vocabulary.yaml")
    assert set(cmap.names) == {"jeby", "ja-insights", "jeby-real-estate", "jebylabs", "unassigned"}
    assert [cmap.names[k] for k in ("jeby", "ja-insights", "jeby-real-estate", "jebylabs")] == ["jeby", "JA Insights", "jeby Real Estate", "jebyLabs"]
    assert len(cmap.projects) == 36 and set(cmap.projects.values()) <= set(cmap.names) and cmap.default == "unassigned"
    assert len(vocab.tracks) == 12 and len(vocab.map) == 40
    assert all(v == hierarchy.FROM_TYPE or v in vocab.tracks for v in vocab.map.values())
    assert set(vocab.from_type.values()) <= set(vocab.tracks)
