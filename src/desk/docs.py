"""Store documents shown inside the app: path-safe lookup, the glossary, and "Mentioned in" backlinks."""

from __future__ import annotations

import re
from pathlib import Path

from .ticket import Ticket

DOC_DIRS = ("docs", "projects", "intake", "standards", "tickets", "tracks")
PRIVATE_NAMES = {"private_context.md"}
PRIVATE_PARTS = {"jeby-private"}
# A store-relative Markdown path, optionally with a #anchor. Used on rendered text, so it needs a clean left edge.
DOC_RE = re.compile(r"(?<![\w/.:~-])(?:%s)/[^\s)]+?\.md(?:#[-\w]+)?(?![\w/])" % "|".join(DOC_DIRS))
DOC_FULL = re.compile(r"(?:%s)/[^\s)]+\.md(?:#[-\w]+)?" % "|".join(DOC_DIRS))
ID_IN_TEXT = r"(?<![A-Za-z0-9-]){}(?!\d)"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def resolve_doc(home: Path, rel: str) -> Path | None:
    """The Markdown file `rel` inside the store, or None. Only the known folders and top-level .md files are
    reachable; `..`, absolute paths, symlinks out of the store and private files are refused."""
    rel = (rel or "").replace("\\", "/").strip("/")
    if not rel or "\x00" in rel or not rel.lower().endswith(".md"):
        return None
    parts = rel.split("/")
    if any(p in ("", ".", "..") or p.startswith(".") or ":" in p for p in parts):
        return None
    if len(parts) > 1 and parts[0] not in DOC_DIRS:
        return None
    if parts[-1].lower() in PRIVATE_NAMES or any(p.lower() in PRIVATE_PARTS for p in parts):
        return None
    root = Path(home).resolve()
    try:
        target = (root / rel).resolve()
        target.relative_to(root)
    except (ValueError, OSError):
        return None
    return target if target.is_file() else None


def glossary(home: Path) -> dict[str, tuple[str, str]]:
    """{lowercase term: (Term, plain-words definition)} from glossary.md: one `## Term` heading per entry."""
    p = Path(home) / "glossary.md"
    if not p.is_file():
        return {}
    out: dict[str, tuple[str, str]] = {}
    term, buf = None, []

    def flush():
        if term:
            text = " ".join(" ".join(buf).split())
            out[term.lower()] = (term, text)

    for line in p.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            flush()
            term, buf = line[3:].strip(), []
        elif term:
            if line.startswith("#"):
                continue
            buf.append(line)
    flush()
    return out


def body_text(t: Ticket) -> str:
    return "\n".join(t.section(name) for name in t.sections)


def mentioned_in(tickets: list[Ticket], *, ticket_id: str = "", path: str = "") -> list[Ticket]:
    """Tickets whose bodies mention a ticket id or a store-relative path (the ticket itself is left out)."""
    if ticket_id:
        pat = re.compile(ID_IN_TEXT.format(re.escape(ticket_id)))
    elif path:
        pat = re.compile(r"(?<![\w/.-])" + re.escape(path) + r"(?![\w/])")
    else:
        return []
    out = []
    for t in tickets:
        if t.id == ticket_id:
            continue
        if pat.search(body_text(t)):
            out.append(t)
    return sorted(out, key=lambda t: t.id)
