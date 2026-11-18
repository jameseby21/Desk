"""DESK-0253: `drew validate` warns when Context, Outcome or How to validate reads above grade 10 or uses listed jargon with no plain
gloss (standards rule 15, "Human-readable by default"). It is a warning, never an error, and only for open tickets.
Protects: the 2026-11-11 complaint that ticket explanations were unreadable and nothing said so."""

from desk import ops, plainwords
from desk.rules import validate_store

PLAIN = "Desk shows a list of tickets. You click one to open it. The page says what you must do next. It also shows who asked."
DENSE = ("The implementation necessarily reconstitutes the authentication infrastructure's configuration, notwithstanding "
         "the previously established organizational prerequisites, thereby guaranteeing comprehensive interoperability "
         "throughout the heterogeneous deployment environments.")


def mine(store, t, what=None):
    _e, w = validate_store(store)
    return [x for x in w if x.startswith(f"{t.id}:") and (what is None or what in x)]


def test_plain_short_sentences_grade_low_and_dense_text_grades_high():
    assert plainwords.grade(PLAIN) < 6
    assert plainwords.grade(DENSE) > 10


def test_a_field_under_fifteen_words_is_not_graded():
    assert plainwords.grade("Approve the logo.") is None


def test_code_ids_urls_and_quotes_do_not_count_toward_the_grade():
    noisy = PLAIN + "\n\n> " + DENSE + "\n\n`" + DENSE + "`\n\nSee DESK-0253 and https://example.com/a/very/long/path/with/many/parts."
    assert plainwords.grade(noisy) < 6


def test_syllable_heuristic_counts_the_obvious_cases():
    assert [plainwords.syllables(w) for w in ("cat", "table", "make", "reading", "interoperability")] == [1, 2, 1, 2, 8]


def test_validate_warns_on_dense_text_in_each_of_the_three_fields_and_names_ticket_and_field(store):
    t = ops.new(store, "Dense", "desk")
    for name in ("Context", "Outcome"):
        t.set_section(name, DENSE)
    t.set_section("How to validate", "1. " + DENSE)
    store.save(t)
    got = mine(store, t, "reads at grade")
    assert [g.split(":")[1].strip().split(" reads")[0] for g in got] == ["Context", "Outcome", "How to validate"]
    assert all(g.endswith("(rule 15)") for g in got)


def test_the_readability_warning_is_a_warning_never_an_error(store):
    t = ops.new(store, "Dense", "desk")
    t.set_section("Context", DENSE)
    store.save(t)
    errors, warnings = validate_store(store)
    assert not any(t.id in e and "grade" in e for e in errors)
    assert any(t.id in w and "grade" in w for w in warnings)


def test_plain_text_gets_no_warning(store):
    t = ops.new(store, "Plain", "desk")
    t.set_section("Context", PLAIN)
    t.set_section("Outcome", PLAIN)
    store.save(t)
    assert mine(store, t, "grade") == [] and mine(store, t, "plain explanation") == []


def test_listed_jargon_warns_once_per_word_and_field_unless_explained_beside_it(store):
    t = ops.new(store, "Jargon", "desk")
    t.set_section("Context", "This is one slice of the work. Another slice follows. The DoD applies. The ADR is open. We flush the cache.")
    t.set_section("Outcome", "The slice (one small piece of work that fits in a session) is done. The DoD (Definition of Done) is met. An ADR (a short record of one decision) is written.")
    store.save(t)
    ctx = mine(store, t, "plain explanation")
    assert [w.split('"')[1] for w in ctx if "Context" in w] == ["slice", "DoD", "ADR", "flush"]
    assert not any("Outcome" in w for w in ctx)


def test_jargon_inside_code_a_path_or_a_quote_is_not_flagged():
    assert plainwords.jargon("Run `drew slice` and open standards/rules/10-slice-contract.md.\n\n> James: the lane is full") == []
    assert plainwords.jargon("The tool is called in your mouth mode.") == ["in your mouth"]


def test_a_closed_ticket_is_not_read(store):
    t = ops.new(store, "Old", "desk")
    t.set_section("Context", DENSE)
    assert plainwords.warnings(t) != []
    t.meta["status"] = "done"  # not saved: a done ticket needs an approval line, and only the check is under test
    assert plainwords.warnings(t) == []
