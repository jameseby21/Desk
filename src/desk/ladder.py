"""The text formatting ladder, as the ticket page and `drew validate` apply it (DESK-0139, standards rule 15-text-formatting.md).

Longer text, stricter shape. On the page a field of up to 600 characters and six lines is shown as written. Longer, it is shown as its
first block or two (up to six lines: a lead sentence and a few bullets) and the rest is folded under a one-line label. One long
paragraph is a wall of text: its lead sentence is set in bold, a readability flag is raised, and the rest is folded. Over 4,000
characters the field should be a Markdown file under intake/ or the project's docs, linked from the field. The files never change;
this only decides what the screen opens with.

`validate` warns on a wall over 1,200 characters (`readability`) and on a field over 4,000 (`save it as a markdown file and link it`)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .ticket import Ticket

SHOW_CHARS = 600  # a field this short (and within SHOW_LINES) is shown as written
SHOW_LINES = 6
LIST_HEAD = 4  # items of a long first list shown before the fold
WALL = 1200
SAVE = 4000
FIELDS = ("Context", "Outcome", "Report")  # the sections the ladder reads; Comments, Decision and the check lists are records or live

_LIST = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_FENCE = re.compile(r"^\s*(```|~~~)")
_LEAD = re.compile(r"(.{40,}?[.!?])\s+(?=[A-Z0-9`\"'(\[])", re.S)


@dataclass
class Fold:
    head: str
    rest: str = ""
    label: str = ""
    flags: list[str] = field(default_factory=list)


def _is_list(block: list[str]) -> bool:
    return bool(block) and bool(_LIST.match(block[0]))


def blocks(text: str) -> list[list[str]]:
    """Lines grouped into blocks: paragraphs (split at blank lines), runs of list items, and fenced code kept whole."""
    out: list[list[str]] = []
    cur: list[str] = []
    in_fence = False
    for ln in text.strip().splitlines():
        if in_fence:
            cur.append(ln)
            if _FENCE.match(ln):
                in_fence = False
                out.append(cur)
                cur = []
            continue
        if _FENCE.match(ln):
            if cur:
                out.append(cur)
            cur, in_fence = [ln], True
            continue
        if not ln.strip():
            if cur:
                out.append(cur)
                cur = []
            continue
        if cur:
            item, cur_list, indented = bool(_LIST.match(ln)), bool(_LIST.match(cur[0])), ln.startswith((" ", "\t"))
            if (item and not cur_list) or (not item and cur_list and not indented):
                out.append(cur)  # a list starting, or a paragraph starting after a list
                cur = []
        cur.append(ln)
    if cur:
        out.append(cur)
    return out


def _words(text: str) -> int:
    return len(text.split())


def _join(bs: list[list[str]]) -> str:
    return "\n\n".join("\n".join(b) for b in bs)


def shape(text: str) -> Fold:
    t = (text or "").strip()
    if not t:
        return Fold("")
    bs = blocks(t)
    n = len(t)
    lines = sum(len(b) for b in bs)
    wall = len(bs) == 1 and not _is_list(bs[0]) and not any(_FENCE.match(x) for x in bs[0]) and len(bs[0]) <= 3
    flags = (["wall"] if n > WALL and wall else []) + (["save"] if n > SAVE else [])
    if n <= SHOW_CHARS and lines <= SHOW_LINES:
        return Fold(t, flags=flags)
    if wall:  # one long paragraph: the lead sentence in bold, the rest folded
        m = _LEAD.match(" ".join(t.split()))
        flat = " ".join(t.split())
        lead = m.group(1) if m else flat[:160].rsplit(" ", 1)[0] + "..."
        rest = flat[len(lead):].strip() if m else flat[len(lead) - 3:].strip()
        return Fold(f"**{lead}**", rest, f"Show the rest ({_words(rest)} words)", flags)
    head: list[list[str]] = []
    rest_bs: list[list[str]] = []
    budget = SHOW_LINES
    for b in bs:
        if rest_bs:
            rest_bs.append(b)
        elif len(b) <= budget:
            head.append(b)
            budget -= len(b)
        elif not head and not any(_FENCE.match(x) for x in b):  # the first block alone is too long: show its start
            take = LIST_HEAD if _is_list(b) else SHOW_LINES
            head.append(b[:take])
            rest_bs.append(b[take:])
        elif not head:
            rest_bs.append(b)  # a long code block first: fold it whole
        else:
            rest_bs.append(b)
    items = sum(len(b) for b in rest_bs if _is_list(b))
    n_rest = sum(len(b) for b in rest_bs)
    if not rest_bs or not n_rest:
        return Fold(_join(head), flags=flags)
    if items and items == n_rest or (items and _is_list(rest_bs[0])):
        label = f"{items} more point{'s' if items != 1 else ''}"
    else:
        label = f"Show all ({n_rest} more line{'s' if n_rest != 1 else ''})"
    return Fold(_join(head) or "_(a long block, folded)_", _join(rest_bs), label, flags)


def warnings(t: Ticket) -> list[str]:
    """`validate` lines for the fields of one ticket that break the ladder."""
    out = []
    for name in FIELDS:
        text = t.section(name)
        f = shape(text)
        if "save" in f.flags:
            out.append(f"{t.id}: {name} is {len(text):,} characters; save it as a markdown file (intake/ or the project's docs) and link it, keeping a three-line summary (rule 15)")
        elif "wall" in f.flags:
            out.append(f"{t.id}: readability: {name} is {len(text):,} characters in one block; open with a lead sentence and use a list (rule 15)")
    return out
