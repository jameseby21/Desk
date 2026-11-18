"""DESK-0263: `drew merge` folds tickets into a survivor without losing a byte, across projects.

Protects: James's rule that a merge never loses data (2026-11-13), and that a ticket may belong to more than one project. Every test
builds its own store in a temporary folder; nothing reads the real one."""

import difflib
import hashlib
import re

import pytest
from fastapi.testclient import TestClient

from desk import boardview as bv, boardfilter as bf, merge, ops
from desk.cli import main
from desk.rules import validate_store
from desk.ticket import Ticket
from desk.ui.app import create_app

FENCED = "Run this:\n\n````\n```\n## Not a heading, inside a fence\n```\n````\n\nThen stop.\n"


def _mk(store, title, project, status="ready", accept=(), prompt="", **meta):
    t = ops.new(store, title, project)
    t.meta.update({"status": status, **meta})
    t.sections["Context"] = f"Why {title} exists.\n"
    t.sections["Acceptance"] = "".join(f"- [ ] {a}\n" for a in accept)
    t.sections["Prompt"] = prompt
    t.write()
    return store.get(t.id)


def _fixture(store):
    surv = _mk(store, "Survivor in desk", "desk", accept=["Survivor line"])
    src = _mk(store, "Source in jre", "jre", accept=["Open one", "Open two"], prompt=FENCED, tags=["x"])
    return surv, src


def _hashes(store):
    """A hash of every file in the store (tickets, activity log, write log), so "nothing was written" means nothing anywhere."""
    return {str(p.relative_to(store.home)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(store.home.rglob("*")) if p.is_file()}


def _go(store, surv, *srcs, kind="linked", **kw):
    return merge.merge(store, surv, list(srcs), kind=kind, reason=kw.pop("reason", "same work, two views"), signed=kw.pop("signed", "decision 113"),
                       actor=kw.pop("actor", "patch"), **kw)


def test_every_line_of_each_source_survives_across_projects(store):
    """Lossless: each line of the source file is in the source after the merge or in the survivor, and the embedded copy restores exactly."""
    surv, src = _fixture(store)
    before_src = src.path.read_text(encoding="utf-8")
    _go(store, surv.id, src.id)
    after_src = src.path.read_text(encoding="utf-8")
    after_surv = surv.path.read_text(encoding="utf-8")
    keep = set(after_src.splitlines()) | {ln.strip() for ln in after_surv.splitlines()}
    lost = [ln for ln in before_src.splitlines() if ln.strip() and ln not in keep and ln.strip() not in keep]
    assert lost == []
    assert merge.embedded(after_surv)[src.id] == before_src  # the copy in the survivor is byte for byte the file at merge time
    assert Ticket.load(src.path).meta["project"] == "jre" and Ticket.load(surv.path).meta["also_projects"] == ["jre"]


def test_survivor_gets_section_before_report_and_header_line(store):
    surv, src = _fixture(store)
    _go(store, surv.id, src.id, kind="family")
    text = surv.path.read_text(encoding="utf-8")
    assert text.index("## Merged tickets") < text.index("## Report")
    head = [ln for ln in text.splitlines() if ln.startswith("**" + src.id)][0]
    assert "Source in jre" in head and "jre" in head and "ready" in head and "family" in head


def test_survivor_parses_back_to_its_own_sections(store):
    """The embedded copy holds `## ` lines; they must never be read as the survivor's sections (the installed parser splits on them)."""
    surv, src = _fixture(store)
    _go(store, surv.id, src.id)
    again = Ticket.load(surv.path)
    assert again.section("Context") == "Why Survivor in desk exists."
    assert again.section("Prompt") == ""  # the source's Prompt did not leak into the survivor's own
    assert "Not a heading" in again.section("Merged tickets")
    assert list(again.sections).count("Merged tickets") == 1


def test_open_acceptance_lines_are_carried_with_their_origin(store):
    surv, src = _fixture(store)
    t = Ticket.load(src.path)
    t.sections["Acceptance"] = "- [x] Done one\n- [ ] Open two\n"  # one line of the source is already done
    t.write()
    _go(store, surv.id, src.id)
    acc = Ticket.load(surv.path).section("Acceptance")
    assert f"- [ ] (from {src.id}) Open two" in acc
    assert "Done one" not in acc  # only open lines are carried; the done one stays in the copy of the source
    assert "- [ ] Survivor line" in acc


def test_survivor_only_gains_lines(store):
    """Nothing of the survivor is rewritten: apart from `updated`, every old line is still there, in order."""
    surv, src = _fixture(store)
    old = surv.path.read_text(encoding="utf-8").splitlines()
    _go(store, surv.id, src.id)
    new = surv.path.read_text(encoding="utf-8").splitlines()
    removed = [d for d in difflib.ndiff(old, new) if d.startswith("- ")]
    assert [r for r in removed if not r.startswith("- updated:")] == []


def test_source_is_rejected_with_a_decision_line_and_kept(store):
    surv, src = _fixture(store)
    _go(store, surv.id, src.id, kind="linked", reason="same release, two angles", signed="https://example.test/comment/7")
    t = Ticket.load(src.path)
    assert t.status == "rejected" and t.meta["merged_into"] == surv.id and "duplicate_of" not in t.meta
    assert t.meta["updated"] == merge.today()
    line = [ln for ln in t.section("Decision").splitlines() if "rejected" in ln][0]
    for word in ("patch", "linked", surv.id, "same release, two angles", "https://example.test/comment/7"):
        assert word in line
    assert src.path.exists()


def test_duplicate_kind_uses_duplicate_of(store):
    surv, src = _fixture(store)
    _go(store, surv.id, src.id, kind="duplicate")
    t = Ticket.load(src.path)
    assert t.meta["duplicate_of"] == surv.id and "merged_into" not in t.meta


def test_survivor_is_on_both_projects_boards(store):
    surv, src = _fixture(store)
    _go(store, surv.id, src.id)
    tickets = store.tickets()
    rows = {r.key: r for r in bv.build(store, tickets, expand_all=True)}
    ids = lambda key: {t.id for g in rows[key].groups for t in g.tickets}  # noqa: E731
    assert surv.id in ids("desk") and surv.id in ids("jre")
    kept = bf.apply(tickets, {"project": ["jre"], "assignee": [], "track": [], "sprint": [], "tag": [], "type": []})
    assert surv.id in {t.id for t in kept}


def test_merged_page_has_banner_and_board_page_lists_survivor(store):
    surv, src = _fixture(store)
    _go(store, surv.id, src.id)
    cl = TestClient(create_app(store.home))
    page = cl.get(f"/ticket/{src.id}").text
    assert "Merged into" in page and f'href="/ticket/{surv.id}"' in page
    assert "Merged into" not in cl.get(f"/ticket/{surv.id}").text
    assert "Survivor in desk" in cl.get("/board?project=jre&company=unsorted").text


def test_depends_on_and_related_follow_the_survivor(store, capsys):
    surv, src = _fixture(store)
    dep = _mk(store, "Waits on source", "jre", depends_on=[src.id, "JRE-9999"][:1], related=[src.id, surv.id])
    twice = _mk(store, "Names both", "desk", depends_on=[src.id, surv.id])
    plan = _go(store, surv.id, src.id)
    assert Ticket.load(dep.path).meta["depends_on"] == [surv.id]
    assert Ticket.load(dep.path).meta["related"] == [surv.id]  # no duplicate when the survivor was already listed
    assert Ticket.load(twice.path).meta["depends_on"] == [surv.id]
    assert any(dep.id in r and src.id in r and surv.id in r for r in plan.repoints)


def test_survivor_never_depends_on_itself(store):
    surv, src = _fixture(store)
    t = Ticket.load(surv.path)
    t.meta["depends_on"] = [src.id]
    t.write()
    _go(store, surv.id, src.id)
    assert Ticket.load(surv.path).meta["depends_on"] == []


@pytest.mark.parametrize("surv_status", ["done", "rejected"])
def test_refuses_a_closed_survivor(store, surv_status):
    surv, src = _fixture(store)
    t = Ticket.load(surv.path)
    t.meta["status"] = surv_status
    t.sections["Decision"] = "- 2026-11-01 · james · approved · \"ok\"\n- 2026-11-01 · james · rejected · \"no\"\n"
    t.write()
    h = _hashes(store)
    with pytest.raises(merge.MergeError, match="done or let go|closed|is " + surv_status):
        _go(store, surv.id, src.id)
    assert _hashes(store) == h


@pytest.mark.parametrize("status", ["in_progress", "claimed"])
def test_refuses_a_source_someone_has(store, status):
    surv, src = _fixture(store)
    t = Ticket.load(src.path)
    t.meta["status"] = status
    t.write()
    h = _hashes(store)
    with pytest.raises(merge.MergeError, match="in progress|claimed"):
        _go(store, surv.id, src.id)
    assert _hashes(store) == h


def test_refuses_self_unknown_done_and_already_merged(store):
    surv, src = _fixture(store)
    h = _hashes(store)
    with pytest.raises(merge.MergeError, match="itself"):
        _go(store, surv.id, surv.id)
    with pytest.raises(merge.MergeError, match="No ticket"):
        _go(store, surv.id, "JRE-9999")
    with pytest.raises(merge.MergeError, match="No ticket"):
        _go(store, "DESK-9999", src.id)
    assert _hashes(store) == h
    _go(store, surv.id, src.id)
    other = _mk(store, "Other survivor", "desk")
    with pytest.raises(merge.MergeError, match="already"):
        _go(store, other.id, src.id)


def test_dry_run_writes_nothing_and_says_what_it_would_do(store, capsys):
    surv, src = _fixture(store)
    dep = _mk(store, "Waits on source", "jre", depends_on=[src.id])
    h = _hashes(store)
    main(["--home", str(store.home), "merge", surv.id, src.id, "--kind", "linked", "--reason", "test", "--signed", "test", "--dry-run"])
    out = capsys.readouterr().out
    assert _hashes(store) == h
    assert "dry run" in out.lower() and src.id in out and "also_projects" in out and dep.id in out


def test_cli_merge_then_validate_is_clean(store, capsys):
    surv, src = _fixture(store)
    main(["--home", str(store.home), "merge", surv.id, src.id, "--kind", "duplicate", "--reason", "same ask", "--signed", "decision 114"])
    out = capsys.readouterr().out
    assert f"merged {src.id} into {surv.id}" in out
    errors, _ = validate_store(store)
    assert errors == []


def test_second_merge_appends_to_the_same_section(store):
    surv, src = _fixture(store)
    third = _mk(store, "Third in jeby-us", "jeby-us", accept=["Third line"])
    _go(store, surv.id, src.id)
    _go(store, surv.id, third.id)
    text = surv.path.read_text(encoding="utf-8")
    assert text.count("## Merged tickets") == 1
    assert set(merge.embedded(text)) == {src.id, third.id}
    assert Ticket.load(surv.path).meta["also_projects"] == ["jre", "jeby-us"]


def test_same_project_source_adds_no_also_projects(store):
    surv = _mk(store, "Survivor", "desk")
    src = _mk(store, "Same project source", "desk")
    _go(store, surv.id, src.id)
    assert "also_projects" not in Ticket.load(surv.path).meta


def test_the_merge_is_recorded_in_the_activity_log(store):
    from desk import activity

    surv, src = _fixture(store)
    _go(store, surv.id, src.id)
    assert (store.home / "events" / f"{src.id}.jsonl").exists()
    assert re.search(r"merged_into|status", (store.home / "events" / f"{src.id}.jsonl").read_text(encoding="utf-8"))
    assert activity is not None


def test_an_interrupted_merge_can_be_run_again_without_copying_twice(store):
    """The survivor is written first; if the run stopped before the source was, running it again copies the source once."""
    surv, src = _fixture(store)
    original = src.path.read_bytes()
    _go(store, surv.id, src.id)
    src.path.write_bytes(original)  # as if the process died after the survivor was written
    _go(store, surv.id, src.id)
    text = surv.path.read_text(encoding="utf-8")
    assert text.count(f"**{src.id}**") == 1 and text.count(f"(from {src.id}) Open one") == 1
    assert Ticket.load(src.path).status == "rejected"


# -- review findings on PR 16 (2026-11-13): links a merge must not leave behind ------------------------------------------------------------

def test_open_subtickets_refuse_the_merge_unless_asked_and_then_follow_the_survivor(store, capsys):
    """Protects: a source's open subtickets never stay under a rejected parent and drop off every board (review finding, high)."""
    surv, src = _fixture(store)
    kid1 = _mk(store, "Kid one", "jre", parent=src.id)
    kid2 = _mk(store, "Kid two", "desk", parent=src.id, status="review")
    h = _hashes(store)
    with pytest.raises(merge.MergeError, match=r"subtickets.*--with-subtickets") as e:
        _go(store, surv.id, src.id)
    assert kid1.id in str(e.value) and kid2.id in str(e.value)
    assert _hashes(store) == h  # refused: nothing written
    main(["--home", str(store.home), "merge", surv.id, src.id, "--kind", "linked", "--reason", "one feature", "--signed", "decision 113",
          "--with-subtickets"])
    out = capsys.readouterr().out
    for kid in (kid1, kid2):
        assert Ticket.load(kid.path).meta["parent"] == surv.id
        assert f"repoint {kid.id} parent: {src.id} -> {surv.id}" in out
    from desk import subtickets
    assert {t.id for t in subtickets.open_subs(store.tickets(), Ticket.load(surv.path))} == {kid1.id, kid2.id}


def test_done_subtickets_follow_without_the_flag(store):
    surv, src = _fixture(store)
    kid = _mk(store, "Finished kid", "jre", parent=src.id, status="done")
    _go(store, surv.id, src.id)
    assert Ticket.load(kid.path).meta["parent"] == surv.id


def test_blocks_follow_the_survivor(store):
    surv, src = _fixture(store)
    first = _mk(store, "Comes first", "jre", blocks=[src.id])
    plan = _go(store, surv.id, src.id)
    assert Ticket.load(first.path).meta["blocks"] == [surv.id]
    assert any(first.id in r and "blocks" in r for r in plan.repoints)


def test_source_depends_on_is_carried_so_the_survivor_waits(store):
    """Protects: the survivor cannot be closed before the work a source waited on (review finding, medium)."""
    from desk.rules import RuleError, check_transition

    surv, src = _fixture(store)
    pre = _mk(store, "Prerequisite", "jre")
    t = Ticket.load(src.path)
    t.meta["depends_on"] = [pre.id, surv.id]
    t.write()
    s = Ticket.load(surv.path)
    s.meta["depends_on"] = [pre.id]  # already listed: carried once
    s.write()
    other = _mk(store, "Other prerequisite", "jre")
    third = _mk(store, "Third source", "jre", depends_on=[other.id, src.id])
    plan = _go(store, surv.id, src.id, third.id)
    assert Ticket.load(surv.path).meta["depends_on"] == [pre.id, other.id]
    assert any(other.id in n and "depends_on" in n for n in plan.notes)
    s = Ticket.load(surv.path)
    s.meta["status"] = "approved"  # the last step before done; only the inherited dependency stands in the way
    with pytest.raises(RuleError, match=pre.id):
        check_transition(s, "done", store, actor="james")


def test_survivor_in_review_gets_a_comment_and_the_higher_priority(store):
    """Protects: the ticket's 2026-11-13 refinement: a reviewer is told the carried lines are unchecked, and priority is carried up."""
    surv, src = _fixture(store)
    s = Ticket.load(surv.path)
    s.meta.update({"status": "review", "priority": "p3"})
    s.write()
    t = Ticket.load(src.path)
    t.meta["priority"] = "p1"
    t.write()
    plan = _go(store, surv.id, src.id)
    after = Ticket.load(surv.path)
    assert after.meta["priority"] == "p1"
    note = [ln for ln in after.section("Comments").splitlines() if src.id in ln]
    assert note and "review" in note[0] and "unchecked" in note[0]
    assert any("priority" in n and "p1" in n for n in plan.notes)


def test_lower_source_priority_and_open_survivor_change_nothing(store):
    surv, src = _fixture(store)
    s = Ticket.load(surv.path)
    s.meta["priority"] = "p1"
    s.write()
    t = Ticket.load(src.path)
    t.meta["priority"] = "p3"
    t.write()
    _go(store, surv.id, src.id)
    after = Ticket.load(surv.path)
    assert after.meta["priority"] == "p1" and "unchecked" not in after.section("Comments")
