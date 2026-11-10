"""No plain blocks (DESK-0209, standards rule 15-text-formatting.md). James, 2026-11-09: "I don't want to see a plain block of text in any ticket."

The detector is the one the 2026-11-09 store cleanup used, so a store that passed the cleanup stays clean: a prose paragraph over
PARA_CHARS characters, or SENTENCES or more sentences over PROSE_CHARS characters without a bold lead, is a wall of text; so is a list
item over ITEM_CHARS characters. Blockquotes, fenced or indented code, tables and headings never count. Only the free-text sections
(SECTIONS) are read: Acceptance, How to validate, Tests and Commands are parsed lists; Attachments is generated; Decision and
Comments are records.

`drew validate` prints one warning per flagged section; the ticket page shows a small flag beside the section's heading. The files
never change."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .ticket import Ticket

SECTIONS = ("Context", "Outcome", "Prompt", "Out of scope", "Report", "Evidence")
PARA_CHARS = 300  # a prose paragraph this long is a wall whatever its shape
PROSE_CHARS = 200  # ... and one this long with SENTENCES sentences and no bold lead is one too
SENTENCES = 3
ITEM_CHARS = 400  # a list item this long is a wall

_URL = re.compile(r"https?://[^\s)>\]\"'`]+")
_LIST = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_ABBREV = {"e.g", "i.e", "etc", "vs", "no", "st", "dr", "mr", "ms", "mrs", "u.s", "fig", "approx", "inc", "ltd", "jr", "sr", "p", "pp", "cf", "al"}


@dataclass(frozen=True)
class Wall:
    kind: str  # "paragraph" or "bullet"
    chars: int
    sentences: int
    start: str  # the first 80 characters, for a message


def sentence_count(text: str) -> int:
    """Sentences in a block: ends at . ! or ? before a capital, digit, quote or bracket; code spans, URLs, decimals and the usual
    abbreviations (e.g., etc., vs.) do not end a sentence."""
    t = re.sub(r"`[^`]*`", "x", text)
    t = _URL.sub("x", t)
    t = re.sub(r"\d+\.\d+", "n", t)
    parts = re.split(r"(?<=[.!?])[\"')\]*]*\s+(?=[A-Z0-9\"'(\[*`])", t.strip())
    n = 0
    for i, p in enumerate(parts):
        if not p.strip():
            continue
        last = re.findall(r"([A-Za-z.]+)\.$", p.strip())
        if last and last[0].lower().rstrip(".") in _ABBREV and i < len(parts) - 1:
            continue
        n += 1
    return max(n, 1 if t.strip() else 0)


def blocks(section_text: str) -> list[tuple[str, str]]:
    """(kind, text) per block: 'para' (prose lines joined with spaces), 'item' (one list item with its indented continuation lines),
    'quote', 'code', 'table', 'heading'. Blank lines separate paragraphs; a fence is kept whole."""
    lines = section_text.splitlines()
    out: list[tuple[str, str]] = []
    para: list[str] = []
    item: list[str] | None = None

    def flush() -> None:
        nonlocal para, item
        if para:
            out.append(("para", " ".join(x.strip() for x in para)))
            para = []
        if item is not None:
            out.append(("item", " ".join(x.strip() for x in item)))
            item = None

    i = 0
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if s.startswith("```") or s.startswith("~~~"):
            flush()
            fence = s[:3]
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith(fence):
                j += 1
            out.append(("code", "\n".join(lines[i:j + 1])))
            i = j + 1
            continue
        if not s:
            flush()
        elif s.startswith(">"):
            flush()
            out.append(("quote", s))
        elif s.startswith("|"):
            flush()
            out.append(("table", s))
        elif s.startswith("#"):
            flush()
            out.append(("heading", s))
        elif _LIST.match(ln):
            flush()
            item = [_LIST.sub("", ln, count=1)]
        elif item is not None and ln.startswith(("  ", "\t")):
            item.append(s)
        elif ln.startswith("    ") and not para:
            flush()
            out.append(("code", ln))
        else:
            if item is not None:
                flush()
            para.append(s)
        i += 1
    flush()
    return out


def walls(section_text: str) -> list[Wall]:
    """The walls of text in one section's markdown, in order."""
    found: list[Wall] = []
    for kind, text in blocks(section_text or ""):
        if kind == "para":
            n, sc = len(text), sentence_count(text)
            if n > PARA_CHARS or (sc >= SENTENCES and n > PROSE_CHARS and not text.startswith("**")):
                found.append(Wall("paragraph", n, sc, text[:80]))
        elif kind == "item" and len(text) > ITEM_CHARS:
            found.append(Wall("bullet", len(text), sentence_count(text), text[:80]))
    return found


def flagged(t: Ticket) -> dict[str, list[Wall]]:
    """{section name: walls} for the free-text sections of one ticket that hold a wall; an empty dict for a clean ticket."""
    out: dict[str, list[Wall]] = {}
    for name in SECTIONS:
        w = walls(t.section(name))
        if w:
            out[name] = w
    return out


def flag_text(section_text: str) -> str:
    """The words the ticket page shows beside a flagged section's heading, or '' when the section is clean."""
    w = walls(section_text)
    if not w:
        return ""
    longest = max(x.chars for x in w)
    what = "a long bullet" if all(x.kind == "bullet" for x in w) else "a wall of text"
    return f"Readability: {what} ({longest:,} characters). Use a bold lead and bullets."


def warnings(t: Ticket) -> list[str]:
    """`drew validate` lines: one per flagged section, naming the ticket, the section and the longest wall."""
    out = []
    for name, found in flagged(t).items():
        longest = max(x.chars for x in found)
        out.append(f"{t.id}: {name} has a wall of text ({longest:,} characters); use a bold lead and bullets (rule 15)")
    return out
