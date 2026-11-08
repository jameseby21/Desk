"""DESK-0037: one word per state in each style, never both. Protects: "Thrown ready" and "Triage triage" seen side by side on James's Today
and board. Plain shows Ready, Dogs shows Thrown; the files and the command line stay plain whatever the style; switching the style
changes screen words only, and a page view never writes a ticket file."""

import re

import pytest
from fastapi.testclient import TestClient

from desk import ops, style as styles, words
from desk.cli import main
from desk.ui.app import create_app

DOG_WORDS = ("Thrown", "Fetching", "Dropped", "Sniffed", "Stuck", "Fetched", "Chewed back", "In your mouth", "Dropped at your feet")


def _mk(store, title, **meta):
    t = ops.new(store, title, "desk")
    t.meta.update(meta)
    t.write()
    return t


def _pages(store):
    a = _mk(store, "Ready one", status="ready", track="T")
    b = _mk(store, "Working one", status="in_progress", track="T")
    c = _mk(store, "Review one", status="review", track="T")
    d = _mk(store, "Triaged one", status="triage", track="T")
    e = _mk(store, "Blocked one", status="blocked", track="T", blocked_on="x")
    f = _mk(store, "Done one", status="done", track="T")
    cl = TestClient(create_app(store.home))
    urls = ["/", "/board?open=desk&g=desk:t", "/board", f"/ticket/{a.id}", f"/ticket/{c.id}", "/projects", "/search?q=one", "/sprint"]
    return cl, urls, (a, b, c, d, e, f)


def test_the_tables_are_one_word_each_and_plain_is_the_default():
    assert words.screen("ready") == "Ready" and words.screen("ready", "dogs") == "Thrown" and words.screen("ready", "cat") == "Ready"
    assert words.screen("triage") == "Triage" and words.screen("triage", "dogs") == "Triage"
    for st in words.PLAIN:
        assert words.PLAIN[st] and " · " not in words.PLAIN[st] and words.DOGS[st] and " · " not in words.DOGS[st]
    assert words.screen("nonsense") == "nonsense"


@pytest.mark.parametrize("style", ["plain", "cat"])
def test_plain_and_cat_never_show_a_dog_word(store, style):
    cl, urls, _ = _pages(store)
    styles.save(store.home, style, "drew")
    for u in urls:
        text = cl.get(u).text
        body = re.sub(r"<script.*?</script>|<style.*?</style>", "", text, flags=re.S)
        for w in DOG_WORDS:
            assert w not in body, (style, u, w)


def test_dogs_show_the_dog_word_alone_never_beside_the_plain_one(store):
    cl, urls, (a, b, c, d, e, f) = _pages(store)
    styles.save(store.home, "dogs", "drew")
    ticket = cl.get(f"/ticket/{a.id}").text
    assert "Thrown" in ticket and not re.search(r"Thrown\s*(·|\(|/)\s*ready", ticket)
    board = cl.get("/board?open=desk&g=desk:t").text
    assert re.findall(r'<h3 class="colhead"[^>]*><span>([^<]*)</span>', board) == ["Sniffed", "Thrown", "Fetching", "Dropped", "Stuck"]
    assert "Triage triage" not in board and "Thrown ready" not in board and "1 fetched" in board  # the Finished line says fetched
    today = cl.get("/").text
    assert "In your mouth" in today and "Dropped at your feet" in today
    for u in urls:
        text = cl.get(u).text
        for st, w in words.DOGS.items():
            if w != words.PLAIN[st]:
                assert not re.search(rf"{w}\s*(·|\()\s*{st}\b", text), (u, w, st)


def test_plain_ticket_page_and_board_say_the_plain_word_once(store):
    cl, urls, (a, *_rest) = _pages(store)
    ticket = cl.get(f"/ticket/{a.id}").text
    assert ">Ready<" in ticket and "Ready · ready" not in ticket and "Ready (ready)" not in ticket
    board = cl.get("/board?open=desk&g=desk:t").text
    assert re.findall(r'<h3 class="colhead"[^>]*><span>([^<]*)</span>', board) == ["Inbox", "Ready", "In progress", "Review", "Blocked"]
    assert "Triage" in board and "Triage triage" not in board
    project_row = re.search(r'class="bcounts">([^<]*)<', board).group(1)
    assert "ready" in project_row.lower() and "thrown" not in project_row.lower()


def test_the_command_line_and_files_stay_plain_in_every_style(store, capsys):
    styles.save(store.home, "dogs", "drew")
    t = _mk(store, "CLI one", status="inbox")
    main(["--home", str(store.home), "move", t.id, "--to", "ready"])
    out = capsys.readouterr().out
    assert f"{t.id} -> ready" in out and "Thrown" not in out
    main(["--home", str(store.home), "board"])
    assert "Thrown" not in capsys.readouterr().out
    assert store.get(t.id).status == "ready"


def test_switching_style_changes_screen_words_only_and_leaves_ticket_files_alone(store):
    cl, urls, tickets = _pages(store)
    before = {t.id: t.path.read_bytes() for t in tickets}
    for style in ("dogs", "cat", "plain"):
        styles.save(store.home, style, "drew")
        for u in urls:
            assert cl.get(u).status_code == 200
    assert {t.id: t.path.read_bytes() for t in tickets} == before
