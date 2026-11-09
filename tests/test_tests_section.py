"""DESK-0143: every ticket has a `## Tests` section (James, 2026-11-08: "ALL FAILURES BECOME TESTS, ALL TICKETS HAVE A 'TESTS' SECTION,
EVEN IF TESTING IS STRICTLY A USER-VISUAL MATTER"). The schema knows it, `drew new` writes it, `drew validate` warns when a ready,
in-progress or review ticket has none, the page shows it under How to validate, and `drew migrate tests` adds it to old tickets."""

import re

import pytest
from fastapi.testclient import TestClient

from desk import ops, review, rules
from desk.cli import main
from desk.tests_migrate import SEED
from desk.ticket import SECTIONS, Ticket
from desk.ui.app import create_app


def _old_ticket(store, title="Old", status="ready", tests=None):
    """A ticket file written before the Tests section existed (no heading at all), or with the given Tests text."""
    t = ops.new(store, title, "jre")
    t.meta["status"] = status
    store.save(t)
    raw = t.path.read_text(encoding="utf-8")
    nl = chr(10)
    pattern = nl + "## Tests" + nl + ".*?" + nl + "(?=" + nl + "## )"
    raw = re.sub(pattern, lambda m: "" if tests is None else nl + "## Tests" + nl + tests + nl, raw, flags=re.S)
    t.path.write_text(raw, encoding="utf-8", newline=nl)
    return t.id


def test_schema_puts_tests_right_after_how_to_validate():
    assert SECTIONS.index("Tests") == SECTIONS.index("Commands") + 1 and SECTIONS.index("Commands") == SECTIONS.index("How to validate") + 1  # Commands sits between (DESK-0177)


def test_a_new_ticket_has_the_section_with_a_hint(store):
    t = ops.new(store, "Fresh", "jre")
    raw = t.path.read_text(encoding="utf-8")
    assert "\n## Tests\n" in raw and raw.index("## How to validate") < raw.index("## Tests") < raw.index("## Prompt")
    assert "<!-- " in store.get(t.id).sections["Tests"] and not store.get(t.id).section("Tests").strip("<!->\n ").startswith("The How")


def test_validate_warns_on_empty_tests_for_ready_in_progress_and_review(store):
    for status in ("ready", "in_progress", "review"):
        t = ops.new(store, f"Needs tests {status}", "jre")
        t.meta["status"] = status
        if status == "review":
            t.set_section("Report", "Did it.")
        store.save(t)
        _, warns = rules.validate_ticket(store.get(t.id), store)
        assert any("Tests" in w for w in warns), (status, warns)
    inbox = ops.new(store, "Inbox is fine", "jre")
    assert not any("Tests" in w for w in rules.validate_ticket(store.get(inbox.id), store)[1])
    done = ops.new(store, "Has tests", "jre")
    done.meta["status"] = "ready"
    done.set_section("Tests", "tests/test_x.py: the thing works")
    store.save(done)
    assert not any("Tests" in w for w in rules.validate_ticket(store.get(done.id), store)[1])
    # words a person performs count too, a hint comment does not
    human = ops.new(store, "Human steps", "jre")
    human.meta["status"] = "ready"
    human.set_section("Tests", "<!-- hint -->\nOpen the page and see the blue button.")
    store.save(human)
    assert not any("Tests" in w for w in rules.validate_ticket(store.get(human.id), store)[1])


def test_migration_dry_run_writes_nothing_then_apply_adds_the_seed(store, capsys):
    a, b = _old_ticket(store, "Old A"), _old_ticket(store, "Old B", tests="tests/test_b.py: b works")
    before = {t.id: t.path.read_bytes() for t in store.tickets()}
    assert "## Tests" not in before[a].decode()
    main(["--home", str(store.home), "migrate", "tests", "--dry-run"])
    out = capsys.readouterr().out
    assert a in out and b not in out and "Dry run" in out and "1 ticket" in out
    assert {t.id: t.path.read_bytes() for t in store.tickets()} == before  # nothing written
    main(["--home", str(store.home), "migrate", "tests", "--apply"])
    capsys.readouterr()
    after = store.get(a)
    assert after.section("Tests") == SEED
    assert str(after.meta["updated"]) == str(Ticket.parse(before[a].decode()).meta["updated"])  # `updated` untouched
    assert store.get(b).section("Tests") == "tests/test_b.py: b works"  # a ticket that has its own text keeps it
    raw = after.path.read_text(encoding="utf-8")
    assert raw.index("## How to validate") < raw.index("## Tests") < raw.index("## Prompt")
    # only the Tests section was added: removing it gives back the original bytes
    assert raw.replace(f"## Tests\n{SEED}\n\n", "") == before[a].decode()


def test_migration_is_idempotent(store, capsys):
    _old_ticket(store)
    main(["--home", str(store.home), "migrate", "tests", "--apply"])
    once = {t.id: t.path.read_bytes() for t in store.tickets()}
    capsys.readouterr()
    main(["--home", str(store.home), "migrate", "tests", "--apply"])
    assert "0 ticket" in capsys.readouterr().out
    assert {t.id: t.path.read_bytes() for t in store.tickets()} == once


def test_migration_keeps_crlf_files_crlf(store, capsys):
    tid = _old_ticket(store)
    p = store.get(tid).path
    p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
    main(["--home", str(store.home), "migrate", "tests", "--apply"])
    raw = p.read_bytes()
    assert b"## Tests\r\n" + SEED.encode() + b"\r\n" in raw and raw.count(b"\n") == raw.count(b"\r\n")


def test_after_migration_validate_has_no_tests_warning(store, capsys):
    _old_ticket(store)
    main(["--home", str(store.home), "migrate", "tests", "--apply"])
    capsys.readouterr()
    with pytest.raises(SystemExit) as e:
        main(["--home", str(store.home), "validate"])
    out = capsys.readouterr().out
    assert e.value.code == 0 and "0 error(s)" in out and "Tests" not in out


def test_the_page_shows_tests_under_how_to_validate(store):
    t = ops.new(store, "Shown", "jre")
    t.set_section("Tests", "tests/test_shown.py: it shows")
    store.save(t)
    page = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    assert page.index('id="validate"') < page.index('id="tests"') and "tests/test_shown.py: it shows" in page


def test_review_week_ticket_lists_open_tickets_whose_tests_are_still_the_seed(store):
    seeded = ops.new(store, "Seeded", "jre")
    seeded.meta["status"] = "ready"
    seeded.set_section("Tests", SEED)
    store.save(seeded)
    own = ops.new(store, "Own tests", "jre")
    own.meta["status"] = "ready"
    own.set_section("Tests", "tests/test_own.py")
    store.save(own)
    t, _ = review.create(store, project="jre", owner="james")
    report = store.get(t.id).section("Report")
    assert "Tests still the seed" in report and seeded.id in report and own.id not in report
