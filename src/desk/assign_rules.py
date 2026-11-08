"""Auto-assignment rules (DESK-0168): a workflow maps to a person, from `assign.yaml` in the store.

    rules:
      - name: human review          # shown on the ticket: "assigned by rule human review"
        assign: james               # a key or display name from people.yaml; "code:{project}" for the project's own session
        when: {type: decision}      # every condition must hold: type, project, tags (any of), title_words (any, whole words), owner
      - name: build
        assign: main
        fallback: true              # used only when no other rule matched
        when: {type: [feature, upgrade, bug]}

`drew new` and `drew intake` fill a blank assignee when the rules agree on exactly one person; two different people leave it blank
and warn. A rule never changes an assignee someone set. With no assign.yaml the built-in table below (standards rule 27) is used;
`drew assign-rules init` writes it out to edit."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import people as peoplemod, yamlio
from .ticket import Ticket

CONDITIONS = {"type", "project", "tags", "title_words", "owner", "status"}
PATH = "assign.yaml"

DEFAULT_RULES = [
    {"name": "human review", "assign": "james", "when": {"type": "decision"}},
    {"name": "human review (title)", "assign": "james", "when": {"title_words": ["human review"]}},
    {"name": "design review", "assign": "chaz", "when": {"title_words": ["design review"]}},
    {"name": "brand", "assign": "chaz", "when": {"project": "brand"}},
    {"name": "design tag", "assign": "chaz", "when": {"tags": ["design", "brand"]}},
    {"name": "security project", "assign": "bigd", "when": {"project": "security"}},
    {"name": "security tag", "assign": "bigd", "when": {"tags": ["security"]}},
    {"name": "research", "assign": "em", "when": {"type": "research"}},
    {"name": "tech and product", "assign": "lily", "when": {"tags": ["tech", "product", "it", "infrastructure"]}},
    {"name": "admin", "assign": "bri", "when": {"tags": ["calendar", "orders", "admin"]}},
    {"name": "legal and finance", "assign": "joe", "when": {"tags": ["legal", "finance", "channel", "boring-stuff"]}},
    {"name": "channel project", "assign": "joe", "when": {"project": "channel"}},
    {"name": "project session", "assign": "code:{project}", "when": {"owner": "{project}"}},
    {"name": "build", "assign": "main", "fallback": True, "when": {"type": ["feature", "upgrade", "bug", "docs", "release"]}},
]


class RulesError(ValueError):
    pass


@dataclass
class Rule:
    name: str
    assign: str
    when: dict
    fallback: bool = False


@dataclass
class Outcome:
    """What the rules say about one ticket: `filled` (one person), `none` (no rule matched) or `conflict` (different people)."""

    state: str
    assignee: str = ""
    rule: str = ""
    matches: list[Rule] = field(default_factory=list)

    @property
    def warning(self) -> str:
        names = ", ".join(f"{r.name} -> {r.assign}" for r in self.matches)
        return f"two rules disagree ({names}); the assignee stays blank"


def _list(v) -> list[str]:
    return [str(x) for x in v] if isinstance(v, list) else [str(v)]


def parse(data) -> list[Rule]:
    rows = data.get("rules") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise RulesError("assign.yaml: expected a `rules:` list")
    out = []
    for i, r in enumerate(rows, 1):
        if not isinstance(r, dict) or not str(r.get("assign") or "").strip():
            raise RulesError(f"assign.yaml: rule {i} needs an `assign:` person")
        when = r.get("when") or {}
        if not isinstance(when, dict) or set(when) - CONDITIONS:
            raise RulesError(f"assign.yaml: rule {i} `when:` may only use {', '.join(sorted(CONDITIONS))}")
        out.append(Rule(str(r.get("name") or f"rule {i}").strip(), str(r["assign"]).strip(), when, bool(r.get("fallback"))))
    return out


def load(home: Path) -> tuple[list[Rule], str]:
    """(rules, where they came from): the store's assign.yaml, else the built-in table."""
    p = Path(home) / PATH
    if p.is_file():
        try:
            return parse(yamlio.load(p.read_text(encoding="utf-8"))), str(p)
        except yamlio.YAMLError as e:
            raise RulesError(f"{p}: not valid YAML ({e})") from e
    return parse({"rules": DEFAULT_RULES}), "the built-in table (run `drew assign-rules init` to write assign.yaml)"


def _words(title: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", title.lower()).strip() + " "


def matches(t: Ticket, when: dict) -> bool:
    title = _words(str(t.meta.get("title") or ""))
    for key, want in when.items():
        want = [w.replace("{project}", t.project) for w in _list(want)]
        if key == "type" and str(t.meta.get("type")) not in want:
            return False
        if key == "project" and t.project not in want:
            return False
        if key == "status" and t.status not in want:
            return False
        if key == "owner" and str(t.meta.get("owner") or "") not in want:
            return False
        if key == "tags" and not {w.lower() for w in want} & {x.lower() for x in t.tags}:
            return False
        if key == "title_words" and not any(_words(w).strip() and _words(w) in title for w in want):
            return False
    return True


def decide(t: Ticket, rules: list[Rule], pe: peoplemod.People) -> Outcome:
    hit = [r for r in rules if not r.fallback and matches(t, r.when)]
    if not hit:
        hit = [r for r in rules if r.fallback and matches(t, r.when)]
    if not hit:
        return Outcome("none")
    people = {pe.resolve(r.assign.replace("{project}", t.project)) for r in hit}
    if len(people) > 1:
        return Outcome("conflict", matches=hit)
    return Outcome("filled", people.pop(), hit[0].name, hit)


def apply_new(store, t: Ticket) -> Outcome:
    """Fill the assignee of a new ticket from the rules when it is blank and exactly one person comes out. Saves the ticket."""
    out = Outcome("none")
    if " ".join(str(t.meta.get("assignee") or "").split()):
        return out  # a rule never overrides an assignee someone set
    rules, _ = load(store.home)
    out = decide(t, rules, peoplemod.load(store.home))
    if out.state == "filled":
        t.meta["assignee"] = out.assignee
        t.meta["assigned_by"] = f"rule:{out.rule}"
        t.meta["assigned_at"] = dt.datetime.now().replace(microsecond=0).isoformat()
        store.save(t)
    return out


def plan(tickets: list[Ticket], rules: list[Rule], pe: peoplemod.People, include_closed: bool = False) -> list[tuple[Ticket, Outcome]]:
    """(ticket, outcome) for every ticket with a blank assignee (open ones, unless `include_closed`)."""
    out = []
    for t in tickets:
        if " ".join(str(t.meta.get("assignee") or "").split()) or (not include_closed and not t.is_open):
            continue
        out.append((t, decide(t, rules, pe)))
    return out


def write_assignee(t: Ticket, who: str, rule: str) -> bool:
    """Add `assignee:` and `assigned_by:` after the `owner:` line by inserting two lines; nothing else changes, `updated` included."""
    path = t.path
    text = path.read_bytes().decode("utf-8")
    m = re.match(r"(---\r?\n)(.*?)(\r?\n---\r?\n)", text, re.S)
    if not m or " ".join(str(t.meta.get("assignee") or "").split()):
        return False  # only a blank (or missing) assignee is ever filled
    nl = "\r\n" if "\r\n" in m.group(3) else "\n"
    lines = [ln for ln in m.group(2).split(nl) if not re.match(r"^assignee:\s*(\"\"|'')?\s*$", ln)]  # a blank assignee line is replaced
    at = next((i + 1 for i, ln in enumerate(lines) if ln.startswith("owner:")), len(lines))
    new = yamlio.dump({"assignee": who, "assigned_by": f"rule:{rule}"}, allow_unicode=True, width=10000, default_flow_style=False).rstrip("\n").split("\n")
    lines[at:at] = new
    path.write_bytes((m.group(1) + nl.join(lines) + m.group(3) + text[m.end():]).encode("utf-8"))
    return True


def seed_text() -> str:
    out = ["# Auto-assignment (DESK-0168): a workflow maps to a person. The first column of conditions must all hold.",
           "# `drew new` and `drew intake` fill a blank assignee when the rules agree on one person; two people leave it blank and warn.",
           "# A rule never changes an assignee someone set. `fallback: true` rules are used only when nothing else matched.", "rules:"]
    for r in DEFAULT_RULES:
        out.append(f"  - name: {r['name']}")
        out.append(f"    assign: \"{r['assign']}\"")
        if r.get("fallback"):
            out.append("    fallback: true")
        out.append("    when: " + yamlio.dump(r["when"], default_flow_style=True, width=10000, allow_unicode=True).strip().replace("'", '"'))
    return "\n".join(out) + "\n"


def write_seed(home: Path, force: bool = False) -> Path:
    p = Path(home) / PATH
    if p.exists() and not force:
        raise FileExistsError(f"{p} already exists; edit it, or pass --force to replace it with the built-in table")
    p.write_text(seed_text(), encoding="utf-8", newline="\n")
    return p
