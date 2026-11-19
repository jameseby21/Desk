"""Two defects the independent checker found on release 2026-11-18 (2026-11-19), each with three methods and the edges.

(1) The Comments section printed the raw session key `claude-code` as the author of a Move comment, while the Latest line, the rail and
the chips said `Code session` (DESK-0305 item f). The ticket file keeps the key as recorded; only what is drawn changes.
(2) The Move button on the ticket page measured 17 px high at a phone width, under the 44 px target of the UI review checklist
(rule 28): a late rule in desk.css reset every .btn to min-height 0, so the 44 px of .btn never applied."""

import re

import pytest
from browser_harness import browser, cdp_measure, live_app
from fastapi.testclient import TestClient

from desk import ops
from desk.ui.app import STATIC, create_app
from desk.ui.markdown import finish_comments, prep_comments

NEEDS_BROWSER = pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
LINE = "- 2026-11-18 14:05 · {who} · moved to blocked: Waiting for the sample file. <!-- kind: FYI -->"


def _visible(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", html))


def _bare_render(text: str) -> str:
    """The list item the Comments section renders for one stamped line (markdown reduced to what the section needs)."""
    return finish_comments("<ul>" + "".join(f"<li>{ln[2:]}</li>" for ln in prep_comments(text).splitlines()) + "</ul>")


# ---- (1) the author of a comment --------------------------------------------------------------------------------------------

def test_author_unit_the_raw_session_key_is_drawn_as_code_session():
    """Method 1, the preparer: the author field of a Comments line reads `Code session` when it is the raw key, in either case."""
    for key in ("claude-code", "Claude-Code"):
        out = prep_comments(LINE.format(who=key))
        assert "· Code session ·" in out and key not in out, out


def test_author_route_the_ticket_page_shows_no_raw_key_in_comments(store):
    """Method 2, the real page: a move comment written by the code session shows `Code session` in Comments and never the key."""
    t = ops.new(store, "A ticket the code session moved", "desk")
    t.append_line("Comments", LINE.format(who="claude-code"))
    store.save(t)
    page = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    comments = page[page.index('id="comments"'):]
    comments = comments[:comments.index("Add a comment")]
    assert "claude-code" not in comments, _visible(comments)
    assert "Code session" in _visible(comments), _visible(comments)


def test_author_file_the_ticket_file_keeps_the_recorded_key(store):
    """Method 3, the record: the display change never rewrites what was recorded; the file still says `claude-code`."""
    t = ops.new(store, "Record keeps the key", "desk")
    t.append_line("Comments", LINE.format(who="claude-code"))
    store.save(t)
    TestClient(create_app(store.home)).get(f"/ticket/{t.id}")
    assert "· claude-code · moved to blocked" in store.get(t.id).path.read_text(encoding="utf-8")


def test_author_edge_people_other_actors_and_text_that_merely_mentions_the_key_are_untouched():
    """Edge: a named person, an unknown actor and the words `claude-code` inside a comment body keep their own wording; a rendered
    list item reads `Code session` once."""
    body = "- 2026-11-18 14:05 · chaz · I asked claude-code to retry. <!-- kind: Question -->"
    assert "· chaz ·" in prep_comments(body) and "asked claude-code to retry" in prep_comments(body)
    other = "- 2026-11-18 14:05 · somebody-new · hello <!-- kind: Question -->"
    assert "· somebody-new ·" in prep_comments(other)
    assert _visible(_bare_render(LINE.format(who="claude-code"))).count("Code session") == 1
    # `desk` is the fallback author of a command-line comment: a person's, unless the identity tag says a session wrote it
    plain = "- 2026-11-18 14:05 · desk · hello <!-- kind: Question -->"
    assert "· desk ·" in prep_comments(plain)
    tagged = "- 2026-11-18 14:05 · desk · hello <!-- by kind=session src=cli via=env ok=0 --> <!-- kind: Question -->"
    assert "· Code session ·" in prep_comments(tagged)


# ---- (2) the size of the Move button ---------------------------------------------------------------------------------------

def _css() -> str:
    return re.sub(r"/\*.*?\*/", "", (STATIC / "desk.css").read_text(encoding="utf-8"), flags=re.S)


def test_move_button_static_a_rule_gives_the_move_button_a_44_px_target():
    """Method 1, the stylesheet: the Move button has its own min-height of at least 44 px, declared after the reset that zeroed .btn."""
    css = _css()
    m = re.search(r"\.mv-go\s*\{[^}]*min-height:\s*(\d+)px", css)
    assert m and int(m.group(1)) >= 44, "no .mv-go min-height of 44 px or more"
    assert css.rindex(m.group(0)) > css.index("a,button,input,select,textarea,.btn{min-height:0}")


def test_move_button_route_the_page_still_draws_a_move_button(store):
    """Method 2, the page: a ticket that is not in review carries the Move form with its button (the target the stylesheet sizes)."""
    t = ops.new(store, "Move me", "desk")
    page = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    assert re.search(r'<button class="btn mv-go"', page)


@NEEDS_BROWSER
@pytest.mark.parametrize("width", [375, 390])
def test_move_button_browser_is_44_px_high_at_a_phone_width_and_does_not_widen_the_page(store, width):
    """Method 3, a real browser (phone width by CDP device metrics): the button is at least 44 px high, enabled or grey, and the
    page does not scroll sideways."""
    t = ops.new(store, "Move me at a phone width", "desk")
    expr = """(function(){var b=document.querySelector('.mv-form .mv-go');if(!b)return null;var r=b.getBoundingClientRect();
return {h:r.height,w:r.width,disabled:b.disabled,sw:document.documentElement.scrollWidth,cw:document.documentElement.clientWidth};})()"""
    with live_app(create_app(store.home)) as base:
        got = cdp_measure(base, [f"/ticket/{t.id}"], [width], expr)
    d = got[f"/ticket/{t.id}@{width}"]
    assert d and d["h"] >= 44 and d["sw"] <= d["cw"], d
