"""DESK-0259: every sign-off (a comment whose words start `Sign-off (`) with its verification state. Only `verified` counts."""

from __future__ import annotations

from . import identity
from .ops import _COMMENT_LINE, comment_identity, comment_lines, split_comment_kind

PREFIX = "Sign-off ("
VERIFIED, UNVERIFIED, SUSPECTED = "verified", "unverified", "impersonation suspected"


def state_of(ticket_id: str, when: str, author: str, rest: str, env=None) -> str:
    info = comment_identity(rest)
    words = split_comment_kind(rest)[1]
    if info.get("impersonation") == "suspected":
        return SUSPECTED
    if info.get("via") == "verified" and info.get("ok") == "1" and identity.check_signature(author, ticket_id, when, words, info.get("sig", ""), env):
        return VERIFIED
    return UNVERIFIED


def rows(store, env=None) -> list[tuple[str, str, str, str]]:
    """(ticket id, when, author, state) for every sign-off in the store."""
    out = []
    for t in store.tickets():
        for ln in comment_lines(t):
            m = _COMMENT_LINE.match(ln)
            if m and split_comment_kind(m.group(4))[1].startswith(PREFIX):
                author = m.group(3).strip()
                out.append((t.id, f"{m.group(1)} {m.group(2)}", author, state_of(t.id, f"{m.group(1)} {m.group(2)}", author, m.group(4), env)))
    return out
