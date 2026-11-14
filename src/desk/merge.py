"""Lossless ticket merge (DESK-0263): `drew merge <survivor> <source>... --kind duplicate|linked|family`.

The survivor takes each source whole: a "Merged tickets" section holds the source file's full text, every open acceptance line moves
over marked "(from <ID>)", and the source's project joins the survivor's `also_projects`. Each source stays on disk as a rejected ticket
with `merged_into:` (or `duplicate_of:` for kind duplicate) and a Decision line naming the reason and who signed. Links that named a
source follow the survivor.

Why the edits are on the text and not through Ticket.render: rendering rewrites quoting and blank lines in most files of a real store
(1,713 of 1,753 in the 2026-11-13 store), and a merge must change nothing it was not asked to change. Only the lines named above move.

Why the copy of a source is indented four spaces inside its fence: Desk's parser (this version and the installed ones) starts a new
section at every line that begins with `## `, even inside a fence. An unindented copy would make the survivor's Context, Prompt and the
rest read as the source's on the next save. `embedded()` removes the indent and returns the exact text."""

from __future__ import annotations

import datetime as dt
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import activity, identity
from .secretscan import looks_secret, refusal as secret_refusal
from .store import Store
from .ticket import MERGED, SECTIONS, Ticket, parse_checks

KINDS = ("duplicate", "linked", "family")
HELD = {"in_progress": "in progress", "claimed": "claimed"}  # someone has it: never merged out from under them
CLOSED_SURVIVOR = {"done", "rejected"}
INDENT = "    "
_ORDER = SECTIONS[:SECTIONS.index("Report")] + [MERGED] + SECTIONS[SECTIONS.index("Report"):]  # where each section belongs in a file
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_HEAD = re.compile(r"^## (.+?)\s*$")
_ENTRY = re.compile(r"^\*\*([A-Z0-9][A-Z0-9-]*-\d{4,})\*\*")


class MergeError(ValueError):
    """A merge that was refused; nothing was written."""


def today() -> str:
    return dt.date.today().isoformat()


# -- text helpers: lines without their newline, joined back with "\n" so every untouched byte stays ------------------------------------

def _fm_end(lines: list[str]) -> int:
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return i
    raise MergeError("a ticket file has no closing --- after its frontmatter")


def _key_range(lines: list[str], key: str) -> tuple[int, int] | None:
    """(first line, line after the last) of `key:` in the frontmatter, with the `- item` lines of a block list under it."""
    end = _fm_end(lines)
    for i in range(1, end):
        if re.match(rf"^{re.escape(key)}:", lines[i]):
            j = i + 1
            while j < end and (lines[j].startswith((" ", "\t", "- ")) or lines[j].strip() == "-"):
                j += 1
            return i, j
    return None


def _put(lines: list[str], key: str, value: str, after: tuple[str, ...] = ()) -> None:
    new = f"{key}: {value}"
    r = _key_range(lines, key)
    if r:
        lines[r[0]:r[1]] = [new]
        return
    for a in after:
        ra = _key_range(lines, a)
        if ra:
            lines.insert(ra[1], new)
            return
    lines.insert(_fm_end(lines), new)


def _flow(ids: list[str]) -> str:
    return "[" + ", ".join(ids) + "]"


def _headings(lines: list[str]) -> list[tuple[str, int]]:
    """(name, line) of every `## ` heading after the frontmatter that is not inside a code fence."""
    out, fence = [], ""
    for i in range(_fm_end(lines) + 1, len(lines)):
        ln = lines[i]
        m = _FENCE.match(ln)
        if fence:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and ln.strip() == m.group(1):
                fence = ""
            continue
        if m:
            fence = m.group(1)
            continue
        h = _HEAD.match(ln)
        if h:
            out.append((h.group(1).strip(), i))
    return out


def _range(lines: list[str], name: str) -> tuple[int, int] | None:
    """(heading line, line after the section's last line) of the section `name`."""
    heads = _headings(lines)
    for k, (n, i) in enumerate(heads):
        if n == name:
            return i, heads[k + 1][1] if k + 1 < len(heads) else len(lines)
    return None


def _add_section(lines: list[str], name: str, body: list[str]) -> None:
    """A new section in the canonical order, before the first existing section that belongs after it, else at the end."""
    later = set(_ORDER[_ORDER.index(name) + 1:]) if name in _ORDER else set()
    for n, i in _headings(lines):
        if n in later:
            lines[i:i] = [f"## {name}", *body, ""]
            return
    while lines and lines[-1] == "":
        lines.pop()
    lines += ["", f"## {name}", *body, ""]


def _append(lines: list[str], name: str, add: list[str]) -> None:
    """Lines after the last written line of a section; the section is created in its place when it is missing."""
    r = _range(lines, name)
    if r is None:
        _add_section(lines, name, add)
        return
    head, end = r
    last = head
    for i in range(head + 1, end):
        if lines[i].strip():
            last = i
    lines[last + 1:last + 1] = add


# -- the copy of a source inside the survivor ---------------------------------------------------------------------------------------------

def _entry(src: Ticket, raw: str, kind: str, on: str) -> list[str]:
    runs = [len(m) for m in re.findall(r"`+", raw)]
    fence = "`" * max(3, max(runs, default=0) + 1)
    head = f"**{src.id}** · {src.meta.get('title', '')} · project {src.project} · {src.status} when merged · {on} as {kind}"
    body = [(INDENT + ln) if ln else "" for ln in raw.rstrip("\n").split("\n")]
    return [head, "", fence, *body, fence]


def embedded(text: str) -> dict[str, str]:
    """{ticket id: the source file's text} for every copy in the "Merged tickets" section of a survivor's text, with the indent removed."""
    lines = text.replace("\r\n", "\n").split("\n")
    r = _range(lines, MERGED)
    out: dict[str, str] = {}
    if r is None:
        return out
    i, end = r[0] + 1, r[1]
    while i < end:
        m = _ENTRY.match(lines[i])
        if not m:
            i += 1
            continue
        j = i + 1
        while j < end and not re.fullmatch(r"`{3,}", lines[j]):
            j += 1
        fence, k, body = lines[j], j + 1, []
        while k < end and lines[k] != fence:
            body.append(lines[k][len(INDENT):] if lines[k].startswith(INDENT) else lines[k])
            k += 1
        out[m.group(1)] = "\n".join(body) + "\n"
        i = k + 1
    return out


# -- the plan -------------------------------------------------------------------------------------------------------------------------------

@dataclass
class Change:
    id: str
    path: Path
    before: str  # the file as read
    after: str
    crlf: bool = False


@dataclass
class Plan:
    survivor: str
    sources: list[str]
    kind: str
    reason: str
    signed: str
    actor: str
    on: str
    changes: dict[str, Change] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)  # one plain line per thing that moves
    repoints: list[str] = field(default_factory=list)

    def report(self, dry_run: bool = False) -> list[str]:
        head = [f"dry run: nothing was written. This is what `drew merge` would do ({self.kind}, signed {self.signed}):"] if dry_run else []
        done = [f"merged {s} into {self.survivor} ({self.kind})" for s in self.sources] if not dry_run else []
        return [*head, *done, *self.notes]


def _read(path: Path) -> tuple[str, bool]:
    raw = path.read_bytes().decode("utf-8")
    return raw.replace("\r\n", "\n"), "\r\n" in raw


def _one_line(text: str) -> str:
    return " ".join((text or "").split())


def plan(store: Store, survivor: str, sources: list[str], *, kind: str, reason: str, signed: str, actor: str, on: str | None = None) -> Plan:
    on = on or today()
    if kind not in KINDS:
        raise MergeError(f"kind must be one of {', '.join(KINDS)}")
    reason, signed = _one_line(reason).rstrip("."), _one_line(signed)
    if not reason:
        raise MergeError("Give --reason: why these tickets are one.")
    if not signed:
        raise MergeError("Give --signed: the comment link or decision number that approves this merge.")
    for what in (reason, signed):
        if (hit := looks_secret(what)):
            raise MergeError(secret_refusal(hit))
    by_id = store.by_id()
    sv_id = survivor.upper()
    ids = list(dict.fromkeys(s.upper() for s in sources))
    if not ids:
        raise MergeError("Name at least one ticket to merge into the survivor.")
    for tid in [sv_id, *ids]:
        if tid not in by_id:
            raise MergeError(f"No ticket {tid}")
    sv = by_id[sv_id]
    if sv_id in ids:
        raise MergeError(f"{sv_id} is the survivor; a ticket cannot be merged into itself.")
    if sv.status in CLOSED_SURVIVOR:
        raise MergeError(f"{sv_id} is {sv.status}: nothing is merged into a ticket that is done or let go. Pick an open survivor.")
    for tid in ids:
        s = by_id[tid]
        if s.status in HELD:
            raise MergeError(f"{tid} is {HELD[s.status]}: someone has it. Merge it after it is handed over or dropped.")
        if s.status == "done":
            raise MergeError(f"{tid} is done: a finished ticket keeps its own record. Link it under related instead.")
        into = str(s.meta.get("merged_into") or s.meta.get("duplicate_of") or "")
        if into:
            raise MergeError(f"{tid} is already merged into {into}.")

    p = Plan(sv_id, ids, kind, reason, signed, actor, on)
    work: dict[str, list[str]] = {}
    crlf: dict[str, bool] = {}
    raw: dict[str, str] = {}

    def lines_of(t: Ticket) -> list[str]:
        if t.id not in work:
            raw[t.id], crlf[t.id] = _read(t.path)
            work[t.id] = raw[t.id].split("\n")
        return work[t.id]

    # the survivor
    sl = lines_of(sv)
    have = embedded(raw[sv_id])  # a source already copied in by an interrupted merge is not copied twice
    also = [str(x) for x in (sv.meta.get("also_projects") or [])]
    carried: list[str] = []
    for tid in ids:
        s = by_id[tid]
        if s.project != sv.project and s.project not in also:
            also.append(s.project)
        if tid in have:
            p.notes.append(f"{tid}: already copied into {sv_id} by an earlier run; copied once")
            continue
        source_text, _ = _read(s.path)
        for c in parse_checks(s.sections.get("Acceptance") or ""):
            if c.state == " " and c.text.strip():
                carried.append(f"- [ ] (from {tid}) {c.text.strip()}")
        if _range(sl, MERGED):
            _append(sl, MERGED, ["", *_entry(s, source_text, kind, on)])
        else:
            _add_section(sl, MERGED, _entry(s, source_text, kind, on))
        p.notes.append(f"{sv_id}: Merged tickets gains {tid} ({len(source_text.splitlines())} lines, verbatim)")
    if carried:
        _append(sl, "Acceptance", carried)
        p.notes.append(f"{sv_id}: Acceptance gains {len(carried)} open line(s): " + "; ".join(c[6:] for c in carried))
    if also != [str(x) for x in (sv.meta.get("also_projects") or [])]:
        _put(sl, "also_projects", _flow(also), after=("project",))
        p.notes.append(f"{sv_id}: also_projects -> {_flow(also)}")
    _put(sl, "updated", on)

    # each source
    key = "duplicate_of" if kind == "duplicate" else "merged_into"
    what = "duplicate of" if kind == "duplicate" else "merged into"
    for tid in ids:
        s = by_id[tid]
        ll = lines_of(s)
        _put(ll, "status", "rejected")
        _put(ll, key, sv_id, after=("related", "depends_on", "status"))
        _put(ll, "updated", on)
        _append(ll, "Decision", [f'- {on} · {actor} · rejected · "{what} {sv_id} (kind {kind}): {reason}. Signed: {signed}."'])
        p.notes.append(f"{tid}: status {s.status} -> rejected, {key}: {sv_id}, Decision line added; the file is kept")

    # links that named a source follow the survivor
    for t in store.tickets():
        if t.id in ids:
            continue
        edits = []
        for fld in ("depends_on", "related"):
            cur = [str(x) for x in (t.meta.get(fld) or [])] if isinstance(t.meta.get(fld), list) else []
            if not any(x in ids for x in cur):
                continue
            new: list[str] = []
            for x in cur:
                if x in ids:
                    if t.id == sv_id:
                        p.repoints.append(f"drop {t.id} {fld}: {x} (the survivor cannot list itself)")
                        continue
                    p.repoints.append(f"repoint {t.id} {fld}: {x} -> {sv_id}")
                    x = sv_id
                if x not in new:
                    new.append(x)
            edits.append((fld, new))
        if edits:
            tl = lines_of(t)
            for fld, new in edits:
                _put(tl, fld, _flow(new), after=("depends_on", "related", "assignee"))
            _put(tl, "updated", on)
    p.notes += p.repoints
    if not p.repoints:
        p.notes.append("no other ticket lists a source under depends_on or related")

    for tid, ls in work.items():
        t = by_id[tid]
        p.changes[tid] = Change(tid, t.path, raw[tid], "\n".join(ls), crlf[tid])
    _check(store, by_id, p)
    return p


def _check(store: Store, by_id: dict[str, Ticket], p: Plan) -> None:
    """Refuse a merge that would leave a ticket with an error it did not have before (nothing is written)."""
    from .rules import validate_ticket

    known = set(by_id)
    for tid, ch in p.changes.items():
        before, _ = validate_ticket(by_id[tid], store, known)
        try:
            after_t = Ticket.parse(ch.after, ch.path)
        except ValueError as e:
            raise MergeError(f"{tid}: the merged file would not read back: {e}") from e
        after, _ = validate_ticket(after_t, store, known)
        new = [e for e in after if e not in before]
        if new:
            raise MergeError("The merge would leave an error, so nothing was written: " + "; ".join(new))


def apply(store: Store, p: Plan) -> None:
    """Write the survivor first (it holds every source's text), then the sources, then the tickets whose links moved. Each file is
    replaced whole in one step, so an interrupted run leaves either the old file or the new one."""
    order = [p.survivor, *p.sources, *[t for t in p.changes if t != p.survivor and t not in p.sources]]
    for tid in order:
        ch = p.changes.get(tid)
        if ch is None:
            continue
        data = (ch.after.replace("\n", "\r\n") if ch.crlf else ch.after).encode("utf-8")
        tmp = ch.path.with_name(ch.path.name + ".merge-tmp")
        tmp.write_bytes(data)
        os.replace(tmp, ch.path)
        try:
            activity.record_save(store, Ticket.parse(ch.before, ch.path), Ticket.parse(ch.after, ch.path))
            identity.record_write(store.home, tid)
        except Exception as e:  # the files are written; the log is a record, never a reason to report the merge as failed
            import logging

            logging.getLogger("desk.merge").warning("activity log not written for %s: %s", tid, e)


def merge(store: Store, survivor: str, sources: list[str], *, kind: str, reason: str, signed: str, actor: str, dry_run: bool = False,
          on: str | None = None) -> Plan:
    p = plan(store, survivor, sources, kind=kind, reason=reason, signed=signed, actor=actor, on=on)
    if not dry_run:
        apply(store, p)
    return p
