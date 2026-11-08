"""`drew migrate checks`: rewrite the older inline `- [!] text — not accepted: reason` lines into the quoted-reason form
(DESK-0096 wrote the inline form; DESK-0100/0120 settled on `- [!] text`, then `  > Not accepted (must fix): reason` under it).

Reading never needed this (both forms are read), so the command is optional and dry-run first. It touches only legacy `[!]` lines
in Acceptance, How to validate, Prompt and Evidence. Every other byte of the file stays, `updated` included. No who or when is
invented: the old form never recorded them. The severity written is the default, `must fix`, which is what the page already showed."""

from __future__ import annotations

import re

from .ops import CHECK_SECTIONS
from .ticket import DEFAULT_SEVERITY, Ticket, parse_checks, render_check


def _span(raw: str, name: str) -> tuple[int, int] | None:
    m = re.search(rf"^## {re.escape(name)}[ \t]*\n", raw, re.M)
    if not m:
        return None
    nxt = re.search(r"^## ", raw[m.end():], re.M)
    return m.end(), m.end() + nxt.start() if nxt else len(raw)


def _convert(section_text: str) -> tuple[str, list[tuple[str, list[str]]]]:
    """(new section text, [(old line, new lines)]) with only the legacy `[!]` lines rewritten."""
    parts = section_text.split("\n")
    done = []
    for c in reversed(parse_checks(section_text)):  # bottom up, so earlier line numbers stay valid
        if not (c.state == "!" and c.legacy and c.reason):
            continue
        old = parts[c.first:c.last + 1]
        c.legacy, c.severity = False, c.severity or DEFAULT_SEVERITY
        new = render_check(c)
        parts[c.first:c.last + 1] = new
        done.append((old[0], new))
    return "\n".join(parts), list(reversed(done))


def find(t: Ticket) -> list[tuple[str, str, list[str]]]:
    """[(section, old line, new lines)] for the legacy lines in this ticket's file."""
    if t.path is None:
        return []
    raw = t.path.read_bytes().decode("utf-8")
    if "\r" in raw:
        return []  # CRLF files are left alone; `plan` reports them
    out = []
    for name in CHECK_SECTIONS:
        span = _span(raw, name)
        if span:
            out += [(name, old, new) for old, new in _convert(raw[span[0]:span[1]])[1]]
    return out


def has_crlf(t: Ticket) -> bool:
    return t.path is not None and b"\r" in t.path.read_bytes()


def plan(tickets: list[Ticket]) -> list[tuple[Ticket, list[tuple[str, str, list[str]]]]]:
    return [(t, lines) for t in tickets if (lines := find(t))]


def rewrite_file(t: Ticket) -> int:
    """Rewrite the legacy lines in the ticket's file in place; every other byte stays. Returns lines converted."""
    raw = t.path.read_bytes().decode("utf-8")
    if "\r" in raw:
        return 0
    total = 0
    for name in CHECK_SECTIONS:
        span = _span(raw, name)
        if not span:
            continue
        text, done = _convert(raw[span[0]:span[1]])
        if done:
            raw = raw[:span[0]] + text + raw[span[1]:]
            total += len(done)
    if total:
        t.path.write_bytes(raw.encode("utf-8"))
    return total
