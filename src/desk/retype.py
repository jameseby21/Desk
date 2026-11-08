"""`desk migrate type`: retire the legacy `slice` and `chore` types by rule. First match wins; only legacy tickets move."""

from __future__ import annotations

import re
from collections import Counter

from .ticket import LEGACY_TYPES, Ticket

KEEP = {"decision", "research", "intake", "bug"}
DECISION_PREFIXES = ("Approve:", "Choose:", "Answer:", "Provide:", "Direction:")
UPGRADE_PREFIXES = ("200% motion", "200% look", "200% feature", "Brand:", "Style pass", "Readability")


def classify(t: Ticket) -> str:
    """The type a ticket should have. Tickets that are not legacy slice or chore keep theirs."""
    cur = str(t.meta.get("type") or "")
    if cur in KEEP or cur not in LEGACY_TYPES:
        return cur
    title = str(t.meta.get("title") or "")
    tags = set(t.tags)
    if title.startswith(DECISION_PREFIXES):
        return "decision"
    if title.startswith("Docs:") or tags & {"docs", "quickstart"}:
        return "docs"
    if "release" in tags or title.startswith("Release"):
        return "release"
    if title.startswith("Do:"):
        return "task"
    if cur == "chore":
        return "task"
    if title.startswith(UPGRADE_PREFIXES) or "upgrade" in tags:
        return "upgrade"
    if "Bug" in title:
        return "bug"
    return "feature"


def plan(tickets: list[Ticket]) -> list[tuple[Ticket, str]]:
    return [(t, new) for t in tickets if t.meta.get("type") in LEGACY_TYPES and (new := classify(t)) != t.meta.get("type")]


def counts(pairs: list[tuple[Ticket, str]]) -> list[tuple[str, int]]:
    c = Counter(new for _, new in pairs)
    return sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))


def write_type(t: Ticket, new: str) -> bool:
    """Rewrite the one `type:` line in the frontmatter; nothing else changes, `updated` included."""
    text = t.path.read_bytes().decode("utf-8")
    m = re.match(r"(---\r?\n)(.*?)(\r?\n---\r?\n)", text, re.S)
    if not m:
        return False
    fm, n = re.subn(r"^type:.*$", f"type: {new}", m.group(2), count=1, flags=re.M)
    if not n:
        return False
    t.path.write_bytes((m.group(1) + fm + m.group(3) + text[m.end():]).encode("utf-8"))
    return True
