"""Display-time profanity filter: keeps the first and last letter, stars the middle (f*****g).

Files stay raw; this runs where people read (the UI, `drew show`). Code spans and fenced code are never touched.
The word list ships here; a store may add its own in `<store>/profanity-extra.txt` (one word per line, `#` comments).
Switch it off per person with `[style] profanity_filter = false` in `.desk/settings.toml` or on /settings."""

from __future__ import annotations

import re
from pathlib import Path

# Stems; the filter also catches s, es, ed, ing, er, ers, y, in' after a stem. Whole words only, so nothing inside other words is touched.
WORDS = (
    "fuck", "motherfuck", "shit", "bullshit", "bitch", "bastard", "asshole", "arsehole", "dickhead", "cunt",
    "piss", "wank", "twat", "prick", "douche", "damn", "goddamn", "crap", "bollocks",
)
EXTRA_FILE = "profanity-extra.txt"
_SUFFIX = r"(?:s|es|ed|ing|er|ers|y|in)?"
_FENCE = re.compile(r"^\s*(```|~~~)")
_CODE_SPAN = re.compile(r"(`+)(.+?)\1", re.S)


def extra_words(home: Path | None) -> list[str]:
    """Additions from the store's profanity-extra.txt; a missing or unreadable file adds nothing."""
    if home is None:
        return []
    try:
        lines = (Path(home) / EXTRA_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        w = ln.split("#", 1)[0].strip().lower()
        if w and re.fullmatch(r"[a-z0-9'-]{2,40}", w):
            out.append(w)
    return out


def censor_word(word: str) -> str:
    """First and last letter kept, the middle replaced with asterisks. A word of two letters or fewer is returned unchanged."""
    return word if len(word) <= 2 else word[0] + "*" * (len(word) - 2) + word[-1]


def make_filter(home: Path | None = None):
    """A function str -> str that censors plain text. Does not look for markdown; see censor_markdown for that."""
    words = sorted(set(WORDS) | set(extra_words(home)), key=len, reverse=True)
    rx = re.compile(r"(?<![A-Za-z0-9])(?:%s)%s(?![A-Za-z0-9])" % ("|".join(re.escape(w) for w in words), _SUFFIX), re.I)

    def censor(text: str) -> str:
        return rx.sub(lambda m: censor_word(m.group(0)), text) if text else text

    return censor


def censor_markdown(text: str, censor) -> str:
    """Censor markdown source for plain display: fenced code and `code spans` are left exactly as written."""
    out, fence = [], None
    for line in (text or "").split("\n"):
        m = _FENCE.match(line)
        if m and (fence is None or m.group(1) == fence):
            fence = None if fence else m.group(1)
            out.append(line)
        elif fence:
            out.append(line)
        else:
            parts, pos = [], 0
            for sp in _CODE_SPAN.finditer(line):
                parts.append(censor(line[pos:sp.start()]))
                parts.append(sp.group(0))
                pos = sp.end()
            parts.append(censor(line[pos:]))
            out.append("".join(parts))
    return "\n".join(out)
