"""Names of people as they appear in comments. `james`, `James`, `james1` and `james_` are one person (DESK-0142, DESK-0144)."""

from __future__ import annotations

import re

_TAIL = re.compile(r"[\s_\-.0-9]+$")


def norm_name(name: str) -> str:
    """Lowercase, punctuation out, a trailing digit or underscore dropped: `James_` and `james1` both become `james`."""
    n = re.sub(r"[^\w\s\-.]", "", (name or "").casefold()).strip()
    stripped = _TAIL.sub("", n)
    return stripped or n


def same_person(a: str, b: str) -> bool:
    return norm_name(a) == norm_name(b)
