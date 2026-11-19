"""DESK-0334: the one shared filter that turns a ticket id into a link, by three methods.

James (2026-11-19): "Why the fuck am I still seeing a reference to a ticket WITHOUT A GODDAMNED LINK". Desk linked ids in the body
sections of a ticket and nowhere else (the Next action card, the smallest step, blocked_on, the event list, rows). The fix is one
filter, desk.idlinks, used by the Jinja filter `tid`, by the response pass that covers every page, and (as JavaScript) by text drawn by
scripts. Protects: an id that exists in the store is a link, wherever it sits in a sentence; an id that does not exist stays plain with
the title "No such ticket"; code, an existing link, a URL and a file name are never touched.

Methods: (1) link_text on plain strings; (2) link_html on markup, with the tag stack; (3) the Jinja filter `tid` on the real app."""

import pytest

from desk import idlinks
from desk.ui.app import create_app

KNOWN = {"CONS-0117": ("Rewrite the first line", "ready"), "MGMT-0112": ("Repetition", "ready"), "DESK-0334": ("Links", "ready")}


def link(id_):
    return f'<a class="tid" href="/ticket/{id_}" title="{KNOWN[id_][0]}" data-status="{KNOWN[id_][1]}">{id_}</a>'


# ---- method 1: plain text ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,expect", [
    ("CONS-0117 is next", link("CONS-0117") + " is next"),                          # start of a sentence
    ("Next is CONS-0117", "Next is " + link("CONS-0117")),                          # end, no full stop
    ("Rewrite CONS-0117.", "Rewrite " + link("CONS-0117") + "."),                    # end with a full stop
    ("Rewrite CONS-0117's first line", "Rewrite " + link("CONS-0117") + "'s first line".replace("'", "&#39;")),  # possessive (escaped)
    ("(CONS-0117)", "(" + link("CONS-0117") + ")"),                                  # in round brackets
    ("[CONS-0117]", "[" + link("CONS-0117") + "]"),                                  # in square brackets
    ("CONS-0117, MGMT-0112", link("CONS-0117") + ", " + link("MGMT-0112")),          # two in a row
    ("CONS-0117 MGMT-0112", link("CONS-0117") + " " + link("MGMT-0112")),            # two with only a space
    ("CONS-0117/MGMT-0112", link("CONS-0117") + "/" + link("MGMT-0112")),            # two with a slash
])
def test_link_text_links_an_existing_id_wherever_it_sits_in_a_sentence(text, expect):
    """Protects: start, end, brackets, a possessive and runs of ids all link; nothing but the id is wrapped."""
    assert idlinks.link_text(text, KNOWN) == expect


def test_link_text_a_missing_id_stays_plain_with_a_title():
    """Protects: an id that is not in the store is not a link; it carries the title that says so (James, 2026-11-19)."""
    assert idlinks.link_text("see ZZZ-9999 now", KNOWN) == 'see <span class="tid-missing" title="No such ticket">ZZZ-9999</span> now'


@pytest.mark.parametrize("text", [
    "see `CONS-0117` in code",
    "open https://example.com/ticket/CONS-0117 now",
    "open http://127.0.0.1:8765/ticket/CONS-0117.",
    "tickets/CONS-0117-rewrite-the-first-line.md is the file",
    "C:\\tickets\\CONS-0117-x.md",
    "XCONS-0117 and A-CONS-0117 and CONS-01177 and cons-0117",                        # a longer word, five digits, lower case
])
def test_link_text_leaves_code_urls_file_names_and_lookalikes_alone(text):
    """Protects: a backtick span, a URL, a file name and an id glued to other characters are never linked."""
    assert "<a " not in idlinks.link_text(text, KNOWN) and 'class="tid"' not in idlinks.link_text(text, KNOWN)


def test_link_text_escapes_the_text_it_returns():
    """Protects: the result is HTML, so the surrounding words are escaped (a ticket's text is never trusted as markup)."""
    out = idlinks.link_text("<b>CONS-0117</b> & more", KNOWN)
    assert out.startswith("&lt;b&gt;") and "&amp; more" in out and "<b>" not in out


def test_link_text_inside_a_link_makes_a_role_link_span_never_a_nested_anchor():
    """Protects: a whole-row link cannot hold an anchor, so the id becomes a span a script and a keyboard can follow."""
    out = idlinks.link_text("CONS-0117", KNOWN, nested=True)
    assert out == f'<span class="tid" role="link" tabindex="0" data-href="/ticket/CONS-0117" title="{KNOWN["CONS-0117"][0]}" data-status="ready">CONS-0117</span>'


def test_link_text_with_no_store_ids_changes_nothing_but_escaping():
    assert idlinks.link_text("CONS-0117 & x", {}) == "CONS-0117 &amp; x"


# ---- method 2: markup, with the tag stack ------------------------------------------------------------------------------------

def test_link_html_links_text_nodes_and_skips_code_links_scripts_and_attributes():
    """Protects: only visible text is linked; <code>, <pre>, <a>, <script>, <style>, <textarea>, <title>, <option>, and every attribute are untouched."""
    page = ('<html><head><title>CONS-0117</title><style>.CONS-0117{}</style></head><body><p title="CONS-0117">CONS-0117 first</p>'
            '<code>CONS-0117</code><pre>CONS-0117</pre><a href="/x">CONS-0117</a><script>var a="CONS-0117"</script>'
            '<textarea>CONS-0117</textarea><select><option>CONS-0117</option></select><button>CONS-0117</button></body></html>')
    out = idlinks.link_html(page, KNOWN)
    assert out.count("<a ") == 2  # the page's own <a href="/x"> and the one new link
    assert out.count('class="tid"') == 1 and '<p title="CONS-0117">' + link("CONS-0117") + " first</p>" in out
    assert "<code>CONS-0117</code>" in out and "<pre>CONS-0117</pre>" in out and '<a href="/x">CONS-0117</a>' in out  # a link to anywhere else keeps its text
    assert 'var a="CONS-0117"' in out and "<title>CONS-0117</title>" in out and "<option>CONS-0117</option>" in out


def test_link_html_inside_an_anchor_uses_a_span_and_leaves_the_anchors_own_ticket_alone():
    """Protects: a row that links to MGMT-0112 keeps its own id as it is, and another id inside it becomes a role=link span."""
    out = idlinks.link_html('<a class="trow" href="/ticket/MGMT-0112"><b>MGMT-0112</b> waits on CONS-0117</a>', KNOWN)
    assert "<b>MGMT-0112</b>" in out and 'data-href="/ticket/CONS-0117"' in out and out.count("<a ") == 1


def test_link_html_is_idempotent():
    """Protects: a page that passes the filter twice (a template filter, then the response pass) is the same as once."""
    once = idlinks.link_html("<p>CONS-0117 then ZZZ-9999</p>", KNOWN)
    assert idlinks.link_html(once, KNOWN) == once


def test_link_html_a_missing_id_gets_the_title_in_markup_too():
    assert '<span class="tid-missing" title="No such ticket">ZZZ-9999</span>' in idlinks.link_html("<p>ZZZ-9999</p>", KNOWN)


# ---- method 3: the Jinja filter on the real app ------------------------------------------------------------------------------

def test_the_jinja_filter_tid_links_a_string_in_the_apps_own_environment(store):
    """Protects: the filter is registered on the app's template environment, escapes, links an id that exists in the store, and
    marks one that does not."""
    from desk import ops

    a = ops.new(store, "A ticket to link", "desk", "task")
    app = create_app(store.home)
    tmpl_env = getattr(app.state, "templates_env", None)
    assert tmpl_env is not None, "the app exposes its Jinja environment as app.state.templates_env so tests can reach the filter"
    out = tmpl_env.from_string("{{ s|tid }}").render(s=f"after {a.id} & ZZZ-9999")
    assert f'<a class="tid" href="/ticket/{a.id}"' in out and "&amp;" in out and 'title="No such ticket"' in out


def test_link_text_runs_the_ticket_title_in_the_hover_through_the_censor():
    """Protects: a censor function given to the filter is applied to the title attribute, never to the id."""
    out = idlinks.link_text("see CONS-0117", KNOWN, censor=lambda s: s.replace("Rewrite", "R*****"))
    assert 'title="R***** the first line"' in out and ">CONS-0117</a>" in out


def test_the_id_index_follows_the_files_new_ticket_new_title_new_status_and_a_removed_file(store):
    """Protects: idlinks.index reads only the front matter of a file that changed, yet a new ticket, a retitled one, a status move and a
    deleted file are all seen on the next call (an out-of-date index would leave a fresh id unlinked, or link a gone one)."""
    from desk import ops

    a = ops.new(store, "First title", "desk", "task")
    assert idlinks.index(store)[a.id] == ("First title", "inbox")
    b = ops.new(store, "Second", "desk", "task")
    t = store.get(a.id)
    t.meta["title"], t.meta["status"] = "Renamed", "triage"
    store.save(t)
    k = idlinks.index(store)
    assert k[b.id][0] == "Second" and k[a.id] == ("Renamed", "triage")
    store.get(b.id).path.unlink()
    assert b.id not in idlinks.index(store) and a.id in idlinks.index(store)


@pytest.mark.parametrize("title", ["It's a title", "Colon: inside", "Hash # inside", "Starts with *star", "\"Double\" quotes", "Dash - and 'both' \"kinds\"", "Café ünïcode — dash", "[bracket] first",
                                   "A very long title " + "word " * 40, "! bang", "yes", "123", "null"])
def test_the_fast_front_matter_read_gives_the_same_title_as_the_full_parse(store, title):
    """Protects: the index reads three lines instead of parsing, and must agree with the real parser for every awkward title."""
    from desk import ops

    t = ops.new(store, title, "desk", "task")
    assert idlinks.index(store)[t.id] == (str(store.get(t.id).meta["title"]), store.get(t.id).status)
