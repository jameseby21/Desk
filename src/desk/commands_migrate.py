"""`drew migrate commands`: give every ticket without a `## Commands` section one (DESK-0177).

The section goes right after How to validate and is seeded with the lines of How to validate that look like commands, one bullet in
backticks each; when there are none it holds the same placeholder `drew new` writes. A ticket that already has the heading, empty or not, is
left alone, so running it twice writes nothing the second time. Nothing else in the file changes: `updated` stays, and the file's own line
endings are kept."""

from __future__ import annotations

from . import commands
from .tests_migrate import _spans
from .ticket import SEED_COMMANDS, Ticket


def needs_section(t: Ticket) -> bool:
    return t.path is not None and "Commands" not in [name for name, *_ in _spans(t.path.read_bytes().decode("utf-8"))]


def plan(tickets: list[Ticket]) -> list[tuple[Ticket, list[str]]]:
    return [(t, commands.seeds(t.section("How to validate"))) for t in tickets if needs_section(t)]


def add_to_text(raw: str, found: list[str]) -> str:
    eol = "\r\n" if "\r\n" in raw else "\n"
    body = eol.join(f"- `{c}`" for c in found) if found else SEED_COMMANDS
    spans = _spans(raw)
    after = next((end for name, _s, _h, end in spans if name == "How to validate"), None)
    if after is None:  # no How to validate heading: before the first section that normally follows it
        later = {"Tests", "Prompt", "Docs to update", "Out of scope", "Attachments", "Report", "Evidence", "Decision", "Comments"}
        after = next((start for name, start, _h, _e in spans if name in later), len(raw))
    block = f"## Commands{eol}{body}{eol}{eol}"
    if after >= len(raw):
        return raw.rstrip("\r\n") + eol + eol + block.rstrip("\r\n") + eol
    head = raw[:after]
    if not head.endswith(eol + eol):  # keep one blank line between the sections
        head = head.rstrip("\r\n") + eol + eol
    return head + block + raw[after:]


def apply_file(t: Ticket, found: list[str]) -> bool:
    raw = t.path.read_bytes().decode("utf-8")
    new = add_to_text(raw, found)
    if new == raw:
        return False
    t.path.write_bytes(new.encode("utf-8"))
    return True
