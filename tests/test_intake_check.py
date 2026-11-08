"""drew intake --check: the ticket-ready table of a SLICES file, row by row."""

import pytest

from desk import cli, intake_check, ops

HEADER = ("| # | Project | Title | Type | Size | Owner | Assignee | Ask · by | Acceptance | How to validate | Depends on | Source |\n"
          "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n")

CLEAN = "# Slices\n\nIntro text.\n\n" + HEADER + (
    "| LILY-01 | desk | Add the bench | feature | m | main | main | none | `drew bench` works at 1k \\| 10k | Run `drew bench --tickets 1000` and read the table | none | SPEC 3.4 |\n"
    "| LILY-02 | jre | Choose the colour | decision | xs | james | james | choose · 2026-11-14 | Decision recorded | Open the ticket; the Decision section holds the line | LILY-01, DESK-0001 | this entry |\n"
    "| LILY-03 | desk | Wire it up | upgrade | s | main | main | - | It is wired | Open the page | LILY\\-02 | SPEC |\n")

FLAWED = "# Slices\n\n" + HEADER + (
    "| F-01 | nosuch | Bad project | feature | s | main | main | none | ok | ok | none | x |\n"
    "| F-02 | desk | Bad type | gadget | s | main | main | none | ok | ok | none | x |\n"
    "| F-03 | desk | Bad size | feature | huge | main | main | none | ok | ok | none | x |\n"
    "| F-04 | desk | Bad ask | decision | xs | james | james | whenever | ok | ok | none | x |\n"
    "| F-05 | desk | Decision without ask | decision | xs | james | james | none | ok | ok | none | x |\n"
    "| F-06 | desk | Missing after | feature | s | main | main | none | ok | ok | F-99, DESK-9999 | x |\n"
    "| F-07 | desk | No acceptance | feature | s | main | main | none |  | ok | none | x |\n"
    "| F-08 | desk | No validate | feature | s | main | main | none | ok |  | none | x |\n"
    "| F-09 | desk | Short row | feature | s | main | main | none | ok | ok |\n"
    "| F-09 | desk | Repeated id | feature | s | main | main | none | ok | ok | none | x |\n")

NO_COLUMNS = "| # | Project | Title | Type |\n| --- | --- | --- | --- |\n| 1 | desk | x | feature |\n"


@pytest.fixture
def seeded(store):
    ops.new(store, "Existing", "desk")  # DESK-0001
    return store


def problems_for(store, text):
    rows, problems = intake_check.check_text(text, store)
    return rows, [(p.row, p.column) for p in problems], " ".join(p.message for p in problems)


def test_clean_file_has_no_problems_and_unescapes_drive_pipes(seeded):
    rows, problems, _ = problems_for(seeded, CLEAN)
    assert rows == 3 and problems == []
    assert intake_check.check_text(CLEAN, seeded)[0] == 3
    cells = intake_check.parse_tables(CLEAN)[0][1][0][1]
    assert "1k | 10k" in cells[8]


def test_each_check_names_its_row(seeded):
    rows, problems, msg = problems_for(seeded, FLAWED)
    assert rows == 9
    got = set(problems)
    assert ("F-01", "Project") in got and "nosuch" in msg
    assert ("F-02", "Type") in got
    assert ("F-03", "Size") in got
    assert ("F-04", "Ask · by") in got
    assert ("F-05", "Ask · by") in got
    assert {("F-06", "Depends on")} <= got and "F-99" in msg and "DESK-9999" in msg
    assert ("F-07", "Acceptance") in got
    assert ("F-08", "How to validate") in got
    assert ("F-09", "row") in got
    assert ("F-09", "#") in got


def test_after_ids_may_be_rows_in_the_file_or_tickets_in_the_store(seeded):
    ok = "| X-1 | desk | One | feature | s | m | m | none | a | b | none | s |\n| X-2 | desk | Two | feature | s | m | m | none | a | b | X-1, DESK-0001 | s |\n"
    assert problems_for(seeded, HEADER + ok)[1] == []


def test_missing_required_columns_and_no_table(seeded):
    _, problems, msg = problems_for(seeded, NO_COLUMNS)
    assert ("-", "Owner") in problems and ("-", "How to validate") in problems
    assert problems_for(seeded, "no tables here")[1] == [("-", "table")]


def test_cli_exit_codes_and_report_file(seeded, tmp_path, capsys):
    flawed = tmp_path / "SLICES.md"
    flawed.write_text(FLAWED, encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        cli.main(["--home", str(seeded.home), "intake", "--check", str(flawed)])
    assert e.value.code == 1
    report = (tmp_path / "SLICES.md.check.md").read_text(encoding="utf-8")
    assert "| F-04 | Ask · by |" in report and "| F-01 | Project |" in report
    assert "F-01" in capsys.readouterr().out
    clean = tmp_path / "CLEAN.md"
    clean.write_text(CLEAN, encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        cli.main(["--home", str(seeded.home), "intake", "--check", str(clean)])
    assert e.value.code == 0
    assert "Clean" in (tmp_path / "CLEAN.md.check.md").read_text(encoding="utf-8")
    assert not list((seeded.home / "intake").iterdir())  # --check imports nothing
