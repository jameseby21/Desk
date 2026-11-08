"""`desk migrate acceptance`: strip the boilerplate Acceptance lines that restate Definition of Done checks.

Done-ness comes from the Definition of Done, by reference; a ticket's Acceptance holds only that ticket's own outcome.
A line goes when its text equals a known boilerplate line, or starts with it and only adds a generic ` in ...` tail with
nothing ticket-specific in it (no digits, backticks, slashes). Everything else stays. Only the one section changes."""

from __future__ import annotations

import re

from .ticket import Ticket

BOILERPLATE = (
    "Proof lists the commands that ran, where, and what was not run",
    "No secret value printed, saved or sent",
    "Evidence attached (screenshot or recording)",
    "Reduced motion respected",
    "Works in light and dark",
    "James confirms the direction",
    "Motion, look and feature tickets linked",
    "Decision recorded",
    "Decision recorded in DECISIONS.md",
    "Answer recorded",
)
_LINE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\[( |x|X|!)\]\s*(.*?)\s*$")
_SPECIFIC = re.compile(r"[0-9`/\\]")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().rstrip(".").strip().lower()


def is_boilerplate(text: str) -> bool:
    t = _norm(text)
    for b in BOILERPLATE:
        b = b.lower()
        if t == b:
            return True
        if t.startswith(b + " in ") and not _SPECIFIC.search(t[len(b):]):
            return True
    return False


def _section_span(text: str) -> tuple[int, int] | None:
    m = re.search(r"^## Acceptance[ \t]*\r?\n", text, re.M)
    if not m:
        return None
    nxt = re.search(r"^## ", text[m.end():], re.M)
    return m.end(), m.end() + nxt.start() if nxt else len(text)


def find_lines(t: Ticket) -> list[tuple[str, str]]:
    """[(state, text)] of the boilerplate lines in this ticket's Acceptance."""
    out, fence = [], False
    for line in (t.sections.get("Acceptance") or "").split("\n"):
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
        elif not fence and (m := _LINE.match(line)) and is_boilerplate(m.group(2)):
            out.append((m.group(1).lower(), m.group(2)))
    return out


def plan(tickets: list[Ticket]) -> list[tuple[Ticket, list[tuple[str, str]]]]:
    return [(t, lines) for t in tickets if (lines := find_lines(t))]


def strip_file(t: Ticket) -> int:
    """Remove the boilerplate lines from the ticket's file; every other byte stays, `updated` included. Returns lines removed."""
    raw = t.path.read_bytes().decode("utf-8")
    span = _section_span(raw)
    if not span:
        return 0
    a, b = span
    keep, removed, fence = [], 0, False
    for line in raw[a:b].splitlines(keepends=True):
        body = line.rstrip("\r\n")
        if body.lstrip().startswith(("```", "~~~")):
            fence = not fence
        elif not fence and (m := _LINE.match(body)) and is_boilerplate(m.group(2)):
            removed += 1
            continue
        keep.append(line)
    if removed:
        t.path.write_bytes((raw[:a] + "".join(keep) + raw[b:]).encode("utf-8"))
    return removed
