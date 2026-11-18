"""Plain words (DESK-0253, standards rule 15 "Human-readable by default"). James, 2026-11-11: explanations "follow human-readability
standards"; this is the check behind it. `drew validate` warns, never errors, when the Context, Outcome or How to validate text of an
open ticket reads above grade 10, or uses a jargon word with no plain gloss in the same field.

Grade is the Flesch-Kincaid grade level, 0.39 * words per sentence + 11.8 * syllables per word - 15.59, with a simple syllable
count (vowel groups, minus a silent final e). It is an estimate: code spans, fenced code, URLs, ticket ids, blockquotes (James's own
words are kept verbatim) and tables are left out, and a field under MIN_WORDS words is not graded. A list item counts as a sentence.
Docs: docs/COMMANDS.md (`drew validate`)."""

from __future__ import annotations

import re

from .ticket import Ticket

FIELDS = ("Context", "Outcome", "How to validate")
MAX_GRADE = 10  # rule 15: grade 8 for explanations, 10 at most for technical detail
MIN_WORDS = 15  # a shorter field has too little text for a grade to mean anything
JARGON = ("bone", "lane", "slice", "DoD", "fixture", "ADR", "flush", "fetched", "in your mouth")  # rule 15, point 4

_FENCE = re.compile(r"^\s*(```|~~~)")
_CODE_SPAN = re.compile(r"`[^`\n]*`")
_URL = re.compile(r"https?://\S+")
_PATH = re.compile(r"\b\S*[/\\]\S*\b")
_ID = re.compile(r"\b[A-Z][A-Z0-9]{1,}-\d{2,}\b")
_MARKUP = re.compile(r"[*_#>|]+|^\s*(?:[-+]|\d+[.)])\s+|\[[ xX]\]")
_SENT_END = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[A-Za-z][A-Za-z']*")


def prose(text: str) -> list[str]:
    """The sentences a person reads in a field's markdown: one per list item or line, split again at . ! or ?; code, URLs, ids, quotes and tables dropped."""
    out: list[str] = []
    fenced = False
    for ln in (text or "").splitlines():
        if _FENCE.match(ln):
            fenced = not fenced
            continue
        s = ln.strip()
        if fenced or not s or s.startswith((">", "|", "<!--")):
            continue
        s = _URL.sub(" ", s)
        s = _CODE_SPAN.sub(" ", s)
        s = _ID.sub(" ", s)
        s = _PATH.sub(" ", s)
        s = _MARKUP.sub(" ", s)
        for part in _SENT_END.split(s):
            if _WORD.search(part):
                out.append(part.strip())
    return out


def syllables(word: str) -> int:
    """A rough count: groups of vowels (y included), one fewer for a silent final e (not -le), never fewer than one."""
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    n = len(re.findall(r"[aeiouy]+", w))
    if w.endswith("e") and not w.endswith(("le", "ee", "ye")) and n > 1:
        n -= 1
    return max(n, 1)


def grade(text: str) -> float | None:
    """The Flesch-Kincaid grade of a field, or None when it has under MIN_WORDS words."""
    sents = prose(text)
    words = [w for s in sents for w in _WORD.findall(s)]
    if len(words) < MIN_WORDS or not sents:
        return None
    syl = sum(syllables(w) for w in words)
    return 0.39 * len(words) / len(sents) + 11.8 * syl / len(words) - 15.59


def _glossed(text: str, end: int) -> bool:
    """A jargon word is explained when a bracket, "means", "stands for" or a colon follows it right away."""
    return bool(re.match(r"\s*(\(|:|=|,\s*(i\.e\.|meaning|that is)|\s(means|stands for)\b)", text[end:end + 40], re.I))


def jargon(text: str) -> list[str]:
    """The listed jargon words used in a field with no gloss beside them. Code spans, paths and quotes are not read."""
    seen: list[str] = []
    clean = "\n".join(s for s in text.splitlines() if not s.strip().startswith(">"))
    clean = _CODE_SPAN.sub(lambda m: " " * len(m.group()), clean)
    clean = _URL.sub(lambda m: " " * len(m.group()), clean)
    clean = _PATH.sub(lambda m: " " * len(m.group()), clean)
    for term in JARGON:
        for m in re.finditer(rf"(?<![\w-]){re.escape(term)}(?:s|es)?(?![\w-])", clean, re.I):
            if not _glossed(clean, m.end()):
                seen.append(term)
                break
    return seen


def warnings(t: Ticket) -> list[str]:
    """`drew validate` lines for an open ticket: one per field above MAX_GRADE, one per field per unexplained jargon word. Never an error."""
    if not t.is_open:  # a closed ticket is a record; its wording is not edited for a score
        return []
    out = []
    for name in FIELDS:
        text = t.validate_steps() if name == "How to validate" else t.section(name)
        g = grade(text)
        if g is not None and g > MAX_GRADE:
            out.append(f"{t.id}: {name} reads at grade {g:.1f} (aim for {MAX_GRADE} or lower); use shorter sentences and everyday words (rule 15)")
        for term in jargon(text):
            out.append(f"{t.id}: {name} uses \"{term}\" with no plain explanation beside it; explain it in brackets or use a plain word (rule 15)")
    return out
