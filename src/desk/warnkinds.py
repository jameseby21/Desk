"""DESK-0265: warning kinds. `drew validate` keeps one flat list of warning lines (rules.validate_store); this module names the kind of each
line so the command can print one line per kind (count, three newest ticket ids), and so `--gate KIND` can turn one kind into an error for
a run. Docs: docs/COMMANDS.md#drew-validate. One owner per fact: the order below is the order a line is tested in; the first match wins."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# (key, label, pattern tested against the whole line). The label reads like the sentence it replaces, so a person who knew the old line
# recognizes the grouped one. Specific kinds come before general ones.
KINDS: list[tuple[str, str, str]] = [
    ("commands", "ready with no Commands", r"ready with no Commands"),
    ("how-to-validate", "in review with no How to validate (legacy ticket)", r"in review with no How to validate"),
    ("screenshot", "ui work in review with no screenshot attached", r"ui work in review with no screenshot"),
    ("design", "ui work with no Chaz design ticket linked", r"ui work with no Chaz design ticket"),
    ("dead-links", "doc link is a dead link", r"doc link `.*`: dead link"),
    ("undispatched", "undispatched work (ready, unblocked, Patch's, no worker)", r": undispatched for \d"),
    ("gps", "GPS fails (Goal, Proof or Steps)", r": GPS fails "),
    ("grade", "text reads above the grade limit", r" reads at grade "),
    ("jargon", "listed jargon with no plain explanation beside it", r" uses \".*\" with no plain explanation"),
    ("wall-of-text", "wall of text in one block", r"has a wall of text|characters; save it as a markdown file|: readability: "),
    ("assignee", "no assignee (James has not scanned this)", r": no assignee"),
    ("tests", "no Tests section text", r"no Tests section text"),
    ("title", "title over the length limit", r": title is \d+ characters"),
    ("brand", "no brand block", r"no brand block"),
    ("repeats", "repeated ask (answer first)", r": repeated ask: "),
    ("handoff", "stale hand-off", r": waiting on .* since "),
    ("sprint", "sprint not in sprints.yaml", r": sprint `"),
    ("subtickets", "subticket chain", r"subticket|main ticket .* is itself part of"),
    ("review-age", "in review for days", r": in review for \d+ days"),
    ("legacy", "legacy forms still read (type, inline not-accepted lines)", r"legacy type|inline `not accepted`"),
    ("acceptance", "ready with no Acceptance checkboxes", r"should have Acceptance checkboxes"),
    ("size", "size l not split", r"size `l` should be split"),
    ("file-name", "file name does not start with the id", r"file name .* does not start with the id"),
    ("links", "related, depends_on or cloned_from names a missing ticket", r"` does not exist"),
]
_COMPILED = [(k, label, re.compile(p)) for k, label, p in KINDS]
OTHER = ("other", "other warnings")
_LABEL = {k: label for k, label, _ in KINDS} | {OTHER[0]: OTHER[1]}
_SUBJECT = re.compile(r"^([A-Za-z][\w-]*-\d+|[^:\s][^:]*?):\s")
_ID = re.compile(r"^([A-Za-z][\w-]*)-(\d+)$")
_HOURS = re.compile(r"undispatched for (\d+) h")
NEWEST = 3


def kind_keys() -> list[str]:
    return [k for k, _, _ in KINDS] + [OTHER[0]]


def classify(line: str) -> str:
    for key, _, pat in _COMPILED:
        if pat.search(line):
            return key
    return OTHER[0]


def subject_of(line: str) -> str:
    """The ticket id (or file) a line is about: what stands before the first colon."""
    m = _SUBJECT.match(line)
    return m.group(1) if m else ""


@dataclass
class Group:
    key: str
    label: str
    lines: list[str] = field(default_factory=list)
    subjects: list[str] = field(default_factory=list)  # distinct, newest first

    @property
    def count(self) -> int:
        return len(self.lines)


def _newest_first(subject: str, tickets: dict) -> tuple:
    t = tickets.get(subject)
    created = str(t.meta.get("created") or "") if t is not None else ""
    m = _ID.match(subject)
    return (created, int(m.group(2)) if m else -1, subject)


def group(lines: list[str], tickets: dict) -> list[Group]:
    """One Group per kind that has lines, the biggest first (ties in the order of KINDS); `tickets` maps id -> Ticket, used only to find the newest."""
    by: dict[str, Group] = {}
    for line in lines:
        key = classify(line)
        g = by.setdefault(key, Group(key, _LABEL[key]))
        g.lines.append(line)
    order = {k: i for i, k in enumerate(kind_keys())}
    for g in by.values():
        seen = {subject_of(ln) for ln in g.lines if subject_of(ln)}
        g.subjects = sorted(seen, key=lambda s: _newest_first(s, tickets), reverse=True)
    return sorted(by.values(), key=lambda g: (-g.count, order[g.key]))


def _plural_of(g: Group) -> str:
    return "ticket(s)" if all(_ID.match(s) for s in g.subjects) else "item(s)"


def render(g: Group) -> str:
    """`warning: [commands] ready with no Commands: 721 ticket(s); newest DESK-0265, DESK-0264, DESK-0263`."""
    n, m = g.count, len(g.subjects)
    unit = _plural_of(g)
    if g.key == "undispatched":
        waits = [(int(_HOURS.search(ln).group(1)), subject_of(ln)) for ln in g.lines if _HOURS.search(ln)]
        oldest = max(waits) if waits else (0, "")
        return (f"warning: [undispatched] {g.label}: {m} {unit}, oldest {oldest[0]} h ({oldest[1]}); "
                "start a worker (`drew dispatch` lists them by repo) or set blocked_on to the worker or the reason")
    count = f"{n} {unit}" if n == m else f"{n} warning(s) in {m} {unit}"
    return f"warning: [{g.key}] {g.label}: {count}; newest {', '.join(g.subjects[:NEWEST])}"


def split_gated(lines: list[str], gates: list[str]) -> tuple[list[str], list[str]]:
    """(lines of a gated kind, the others)."""
    gated = [ln for ln in lines if classify(ln) in gates]
    rest = [ln for ln in lines if classify(ln) not in gates]
    return gated, rest
