"""Delegations (DESK-0285): "approve things like this for me". Each line in the store's `delegations.yaml` says who may approve what
kind of ask for the owner, since when and who said so. The owner reads and removes them on /settings (or edits the file).

Nothing approves on its own from this file yet: it is a record of the owner's word. The automation engine (DESK-0292) may read it
later; until then a delegate still answers through the normal path. Docs: docs/COMMANDS.md (`/settings` Delegations)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from . import yamlio
from .secretscan import looks_secret

FILE = "delegations.yaml"
HEADER = ("# Delegations (DESK-0285): who may approve what kind of ask for the owner. Written from Decide mode; remove a line on /settings.\n"
          "# Nothing approves on its own from this file yet; the automation engine may read it later.\n")
MAX_PATTERN = 200


def path(home: Path) -> Path:
    return Path(home) / FILE


def load(home: Path) -> list[dict]:
    """Every delegation line, oldest first; an unreadable or missing file is no delegations."""
    try:
        data = yamlio.load(path(home).read_text(encoding="utf-8"))
    except (OSError, yamlio.YAMLError):
        return []
    rows = (data or {}).get("delegations") if isinstance(data, dict) else data
    return [r for r in (rows or []) if isinstance(r, dict) and r.get("pattern")]


def _write(home: Path, rows: list[dict]) -> None:
    body = yamlio.dump({"delegations": rows}, sort_keys=False, allow_unicode=True, default_flow_style=False)
    path(home).write_bytes((HEADER + body).encode("utf-8"))


def add(home: Path, who: str, pattern: str, by: str, project: str = "", source: str = "", today: dt.date | None = None) -> dict:
    """Append one line. `who` may approve asks like `pattern` (in `project` when given) for `by`, the owner who said so."""
    pattern = " ".join((pattern or "").split())
    who = " ".join((who or "").split())
    if not pattern:
        raise ValueError("Say what kind of ask this covers, in a few words.")
    if len(pattern) > MAX_PATTERN:
        raise ValueError(f"Keep it under {MAX_PATTERN} characters.")
    if not who:
        raise ValueError("Pick who may approve these for you.")
    if looks_secret(pattern):
        raise ValueError("That looks like a password or key; it is never written to a file.")
    row = {"who": who, "pattern": pattern, "project": project or "", "date": (today or dt.date.today()).isoformat(), "by": by, "from": source or ""}
    rows = load(home)
    if any(r.get("who") == who and r.get("pattern") == pattern and (r.get("project") or "") == (project or "") for r in rows):
        raise ValueError("That delegation is already on the list.")
    _write(home, rows + [row])
    return row


def remove(home: Path, index: int, pattern: str) -> dict:
    """Remove line `index`, checked against its pattern so a list that changed under the page does not lose the wrong line."""
    rows = load(home)
    if not 0 <= index < len(rows) or rows[index].get("pattern") != pattern:
        raise ValueError("That line has changed since the page opened. Reload Settings and try again.")
    gone = rows.pop(index)
    _write(home, rows)
    return gone
