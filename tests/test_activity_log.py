"""DESK-0162: the append-only activity log. Protects: one event per change with actor, kind, source and time; nothing earlier in a
ticket's log is ever rewritten; old comments and git history migrate in once (a dry run counts exactly what an apply writes, and a
second apply writes nothing)."""

import dataclasses
import json
import subprocess

import pytest

from desk import activity, identity, ops
from desk.cli import main


@pytest.fixture
def as_patch():
    token = identity.CURRENT.set(identity.Actor("patch", "session", "s1", "cli", "claimed", False))
    yield
    identity.CURRENT.reset(token)


def test_five_kinds_of_change_are_five_kinds_of_event_in_order_with_the_actor(store, as_patch):
    t = ops.new(store, "A thing to log", "desk")
    ops.move(store, store.get(t.id), "triage")
    t = store.get(t.id)
    t.meta["priority"] = "p0"
    store.save(t)
    ops.comment(store, store.get(t.id), "Why is this like that?\nsecond line")
    t = store.get(t.id)
    t.set_section("Context", "Now with context.")
    store.save(t)
    evs = activity.read(store.home, t.id)
    changes = [e.change for e in evs]
    assert changes[0] == "created" and {"field", "comment", "section"} <= set(changes), changes
    fields = {e.field: e for e in evs if e.change == "field"}
    assert fields["status"].old == "inbox" and fields["status"].new == "triage", fields
    assert fields["priority"].old == "p2" and fields["priority"].new == "p0"
    assert "updated" not in fields  # the save itself is the event; the date it stamps is not a change of its own
    com = next(e for e in evs if e.change == "comment")
    assert com.what == "Why is this like that?", com  # the first line only
    assert next(e for e in evs if e.change == "section").field == "Context"
    for e in evs:
        assert e.actor == "main" and e.kind == "main" and e.source == "cli" and e.ts[:4].isdigit(), e
    assert [e.seq for e in evs] == list(range(1, len(evs) + 1))


def test_the_log_lives_beside_the_tickets_never_inside_the_markdown(store, as_patch):
    t = ops.new(store, "Where it lives", "desk")
    p = activity.path(store.home, t.id)
    assert p == store.home / "events" / f"{t.id}.jsonl" and p.is_file()
    assert "created" not in t.path.read_text(encoding="utf-8").split("## Comments")[-1]
    row = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    assert {"ticket", "ts", "actor", "kind", "source", "change", "field", "old", "new", "what", "seq", "prev"} <= set(row)


def test_an_event_cannot_be_edited_or_replaced_and_an_edited_file_is_caught(store, as_patch):
    t = ops.new(store, "Immutable", "desk")
    e = activity.read(store.home, t.id)[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.what = "something else"  # type: ignore[misc]
    with pytest.raises(activity.AppendOnlyError):
        activity.append(store.home, [dataclasses.replace(e, what="rewritten")])  # a written event handed back in is an edit
    with pytest.raises(activity.AppendOnlyError):
        activity.rewrite(store.home, t.id)
    ops.comment(store, store.get(t.id), "one more")
    assert activity.verify(store.home, t.id) == []
    p = activity.path(store.home, t.id)
    lines = p.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace("Immutable", "Mutable")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert any("edited" in x for x in activity.verify(store.home, t.id))


def test_appends_only_ever_add_lines_at_the_end(store, as_patch):
    t = ops.new(store, "Grows only", "desk")
    p = activity.path(store.home, t.id)
    before = p.read_text(encoding="utf-8")
    ops.comment(store, store.get(t.id), "later")
    after = p.read_text(encoding="utf-8")
    assert after.startswith(before) and len(after) > len(before)


def test_ticking_an_acceptance_line_says_which_line_was_ticked(store, as_patch):
    t = ops.new(store, "Ticks", "desk")
    x = store.get(t.id)
    x.set_section("Acceptance", "- [ ] First thing\n- [ ] Second thing\n")
    store.save(x)
    x = store.get(t.id)
    x.set_section("Acceptance", "- [ ] First thing\n- [x] Second thing\n")
    store.save(x)
    assert activity.read(store.home, t.id)[-1].what == "Acceptance changed: ticked: Second thing"


def test_a_log_that_cannot_be_written_never_fails_the_save(store, as_patch, monkeypatch, caplog):
    t = ops.new(store, "Saved anyway", "desk")
    monkeypatch.setattr(activity, "append", lambda *a, **k: (_ for _ in ()).throw(PermissionError("locked")))
    x = store.get(t.id)
    x.meta["priority"] = "p1"
    store.save(x)
    assert store.get(t.id).meta["priority"] == "p1" and "activity log not written" in caplog.text


def test_a_save_with_no_change_writes_no_event(store, as_patch):
    t = ops.new(store, "Quiet", "desk")
    n = len(activity.read(store.home, t.id))
    store.save(store.get(t.id))
    assert len(activity.read(store.home, t.id)) == n


def test_a_ui_write_is_recorded_as_the_browser_person_and_source_ui(store):
    token = identity.CURRENT.set(identity.Actor("james", "person", "dev1", "ui", "os-user", True))
    try:
        t = ops.new(store, "From the page", "desk")
    finally:
        identity.CURRENT.reset(token)
    e = activity.read(store.home, t.id)[0]
    assert (e.actor, e.kind, e.source, e.confirmed, e.instance) == ("james", "owner", "ui", True, "dev1")


def test_kinds_name_owner_main_coworker_code_and_subagent():
    from desk import people

    pe = people.People(people.SEED)
    assert activity.kind_for("james", "person", "james", pe) == "owner"
    assert activity.kind_for("patch", "session", "james", pe) == "main"
    assert activity.kind_for("chaz", "thread", "james", pe) == "coworker"
    assert activity.kind_for("code:jre", "session", "james", pe) == "code"
    assert activity.kind_for("subagent:ab12", "subagent", "james", pe) == "subagent"
    assert activity.kind_for("someone", "person", "james", pe) == "person"


# -- migration ----------------------------------------------------------------------------

def _old_ticket(store, title, comments):
    """A ticket as it was before the log existed: its file written straight to disk, no events."""
    t = ops.new(store, title, "desk")
    t = store.get(t.id)
    t.set_section("Comments", "\n".join(comments) + "\n")
    t.write()
    activity.path(store.home, t.id).unlink()
    return t


def _git(home, *args):
    return subprocess.run(["git", "-C", str(home), *args], capture_output=True, text=True, check=True)


def test_migration_dry_run_counts_equal_what_apply_writes_and_a_second_apply_writes_nothing(store):
    a = _old_ticket(store, "Old one", ["- 2026-11-01 · james · Why is this like that? <!-- kind: Question -->",
                                       "- 2026-11-02 14:05 · patch · Answered: because. <!-- by kind=session src=cli via=claimed ok=0 -->"])
    b = _old_ticket(store, "Old two", ["- 2026-11-03 · chaz · Boards posted"])
    _git(store.home, "init", "-q")
    _git(store.home, "-c", "user.name=James", "-c", "user.email=james@example.com", "add", "-A")
    _git(store.home, "-c", "user.name=James", "-c", "user.email=james@example.com", "commit", "-q", "-m", "desk: old tickets")
    rows, wrote, note = activity.migrate(store, apply=False)
    assert note == "" and wrote == 0
    planned = {tid: (c, g) for tid, c, g, _ in rows}
    assert planned[a.id] == (2, 1) and planned[b.id] == (1, 1), planned
    assert not activity.path(store.home, a.id).exists()  # a dry run writes nothing
    rows2, wrote2, _ = activity.migrate(store, apply=True)
    assert wrote2 == sum(c + g for _, c, g, _ in rows) == 5
    evs = activity.read(store.home, a.id)
    assert [e.actor for e in evs if e.change == "comment"] == ["james", "main"]
    assert evs[0].ts == "2026-11-01T00:00:00" and evs[1].ts == "2026-11-02T14:05:00"
    assert {e.source for e in evs} == {"migration", "git"}
    g = next(e for e in evs if e.change == "git")
    assert g.actor == "james" and g.kind == "owner" and g.what == "Saved in git: desk: old tickets"
    rows3, wrote3, _ = activity.migrate(store, apply=True)
    assert rows3 == [] and wrote3 == 0  # idempotent
    assert activity.verify(store.home, a.id) == []


def test_two_identical_comment_lines_are_two_events_and_a_third_added_later_is_migrated_too(store):
    line = "- 2026-11-08 · james · testing duplication"
    a = _old_ticket(store, "Twice", [line, line])
    assert activity.migrate(store, apply=True)[1] == 2
    x = store.get(a.id)
    x.set_section("Comments", "\n".join([line, line, line]) + "\n")
    x.write()
    rows, wrote, _ = activity.migrate(store, apply=True)
    assert wrote == 1 and [e.what for e in activity.read(store.home, a.id)] == ["testing duplication"] * 3


def test_migration_without_git_says_so_and_still_migrates_comments(store):
    a = _old_ticket(store, "No git here", ["- 2026-11-01 · james · Hello"])
    rows, wrote, note = activity.migrate(store, apply=True)
    assert "not read" in note and wrote == 1 and rows[0][0] == a.id


def test_drew_migrate_events_prints_counts_per_ticket_progress_and_a_plain_last_sentence(store, capsys):
    a = _old_ticket(store, "Counted", ["- 2026-11-01 · james · One", "- 2026-11-02 · james · Two"])
    main(["--home", str(store.home), "migrate", "events", "--dry-run"])
    out = capsys.readouterr()
    assert a.id in out.out and "2" in out.out
    assert "Looked at" in out.err  # progress as it goes
    assert "Dry run: nothing written. 2 events would be added to 1 ticket" in out.out
    main(["--home", str(store.home), "migrate", "events", "--apply"])
    assert "Wrote 2 events to 1 ticket" in capsys.readouterr().out
    main(["--home", str(store.home), "migrate", "events", "--apply"])
    assert "Nothing to add: every ticket's history is already in its log." in capsys.readouterr().out
