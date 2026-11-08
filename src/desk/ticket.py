"""One ticket: YAML frontmatter plus fixed markdown sections."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

SECTIONS = [
    "Context",
    "Outcome",
    "Acceptance",
    "How to validate",  # written before the work starts: the steps that confirm the Outcome once the work is claimed done
    "Prompt",  # what an agent pastes to execute the ticket
    "Docs to update",
    "Out of scope",
    "Attachments",
    "Report",
    "Evidence",  # what proves done: commands, records, commits
    "Decision",
    "Comments",
]
# Sections copied by `desk clone`.
CLONED_SECTIONS = ["Context", "Outcome", "Acceptance", "Prompt"]

TYPES = ["feature", "upgrade", "bug", "task", "docs", "decision", "research", "intake", "release"]
# Old types still read and validate (with a warning); `desk new` never offers them. `desk migrate type` retires them.
LEGACY_TYPES = {"slice": "feature", "chore": "task"}
TYPE_HELP = {
    "feature": "a new capability", "upgrade": "an improvement to something that exists", "bug": "something broken",
    "task": "a thing to do that is not code: paperwork, accounts, a call", "docs": "documentation only",
    "decision": "an ask of a person: approve, choose, answer, direction, provide", "research": "find something out",
    "intake": "material handed in from outside", "release": "ship a version",
}


VALIDATE_HINTS = {
    "feature": "Run it and look: the command or URL, and what you should see.",
    "upgrade": "Run it and look: the command or URL, and what is better than before.",
    "bug": "Reproduce the bug first, then confirm it is gone: the steps and what you should see.",
    "task": "The artifact to open (the account, the form, the note of the call) and what it must show.",
    "decision": "The recorded line to find in Decision, and what it must say.",
    "docs": "The page to read on a clean clone, and what a reader should be able to do after.",
    "research": "The finding to open and what it must answer.",
    "intake": "The intake folder to open and what it must contain.",
    "release": "The version to install or fetch and the command that shows it.",
}
_ONE_COMMENT = re.compile(r"^\s*<!--.*-->\s*$")


def validate_steps(text: str) -> str:
    """The written steps of a How to validate section: hint comments and blank lines do not count."""
    return chr(10).join(ln for ln in (text or "").splitlines() if ln.strip() and not _ONE_COMMENT.match(ln)).strip()


def canon_type(t: str) -> str:
    """Legacy types count as their modern equivalent (chore = task, slice = feature) wherever behaviour depends on type."""
    return LEGACY_TYPES.get(t, t)
STATUSES = [
    "inbox",
    "triage",
    "ready",
    "claimed",
    "in_progress",
    "review",
    "changes_requested",
    "approved",
    "done",
    "blocked",
    "rejected",
]
PRIORITIES = ["p0", "p1", "p2", "p3"]
SIZES = ["xs", "s", "m", "l"]
CLOSED = {"done", "rejected"}

REQUIRED = ["id", "title", "project", "type", "status", "priority", "size", "owner", "created", "updated"]

# Canonical frontmatter order on write.
META_ORDER = [
    "id", "title", "project", "type", "status", "priority", "size", "owner", "assignee", "branch",
    "depends_on", "blocks", "related", "cloned_from", "docs_to_update", "dod", "tags", "effort_hours",
    "confidence", "review_by", "smallest_step", "blocked_on", "blocked_from", "source", "evidence", "track",
    "created", "updated", "done_at",
]
# Optional list-of-ids fields: every id must exist (errors) or should (warnings).
ID_LISTS_STRICT = ["depends_on", "blocks"]
ID_LISTS_LOOSE = ["related"]

_FM = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.S)
_H2 = re.compile(r"^## (.+?)\s*$", re.M)


def today() -> str:
    return dt.date.today().isoformat()


def _plain(v):
    if isinstance(v, dt.datetime):  # done_at keeps its time
        return v.replace(microsecond=0).isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()[:10]
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    return v


_CHECK = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+\[)( |x|X)(\]\s)")


def check_lines(text: str) -> list[tuple[int, bool]]:
    """(line number, checked) for every checkbox line in a section's text, fenced code skipped."""
    out, fence = [], False
    for n, line in enumerate((text or "").split("\n")):
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
        elif not fence:
            m = _CHECK.match(line)
            if m:
                out.append((n, m.group(2) != " "))
    return out


def slugify(text: str, limit: int = 48) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:limit].rstrip("-") or "ticket"


@dataclass
class Ticket:
    meta: dict = field(default_factory=dict)
    sections: dict = field(default_factory=dict)
    path: Path | None = None

    # -- convenience -----------------------------------------------------
    @property
    def id(self) -> str:
        return str(self.meta.get("id", ""))

    @property
    def status(self) -> str:
        return str(self.meta.get("status", ""))

    @property
    def project(self) -> str:
        return str(self.meta.get("project", ""))

    @property
    def is_open(self) -> bool:
        return self.status not in CLOSED

    def section(self, name: str) -> str:
        return (self.sections.get(name) or "").strip()

    def set_section(self, name: str, text: str) -> None:
        self.sections[name] = text.strip() + "\n" if text.strip() else ""

    def append_line(self, name: str, line: str) -> None:
        cur = self.section(name)
        self.sections[name] = (cur + "\n" if cur else "") + line.rstrip() + "\n"

    @property
    def tags(self) -> list[str]:
        v = self.meta.get("tags")
        return [str(x) for x in v] if isinstance(v, list) else []

    def validate_steps(self) -> str:
        return validate_steps(self.section("How to validate"))

    def acceptance(self) -> list[tuple[bool, str]]:
        out = []
        for line in self.section("Acceptance").splitlines():
            m = re.match(r"^\s*[-*]\s*\[( |x|X)\]\s*(.*)$", line)
            if m:
                out.append((m.group(1).lower() == "x", m.group(2)))
        return out

    # -- (de)serialisation ------------------------------------------------
    @classmethod
    def parse(cls, text: str, path: Path | None = None) -> "Ticket":
        m = _FM.match(text)
        if not m:
            raise ValueError(f"{path or 'ticket'}: no YAML frontmatter")
        meta = yaml.safe_load(m.group(1)) or {}
        if not isinstance(meta, dict):
            raise ValueError(f"{path or 'ticket'}: frontmatter is not a mapping")
        meta = {k: _plain(v) for k, v in meta.items()}
        body = m.group(2)
        sections: dict = {}
        heads = list(_H2.finditer(body))
        for i, h in enumerate(heads):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
            sections[h.group(1).strip()] = body[h.end():end].strip("\n") + ("\n" if body[h.end():end].strip() else "")
        return cls(meta=meta, sections=sections, path=path)

    def render(self) -> str:
        ordered = {k: self.meta[k] for k in META_ORDER if k in self.meta}
        for k, v in self.meta.items():
            if k not in ordered:
                ordered[k] = v
        fm = yaml.safe_dump(_plain(ordered), sort_keys=False, allow_unicode=True, default_flow_style=None, width=1000).rstrip("\n")
        fm = re.sub(r"^(\w+): '(\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2})?)'$", r"\1: \2", fm, flags=re.M)  # dates unquoted, as people write them
        parts = [f"---\n{fm}\n---\n"]
        names = list(SECTIONS) + [s for s in self.sections if s not in SECTIONS]
        for name in names:
            body = self.section(name)
            parts.append(f"\n## {name}\n" + (body + "\n" if body else ""))
        return "".join(parts)

    @classmethod
    def load(cls, path: Path) -> "Ticket":
        return cls.parse(path.read_text(encoding="utf-8"), path)

    def write(self, path: Path | None = None) -> Path:
        p = path or self.path
        if p is None:
            raise ValueError("ticket has no path")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.render(), encoding="utf-8", newline="\n")
        self.path = p
        return p


def new_ticket(ticket_id: str, title: str, project: str, type_: str = "feature", *, priority: str = "p2",
               size: str = "s", owner: str = "main", dod: list[str] | None = None, source: str = "",
               assignee: str | None = None) -> Ticket:
    now = today()
    meta = {
        "id": ticket_id,
        "title": title,
        "project": project,
        "type": type_,
        "status": "inbox",
        "priority": priority,
        "size": size,
        "owner": owner,
        "depends_on": [],
        "docs_to_update": [],
        "dod": dod or ["core"],
        "source": source,
        "created": now,
        "updated": now,
    }
    if assignee:
        meta["assignee"] = assignee
    t = Ticket(meta=meta, sections={s: "" for s in SECTIONS})
    hint = VALIDATE_HINTS.get(canon_type(type_), VALIDATE_HINTS["feature"])
    t.sections["How to validate"] = f"<!-- Write this before the work starts. {hint} -->" + chr(10)
    return t
