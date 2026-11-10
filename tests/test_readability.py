"""DESK-0209: no plain blocks. James (2026-11-09): "I don't want to see a plain block of text in any ticket." The detector is the one the
store cleanup used: a prose paragraph over 300 characters, or three or more sentences over 200 without a bold lead, or a bullet over
400, is a wall; quotes, code and tables never count. `drew validate` prints one warning per flagged section of the free-text sections
(Context, Outcome, Prompt, Out of scope, Report, Evidence); the ticket page shows a plain-words flag beside the section heading.
Protects: a 400-character paragraph pasted into Context slipping through validate with no word said."""

from fastapi.testclient import TestClient

from desk import ops, readability
from desk.rules import validate_store
from desk.ui.app import create_app

SENTENCE = "The detector reads every free-text section and names the ones that need a lead and bullets. "  # 91 characters
PARA_400 = (SENTENCE * 5).strip()  # one paragraph of five sentences, about 450 characters
SHORT_3 = "It runs before anything else and reads every free-text section. It names the section that holds the wall and the ticket it belongs to. It says what to do instead, in words a person reads once and acts on without a second look."  # 3 sentences, over 200, under 300
BOLD_3 = "**It runs before anything else and reads every free-text section.** It names the section that holds the wall and the ticket it belongs to. It says what to do instead, in words a person reads once and acts on without a second look."
QUOTE = "> " + PARA_400
CODE = "```\n" + PARA_400 + "\n```"
TABLE = "| a | b |\n| --- | --- |\n| " + PARA_400 + " | x |"
LONG_BULLET = "- " + (SENTENCE * 5).strip()  # one list item over 400 characters
SHORT_BULLETS = "\n".join(f"- {SENTENCE.strip()}" for _ in range(6))  # six items, each well under 400


def test_a_paragraph_over_300_characters_is_a_wall():
    (w,) = readability.walls(PARA_400)
    assert w.kind == "paragraph" and w.chars > 300 and w.sentences == 5


def test_three_sentences_over_200_characters_without_a_bold_lead_is_a_wall_and_with_one_is_not():
    assert len(SHORT_3) > 200 and len(SHORT_3) <= 300
    assert [w.kind for w in readability.walls(SHORT_3)] == ["paragraph"]
    assert readability.walls(BOLD_3) == []


def test_a_short_paragraph_is_not_a_wall():
    assert readability.walls(SENTENCE.strip()) == []
    assert readability.walls("**Why.** One sentence that says it. Another that finishes the thought.") == []


def test_a_bullet_over_400_characters_is_a_wall_and_short_bullets_are_not():
    (w,) = readability.walls(LONG_BULLET)
    assert w.kind == "bullet" and w.chars > 400
    assert readability.walls(SHORT_BULLETS) == []


def test_a_bullet_with_indented_continuation_lines_counts_as_one_item():
    item = "- " + SENTENCE.strip() + "\n  " + SENTENCE.strip() + "\n  " + SENTENCE.strip() + "\n  " + SENTENCE.strip() + "\n  " + SENTENCE.strip()
    (w,) = readability.walls(item)
    assert w.kind == "bullet"


def test_quotes_code_and_tables_never_count():
    assert readability.walls(QUOTE) == []
    assert readability.walls(CODE) == []
    assert readability.walls(TABLE) == []
    assert readability.walls("    " + PARA_400) == []  # indented code
    assert readability.walls("# " + PARA_400) == []  # a heading line


def test_a_quote_between_two_short_paragraphs_separates_them():
    text = f"{SENTENCE.strip()}\n{QUOTE}\n{SENTENCE.strip()}"
    assert readability.walls(text) == []


def test_sentence_count_ignores_abbreviations_code_spans_urls_and_decimals():
    assert readability.sentence_count("Use e.g. `drew validate` on https://example.com/x.y now. Then stop.") == 2
    assert readability.sentence_count("Version 1.2 shipped. Version 1.3 did not.") == 2
    assert readability.sentence_count("") == 0


def test_validate_prints_one_warning_per_flagged_section_in_the_agreed_words(store):
    t = ops.new(store, "A wall in Context", "desk")
    t.set_section("Context", PARA_400)
    t.set_section("Report", PARA_400 + "\n\n" + LONG_BULLET)  # two walls in one section: still one warning, the longest counted
    t.set_section("Decision", PARA_400)  # a record, never read
    t.set_section("Acceptance", "- [ ] " + (SENTENCE * 6).strip())  # a parsed list, never read
    store.save(t)
    _errors, warnings = validate_store(store)
    mine = [w for w in warnings if w.startswith(f"{t.id}:") and "wall of text" in w]
    assert len(mine) == 2, mine
    assert mine[0] == f"{t.id}: Context has a wall of text ({len(PARA_400):,} characters); use a bold lead and bullets (rule 15)"
    assert mine[1].startswith(f"{t.id}: Report has a wall of text (") and mine[1].endswith("; use a bold lead and bullets (rule 15)")
    assert not any("Decision" in w or "Acceptance" in w for w in mine)


def test_the_warning_goes_when_the_paragraph_becomes_a_bold_lead_and_bullets(store):
    t = ops.new(store, "Fixed", "desk")
    t.set_section("Context", PARA_400)
    store.save(t)
    assert any(t.id in w and "Context has a wall of text" in w for w in validate_store(store)[1])
    t.set_section("Context", f"**{SENTENCE.strip()}**\n\n{SHORT_BULLETS}")
    store.save(t)
    assert not any(t.id in w and "wall of text" in w for w in validate_store(store)[1])


def test_the_ticket_page_shows_a_plain_words_flag_beside_the_flagged_heading_only(store):
    t = ops.new(store, "Page flag", "desk")
    t.set_section("Context", PARA_400)
    t.set_section("Outcome", f"**{SENTENCE.strip()}**\n\n{SHORT_BULLETS}")
    t.set_section("Prompt", LONG_BULLET)
    store.save(t)
    page = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    assert f'<span class="wallflag" data-wall="Context">Readability: a wall of text ({len(PARA_400):,} characters). Use a bold lead and bullets.</span>' in page
    assert 'data-wall="Prompt">Readability: a long bullet (' in page
    assert 'data-wall="Outcome"' not in page
    assert store.get(t.id).section("Context") == PARA_400  # the file is untouched


def test_a_clean_ticket_page_has_no_flag(store):
    t = ops.new(store, "Clean", "desk")
    t.set_section("Context", f"**Why.** {SENTENCE.strip()}")
    store.save(t)
    page = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    assert 'class="wallflag"' not in page
