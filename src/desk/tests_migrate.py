"""`drew migrate tests`: give every ticket a `## Tests` section (DESK-0143).

A ticket whose Tests section is missing, or has no words in it (a hint comment does not count), gets the seed sentence. Nothing else
in the file changes: `updated` stays, the line endings of the file stay, and a ticket that already has its own Tests text is left
alone, so running it twice writes nothing the second time. In the file the section sits right after How to validate."""

from __future__ import annotations

import re

from .ticket import SEED_TESTS as SEED, Ticket, validate_steps

_HEAD = re.compile(r"^## (.+?)[ \t]*\r?$", re.M)


def needs_tests(t: Ticket) -> bool:
    return not validate_steps(t.sections.get("Tests") or "")


def is_seed(t: Ticket) -> bool:
    return t.section("Tests") == SEED


def plan(tickets: list[Ticket]) -> list[Ticket]:
    return [t for t in tickets if needs_tests(t)]


def _spans(raw: str) -> list[tuple[str, int, int, int]]:
    """(name, start of the heading, end of the heading line, end of the section) for every `## ` heading."""
    heads = list(_HEAD.finditer(raw))
    return [(h.group(1).strip(), h.start(), raw.index("\n", h.start()) + 1 if "\n" in raw[h.start():] else len(raw),
             heads[i + 1].start() if i + 1 < len(heads) else len(raw)) for i, h in enumerate(heads)]


def add_to_text(raw: str) -> str:
    """The file text with the seed in its Tests section: written into an existing empty one, or added as a new section after How to
    validate (before the next heading, else at the end). The file's own line ending is used."""
    eol = "\r\n" if "\r\n" in raw else "\n"
    spans = _spans(raw)
    for name, _start, head_end, _end in spans:
        if name == "Tests":
            return raw[:head_end] + SEED + eol + raw[head_end:]
    after = next((end for name, _s, _h, end in spans if name == "How to validate"), None)
    if after is None:  # no How to validate heading: before the first section that normally follows it
        later = {"Prompt", "Docs to update", "Out of scope", "Attachments", "Report", "Evidence", "Decision", "Comments"}
        after = next((start for name, start, _h, _e in spans if name in later), len(raw))
    block = f"## Tests{eol}{SEED}{eol}{eol}"
    if after >= len(raw):
        return raw.rstrip("\r\n") + eol + eol + block.rstrip("\r\n") + eol
    return raw[:after] + block + raw[after:]


def apply_file(t: Ticket) -> bool:
    raw = t.path.read_bytes().decode("utf-8")
    new = add_to_text(raw)
    if new == raw:
        return False
    t.path.write_bytes(new.encode("utf-8"))
    return True
