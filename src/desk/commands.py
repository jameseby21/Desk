"""The `## Commands` section (DESK-0177, standards rule 45): the exact commands that check a ticket, one per bullet in backticks or in
a fenced block. The page shows each with a copy button; `validate` warns when a ready ticket has none and when a ui ticket in review has
no screenshot attached; `drew migrate commands` seeds the section from the How to validate lines that look like commands."""

from __future__ import annotations

import re

from .ticket import SEED_COMMANDS, Ticket, validate_steps

# Programs a validate line may start with. A line that starts with one (or holds a backticked span that does) is a command.
_PROGRAMS = ("drew", "drew-ui", "desk", "uv", "uvx", "pytest", "python", "py", "pip", "git", "gh", "npm", "npx", "node", "curl", "pwsh", "powershell",
             "bash", "sh", "cd", "docker", "make", "cargo", "go", "dotnet", "rg", "grep", "ls", "cat", "tree", "just")
_START = re.compile(r"^(?:\$\s*|PS>\s*)?(?:%s)\b(?![\w-]*\.(?:md|yaml|json)\b)" % "|".join(re.escape(p) for p in sorted(_PROGRAMS, key=len, reverse=True)))
_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_CODE = re.compile(r"`([^`]+)`")
_FENCE = re.compile(r"^\s*(```|~~~)")
_NONE = re.compile(r"^(?:none|n/a|-)\b", re.I)
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]+\)|\bassets/[\w./ -]+\.(?:png|jpe?g|gif|webp)\b", re.I)


def looks_like_command(text: str) -> bool:
    return bool(_START.match(text.strip()))


def _spans(line: str) -> list[str]:
    """Commands on one list line: its backticked spans that look like commands, else the whole line when it starts like one."""
    body = _ITEM.sub("", line).strip()
    spans = [m.group(1).strip() for m in _CODE.finditer(body) if looks_like_command(m.group(1))]
    if spans:
        return spans[:1] if not _START.match(body) else spans  # a sentence that mentions a command names its first one
    if looks_like_command(body):
        return [re.split(r"\s+(?:prints?|shows?|lists?|returns?|says?|reads?)\b", body, maxsplit=1)[0].strip()]
    return []


def seeds(validate_text: str) -> list[str]:
    """The commands among the How to validate lines, in order, without repeats."""
    out: list[str] = []
    in_fence = False
    for ln in validate_steps(validate_text).splitlines():
        if _FENCE.match(ln):
            in_fence = not in_fence
            continue
        found = [ln.strip()] if in_fence and looks_like_command(ln) else _spans(ln) if not in_fence else []
        for c in found:
            if c and c not in out:
                out.append(c)
    return out


def parse(text: str) -> tuple[list[str], list[str]]:
    """(commands, prose lines) of a Commands section: bullets with a backticked command, bullets that start like one, and every line
    of a fenced block are commands; other bullets are notes. The seed sentence and hint comments count as nothing."""
    cmds: list[str] = []
    prose: list[str] = []
    in_fence = False
    for ln in validate_steps(text).splitlines():
        if _FENCE.match(ln):
            in_fence = not in_fence
            continue
        if in_fence:
            if ln.strip():
                cmds.append(ln.strip())
            continue
        if ln.strip() == SEED_COMMANDS:
            continue
        found = _spans(ln)
        if found:
            cmds += found
        elif ln.strip() and not _NONE.match(_ITEM.sub("", ln).strip()):
            prose.append(_ITEM.sub("", ln).strip())
    return cmds, prose


def is_empty(text: str) -> bool:
    """True when the section is missing, blank, only a hint comment, the seed sentence, or `none`: nobody wrote a command or a note."""
    cmds, prose = parse(text)
    return not cmds and not prose


def has_screenshot(t: Ticket) -> bool:
    return bool(_IMAGE.search(t.section("Attachments")))


def warnings(t: Ticket) -> list[str]:
    out = []
    if t.status == "ready" and is_empty(t.sections.get("Commands") or ""):
        out.append(f"{t.id}: ready with no Commands (list the exact commands that check it under `## Commands`, rule 45)")
    if t.status == "review" and "ui" in [str(x) for x in (t.meta.get("dod") or [])] and not has_screenshot(t):
        out.append(f"{t.id}: ui work in review with no screenshot attached (`drew snap`, or paste one in a comment, rule 45)")
    return out
