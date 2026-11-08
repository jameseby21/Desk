"""DESK-0146: `drew comments --author james --since <when> --unanswered` lists comments as work items (standards rule: comments are
work). Unanswered means no later line by patch in that ticket's Comments, and the ticket has not moved on since."""

import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient

from desk import comments, ops, review
from desk.cli import main
from desk.ui.app import create_app


def _t(store, title, lines, updated="2026-11-08", project="jre"):
    t = ops.new(store, title, project)
    t.set_section("Comments", "\n".join(lines))
    store.save(t)
    t = store.get(t.id)
    t.meta["updated"] = updated  # save() stamps today; the fixture needs a chosen date
    store.save(t, touch=False)
    return t


@pytest.fixture
def mixed(store):
    """Four tickets: one unanswered James question, one answered by patch, one where James spoke last after patch, one moved on."""
    a = _t(store, "Open question", ["- 2026-11-08 03:01 · james · why is this like that? <!-- kind: Question -->"])
    b = _t(store, "Answered", ["- 2026-11-08 03:04 · james · tests please <!-- kind: Suggestion -->", "- 2026-11-08 04:55 · patch · Answer: filed DESK-0143. <!-- kind: FYI -->"])
    c = _t(store, "Asked again", ["- 2026-11-08 03:04 · james · first <!-- kind: Question -->", "- 2026-11-08 04:00 · patch · Answer: done <!-- kind: FYI -->",
                                  "- 2026-11-08 05:30 · james · still wrong <!-- kind: Concern -->"])
    d = _t(store, "Moved on", ["- 2026-11-07 09:00 · james · looks off"], updated="2026-11-08")
    e = _t(store, "Not james", ["- 2026-11-08 06:00 · chaz · FYI the mark is done"])
    return a, b, c, d, e


def _ids(rows):
    return {r.ticket_id for r in rows}


def test_filters_by_author_since_and_unanswered(store, mixed):
    a, b, c, d, e = mixed
    assert _ids(comments.query(store, author="james")) == {a.id, b.id, c.id, d.id}
    assert _ids(comments.query(store, author="JAMES", since=dt.datetime(2026, 11, 8))) == {a.id, b.id, c.id}  # d is from the 7th
    assert _ids(comments.query(store, author="james", unanswered=True)) == {a.id, c.id}  # b has a later patch line, d's ticket moved on
    rows = comments.query(store, author="james", unanswered=True)
    assert [r.text for r in rows if r.ticket_id == c.id] == ["still wrong"]  # c's first comment was answered, the second was not
    assert rows[0].kind in ("Question", "Concern")


def test_a_later_patch_line_or_the_ticket_moving_counts_as_answered(store):
    on_day = _t(store, "Same day", ["- 2026-11-08 03:00 · james · q"], updated="2026-11-08")
    assert _ids(comments.query(store, author="james", unanswered=True)) == {on_day.id}  # same day: cannot tell, so it stays open
    moved = _t(store, "Next day", ["- 2026-11-07 03:00 · james · q"], updated="2026-11-08")
    assert moved.id not in _ids(comments.query(store, author="james", unanswered=True))


def test_cli_lists_rows_and_json(store, mixed, capsys):
    a, b, c, d, e = mixed
    main(["--home", str(store.home), "comments", "--author", "james", "--since", "2026-11-08", "--unanswered"])
    out = capsys.readouterr().out
    assert a.id in out and c.id in out and b.id not in out and "why is this like that?" in out and "2026-11-08 03:01" in out
    assert "2 comment(s)" in out
    main(["--home", str(store.home), "comments", "--author", "james", "--since", "2026-11-08", "--unanswered", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert {r["ticket"] for r in data} == {a.id, c.id} and all(r["author"] == "james" and r["answered"] is False for r in data)
    assert {"ticket", "title", "when", "author", "kind", "text", "answered"} <= set(data[0])


def test_since_last_sweep_reads_and_marks_the_state_file(store, mixed, capsys):
    a, b, c, d, e = mixed
    main(["--home", str(store.home), "comments", "--author", "james", "--since", "last-sweep"])
    assert "5 comment(s)" in capsys.readouterr().out  # no sweep yet: everything
    main(["--home", str(store.home), "comments", "--mark-sweep"])
    capsys.readouterr()
    state = json.loads((store.home / ".desk" / "state").read_text(encoding="utf-8"))
    assert state["last_sweep"].startswith(dt.date.today().isoformat())
    comments.mark_sweep(store.home, dt.datetime(2026, 11, 8, 12, 0))
    main(["--home", str(store.home), "comments", "--author", "james", "--since", "last-sweep"])
    assert "0 comment(s)" in capsys.readouterr().out  # every fixture comment is before noon
    comments.mark_sweep(store.home, dt.datetime(2026, 11, 8, 5, 0))
    main(["--home", str(store.home), "comments", "--author", "james", "--since", "last-sweep"])
    assert "1 comment(s)" in capsys.readouterr().out  # only 05:30 is newer than a 05:00 sweep


def test_a_bad_since_is_a_sentence(store, capsys):
    with pytest.raises(SystemExit):
        main(["--home", str(store.home), "comments", "--since", "last tuesday"])
    assert "YYYY-MM-DD" in capsys.readouterr().err


def test_today_page_has_the_asked_block_with_the_open_count(store, mixed):
    c = TestClient(create_app(store.home))
    page = c.get("/").text
    assert "James asked" in page and "2 waiting for an answer" in page and "why is this like that?" in page
    quiet = c.get("/?cap=rough").text
    assert "James asked" in quiet


def test_review_week_ticket_shows_the_open_count(store, mixed):
    t, _ = review.create(store, project="jre", owner="james")
    assert "Comments still open: 2 (james)" in store.get(t.id).section("Report")
