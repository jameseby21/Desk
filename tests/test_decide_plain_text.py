"""DESK-0282 checker: Decide and Triage showed raw markdown asterisks ("*Summary. Built on branch ... *What changed.") in the card text.
On a copy of the store 231 of 483 asks had one. Cause: plain() stripped list markers before bold, and its list-marker pattern needed no
space after the marker, so the first `*` of a `**Summary.**` that opens a line was taken for a bullet and the `**` pair became a stray
`*`. Protects: the excerpt drops bold, italic and list marks whatever their order, and keeps a lone `*` that is not markdown
(2 * 3). Three methods: unit cases on plain(), the /decide page rendered, and a generated sweep of markdown shapes."""

import itertools

from fastapi.testclient import TestClient

from desk import identity, ops, people
from desk.ui.app import create_app
from desk.ui.routes_decide import plain

HERE = "http://127.0.0.1:8799"


# -- method 1: unit cases on the real shapes -------------------------------------------------------------------------------------

def test_plain_drops_bold_at_the_start_of_a_line_and_after_it():
    """Protects the cause: a bold lead sentence at the start of a line (the house style for a Report) loses both of its marks."""
    report = "**Summary.** Built on branch desk/DESK-0113, commit c8bb286.\n\n**What changed.** src/desk/x.py; *quietly* better."
    assert plain(report) == "Summary. Built on branch desk/DESK-0113, commit c8bb286. What changed. src/desk/x.py; quietly better."


def test_plain_still_drops_list_marks_and_keeps_a_lone_star():
    """Protects what plain() already did (bullets, numbers, quotes, headings, links, code) and an asterisk that is not markdown."""
    text = "# Head\n- one **bold** item\n* star bullet\n+ plus\n1. first\n> quoted\n[a link](http://x) and `code` and 2 * 3 = 6"
    assert plain(text) == "Head one bold item star bullet plus first quoted a link and code and 2 * 3 = 6"


# -- method 2: the page a person sees ----------------------------------------------------------------------------------------------

def test_decide_page_card_text_has_no_markdown_asterisks(store, monkeypatch):
    """Protects the screen: a review ask whose Report opens with bold lead sentences shows them as plain text on /decide."""
    t = ops.new(store, "A feature to review", "jre", "feature")
    t.meta["size"] = "m"
    t.meta["status"] = "review"
    t.set_section("Report", "**Summary.** Built and tested.\n\n**What changed.** One file.\n\n- **Not done.** Nothing.")
    store.save(t)
    monkeypatch.setattr(identity, "os_user", lambda: "owner-login")
    people.link_os_user(store.home, "james", "owner-login")
    page = TestClient(create_app(store.home), base_url=HERE).get("/decide").text
    about = page.split('<p class="dm-about">', 1)[1].split("</p>", 1)[0]
    assert about == "Summary. Built and tested. What changed. One file. Not done. Nothing.", about


# -- method 3: a generated sweep of markdown shapes -------------------------------------------------------------------------------

def test_no_emphasis_mark_survives_any_combination_of_line_start_and_emphasis():
    """Protects every order: each line start (none, bullet, number, quote, heading) with each emphasis (bold, italic, bold-italic,
    underscore bold) on a word, alone or after other text, comes out with no `*` and no `__`."""
    starts = ["", "- ", "* ", "+ ", "1. ", "> ", "## ", "  - "]
    marks = [("**", "**"), ("*", "*"), ("***", "***"), ("__", "__")]
    for start, (o, c), lead in itertools.product(starts, marks, ["", "Then "]):
        line = f"{start}{lead}{o}Word here.{c} After it."
        out = plain(line + "\n" + line)
        assert "*" not in out and "__" not in out, (line, out)
        assert "Word here." in out, (line, out)
