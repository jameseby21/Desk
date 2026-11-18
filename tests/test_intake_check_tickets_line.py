"""DESK-0165: `drew intake --check` refuses an entry that proposes or delivers work and does not name its tickets.
Protects: Chaz's print entry said `Tickets: Em's artwork ticket (id TBD)` and Em's doc had four `id: TBD` tickets; every item and
deliverable must reach a ticket (rule 25), so the check says no and says the fix."""

import pytest

from desk import cli, intake_check, ops

HEADER = ("Date: 2026-11-08 · From: Chaz · To: Patch · Expires: 2026-11-15\n"
          "Supersedes: none\n")

PRINT_ENTRY = HEADER + "Tickets: Em's artwork ticket (id TBD)\n\nChaz's print files are ready.\n\n## Deliverables\n- print/poster.pdf\n- print/flyer.pdf\n"

QUESTION_ONLY = HEADER + "Tickets: none\n\nJames asked which of the two greens we use. Answer: the lime, #C8F03C.\n"

TABLE = ("| # | Project | Title | Type | Size | Owner | Assignee | Ask · by | Acceptance | How to validate | Depends on | Source |\n"
         "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
         "| C-01 | desk | Print the poster | task | s | chaz | chaz | none | The poster is printed | Open the PDF | none | this entry |\n")


@pytest.fixture
def seeded(store):
    ops.new(store, "Existing", "desk")
    return store


def _problems(store, text):
    return intake_check.check_text(text, store)[1]


def test_deliverables_with_a_tbd_tickets_line_is_an_error_naming_the_rule_and_the_fix(seeded):
    (p,) = _problems(seeded, PRINT_ENTRY)
    assert p.error and "says TBD" in p.message and "Deliverables" in p.message
    assert "Rule 25 (Every item has a ticket)" in p.message and "ask Patch for the ticket id first" in p.message


def test_a_question_only_entry_may_say_none(seeded):
    assert _problems(seeded, QUESTION_ONLY) == []


def test_a_ticket_table_with_an_empty_or_none_tickets_line_is_an_error(seeded):
    for line, why in (("Tickets:", "is empty"), ("Tickets: none", "says none"), ("Tickets: -", "says none"), ("Tickets: soon", "names no ticket id")):
        text = HEADER + line + "\n\n" + TABLE
        errs = [p for p in _problems(seeded, text) if p.column == "Tickets:"]
        assert len(errs) == 1 and why in errs[0].message and "a ticket table" in errs[0].message, (line, errs)
    missing = [p for p in _problems(seeded, HEADER + "\n" + TABLE) if p.column == "Tickets:"]
    assert len(missing) == 1 and "is missing" in missing[0].message


def test_real_ids_pass_with_a_table_or_deliverables(seeded):
    assert _problems(seeded, HEADER + "Tickets: CHAZ-0001, DESK-0002\n\n" + TABLE) == []
    assert _problems(seeded, PRINT_ENTRY.replace("Em's artwork ticket (id TBD)", "BRAND-0007 and BRAND-0008")) == []
    assert [p for p in _problems(seeded, PRINT_ENTRY.replace("(id TBD)", "BRAND-0007 (id TBD)")) if p.column == "Tickets:"]  # one real id does not excuse a TBD


def test_a_slices_file_without_an_entry_header_is_checked_as_before(seeded):
    assert intake_check.check_text("# Slices\n\nNo table here.\n", seeded)[1][0].column == "table"
    assert [p for p in _problems(seeded, "# Slices\n\n" + TABLE) if p.column == "Tickets:"] == []


def test_cli_exit_codes_on_the_three_fixtures(seeded, tmp_path, capsys):
    cases = {"print.md": (PRINT_ENTRY, 1), "question.md": (QUESTION_ONLY, 0), "table.md": (HEADER + "Tickets: none\n\n" + TABLE, 1)}
    for name, (text, code) in cases.items():
        f = tmp_path / name
        f.write_text(text, encoding="utf-8")
        with pytest.raises(SystemExit) as e:
            cli.main(["--home", str(seeded.home), "intake", "--check", str(f)])
        assert e.value.code == code, name
        out = capsys.readouterr().out
        assert ("Rule 25" in out) == (code == 1), name
        assert f.with_name(name + ".check.md").is_file()
