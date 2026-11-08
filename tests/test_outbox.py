"""DESK-0174: drew outbox. Protects: a coworker thread gets one entry with exactly the tickets to look at (handed to it, changed by Patch or
James since it last acted, new and naming it), the Tickets header matches the body, and the cut-off moves only when the entry is posted."""

import datetime as dt
import json
import re

from desk import ops, outbox
from desk.cli import main

NOW = dt.datetime.now().replace(microsecond=0)
DAY_AGO = (NOW - dt.timedelta(days=1)).strftime("%Y-%m-%d %H:%M")


def _mk(store, title, project="jre", **meta):
    t = ops.new(store, title, project)
    t.meta.update(meta)
    t.write()
    return store.get(t.id)


def _run(store, capsys, *args):
    main(["--home", str(store.home), "outbox", *args])
    return capsys.readouterr().out


def _ids(text):
    return re.findall(r"^- ([A-Z0-9]+-\d{4}) ·", text, re.M)


def _header_ids(text):
    return [x.strip() for x in re.search(r"^Tickets: (.*)$", text, re.M).group(1).split(",")]


def test_assigned_and_reassigned_tickets_are_listed_with_the_ask(store, capsys):
    a = _mk(store, "Design the thing", status="ready", review_by="2026-11-12")
    ops.assign(store, store.get(a.id), "chaz", actor="james")
    b = _mk(store, "Old news", status="ready")
    ops.assign(store, store.get(b.id), "chaz", actor="james")
    c = _mk(store, "Not for chaz", status="ready")
    ops.assign(store, store.get(c.id), "em", actor="james")
    out = _run(store, capsys, "--for", "chaz", "--since", "2000-01-01")
    assert out.startswith("Date: ") and "To: Chaz" in out and "From: Patch" in out and "Expires: " in out and "Supersedes: none" in out
    assert _ids(out) == [a.id, b.id] and _header_ids(out) == [a.id, b.id]  # the Tickets line matches the body
    line = [ln for ln in out.splitlines() if ln.startswith(f"- {a.id}")][0]
    assert "Design the thing" in line and " · ready · " in line and "assigned by James" in line and "ask: do by 2026-11-12" in line


def test_reassigned_is_named(store, capsys):
    a = _mk(store, "Moved around", status="ready")
    ops.assign(store, store.get(a.id), "em", actor="james")
    ops.assign(store, store.get(a.id), "chaz", actor="james")
    assert "reassigned" in _run(store, capsys, "--for", "chaz", "--since", "2000-01-01")


def test_a_comment_by_patch_or_james_on_a_ticket_they_touched_is_listed(store, capsys):
    t = _mk(store, "Chaz is on it", status="in_progress", assignee="chaz")
    ops.comment(store, store.get(t.id), "Please use the lime.", actor="james")
    other = _mk(store, "Chaz is not on this", status="in_progress", assignee="em")
    ops.comment(store, store.get(other.id), "Hello", actor="james")
    out = _run(store, capsys, "--for", "chaz", "--since", DAY_AGO)
    assert _ids(out) == [t.id] and "comment by james" in out


def test_a_thread_does_not_hear_about_its_own_comments(store, capsys):
    t = _mk(store, "Chaz works", status="in_progress", assignee="chaz")
    ops.comment(store, store.get(t.id), "Done a first pass.", actor="chaz")
    assert "Nothing new for Chaz" in _run(store, capsys, "--for", "chaz", "--since", DAY_AGO)


def test_a_decision_by_james_is_listed(store, capsys):
    t = _mk(store, "Review this", status="review", assignee="chaz")
    t.sections["Report"] = "Did it.\n"
    t.write()
    ops.decide(store, store.get(t.id), False, "Needs the second colour.", actor="james")
    out = _run(store, capsys, "--for", "chaz", "--since", DAY_AGO)
    assert t.id in out and "decision: changes_requested" in out


def test_new_tickets_that_name_the_thread_in_context_or_prompt_are_listed(store, capsys):
    t = _mk(store, "Brand the print run", status="ready")
    t.set_section("Context", "Chaz should pick the paper stock.")
    t.write()
    other = _mk(store, "Unrelated", status="ready")
    other.set_section("Context", "Purchase order for the printer, nothing about the team.")
    other.write()
    out = _run(store, capsys, "--for", "chaz", "--since", "2000-01-01")
    assert _ids(out) == [t.id] and "new, names Chaz" in out


def test_cutoff_moves_only_when_posted_and_the_next_entry_is_empty(store, capsys):
    t = _mk(store, "Hand over", status="ready")
    ops.assign(store, store.get(t.id), "chaz", actor="james")
    first = _run(store, capsys, "--for", "chaz")  # no earlier entry: a week back
    assert t.id in first and "7 days back" in first
    again = _run(store, capsys, "--for", "chaz")
    assert t.id in again  # printing alone does not move the cut-off
    posted = _run(store, capsys, "--for", "chaz", "--posted")
    assert t.id in posted and outbox.get_cutoff(store.home, "chaz") is not None
    empty = _run(store, capsys, "--for", "chaz")
    assert "Nothing new for Chaz" in empty and "the last entry" in empty
    ops.comment(store, store.get(t.id), "One more thing.", actor="patch")
    store_state = json.loads((store.home / ".desk" / "state").read_text(encoding="utf-8"))
    assert "chaz" in store_state["outbox"]
    # a change after the cut-off shows again (the cut-off is the second the entry was posted; step it back a minute to be sure of the clock)
    outbox.set_cutoff(store.home, "chaz", NOW - dt.timedelta(minutes=1))
    assert t.id in _run(store, capsys, "--for", "chaz")


def test_all_prints_one_entry_per_thread_with_changes_and_json_for_the_poster(store, capsys):
    a = _mk(store, "For chaz", status="ready")
    ops.assign(store, store.get(a.id), "chaz", actor="james")
    b = _mk(store, "For em", status="ready")
    ops.assign(store, store.get(b.id), "Em", actor="james")
    out = _run(store, capsys, "--all", "--since", "2000-01-01")
    assert out.count("Date: ") == 2 and "To: Chaz" in out and "To: Em" in out and "To: Lily" not in out
    data = json.loads(_run(store, capsys, "--all", "--json", "--since", "2000-01-01"))
    by = {d["actor"]: d for d in data}
    assert set(by) == {"chaz", "em", "lily", "bri", "joe", "bigd"}
    assert [t["id"] for t in by["chaz"]["tickets"]] == [a.id] and by["chaz"]["tickets"][0]["ask"] == "do" and a.id in by["chaz"]["text"]
    assert by["lily"]["tickets"] == [] and by["lily"]["text"] == ""


def test_needs_an_actor_and_a_good_time(store, capsys):
    import pytest

    with pytest.raises(SystemExit):
        main(["--home", str(store.home), "outbox"])
    with pytest.raises(SystemExit):
        main(["--home", str(store.home), "outbox", "--for", "chaz", "--since", "yesterday-ish"])
