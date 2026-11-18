"""DESK-0037, changed by DESK-0253 (standards rule 15: a mascot word may only appear beside a plain label, never instead of one). Protects:
"Thrown ready" and "Triage triage" seen side by side. Plain shows Ready, Dogs shows Ready (thrown), once; the files and the command line stay plain whatever the style; switching the style
changes screen words only, and a page view never writes a ticket file."""

import re

import pytest
from fastapi.testclient import TestClient

from desk import ops, style as styles, words
from desk.cli import main
from desk.ui.app import create_app

DOG_WORDS = ("thrown", "fetching", "dropped", "sniffed", "stuck", "fetched", "chewed back", "in your mouth", "dropped at your feet")


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


def test_the_tables_put_the_plain_label_first_and_plain_is_the_default():
    assert words.screen("ready") == "Ready" and words.screen("ready", "dogs") == "Ready (thrown)" and words.screen("ready", "cat") == "Ready"
    assert words.screen("triage") == "To sort" and words.screen("triage", "dogs") == "To sort"
    assert words.screen("review") == "Sent for review" and words.screen("done", "dogs") == "Done (fetched)"
    for st in words.PLAIN:
        assert words.PLAIN[st] and " · " not in words.PLAIN[st] and words.DOGS[st].startswith(words.PLAIN[st])  # a dog word never replaces the plain label
    assert words.screen("nonsense") == "nonsense"


@pytest.mark.parametrize("style", ["plain", "cat"])
def test_plain_and_cat_never_show_a_dog_word(store, style):
    cl, urls, _ = _pages(store)
    styles.save(store.home, style, "drew")
    for u in urls:
        text = cl.get(u).text
        body = re.sub(r"<script.*?</script>|<style.*?</style>", "", text, flags=re.S)
        for w in DOG_WORDS:
            assert w not in body.lower(), (style, u, w)


def test_dogs_show_the_dog_word_only_in_brackets_beside_the_plain_label(store):
    cl, urls, (a, b, c, d, e, f) = _pages(store)
    styles.save(store.home, "dogs", "drew")
    ticket = cl.get(f"/ticket/{a.id}").text
    assert "Ready (thrown)" in ticket and "Thrown" not in ticket  # the plain label leads; the dog word is only beside it
    board = cl.get("/board?open=desk&g=desk:t").text
    assert re.findall(r'<h3 class="colhead"[^>]*><span>([^<]*)</span>', board) == ["Inbox (sniffed)", "Ready (thrown)", "In progress (fetching)", "Sent for review (dropped)", "Blocked (stuck)"]
    assert "Triage triage" not in board and "Thrown ready" not in board and "1 done (fetched)" in board  # the Finished line keeps the plain label first
    today = cl.get("/").text
    assert "Claimed tickets (in your mouth)" in today and "Waiting on you (dropped at your feet)" in today
    for u in urls:
        text = cl.get(u).text
        for st, w in words.DOGS.items():
            if w != words.PLAIN[st]:
                assert not re.search(rf"{words.PLAIN[st]}\s*(·|/)\s*{st}", text), (u, w, st)  # never "Ready · ready": the dog word is bracketed once


def test_plain_ticket_page_and_board_say_the_plain_word_once(store):
    cl, urls, (a, *_rest) = _pages(store)
    ticket = cl.get(f"/ticket/{a.id}").text
    assert ">Ready<" in ticket and "Ready · ready" not in ticket and "Ready (ready)" not in ticket
    board = cl.get("/board?open=desk&g=desk:t").text
    assert re.findall(r'<h3 class="colhead"[^>]*><span>([^<]*)</span>', board) == ["Inbox", "Ready", "In progress", "Sent for review", "Blocked"]
    assert "To sort" in board and "Triage triage" not in board
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
