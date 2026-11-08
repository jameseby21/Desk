"""Lifecycle rules: each test names the rule it protects."""

import datetime as dt

import pytest

from desk import ops
from desk.rules import RuleError, validate_store
from desk.store import Store
from desk.ticket import Ticket


def make(store, title="A slice", project="jre", type_="slice", **kw):
    t = ops.new(store, title, project, type_, **kw)
    t.set_section("Acceptance", "- [ ] it works")
    store.save(t)
    return t


def to_ready(store, t):
    return ops.move(store, t, "ready", actor="main")


def to_review(store, t, owner="jre"):
    ops.claim(store, t, owner)
    ops.start(store, t)
    return ops.report(store, t, "Ran `uv run pytest`: 12 passed.")


def test_ids_number_per_project(store):
    """Ids are <KEY>-NNNN and count separately for each project."""
    a = make(store, "one", "jre")
    b = make(store, "two", "jre")
    c = make(store, "three", "desk")
    d = make(store, "four", "jeby-us")
    assert (a.id, b.id, c.id, d.id) == ("JRE-0001", "JRE-0002", "DESK-0001", "JEBY-US-0001")
    assert a.path.name == "JRE-0001-one.md"


def test_only_main_or_owner_throws_into_ready(store):
    """Only `main` or `james` moves a ticket into ready."""
    t = make(store, owner="jre")
    with pytest.raises(RuleError):
        ops.move(store, t, "ready", actor="jre")
    ops.move(store, t, "ready", actor="main")
    assert store.get(t.id).status == "ready"


def test_claim_is_for_project_sessions(store):
    """main and the owner do not claim; a project session does, and gets a branch."""
    t = to_ready(store, make(store))
    with pytest.raises(RuleError):
        ops.claim(store, t, "main")
    ops.claim(store, store.get(t.id), "jre")
    t = store.get(t.id)
    assert t.status == "claimed" and t.meta["owner"] == "jre" and t.meta["branch"].startswith("jre/JRE-0001")


def test_review_requires_report(store):
    """`review` needs a non-empty Report."""
    t = to_ready(store, make(store))
    ops.claim(store, t, "jre")
    ops.start(store, t)
    with pytest.raises(RuleError):
        ops.move(store, t, "review")
    with pytest.raises(RuleError):
        ops.report(store, t, "   ")
    ops.report(store, t, "Ran tests.")
    assert store.get(t.id).status == "review"


def test_decide_requires_words(store):
    """Approve and send back both need a reason of at least four characters."""
    t = to_review(store, to_ready(store, make(store)))
    with pytest.raises(RuleError):
        ops.decide(store, t, True, "ok", actor="james")
    with pytest.raises(RuleError):
        ops.decide(store, t, False, "", actor="james")


def test_approval_is_the_owner_or_delegated(store):
    """Only the configured owner approves, unless type and size are delegated (chore <= s)."""
    t = to_review(store, to_ready(store, make(store)))
    with pytest.raises(RuleError):
        ops.decide(store, t, True, "Looks right to me", actor="main")
    ops.decide(store, store.get(t.id), True, "Approve: ship it after the DNS lands.", actor="james")
    t = store.get(t.id)
    assert t.status == "approved"
    assert "· james · approved ·" in t.section("Decision")

    chore = to_review(store, to_ready(store, make(store, "tiny chore", type_="chore", size="xs")))
    ops.decide(store, chore, True, "Delegated chore, fine.", actor="main")
    assert store.get(chore.id).status == "approved"

    big_chore = to_review(store, to_ready(store, make(store, "big chore", type_="chore", size="m")))
    with pytest.raises(RuleError):
        ops.decide(store, big_chore, True, "Too big for main.", actor="main")


def test_send_back_goes_to_changes_requested(store):
    """Send back moves to changes_requested and records the words; then back to in_progress."""
    t = to_review(store, to_ready(store, make(store)))
    ops.decide(store, t, False, "Send back: the QR step was never run.", actor="james")
    t = store.get(t.id)
    assert t.status == "changes_requested"
    assert "changes_requested" in t.section("Decision")
    ops.move(store, t, "in_progress")
    assert store.get(t.id).status == "in_progress"


def test_done_blocked_by_dependencies(store):
    """A ticket cannot be done while any depends_on ticket is not done."""
    dep = make(store, "first")
    t = make(store, "second")
    t.meta["depends_on"] = [dep.id]
    store.save(t)
    t = to_review(store, to_ready(store, store.get(t.id)))
    ops.decide(store, t, True, "Approved in words.", actor="james")
    with pytest.raises(RuleError):
        ops.done(store, store.get(t.id))
    dep = to_review(store, to_ready(store, store.get(dep.id)))
    ops.decide(store, dep, True, "Approved too.", actor="james")
    ops.done(store, store.get(dep.id), commit="abc123")
    ops.done(store, store.get(t.id))
    assert store.get(t.id).status == "done"


def test_blocked_requires_blocked_on_and_unblock_restores(store):
    """`blocked` needs blocked_on; unblock returns to the status before the block."""
    t = to_ready(store, make(store))
    with pytest.raises(RuleError):
        ops.block(store, t, "", "no name given")
    ops.block(store, t, "LILY-0004", "needs the Squarespace login")
    t = store.get(t.id)
    assert t.status == "blocked" and t.meta["blocked_on"] == "LILY-0004" and t.meta["blocked_from"] == "ready"
    with pytest.raises(RuleError):
        ops.move(store, t, "claimed")
    ops.unblock(store, t)
    assert store.get(t.id).status == "ready"


def test_reject_needs_reason(store):
    """Let go always says why."""
    t = make(store)
    with pytest.raises(RuleError):
        ops.reject(store, t, "no")
    ops.reject(store, t, "Superseded by JRE-0002.")
    assert store.get(t.id).status == "rejected"


def test_validate_catches_dangling_depends_on_and_bad_enums(store):
    """validate reports dangling depends_on, unknown projects and bad enum values."""
    t = make(store)
    text = t.path.read_text(encoding="utf-8").replace("depends_on: []", "depends_on: [JRE-9999]").replace("priority: p2", "priority: urgent")
    t.path.write_text(text, encoding="utf-8")
    errors, _ = validate_store(store)
    assert any("JRE-9999" in e for e in errors)
    assert any("priority" in e for e in errors)

    bad = Ticket.parse(t.path.read_text(encoding="utf-8"))
    bad.meta.update({"id": "NOPE-0001", "project": "nope", "priority": "p1", "depends_on": []})
    bad.write(store.tickets_dir / "NOPE-0001-x.md")
    errors, _ = validate_store(store)
    assert any("unknown project" in e for e in errors)


def test_validate_flags_review_without_report_in_file(store):
    """A hand-edited file that says review with no Report fails validation."""
    t = make(store)
    t.path.write_text(t.path.read_text(encoding="utf-8").replace("status: inbox", "status: review"), encoding="utf-8")
    errors, _ = validate_store(store)
    assert any("Report" in e for e in errors)


def test_stale_is_computed_from_updated(store):
    """Open tickets untouched past [stale].days are stale; closed ones never are."""
    t = make(store)
    assert not store.is_stale(t)
    old = (dt.date.today() - dt.timedelta(days=22)).isoformat()
    t.meta["updated"] = old
    store.save(t, touch=False)
    t = store.get(t.id)
    assert store.is_stale(t)
    assert store.is_stale(t, on=dt.date.today() - dt.timedelta(days=10)) is False
    ops.reject(store, t, "Letting it go for the test.")
    assert not store.is_stale(store.get(t.id))


def test_next_orders_by_priority_then_age(store):
    """`next` returns the highest priority, then the oldest, ready ticket for the project."""
    a = to_ready(store, make(store, "older p2", priority="p2"))
    b = to_ready(store, make(store, "newer p0", priority="p0"))
    c = to_ready(store, make(store, "other project", "desk", priority="p0"))
    a.meta["created"] = "2026-01-01"
    store.save(a, touch=False)
    assert store.next_ready("jre").id == b.id
    assert store.next_ready("desk").id == c.id
    ops.claim(store, store.get(b.id), "jre")
    assert store.next_ready("jre").id == a.id
    assert store.next_ready("jeby-us") is None


def test_intake_refuses_private_files(store, tmp_path):
    """intake refuses a package containing PRIVATE_CONTEXT.md or a jeby-private path."""
    pkg = tmp_path / "pkg"
    (pkg / "handoff").mkdir(parents=True)
    (pkg / "handoff" / "HANDOFF.md").write_text("hello", encoding="utf-8")
    (pkg / "handoff" / "PRIVATE_CONTEXT.md").write_text("secret", encoding="utf-8")
    with pytest.raises(RuleError):
        ops.intake(store, pkg, "em round 9", "desk")
    assert not list(store.intake_dir.iterdir())

    pkg2 = tmp_path / "pkg2" / "jeby-private"
    pkg2.mkdir(parents=True)
    (pkg2 / "notes.md").write_text("x", encoding="utf-8")
    with pytest.raises(RuleError):
        ops.intake(store, tmp_path / "pkg2", "private stuff", "desk")


def test_intake_copies_and_opens_ticket(store, tmp_path):
    """intake copies the folder into intake/<date>-<topic>/ and opens an intake ticket."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "SLICES.md").write_text("# slices", encoding="utf-8")
    t, dest = ops.intake(store, pkg, "Em funding round", "desk")
    assert dest.parent == store.intake_dir and (dest / "SLICES.md").exists()
    assert t.meta["type"] == "intake" and t.status == "inbox" and t.meta["source"].startswith("intake/")


def test_round_trip_preserves_sections(store):
    """A ticket rendered and parsed again keeps its frontmatter and sections."""
    t = make(store)
    t.set_section("Context", "Why this exists.")
    ops.comment(store, t, "First comment", actor="em")
    again = Ticket.load(t.path)
    assert again.meta["id"] == t.id and again.section("Context") == "Why this exists."
    assert "· em · First comment" in again.section("Comments")
    errors, _ = validate_store(store)
    assert errors == []


def test_index_builds_sqlite(store):
    import sqlite3

    make(store)
    p = store.index()
    con = sqlite3.connect(p)
    assert con.execute("select count(*) from tickets").fetchone()[0] == 1
    con.close()
