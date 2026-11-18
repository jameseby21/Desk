"""DESK-0142: a comment that matches the author's earlier one after normalizing (case, spacing, punctuation, brackets, a trailing counter,
a typo or two) within 60 seconds is the same post: not stored, the existing one named, a one-line notice. James's DESK-0082 test
sequence is replayed here. Protects: 'let's be smart about this' never has to be said again."""

import datetime as dt

from fastapi.testclient import TestClient

from desk import ops
from desk.ui.app import create_app

T0 = dt.datetime(2026, 11, 8, 3, 3, 0)


def _post(store, t, text, who, at):
    return ops.comment(store, store.get(t.id), text, actor=who, now=T0 + dt.timedelta(seconds=at))


def _count(store, t):
    return len(ops.comment_lines(store.get(t.id)))


def test_normalization_rules():
    assert ops.norm_comment("Dup test # 2 ()") == ("dup test 2", "dup test")
    assert ops.norm_comment("DUP   test #2 [] ")[0] == "dup test 2"
    assert ops.norm_comment("(s) Dup test #5")[1] == "s dup test"  # a leading mark is not a counter


def test_james_dup_test_sequence_collapses_to_one(store):
    """Dup test # 2 and its '()', '(s)', '[]', '(]' variants are one comment."""
    t = ops.new(store, "Talk", "jre")
    assert _post(store, t, "Dup test # 2", "james", 0).added
    for n, variant in enumerate(["Dup test # 2 ()", "Dup test # 2 (s)", "Dup test # 2 []", "Dup test # 2 (]", "dup test #2"], start=5):
        r = _post(store, t, variant, "james", n)
        assert not r.added and r.near and r.existing == 0 and "almost the same" in r.message, variant
    assert _count(store, t) == 1


def test_dup_test_3_posts_by_james_james1_and_james_underscore_are_one_person(store):
    """The later acceptance line wins: 'The same' tolerates a changed user (Patch comment on DESK-0142, 2026-11-08 05:40)."""
    t = ops.new(store, "Talk", "jre")
    assert _post(store, t, "Dup test #3", "james", 0).added
    assert not _post(store, t, "Dup test #3", "james1", 10).added
    assert not _post(store, t, "Dup test #3", "james_", 20).added
    assert _count(store, t) == 1


def test_a_clearly_different_name_or_a_later_time_is_stored(store):
    t = ops.new(store, "Talk", "jre")
    assert _post(store, t, "Dup test #4", "james", 0).added
    assert _post(store, t, "Dup test #4", "chaz", 5).added  # a different person
    assert _post(store, t, "Dup test #4 ()", "james", 300).added  # outside 60 s and the byte-identical repeat is not identical either
    assert _count(store, t) == 3


def test_typos_within_two_edits_but_not_short_words_or_other_text(store):
    t = ops.new(store, "Talk", "jre")
    assert _post(store, t, "The checkbox looks wrong on the right side", "james", 0).added
    assert not _post(store, t, "The chekbox looks wrong on the right side.", "james", 5).added
    assert _post(store, t, "The checkbox looks fine now", "james", 8).added
    assert _post(store, t, "ok", "james", 12).added and _post(store, t, "no", "james", 14).added  # two letters: equal or different
    assert _count(store, t) == 4


def test_the_ui_says_so_in_one_line_and_names_the_earlier_comment(store):
    t = ops.new(store, "Talk", "jre")
    c = TestClient(create_app(store.home))
    r = c.post(f"/ticket/{t.id}/comment", data={"token": c.app.state.tokens.issue(), "text": "Dup test # 5", "who": "james"}, follow_redirects=True)
    assert "Comment added" in r.text
    r = c.post(f"/ticket/{t.id}/comment", data={"token": c.app.state.tokens.issue(), "text": "dup test #5 ()", "who": "james1"}, follow_redirects=True)
    assert "almost the same as a comment you just posted" in r.text
    assert _count(store, t) == 1
    r = c.post(f"/ticket/{t.id}/comment", data={"token": c.app.state.tokens.issue(), "text": "dup test #5 []", "who": "james"}, headers={"Accept": "application/json"})
    j = r.json()
    assert j["duplicate"] and j["index"] == 0 and "almost the same" in j["message"]
