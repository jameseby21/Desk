"""`drew intake --check <SLICES.md>`: check the ticket-ready table of a hand-off before anything is imported.

The table shape is the one in standards/templates/UPDATE.md. Every problem names its row id, so the sender can fix the row.
Cells may carry Drive's escapes (`\\|` for a pipe inside a cell, `\\_`, `\\.` and the like); they are undone before checking."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

from .store import Store, id_parts
from .ticket import SIZES, TYPES

ASKS = ("approve", "choose", "answer", "direction", "provide")
# column -> accepted header spellings (lower case, escapes removed); the first is the canonical name
COLUMNS = {
    "id": ("#", "id", "row"),
    "project": ("project",),
    "title": ("title",),
    "type": ("type",),
    "size": ("size",),
    "owner": ("owner",),
    "assignee": ("assignee",),
    "ask": ("ask · by", "ask", "ask by"),
    "acceptance": ("acceptance",),
    "validate": ("how to validate", "validate"),
    "after": ("depends on", "after", "depends_on"),
    "source": ("source",),
}
REQUIRED = tuple(COLUMNS)
DISPLAY = dict(zip(COLUMNS, ["#", "Project", "Title", "Type", "Size", "Owner", "Assignee", "Ask · by", "Acceptance", "How to validate", "Depends on", "Source"]))
NONE_WORDS = {"", "none", "-", "n/a", "na", "—", "–"}
_ESC = re.compile(r"\\([^\w\s])")
_SPLIT = re.compile(r"(?<!\\)\|")
_IDREF = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")


_ENTRY = re.compile(r"^\s*Date:\s*\S.*\bFrom:\s*\S", re.M)
_TICKETS = re.compile(r"^\s*Tickets:[ \t]*(.*)$", re.M | re.I)
_DELIVERS = re.compile(r"^\s*(?:#+\s*)?[*_]*Deliverables?\b", re.M | re.I)
_TICKET_ID = re.compile(r"\b[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\d{4}\b")
RULE_25 = "Rule 25 (Every item has a ticket)"


@dataclass
class Problem:
    row: str
    column: str
    message: str
    error: bool = True


def _clean(cell: str) -> str:
    return _ESC.sub(r"\1", cell).strip()


def _cells(line: str) -> list[str]:
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|") and not inner.endswith("\\|"):
        inner = inner[:-1]
    return [_clean(c) for c in _SPLIT.split(inner)]


def parse_tables(text: str) -> list[tuple[list[str], list[tuple[int, list[str]]]]]:
    """[(header cells, [(line number, row cells)])] for every table whose header names Project and Title."""
    tables, lines = [], text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|[\s:|\\-]+\|?\s*$", lines[i + 1]):
            header = _cells(lines[i])
            rows, j = [], i + 2
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                rows.append((j + 1, _cells(lines[j])))
                j += 1
            low = [h.lower() for h in header]
            if "project" in low and "title" in low:
                tables.append((header, rows))
            i = j
        else:
            i += 1
    return tables


def _column_map(header: list[str]) -> dict[str, int]:
    low = [h.lower() for h in header]
    out = {}
    for name, spellings in COLUMNS.items():
        for sp in spellings:
            if sp in low:
                out[name] = low.index(sp)
                break
    return out


def entry_problems(text: str, has_rows: bool) -> list[Problem]:
    """DESK-0165: an entry (the rule 25 header: Date, From, To ... then `Tickets:`) that proposes work (a ticket table) or delivers it (a
    Deliverables line) must name the real ticket ids on its Tickets line. Empty, `none` and `TBD` (even inside a sentence) are refused;
    an entry that only answers a question may say none."""
    if not _ENTRY.search(text):
        return []
    m = _TICKETS.search(text)
    value = m.group(1).strip() if m else ""
    delivers = _DELIVERS.search(text) is not None
    if not (has_rows or delivers):
        return []  # a question-only entry
    what = "a ticket table" if has_rows else "a Deliverables line"
    if not m or not value:
        why = "is empty" if m else "is missing"
    elif re.search(r"\btbd\b", value, re.I):
        why = f"says TBD (`{value[:60]}`)"
    elif value.lower().strip(" .") in NONE_WORDS:
        why = "says none"
    elif not _TICKET_ID.search(value):
        why = f"names no ticket id (`{value[:60]}`)"
    else:
        return []
    return [Problem("Tickets", "Tickets:", f"the Tickets line {why}, but the entry has {what}. {RULE_25}: ask Patch for the ticket id first, put the real ids on the Tickets line, then send the entry again")]


def check_text(text: str, store: Store) -> tuple[int, list[Problem]]:
    """(row count, problems) for the ticket-ready tables in `text`, against the projects and tickets of `store`."""
    problems: list[Problem] = []
    tables = parse_tables(text)
    problems += entry_problems(text, any(rows for _, rows in tables))
    if not tables:
        if _ENTRY.search(text):
            return 0, problems  # an entry with no table is checked by its header alone
        return 0, [Problem("-", "table", "no ticket-ready table found (a table whose header has Project and Title)")]
    projects = set(store.projects())
    known_ids = {t.id for t in store.tickets()}
    ready: list[tuple[str, dict[str, str], int]] = []
    seen: dict[str, int] = {}
    for header, rows in tables:
        cmap = _column_map(header)
        for name in REQUIRED:
            if name not in cmap:
                problems.append(Problem("-", DISPLAY[name], f"required column `{DISPLAY[name]}` is missing from the table header"))
        if any(n not in cmap for n in REQUIRED):
            continue
        for lineno, cells in rows:
            row = cells[cmap["id"]] if len(cells) > cmap["id"] and cells[cmap["id"]] else f"line {lineno}"
            if row in seen:
                problems.append(Problem(row, "#", f"row id repeats (also on line {seen[row]})"))
            seen.setdefault(row, lineno)
            if len(cells) != len(header):
                problems.append(Problem(row, "row", f"has {len(cells)} cells, the header has {len(header)} (an unescaped pipe in a cell?)"))
                continue
            ready.append((row, {n: cells[cmap[n]] for n in REQUIRED}, lineno))
    row_ids = {r for r, _, _ in ready}
    for row, v, _ in ready:
        def bad(col: str, msg: str, error: bool = True):
            problems.append(Problem(row, col, msg, error))

        if v["project"] not in projects:
            bad("Project", f"unknown project key `{v['project']}` (known: {', '.join(sorted(projects)) or 'none'})")
        if not v["title"]:
            bad("Title", "empty")
        if v["type"] not in TYPES:
            bad("Type", f"`{v['type']}` is not one of {', '.join(TYPES)}")
        if v["size"] not in SIZES:
            bad("Size", f"`{v['size']}` is not one of {', '.join(SIZES)}")
        ask = v["ask"]
        if ask.lower() in NONE_WORDS:
            if v["type"] == "decision":
                bad("Ask · by", "a decision row needs an ask and a date, like `choose · 2026-11-14`")
        else:
            m = re.match(r"^([A-Za-z]+)\s*[·\-:]\s*(\d{4}-\d{2}-\d{2})$", ask)
            if not m or m.group(1).lower() not in ASKS:
                bad("Ask · by", f"`{ask}` should be `<{' | '.join(ASKS)}> · YYYY-MM-DD`")
            else:
                try:
                    dt.date.fromisoformat(m.group(2))
                except ValueError:
                    bad("Ask · by", f"`{m.group(2)}` is not a real date")
        if v["acceptance"].lower() in NONE_WORDS or v["acceptance"].startswith("<"):
            bad("Acceptance", "empty")
        if v["validate"].lower() in NONE_WORDS or v["validate"].startswith("<"):
            bad("How to validate", "empty (every row needs steps that confirm the outcome)")
        after = v["after"]
        if after.lower() not in NONE_WORDS:
            for ref in [x for x in re.split(r"[,;\s]+", after) if x]:
                ref = ref.lstrip("#")
                if ref in row_ids or ref in known_ids:
                    continue
                kind = "an id in this file or the store" if id_parts(ref) or ref.isdigit() else "an id"
                bad("Depends on", f"`{ref}` is not {kind}")
    return len(ready), problems


def report(path: Path, rows: int, problems: list[Problem]) -> str:
    errors = [p for p in problems if p.error]
    out = [f"# Intake check: {path.name}", "",
           f"Checked {dt.date.today().isoformat()}: {rows} row(s), {len(errors)} error(s), {len(problems) - len(errors)} warning(s).", ""]
    if not problems:
        out += ["Clean: every row passes. It can be imported as it stands.", ""]
    else:
        out += ["| Row | Column | Problem |", "| --- | --- | --- |"]
        out += [f"| {p.row} | {p.column} | {p.message.replace('|', '/')}{'' if p.error else ' (warning)'} |" for p in problems]
        out += ["", "Fix these rows and send the file again."]
    return "\n".join(out) + "\n"


def run(path: Path, store: Store) -> tuple[str, int]:
    """Check the file, write `<file>.check.md` beside it, return (printable report, error count)."""
    path = Path(path)
    rows, problems = check_text(path.read_text(encoding="utf-8"), store)
    text = report(path, rows, problems)
    path.with_name(path.name + ".check.md").write_text(text, encoding="utf-8", newline="\n")
    return text, sum(1 for p in problems if p.error)
