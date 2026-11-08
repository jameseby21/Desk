"""DESK-0175: the ticket page folds its boilerplate. Done means is one line (modules and check count) that opens on a click; Tests,
Attachments, Docs to update and Out of scope hide while they are still the seeded default or empty, named in one line that opens
them; ALGO-0002 at 1440 px is under 2,000 px tall. Protects: Done means filling 27 percent of a ticket page and four boilerplate
sections drawn on every ticket. Nothing is removed: every folded piece is in the page and one click away."""

import re

import pytest
from browser_harness import browser, drive
from fastapi.testclient import TestClient

from desk import ops
from desk.ui.app import create_app

CHECK = "- [ ] {m} check {i}: the claim names what was run, where, and what it printed.\n"


def _dod(store, modules=("core", "research", "decision"), n=8):
    d = store.home / "standards" / "definition-of-done"
    d.mkdir(parents=True, exist_ok=True)
    for m in modules:
        (d / f"{m}.md").write_text(f"# {m}\n\n" + "".join(CHECK.format(m=m.upper(), i=i) for i in range(n)), encoding="utf-8")


def _page(store, t):
    return TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text


def _algo(store, long_proof=True):
    """A ticket shaped like ALGO-0002: a long proof, a list of points to verify, three DoD modules, seeded boilerplate sections."""
    _dod(store)
    t = ops.new(store, "Research: monetization options and their implications", "desk", "research", dod=["core", "research", "decision"])
    t.set_section("Context", "Why this exists: the cost of the tools that run the signals and what may be sold.")
    t.set_section("Outcome", "A recommendation James can approve or send back.")
    t.set_section("Acceptance", "".join(f"- [ ] Line {i} of the acceptance holds.\n" for i in range(3)))
    t.set_section("Report", "Start with the software-only path: a tool, with no signals. Not run: no lawyer has read it." if not long_proof else "Start with the software-only path: a tool, with no signals. " + "The core UI and generic strategies stay open source, and paid is infrastructure only. " * 6
                  + "\n\n" + "".join(f"- Point {i} to verify before relying on this, with enough words to take a full line on the page.\n" for i in range(13)))
    t.set_section("Tests", "The How to validate lines, performed by a person, are the test until an automated one exists.")
    t.set_section("Docs to update", "- per the project's doc-ownership map")
    t.set_section("Out of scope", "- Anything not named in Acceptance.")
    t.set_section("Attachments", "")
    store.save(t)
    return t


def test_done_means_is_one_line_with_modules_and_check_count_and_the_text_is_still_there(store):
    t = _algo(store)
    page = _page(store, t)
    m = re.search(r'<details class="card section dm" id="done-means"><summary>(.*?)</summary>', page, re.S)
    assert m, "Done means is a folded block"
    line = re.sub(r"<[^>]+>", " ", m.group(1))
    assert "core, research and decision" in line and "24 checks" in line and "Show checklist" in line
    assert "<details class=\"dm\"" not in page.split('id="done-means"')[0]
    assert "CORE check 3" in page and "DECISION check 7" in page  # by reference, drawn, and reachable
    assert "open" not in re.search(r'<details class="card section dm" id="done-means"[^>]*>', page).group(0).replace("id=", "")


def test_a_project_definition_of_done_is_one_line_too(store):
    (store.projects_dir / "jre.md").write_text("---\nkey: jre\nprefix: JRE\ndod_doc: docs/DOD.md\n---\n# jRE\n\n| Repo | `jre repo` |\n", encoding="utf-8")
    g = store.home / "jre repo" / "docs"
    g.mkdir(parents=True)
    (g / "DOD.md").write_text("# Definition of Done\n\n- [ ] One.\n- [ ] Two.\n", encoding="utf-8")
    t = ops.new(store, "Listing", "jre", dod=["core"])
    line = re.search(r'id="done-means"><summary>(.*?)</summary>', _page(store, t), re.S).group(1)
    assert "jRE Definition of Done" in line and "2 checks" in line


def test_default_or_empty_sections_hide_and_are_named_in_one_line(store):
    t = _algo(store)
    page = _page(store, t)
    note = re.search(r'<details class="fold hidden-secs" id="hidden-sections"><summary>(.*?)</summary>(.*?)</details>', page, re.S)
    assert note and "Hidden while still default or empty" in note.group(1)
    for name in ("Tests", "Docs to update", "Out of scope"):
        assert name in note.group(1)
        assert f'<span class="label">{name}</span>' in note.group(2)  # one click away, inside the fold
        assert f'<span class="label">{name}</span>' not in page.replace(note.group(0), "")  # and nowhere else
    assert "Attachments" in note.group(1) and 'id="attachments" hidden' in page  # empty: named, and the in-place attach still has its box


def test_written_sections_stay_visible_and_leave_the_hidden_line(store):
    t = _algo(store)
    t.set_section("Tests", "tests/test_widget.py: the widget glows.")
    t.set_section("Docs to update", "- docs/COMMANDS.md")
    t.set_section("Out of scope", "- The mobile app.")
    t.set_section("Attachments", "![shot](assets/X/a.png)")
    store.save(t)
    page = _page(store, t)
    line = re.search(r"Hidden while still default or empty: ([^<]*?) &middot;", page).group(1)
    assert line == "Decision, Mentioned in"  # only what is still empty
    for name in ("Tests", "Docs to update", "Out of scope", "Attachments"):
        assert f'<span class="label">{name}</span>' in page
    assert "tests/test_widget.py" in page and "The mobile app." in page


def test_decision_and_mentioned_in_show_when_they_have_something(store):
    t = _algo(store)
    other = ops.new(store, "Other", "desk")
    other.set_section("Context", f"See {t.id} for the reason.")
    store.save(other)
    t.set_section("Decision", "- 2026-11-08 10:00 · james · approved: ship it.")
    store.save(t)
    page = _page(store, t)
    assert "Hidden while still default or empty" in page  # Tests and the others are still hidden
    line = re.search(r"Hidden while still default or empty: ([^<]*?) &middot;", page).group(1)
    assert "Decision" not in line and "Mentioned in" not in line
    assert 'id="decision"' in page and "approved: ship it." in page and other.id in page.split("Mentioned in")[1]


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_the_page_without_a_wall_of_proof_is_under_2000_px_at_1440(store):
    """The boilerplate folds alone bring a page to under 2,000 px; the long proof is folded by the text ladder (DESK-0139, same file)."""
    t = _algo(store, long_proof=False)
    got = drive(store, "<script>addEventListener('load',function(){setTimeout(function(){fetch('/m',{method:'POST',body:JSON.stringify({h:document.documentElement.scrollHeight,w:innerWidth})});},400);});</script>",
                f"/ticket/{t.id}", width=1440)
    assert got["w"] >= 1400 and got["h"] < 2000, got
