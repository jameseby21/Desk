"""DESK-0151: validate warns on ui work with no Chaz design ticket linked. Protects: user-facing work filed without routing the
design to Chaz (the lesson of 2026-11-08) is named by the tool, not left to memory."""

from desk import ops
from desk.rules import validate_store


def _mk(store, title, project="jre", **meta):
    t = ops.new(store, title, project)
    t.meta.update(meta)
    t.write()
    return store.get(t.id)


def _warns(store):
    return [w.split(":")[0] for w in validate_store(store)[1] if "no Chaz design ticket" in w]


def test_ui_work_with_no_chaz_ticket_warns_with_the_rule_and_the_fix(store):
    t = _mk(store, "Build the page", status="ready", dod=["core", "ui"])
    (w,) = [w for w in validate_store(store)[1] if "no Chaz design ticket" in w]
    assert w.startswith(t.id) and "designed by Chaz first" in w and "depends_on or related" in w and "design: none - <reason>" in w


def test_a_linked_chaz_ticket_in_depends_on_or_related_clears_it(store):
    design = _mk(store, "Design the page", status="done", assignee="chaz")
    a = _mk(store, "Needs it", status="ready", dod=["core", "ui"], depends_on=[design.id])
    b = _mk(store, "Related to it", status="in_progress", dod=["core", "ui"], related=[design.id])
    c = _mk(store, "No link", status="claimed", dod=["core", "ui"])
    assert _warns(store) == [c.id]
    c.meta["related"] = [design.id]
    c.write()
    assert _warns(store) == []
    assert a and b


def test_only_a_chaz_ticket_counts(store):
    other = _mk(store, "Em's research", status="done", assignee="em")
    t = _mk(store, "Needs design", status="ready", dod=["core", "ui"], depends_on=[other.id])
    assert _warns(store) == [t.id]


def test_exemptions_bug_type_chaz_own_ticket_design_none_and_other_statuses(store):
    _mk(store, "A bug", status="ready", type="bug", dod=["core", "ui"])
    _mk(store, "Chaz builds it", status="ready", assignee="chaz", dod=["core", "ui"])
    t = _mk(store, "Plain text change", status="ready", dod=["core", "ui"])
    t.set_section("Context", "Copy fix only.\n\ndesign: none - wording of one error sentence")
    t.write()
    _mk(store, "Not started", status="inbox", dod=["core", "ui"])
    _mk(store, "In review already", status="review", dod=["core", "ui"])
    _mk(store, "Not a ui ticket", status="ready", dod=["core", "code"])
    assert _warns(store) == []


def test_design_none_needs_a_reason(store):
    t = _mk(store, "Empty excuse", status="ready", dod=["core", "ui"])
    t.set_section("Context", "design: none -")
    t.write()
    assert _warns(store) == [t.id]
