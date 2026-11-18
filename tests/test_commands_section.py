"""DESK-0177: a ticket carries a `## Commands` section right after How to validate. `drew new` writes it; `drew migrate commands` adds it
to existing tickets (seeded from How to validate lines that look like commands); `drew validate` warns on a ready ticket whose Commands
is empty and on a ui ticket in review with no screenshot attached; the ticket page shows each command with a copy button.
Protects: James's rule that a good ticket has how to validate, the commands, and screenshots (rule 45)."""

import re

import pytest
from fastapi.testclient import TestClient

from desk import commands, ops
from desk.cli import main
from desk.rules import validate_store
from desk.ticket import SECTIONS, SEED_COMMANDS, new_ticket
from desk.ui.app import create_app


def run(store, *argv):
    main(["--home", str(store.home), *argv])


def _warns(store):
    return validate_store(store)[1]


# -- schema -------------------------------------------------------------------------------------------------------------------

def test_commands_is_a_section_right_after_how_to_validate():
    assert SECTIONS.index("Commands") == SECTIONS.index("How to validate") + 1


def test_drew_new_writes_the_commands_section_after_how_to_validate(store, capsys):
    run(store, "new", "--project", "desk", "--type", "feature", "A thing")
    path = capsys.readouterr().out.strip().splitlines()[-1]
    text = open(path, encoding="utf-8").read()
    heads = re.findall(r"^## (.+)$", text, re.M)
    assert heads[heads.index("How to validate") + 1] == "Commands"
    fresh = new_ticket("DESK-0001", "A thing", "desk").render()  # the real template (the tests' fixture fills Commands for the other rules' tests)
    assert f"## Commands\n{SEED_COMMANDS}\n\n## Tests" in fresh
    assert fresh.index("## How to validate") < fresh.index("## Commands") < fresh.index("## Tests")


# -- what counts as a command ---------------------------------------------------------------------------------------------------

def test_looks_like_a_command_and_seeding_from_how_to_validate():
    steps = ("- `drew validate` prints `0 error(s)`\n- open http://127.0.0.1:8765/about and read the first paragraph\n"
             "1. uv run --no-sync pytest -q tests/test_x.py prints 3 passed\n- `git log --oneline -1` in desk/ shows a commit newer than 0017e60\n"
             "- the file tickets/ALGO-0002-x.md has one line under Comments\n")
    assert commands.seeds(steps) == ["drew validate", "uv run --no-sync pytest -q tests/test_x.py", "git log --oneline -1"]
    assert commands.seeds("- open the page and look\n") == []
    assert commands.is_empty("") and commands.is_empty(SEED_COMMANDS) and commands.is_empty("<!-- a hint -->\n") and commands.is_empty("- none\n")
    assert not commands.is_empty("- `drew validate`")


def test_parse_splits_commands_from_prose_and_reads_fenced_blocks():
    cmds, prose = commands.parse("- `drew validate`\n- run `uv run pytest -q` before the report\n- Runs on Windows only.\n\n```\ngit status\ngit diff --stat\n```\n")
    assert cmds == ["drew validate", "uv run pytest -q", "git status", "git diff --stat"] and prose == ["Runs on Windows only."]


# -- drew migrate commands ----------------------------------------------------------------------------------------------------------

def _raw(store, title, validate, commands_text=None, eol="\n"):
    t = ops.new(store, title, "desk")
    t.sections["How to validate"] = validate
    t.write()
    text = t.path.read_text(encoding="utf-8")
    if commands_text is None:  # a file from before the schema: no Commands heading at all
        text = re.sub(r"\n## Commands\n.*?(?=\n## )", "", text, flags=re.S)
    else:
        text = re.sub(r"(\n## Commands\n).*?(?=\n## )", lambda m: m.group(1) + commands_text + "\n", text, flags=re.S)
    t.path.write_bytes(text.replace("\n", eol).encode("utf-8"))
    return t.path


def test_migrate_commands_dry_run_writes_nothing_then_apply_adds_the_section_seeded(store, capsys):
    a = _raw(store, "Old with commands", "- `drew validate` prints 0 errors\n- open the page\n")
    b = _raw(store, "Old without", "- open the page and look\n")
    c = _raw(store, "Already has it", "- `drew list`\n", commands_text="- `drew list --all`\n")
    before = {p: p.read_bytes() for p in (a, b, c)}
    run(store, "migrate", "commands", "--dry-run")
    out = capsys.readouterr().out
    assert "Old with commands" in out and "drew validate" in out and "Old without" in out and "Already has it" not in out
    assert "2 ticket(s)" in out and "Dry run" in out and {p: p.read_bytes() for p in (a, b, c)} == before
    run(store, "migrate", "commands", "--apply")
    capsys.readouterr()
    ta, tb = a.read_text(encoding="utf-8"), b.read_text(encoding="utf-8")
    assert re.search(r"## How to validate\n.*?\n\n## Commands\n- `drew validate`\n\n## ", ta, re.S)
    assert f"## Commands\n{SEED_COMMANDS}\n" in tb
    assert c.read_bytes() == before[c]  # an existing section is never touched
    again = {p: p.read_bytes() for p in (a, b, c)}
    run(store, "migrate", "commands", "--apply")
    assert {p: p.read_bytes() for p in (a, b, c)} == again and "0 ticket(s)" in capsys.readouterr().out  # running it twice writes nothing


def test_migrate_commands_keeps_the_updated_date_and_the_line_endings(store, capsys):
    p = _raw(store, "Windows file", "- `drew validate`\n", eol="\r\n")
    before = p.read_bytes()
    run(store, "migrate", "commands", "--apply")
    after = p.read_bytes()
    assert b"\r\n" in after and b"\n" not in after.replace(b"\r\n", b"")  # every line ending is still CRLF
    assert re.search(rb"updated: \S+", before).group(0) == re.search(rb"updated: \S+", after).group(0)
    assert after.replace(b"\r\n", b"\n").replace(b"## Commands\n- `drew validate`\n\n", b"") == before.replace(b"\r\n", b"\n")


# -- drew validate -----------------------------------------------------------------------------------------------------------------

def test_validate_warns_on_a_ready_ticket_with_empty_commands(store):
    t = ops.new(store, "Ready one", "desk")
    t.meta["status"] = "ready"
    t.meta["assignee"] = "main"
    t.sections["Commands"] = SEED_COMMANDS
    store.save(t)
    assert any(t.id in w and "no Commands" in w for w in _warns(store))
    t.sections["Commands"] = "- `drew validate`"
    store.save(t)
    assert not any(t.id in w and "Commands" in w for w in _warns(store))
    t.meta["status"] = "inbox"
    t.sections["Commands"] = ""
    store.save(t)
    assert not any(t.id in w and "Commands" in w for w in _warns(store))  # only a ready ticket is asked for them


def test_validate_warns_on_a_ui_ticket_in_review_with_no_screenshot(store):
    t = ops.new(store, "Look", "desk", dod=["core", "ui"])
    t.meta.update(status="review", assignee="main")
    t.sections["Report"] = "Ran it."
    t.sections["Commands"] = "- `drew validate`"
    store.save(t)
    assert any(t.id in w and "screenshot" in w for w in _warns(store))
    t.sections["Attachments"] = "![the board](assets/X/2026-11-08-board.png)"
    store.save(t)
    assert not any(t.id in w and "screenshot" in w for w in _warns(store))
    plain = ops.new(store, "No ui", "desk", dod=["core"])
    plain.meta.update(status="review", assignee="main")
    plain.sections["Report"] = "Ran it."
    store.save(plain)
    assert not any(plain.id in w and "screenshot" in w for w in _warns(store))


# -- the ticket page ---------------------------------------------------------------------------------------------------------------

def test_the_page_shows_each_command_with_a_copy_button_and_hides_an_empty_section(store):
    t = ops.new(store, "Has commands", "desk")
    t.sections["Commands"] = "- `drew validate`\n- run `uv run --no-sync pytest -q` first\n- Windows only.\n"
    store.save(t)
    page = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    assert page.index('id="validate"') < page.index('id="commands"')
    rows = re.findall(r'<div class="cmd"><code>([^<]*)</code><button type="button" class="btn small copy" data-copy="([^"]*)"', page)
    assert rows == [("drew validate", "drew validate"), ("uv run --no-sync pytest -q", "uv run --no-sync pytest -q")]
    assert "Windows only." in page and "/static/copy.js" in page
    bare = ops.new(store, "No commands", "desk")
    bare.sections["Commands"] = SEED_COMMANDS
    store.save(bare)
    page = TestClient(create_app(store.home)).get(f"/ticket/{bare.id}").text
    assert 'id="commands"' not in page.split('id="hidden-sections"')[0]  # an empty section is not in the flow, only behind the fold
    assert re.search(r"Hidden while still default or empty: [^<]*Commands", page)
