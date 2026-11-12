"""DESK-0248: ticket ids reserved by open pull requests of the store repo.

`gh pr list` names the files each open pull request adds; the ticket files among them reserve their ids, so `drew new` numbers above
them and `drew validate --prs` reports a pull request id that names a different ticket on main. Without gh or network both say so in
one line and use the local folder only.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys

_TICKET = re.compile(r"(?:^|/)tickets/([A-Za-z][A-Za-z0-9-]*?-\d{4,})-[^/]*\.md$")


def _gh(home) -> str | None:
    """The raw JSON of the open pull requests with their files, or None when gh or the network is unavailable (tests replace this)."""
    try:
        r = subprocess.run(["gh", "pr", "list", "--state", "open", "--limit", "100", "--json", "number,files"], cwd=home,
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def open_pr_tickets(store, quiet: bool = False) -> list[tuple[int, str, str]]:
    """(pull request number, ticket id, file name) for every ticket file an open pull request adds."""
    raw = _gh(store.home)
    try:
        prs = json.loads(raw) if raw else None
    except ValueError:
        prs = None
    if prs is None:
        if not quiet:
            print("open pull requests not read (gh or network unavailable); using the local tickets folder only", file=sys.stderr)
        return []
    out = []
    for pr in prs:
        for f in pr.get("files", []):
            m = _TICKET.search(f.get("path", ""))
            if m:
                out.append((int(pr["number"]), m.group(1).upper(), f["path"].rsplit("/", 1)[-1]))
    return out


def collisions(store, found: list[tuple[int, str, str]]) -> list[str]:
    """One line per pull request id that equals a different ticket on main."""
    local = {p.name: p for p in store.ticket_paths()}
    by_id = {t.id: t.path.name for t in store.tickets() if t.path}
    lines = []
    for num, tid, name in found:
        have = by_id.get(tid)
        if have and have != name and name not in local:
            lines.append(f"PR #{num} adds {name}, but {tid} on main is {have}")
    return lines
