"""Refuse text that looks like a secret value (standards rule 80: never store or print one). Deliberately narrow: well-known key
shapes, `password = value` style assignments, and one long unbroken token that mixes letters and digits. A git commit hash
(hex only) and ordinary words and paths pass."""

from __future__ import annotations

import re

_SHAPES = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "a private key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "an AWS access key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), "a GitHub token"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "an API key"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), "a Slack token"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "a login token"),
    (re.compile(r"(?i)\b(pass(word|wd)?|pwd|secret|api[_ -]?key|token)\b\s*[:=]\s*[^\s,;]{6,}"), "a password or key"),
)
_LONG = re.compile(r"(?<![\w/.\\-])[A-Za-z0-9+_=-]{32,}(?![\w/\\])")


def looks_secret(text: str) -> str | None:
    """What the text looks like ('a private key', ...) or None."""
    for rx, what in _SHAPES:
        if rx.search(text or ""):
            return what
    for m in _LONG.finditer(text or ""):
        tok = m.group(0)
        if re.fullmatch(r"[0-9a-fA-F]+", tok):
            continue  # a commit or file hash
        if tok.count("-") >= 3:
            continue  # a ticket file name or a kebab-case slug, not a key
        if re.search(r"[A-Za-z]", tok) and re.search(r"\d", tok) and len(set(tok)) > 12:
            return "a key or token"
    return None


def refusal(what: str) -> str:
    return (f"That looks like {what}. Desk does not store secrets, so nothing was saved. "
            "Say where it lives instead (for example: the key in the password manager) and send it again.")
