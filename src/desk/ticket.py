"""One ticket: YAML frontmatter plus fixed markdown sections."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import yamlio

SECTIONS = [
    "Context",
    "Outcome",
    "Acceptance",
    "How to validate",  # written before the work starts: the steps that confirm the Outcome once the work is claimed done
    "Commands",  # the exact commands that check it, one per bullet in backticks; a worker adds the ones it ran (DESK-0177, rule 45)
    "Tests",  # the automated test that proves it, or the exact steps a person performs and what they must see (all failures become tests)
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
# What a section holds while nobody has written anything of their own into it (DESK-0175): the page hides it until they do.
SEED_COMMANDS = "- none yet (a worker adds the exact commands it ran)"
SEED_TESTS = "The How to validate lines, performed by a person, are the test until an automated one exists."
SECTION_DEFAULTS = {
    "Tests": SEED_TESTS,
    "Docs to update": "- per the project's doc-ownership map",
    "Out of scope": "- Anything not named in Acceptance.",
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
    "id", "title", "project", "type", "status", "priority", "size", "creator", "owner", "assignee", "associated", "assigned_at", "assigned_by", "handoff", "branch",
    "depends_on", "blocks", "related", "parent", "cloned_from", "split_from", "docs_to_update", "dod", "tags", "effort_hours",
    "confidence", "review_by", "deadline", "deadline_kind", "smallest_step", "blocked_on", "blocked_from", "source", "evidence", "track", "sprint",
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


_CHECK = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+\[)( |x|X|!)(\]\s)")
# Three states in the file: `[ ]` open, `[x]` done, `[!]` not accepted. A `[!]` line carries its reason on the same line.
NOT_ACCEPTED = re.compile(r"\s+— not accepted:\s*(.*?)\s*$")


def split_reason(line: str) -> tuple[str, str]:
    """(line without the ` — not accepted: <reason>` suffix, reason or '')."""
    m = NOT_ACCEPTED.search(line)
    return (line[:m.start()], m.group(1)) if m else (line, "")


def check_states(text: str) -> list[tuple[int, str]]:
    """(line number, state char: ' ', 'x' or '!') for every checkbox line in a section's text, fenced code skipped."""
    out, fence = [], False
    for n, line in enumerate((text or "").split("\n")):
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
        elif not fence:
            m = _CHECK.match(line)
            if m:
                out.append((n, "x" if m.group(2) in "xX" else m.group(2)))
    return out


SEVERITIES = ("must fix", "should", "nit")  # shown Must fix, Should fix, Minor (James, 2026-11-08: "Nit" is not human-readable)
DEFAULT_SEVERITY = "must fix"
MIN_REASON = 3  # non-space characters

# The three states in the file (Chaz, DESK-0100; DESK-0120):
#   - [ ] open
#   - [x] done
#   - [!] not accepted  <!-- by james 2026-11-08T03:41 -->
#     > Not accepted (must fix): the quoted reason, one line
#     > Evidence: assets/DESK-0082/2026-11-08-0341-run.png
# `> Evidence:` lines may follow a line in any state. The older inline form `- [!] text — not accepted: reason` is still read.
_CHECK_LINE = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+\[)( |x|X|!)(\]\s)(.*)$")
_BYLINE = re.compile(r"\s*<!--\s*by\s+(.+?)\s+(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})\s*-->\s*$")
_QUOTE = re.compile(r"^(\s+)>\s?(.*)$")
_NA_QUOTE = re.compile(r"^Not accepted(?:\s*\(([^)]*)\))?\s*:\s*(.*)$", re.I)
_EV_QUOTE = re.compile(r"^Evidence:\s*(.+?)\s*$", re.I)


@dataclass
class Check:
    """One checkbox line and the quoted lines under it. `first`/`last` are line numbers in the section text (inclusive)."""

    first: int
    last: int
    head: str  # everything up to and including `[`
    state: str  # ' ', 'x' or '!'
    mid: str  # `] ` as written
    text: str  # the line's words: no byline comment, no legacy ` — not accepted:` suffix
    who: str = ""
    when: str = ""  # 2026-11-08T03:41
    severity: str = ""  # '' when the file did not say (the legacy form)
    reason: str = ""
    evidence: list = field(default_factory=list)
    extra: list = field(default_factory=list)  # other quoted lines, kept as written
    legacy: bool = False  # the reason was on the line itself

    @property
    def cont(self) -> str:
        """Indent of the quoted lines: the width of the list marker, so they belong to this list item."""
        return " " * (len(self.head) - 1)


def reason_ok(reason: str) -> bool:
    return len("".join((reason or "").split())) >= MIN_REASON


def parse_checks(text: str) -> list[Check]:
    """Every checkbox line in a section's text with the quoted reason and evidence lines under it; fenced code is skipped."""
    lines = (text or "").split("\n")
    out, fence, i = [], False, 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
            i += 1
            continue
        m = None if fence else _CHECK_LINE.match(line)
        if not m:
            i += 1
            continue
        head, ch, mid, body = m.groups()
        c = Check(i, i, head, "x" if ch in "xX" else ch, mid, body.rstrip())
        b = _BYLINE.search(c.text)
        if b:
            c.who, c.when, c.text = b.group(1), b.group(2), c.text[:b.start()].rstrip()
        if c.state == "!":
            c.text, legacy = split_reason(c.text)
            if legacy:
                c.reason, c.legacy = legacy, True
        indent = len(line) - len(line.lstrip())
        j = i + 1
        while j < len(lines) and (q := _QUOTE.match(lines[j])) and len(q.group(1)) > indent:
            body_q = q.group(2)
            na, ev = _NA_QUOTE.match(body_q), _EV_QUOTE.match(body_q)
            if c.state == "!" and na and not c.reason:
                sev = (na.group(1) or "").strip().lower()
                c.severity, c.reason = (sev if sev in SEVERITIES else ""), na.group(2).strip()
            elif ev:
                c.evidence.append(ev.group(1))
            else:
                c.extra.append(lines[j])
            j += 1
        c.last = j - 1
        out.append(c)
        i = j
    return out


def check_counts(text: str) -> dict:
    """{"done", "no", "open", "total"} for the checkbox lines in a section's text (nested lines count; fenced code does not)."""
    checks = parse_checks(text)
    n = {"done": 0, "no": 0, "open": 0}
    for c in checks:
        n[{"x": "done", "!": "no", " ": "open"}[c.state]] += 1
    return {**n, "total": len(checks)}


def render_check(c: Check) -> list[str]:
    """The lines for one check in the current file format (a legacy inline reason becomes a quoted line)."""
    byline = f"  <!-- by {c.who} {c.when} -->" if c.who and c.when else ""
    out = [f"{c.head}{c.state}{c.mid}{c.text}{byline}"]
    if c.state == "!" and c.reason:
        out.append(f"{c.cont}> Not accepted" + (f" ({c.severity})" if c.severity else "") + f": {c.reason}")
    out += [f"{c.cont}> Evidence: {e}" for e in c.evidence]
    return out + list(c.extra)


def check_lines(text: str) -> list[tuple[int, bool]]:
    """(line number, checked) for every checkbox line in a section's text, fenced code skipped. Failed counts as not checked."""
    return [(n, st == "x") for n, st in check_states(text)]


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

    def is_default(self, name: str) -> bool:
        """True while the section is empty (hint comments and blank lines do not count) or still holds its seeded default text."""
        text = validate_steps(self.sections.get(name) or "")
        return not text or text == SECTION_DEFAULTS.get(name)

    def tests_text(self) -> str:
        """The written words of the Tests section: a hint comment and blank lines do not count."""
        return validate_steps(self.section("Tests"))

    def acceptance(self) -> list[tuple[bool, str]]:
        out = []
        for line in self.section("Acceptance").splitlines():
            m = re.match(r"^\s*[-*]\s*\[( |x|X|!)\]\s*(.*)$", line)
            if m:
                out.append((m.group(1).lower() == "x", _BYLINE.sub("", m.group(2))))
        return out

    def failed_checks(self, sections: tuple = ("Acceptance", "Evidence")) -> list[tuple[str, str]]:
        """(section, text) of every `- [!]` line: checked off as tried, and it did not work."""
        out = []
        for name in sections:
            out += [(name, c.text) for c in parse_checks(self.sections.get(name) or "") if c.state == "!"]
        return out

    # -- (de)serialisation ------------------------------------------------
    @classmethod
    def parse(cls, text: str, path: Path | None = None) -> "Ticket":
        m = _FM.match(text)
        if not m:
            raise ValueError(f"{path or 'ticket'}: no YAML frontmatter")
        meta = yamlio.load(m.group(1)) or {}
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
        fm = yamlio.dump(_plain(ordered), sort_keys=False, allow_unicode=True, default_flow_style=None, width=1000).rstrip("\n")
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
    t.sections["Commands"] = SEED_COMMANDS + chr(10)
    t.sections["Tests"] = "<!-- Name the automated test (file and what it protects), or the steps a person performs and what they must see. -->" + chr(10)
    return t
