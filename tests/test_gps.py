"""DESK-0215: `drew validate` scores Goal, Proof and Steps per ticket (James's GPS rule, 2026-11-09, the `gps` skill). G: the Outcome opens
with one sentence of at most 30 words (after an optional **Goal.** marker or bold lead); P: an Acceptance line and a How to validate line
that names something observable; S: a Prompt with steps and no handwaving word. A project's `gps:` block overrides the defaults.
Protects: a vague ticket ("improve the board as needed") reaching a worker with no word said, and a project that wants a longer goal or
a screenshot as proof being held to the defaults."""

from desk import gps, ops
from desk.rules import validate_store

GOOD_GOAL = "**Goal.** `drew validate` names every ticket whose Outcome, Proof or Prompt leaves the reader guessing."
LONG_GOAL = "**Goal.** " + " ".join(f"word{i}" for i in range(31)) + "."
GOOD_STEPS = "**Steps.**\n\n1. Implement the check in `gps.py`\n2. Read the overrides from the project profile\n3. Write the tests"


def _ticket(store, *, outcome=GOOD_GOAL, acceptance="- [ ] Warnings name the ticket and the letter", validate="- Run `drew validate`: it prints the GPS line", prompt=GOOD_STEPS):
    t = ops.new(store, "A GPS ticket", "desk")
    t.set_section("Outcome", outcome)
    t.set_section("Acceptance", acceptance)
    t.set_section("How to validate", validate)
    t.set_section("Prompt", prompt)
    store.save(t)
    ops.move(store, t, "ready", actor="main")  # GPS scores tickets that have left triage
    return store.get(t.id)


def _gps(store, t):
    return [w for w in validate_store(store)[1] if w.startswith(f"{t.id}: GPS fails")]


def test_a_ticket_in_gps_shape_passes(store):
    t = _ticket(store)
    assert gps.score(t) == {}
    assert _gps(store, t) == []


def test_g_fails_when_the_outcome_has_no_sentence_or_the_goal_is_over_30_words(store):
    empty = _ticket(store, outcome="")
    assert "G" in gps.score(empty) and "no one-sentence Goal" in gps.score(empty)["G"]
    listed = _ticket(store, outcome="- a bullet, not a sentence")
    assert "G" in gps.score(listed)
    long = _ticket(store, outcome=LONG_GOAL)
    assert gps.score(long)["G"] == "Goal sentence is 31 words (limit 30); one sentence, or two tickets"
    (line,) = _gps(store, long)
    assert line == f"{long.id}: GPS fails G: Goal sentence is 31 words (limit 30); one sentence, or two tickets"


def test_g_reads_the_first_sentence_after_a_goal_marker_or_a_bold_lead():
    assert gps.goal_sentence("**Goal.** Desk warns. More words follow in a second sentence that is not counted.") == "Desk warns."
    assert gps.goal_sentence("**Walls of text are caught where they are written.**\n\n- bullets after") == "Walls of text are caught where they are written."
    assert gps.goal_sentence("Plain first sentence, e.g. this one. Second.") == "Plain first sentence, e.g. this one."
    assert gps.goal_sentence("Use `drew validate` in 1.2 form. Then more.") == "Use x in n form."
    assert gps.goal_sentence("") is None


def test_p_fails_without_an_acceptance_line_or_an_observable_validate_line(store):
    no_acc = _ticket(store, acceptance="Looks good when done.")
    assert gps.score(no_acc)["P"] == "no Acceptance checkbox line"
    no_val = _ticket(store, validate="<!-- Write this before the work starts. -->")
    assert gps.score(no_val)["P"] == "no How to validate line"
    vague = _ticket(store, validate="- It works and feels right.")
    assert gps.score(vague)["P"] == "How to validate names nothing observable (run, open, prints, shows, lists, returns, see)"
    (line,) = _gps(store, vague)
    assert line.startswith(f"{vague.id}: GPS fails P: How to validate names nothing observable")
    for ok in ("- Open /board and see the column", "- `drew list` prints one row", "- The page shows the flag", "- `drew next` returns DESK-0001"):
        assert "P" not in gps.score(_ticket(store, validate=ok)), ok


def test_s_fails_on_an_empty_prompt_or_a_handwaving_word(store):
    empty = _ticket(store, prompt="")
    assert gps.score(empty)["S"] == "Prompt is empty; write the numbered steps"
    waving = _ticket(store, prompt="1. Handle the edge cases as needed\n2. Clean up the rest, etc.\n3. Make sure it works")
    assert gps.score(waving)["S"] == 'Prompt handwaves ("handle", "clean up", "as needed", "etc.", "make sure it works"); name the action and its object'
    assert gps.handwaving_in("Steps that handle `as needed` inside code are fine; the word handles outside is not") == ["handle"]
    assert gps.handwaving_in("TBD later") == ["TBD"] and gps.handwaving_in("Relevance is a different word") == []


def test_all_three_letters_on_one_line_in_the_agreed_words(store):
    t = _ticket(store, outcome="", acceptance="", prompt="")
    (line,) = _gps(store, t)
    assert line == (f"{t.id}: GPS fails G, P and S: Outcome has no one-sentence Goal; open it with **Goal.** and one sentence; "
                    "no Acceptance checkbox line; Prompt is empty; write the numbered steps")


def test_inbox_triage_and_closed_tickets_are_not_scored(store):
    t = _ticket(store, outcome="", prompt="")
    assert _gps(store, t)  # ready: scored
    for status in ("inbox", "triage", "done", "rejected"):
        t.meta["status"] = status
        t.set_section("Decision", "2026-11-10 · approved · james · fine")
        store.save(t)
        assert _gps(store, t) == [], status


def test_a_project_profile_gps_block_overrides_the_defaults(store):
    (store.projects_dir / "desk.md").write_text(
        "---\nkey: desk\nname: Desk\ngps:\n  goal_max_words: 40\n  proof_requires: [observable, screenshot]\n  extra_handwaving: [tweak, polish]\n  allow: [\"as needed\"]\n---\n# Desk\n",
        encoding="utf-8")
    st = gps.settings(store, "desk")
    assert st.goal_max_words == 40 and st.proof_requires == ("observable", "screenshot")
    assert "as needed" not in st.handwaving and st.handwaving[-2:] == ("tweak", "polish") and "etc." in st.handwaving
    long = _ticket(store, outcome=LONG_GOAL, prompt="1. Tweak the spacing as needed")
    (line,) = _gps(store, long)
    assert line == f"{long.id}: GPS fails P and S: no screenshot named in How to validate, Evidence or Attachments; Prompt handwaves (\"tweak\"); name the action and its object"
    shot = _ticket(store, outcome=LONG_GOAL, validate="- Open /board and see the column; screenshot attached")
    assert _gps(store, shot) == []
    assert gps.settings(store, "jre") == gps.Settings()  # another project keeps the defaults


def test_a_malformed_gps_block_keeps_the_defaults():
    assert gps.settings_from("nonsense") == gps.Settings()
    assert gps.settings_from({"goal_max_words": "lots", "proof_requires": [], "allow": None}) == gps.Settings()
    assert gps.settings_from({"proof_requires": "command, release"}).proof_requires == ("command", "release")
