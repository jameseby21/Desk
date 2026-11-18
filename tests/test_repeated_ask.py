"""DESK-0158: the repeated-ask detector. Protects: rule 'repetition is an emergency' (James, 2026-11-08). Two comments by the owner on one
ticket asking for the same outcome within 7 days make it p0 with the tag emergency and a warning in validate; a hand-off that names the
owner and sat unchanged across two sweeps is named, so nothing waits on anyone's memory."""

import datetime as dt
import os
import time

from desk import comments as cm, ops, repeats
from desk.cli import main
from desk.rules import validate_store


def _mk(store, title="Needs the owner", **meta):
    t = ops.new(store, title, "jre")
    t.meta.update(meta)
    t.write()
    return store.get(t.id)


def _line(who, when, text):
    return f"- {when:%Y-%m-%d %H:%M} · {who} · {text} <!-- kind: Question -->"


def test_normalize_drops_punctuation_filler_and_the_kind_marker():
    assert repeats.normalize("Please, FIX the board!  <!-- kind: Concern -->") == ["fix", "board"]


def test_same_ask_in_different_words_but_not_a_different_ask():
    assert repeats.same_ask("The assignee dropdown should list Patch and Chaz.", "Make the assignee dropdown list Patch, Chaz and Em")
    assert not repeats.same_ask("The assignee dropdown should list Patch and Chaz.", "Add a search box to the header of every page")
    assert not repeats.same_ask("ok thanks", "ok thanks")  # too short to be an ask


def test_two_owner_comments_asking_for_the_same_thing_escalate_and_warn(store):
    t = _mk(store, priority="p2")
    ops.comment(store, store.get(t.id), "Make the board show only projects at first, then drill in.", actor="james")
    assert store.get(t.id).meta["priority"] == "p2"  # one ask is just an ask
    ops.comment(store, store.get(t.id), "I said make the board show projects first and drill in!", actor="james", kind="Concern")
    got = store.get(t.id)
    assert got.meta["priority"] == "p0" and "emergency" in got.tags
    warn = [w for w in validate_store(store)[1] if "repeated ask" in w]
    assert len(warn) == 1 and warn[0].startswith(t.id) and "repetition is an emergency" in warn[0]


def test_different_asks_other_authors_and_old_asks_do_not_count(store):
    t = _mk(store, priority="p2")
    ops.comment(store, store.get(t.id), "Make the board show only projects at first.", actor="james")
    ops.comment(store, store.get(t.id), "Please add search by assignee to the palette.", actor="james")
    ops.comment(store, store.get(t.id), "Make the board show only projects at first.", actor="chaz")  # not the owner
    assert store.get(t.id).meta["priority"] == "p2" and "emergency" not in store.get(t.id).tags
    old = dt.datetime.now() - dt.timedelta(days=9)
    u = _mk(store, "Old", priority="p2")
    u.append_line("Comments", _line("james", old, "Make the board show only projects at first and drill in."))
    u.write()
    ops.comment(store, store.get(u.id), "Make the board show only projects at first and drill in.", actor="james")
    assert store.get(u.id).meta["priority"] == "p2"  # nine days apart: outside the window
    assert not [w for w in validate_store(store)[1] if "repeated ask" in w]


def test_drew_repeats_lists_and_apply_escalates_what_validate_found(store, capsys):
    t = _mk(store, priority="p3")
    now = dt.datetime.now()
    t.append_line("Comments", _line("james", now - dt.timedelta(days=2), "Search must tolerate typos in ticket titles."))
    t.append_line("Comments", _line("james", now - dt.timedelta(days=1), "Typos in ticket titles must be tolerated by search."))
    t.write()
    main(["--home", str(store.home), "repeats"])
    out = capsys.readouterr().out
    assert t.id in out and "1 repeated ask(s)" in out and store.get(t.id).meta["priority"] == "p3"
    main(["--home", str(store.home), "repeats", "--apply"])
    got = store.get(t.id)
    assert got.meta["priority"] == "p0" and "emergency" in got.tags and "repeated ask: priority p0" in got.section("Comments")


def test_mark_sweep_keeps_the_last_sweeps_and_prints_flags_first(store, capsys):
    now = dt.datetime.now().replace(microsecond=0)
    cm.mark_sweep(store.home, now - dt.timedelta(days=2))
    cm.mark_sweep(store.home, now - dt.timedelta(days=1))
    assert [s.date() for s in repeats.sweep_times(store.home)] == [(now - dt.timedelta(days=2)).date(), (now - dt.timedelta(days=1)).date()]
    t = _mk(store, "Waits on James", status="blocked", blocked_on="james: decide the colour", blocked_from="ready")
    old = time.time() - 3 * 86400
    os.utime(t.path, (old, old))
    main(["--home", str(store.home), "comments", "--mark-sweep"])
    out = capsys.readouterr().out
    assert out.startswith("FLAG ") and f"{t.id}: waiting on James since" in out and "is the hand-off real?" in out and "sweep marked at" in out


def test_stale_handoff_needs_two_sweeps_since_the_last_change_and_a_name(store):
    now = dt.datetime.now().replace(microsecond=0)
    t = _mk(store, "Waits on James", status="blocked", blocked_on="james", blocked_from="ready")
    old = time.time() - 3 * 86400
    os.utime(t.path, (old, old))
    cm.mark_sweep(store.home, now - dt.timedelta(days=1))
    assert repeats.stale_handoffs(store) == []  # one sweep is not enough
    cm.mark_sweep(store.home, now - dt.timedelta(hours=1))
    assert [x.id for x, _ in repeats.stale_handoffs(store)] == [t.id]
    assert any("waiting on James since" in w and "is the hand-off real?" in w for w in validate_store(store)[1])
    fresh = _mk(store, "Changed after the sweeps", status="blocked", blocked_on="james", blocked_from="ready")
    other = _mk(store, "Waits on Chaz", status="blocked", blocked_on="chaz", blocked_from="ready")
    os.utime(other.path, (old, old))
    assert [x.id for x, _ in repeats.stale_handoffs(store)] == [t.id] and fresh and other
