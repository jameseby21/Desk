"""DESK-0120 (Chaz R4): the three states live in the ticket file as `[ ]`, `[x]` and `[!]` with a quoted reason; both the old inline
form and the new block are read; a read-then-write round trip changes no byte; `drew migrate checks` is dry-run first."""

import re
from pathlib import Path

import pytest

from desk import check_migrate, ops
from desk.cli import main
from desk.rules import validate_ticket
from desk.ticket import Ticket

# five fixtures: A plain (only [ ] and [x]), B and C legacy (the inline form), D the new block, E mixed with nesting, numbers and code
FIXTURES = {
    "A": {"Acceptance": "- [ ] open one\n- [x] done one\n  - [ ] a nested open\n- [x] done two\n"},
    "B": {"Acceptance": "- [x] fine\n- [!] wrong colour — not accepted: The button is blue\n- [!] tried it\n- [ ] later\n"},
    "C": {"Acceptance": "- [ ] one\n", "How to validate": "- [!] open the page — not accepted: Nothing there\n- [x] run it\n",
          "Evidence": "- [!] screenshot — not accepted: It is the wrong page\n"},
    "D": {"Acceptance": ("- [x] done  <!-- by james 2026-11-08T03:40 -->\n"
                         "- [!] README  <!-- by james 2026-11-08T03:41 -->\n"
                         "  > Not accepted (should): The README lists 12 commands.\n"
                         "  > Evidence: assets/DESK-0082/2026-11-08-0341-run.png\n"
                         "- [ ] open\n  > Evidence: assets/DESK-0082/a.png\n")},
    "E": {"Acceptance": ("1. [!] numbered — not accepted: Legacy in a numbered list\n"
                         "2. [!] new form  <!-- by em 2026-11-07T09:00 -->\n   > Not accepted (nit): tiny\n"
                         "- [x] done\n  - [!] nested — not accepted: Nested legacy\n"
                         "```\n- [!] inside a fence — not accepted: never touched\n```\n"
                         "- [ ] last\n")},
}
LEGACY = {"B": 1, "C": 2, "E": 2}  # inline-form lines that `migrate checks` rewrites (the bare `[!] tried it` has no reason to move)


def _write(store, key: str) -> Path:
    t = ops.new(store, f"Fixture {key}", "jre")
    for name, body in FIXTURES[key].items():
        t.sections[name] = body
    store.save(t)
    return t.path


@pytest.fixture
def files(store):
    return {k: _write(store, k) for k in FIXTURES}


def _bytes(files):
    return {k: p.read_bytes() for k, p in files.items()}


def test_round_trip_changes_no_byte_on_five_fixtures(files):
    """Protects: reading a ticket and writing it back leaves every byte alone, for plain, legacy, new-format and mixed files."""
    for k, p in files.items():
        before = p.read_bytes()
        t = Ticket.load(p)
        assert t.render().encode("utf-8") == before, k
        t.write()
        assert p.read_bytes() == before, k


def test_toggling_one_line_changes_only_that_lines_block(store, files):
    """Protects: a single toggle rewrites that check line (and its quoted lines) and nothing else in the file."""
    from desk.ticket import parse_checks

    for k, idx in (("A", 0), ("B", 1), ("B", 2), ("D", 1), ("D", 2), ("E", 0), ("E", 3)):
        t = Ticket.load(files[k])
        block = parse_checks(t.sections["Acceptance"])[idx]
        before = files[k].read_text(encoding="utf-8").split("\n")
        start = next(i for i, ln in enumerate(before) if ln == t.sections["Acceptance"].split("\n")[block.first])  # block start in the file
        ops.toggle_check(store, t.id, "Acceptance", idx)  # flips open <-> done, or takes a Not accepted line to done
        after = files[k].read_text(encoding="utf-8").split("\n")
        head = next((i for i, (x, y) in enumerate(zip(before, after)) if x != y), len(before))
        tail = next((i for i, (x, y) in enumerate(zip(before[::-1], after[::-1])) if x != y), len(before))
        assert start <= head and len(before) - tail <= start + (block.last - block.first) + 1, (k, idx, head, tail)


def test_a_plain_file_has_the_same_bytes_after_an_untouched_save(store, files):
    t = Ticket.load(files["A"])
    store.save(t, touch=False)
    assert files["A"].read_bytes() == _bytes(files)["A"]


def test_both_forms_read_the_same_way():
    from desk.ticket import parse_checks

    old = parse_checks("- [!] wrong — not accepted: The button is blue")[0]
    new = parse_checks("- [!] wrong\n  > Not accepted (must fix): The button is blue")[0]
    assert (old.text, old.reason, old.state) == (new.text, new.reason, new.state) == ("wrong", "The button is blue", "!")
    assert old.legacy and not new.legacy


def test_validate_accepts_every_fixture(store, files):
    for k, p in files.items():
        errors, _ = validate_ticket(Ticket.load(p), store)
        assert errors == [], (k, errors)


def test_validate_warns_about_the_inline_form_and_never_errors(store, files):
    warns = {k: validate_ticket(Ticket.load(p), store)[1] for k, p in files.items()}
    assert any("inline `not accepted`" in w and "drew migrate checks" in w for w in warns["B"]) and not any("inline" in w for w in warns["A"] + warns["D"])


def test_validate_command_prints_zero_errors(store, files, capsys):
    with pytest.raises(SystemExit) as ex:
        main(["--home", str(store.home), "validate"])
    assert ex.value.code == 0 and re.search(r"\b0 error\(s\)", capsys.readouterr().out)


def test_migrate_dry_run_writes_nothing_and_says_what_it_would_do(store, files, capsys):
    before, mtimes = _bytes(files), {k: p.stat().st_mtime_ns for k, p in files.items()}
    main(["--home", str(store.home), "migrate", "checks", "--dry-run"])
    out = capsys.readouterr().out
    assert "- [!] wrong colour — not accepted: The button is blue" in out and "+   > Not accepted (must fix): The button is blue" in out
    assert f"{sum(LEGACY.values())} inline `not accepted` lines in {len(LEGACY)} tickets" in out and "Dry run: nothing written" in out
    assert _bytes(files) == before and {k: p.stat().st_mtime_ns for k, p in files.items()} == mtimes


def test_migrate_apply_rewrites_only_legacy_lines_and_is_idempotent(store, files, capsys):
    before = _bytes(files)
    main(["--home", str(store.home), "migrate", "checks", "--apply"])
    capsys.readouterr()
    after = _bytes(files)
    for k in ("A", "D"):  # no legacy lines: not one byte moved
        assert after[k] == before[k], k
    b = files["B"].read_text(encoding="utf-8")
    assert "- [!] wrong colour\n  > Not accepted (must fix): The button is blue\n" in b and "- [!] tried it\n- [ ] later" in b  # bare line kept
    assert "updated: 2026-" in b and re.search(r"^updated: ", b, re.M)
    e = files["E"].read_text(encoding="utf-8")
    assert "1. [!] numbered\n   > Not accepted (must fix): Legacy in a numbered list\n" in e
    assert "  - [!] nested\n    > Not accepted (must fix): Nested legacy\n" in e
    assert "- [!] inside a fence — not accepted: never touched" in e  # code fences are not checkboxes
    assert "2. [!] new form  <!-- by em 2026-11-07T09:00 -->\n   > Not accepted (nit): tiny" in e  # new form untouched
    # only the legacy lines differ from before; same frontmatter, same other sections
    for k in ("B", "C", "E"):
        old_lines, new_lines = before[k].decode().split("\n"), after[k].decode().split("\n")
        assert len(new_lines) - len(old_lines) == LEGACY[k]
        assert [ln for ln in old_lines if ln not in new_lines] == [ln for ln in old_lines if "— not accepted:" in ln and "fence" not in ln]
    # reading the migrated files gives the same reasons as before, and a second run finds nothing
    from desk.ticket import parse_checks

    assert [c.reason for c in parse_checks(Ticket.load(files["B"]).sections["Acceptance"])] == ["", "The button is blue", "", ""]
    assert check_migrate.plan([Ticket.load(p) for p in files.values()]) == []
    main(["--home", str(store.home), "migrate", "checks", "--apply"])
    assert "Rewrote 0 lines in 0 tickets" in capsys.readouterr().out
    assert _bytes(files) == after
    for k, p in files.items():  # and every file still round-trips
        t = Ticket.load(p)
        assert t.render().encode() == p.read_bytes(), k


def test_crlf_files_are_skipped_and_named(store, files, capsys):
    p = files["B"]
    p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
    main(["--home", str(store.home), "migrate", "checks", "--apply"])
    out = capsys.readouterr().out
    assert "CRLF" in out and b"\xe2\x80\x94 not accepted" in p.read_bytes()
