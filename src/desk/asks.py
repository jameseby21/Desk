"""What a decision ticket asks of its owner, read from the ticket: the kind (approve, choose, answer or provide) from the title's
first word, and the options from the `Options:` line of Context. The UI draws the reply box from this (DESK-0138)."""

from __future__ import annotations

import re

from .ticket import Ticket, canon_type

KINDS = ("approve", "choose", "answer", "provide")
_LEAD = re.compile(r"\s*(approve|choose|answer|provide)\b", re.I)
_OPTIONS = re.compile(r"^\s*Options\s*:\s*(.+?)\s*$", re.I | re.M)
_LETTER = re.compile(r"^([A-Z])\b[\s:.)\-]*(.+)$")


def parse_options(context: str) -> list[tuple[str, str]]:
    """[(label, text)] from a line like `Options: A final; B not final, draw another.` Without letters the whole piece is the label
    (`Options: yes; new names.`). Past-tense lines ("Options were: ...") are history and are not offered."""
    m = _OPTIONS.search(context or "")
    if not m:
        return []
    out = []
    for piece in re.split(r"\s*;\s*", m.group(1).strip().rstrip(".")):
        piece = piece.strip()
        if not piece:
            continue
        lm = _LETTER.match(piece)
        out.append((lm.group(1), lm.group(2).strip()) if lm else (piece, piece))
    return out if len(out) >= 2 else []


def ask_of(t: Ticket) -> dict | None:
    """{'kind', 'options'} for a decision ticket, else None. A `choose` with nothing to choose between is an `answer`."""
    if canon_type(t.meta.get("type")) != "decision":
        return None
    m = _LEAD.match(str(t.meta.get("title") or ""))
    options = parse_options(t.section("Context"))
    kind = m.group(1).lower() if m else ("choose" if options else "answer")
    if kind == "choose" and not options:
        kind = "answer"
    return {"kind": kind, "options": options if kind == "choose" else []}


def words_for(ask: dict, option: str, note: str) -> str:
    """The person's decision as one sentence: the option they picked (by index into the options) and/or their own line."""
    note = " ".join((note or "").split())
    if option.strip() == "":
        return note
    try:
        label, text = ask["options"][int(option)]
    except (ValueError, IndexError):
        raise ValueError("That option is not on this ticket. Reload the page and pick again.") from None
    base = f"Chose {label}: {text}" if label != text else f"Chose: {text}"
    return f"{base}. {note}" if note else base + "."
