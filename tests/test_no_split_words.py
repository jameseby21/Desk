"""DESK-0302: a word never splits across lines in a header or label (James, 2026-11-15, second time: "NOT / ACCEPT / ED")."""

import re
from pathlib import Path

CSS = re.sub(r"/\*.*?\*/", "", (Path(__file__).resolve().parents[1] / "src" / "desk" / "ui" / "static" / "desk.css").read_text(encoding="utf-8"),
             flags=re.S)  # comments out, so a comment is never read as part of a selector

# Rules allowed to break inside a long token (URLs, paths, ids in flexible-width text). A new rule that allows mid-word breaks
# fails this test until someone adds it here on purpose, after checking it can never sit in a box narrower than a word.
MAY_BREAK_LONG_TOKENS = {
    ".shot-meta", ".toast a", ".tl-opts label", ".tl-ev .tl-what", ".part-of", ".sub-title", ".cc-sub",
    ".tr-part,.nm-part", ".part-line",
    # Reviewed 2026-11-17 (DESK-0282): the phone rule for the Latest change line shows the same free text as .tl-ev .tl-what
    # (shown_what: commit subjects, paths, ids) as a full-row, two-line clamp; tests/test_latest_change_phone.py needs a
    # 300-character token to stay inside it. Full row width is guarded below, so it never sits in a box narrower than a word.
    ".tl-latest .tl-what",
}


def _rule(selector: str) -> str:
    m = re.search(re.escape(selector) + r"\{([^}]*)\}", CSS)
    assert m, f"rule {selector} not found in desk.css"
    return m.group(1)


def _px(decl: str, prop: str) -> list[int]:
    m = re.search(prop + r":([^;]+)", decl)
    assert m, f"{prop} missing in {decl}"
    return [int(x) for x in re.findall(r"(\d+)px", m.group(1))]


def test_check_header_never_splits_a_word():
    """Protects the Done / Not accepted header from breaking ACCEPTED into ACCEPT and ED."""
    decl = _rule(".chk-head span+span")
    assert "overflow-wrap:anywhere" not in decl
    assert "hyphens:auto" not in decl
    assert "word-break:keep-all" in decl


def test_not_accepted_column_fits_the_word_whole():
    """Protects the column width: ACCEPTED in 11 px uppercase mono with .12em spacing is about 64 px, so the column is at least 80."""
    head = _px(_rule(".chk-head"), "grid-template-columns")
    boxes = _px(_rule(".chk2"), "grid-template-columns")
    assert head[-1] >= 80, head
    assert boxes == head[-2:], (boxes, head)  # the boxes sit under their headers


def test_line_padding_matches_the_two_columns():
    """Protects the acceptance text from running under the boxes when the column widens."""
    pad = _px(_rule(".md li.task-list-item:has(.chk2)"), "padding")
    assert pad[1] == sum(_px(_rule(".chk2"), "grid-template-columns")), pad


def test_mid_word_breaks_are_a_reviewed_exception():
    """Protects every header and label: a new rule with overflow-wrap:anywhere or hyphens:auto must be reviewed first."""
    found = set()
    for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", CSS):
        if "overflow-wrap:anywhere" in body or "hyphens:auto" in body:
            found.add(sel.strip().split("}")[-1].strip())
    assert found <= MAY_BREAK_LONG_TOKENS, sorted(found - MAY_BREAK_LONG_TOKENS)


def test_the_latest_change_text_breaks_long_tokens_only_on_a_full_row():
    """Protects the .tl-latest .tl-what exception: on a phone it takes the whole row (flex-basis 100%), so a normal word always fits
    and only an over-long token (a path, a URL) ever breaks; if it shares a row again, the exception no longer holds."""
    rules = re.findall(r"\.tl-latest \.tl-what\{([^}]*)\}", CSS)
    breaking = [r for r in rules if "overflow-wrap:anywhere" in r]
    assert breaking, rules
    for r in breaking:
        assert "flex:1 1 100%" in r and "hyphens" not in r, r
