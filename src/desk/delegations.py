"""Delegations (DESK-0285): "approve things like this for me". Each line in the store's `delegations.yaml` says who may approve what
kind of ask for the owner, since when and who said so. The owner reads and removes them on /settings (or edits the file).

Nothing approves on its own from this file yet: it is a record of the owner's word. The automation engine (DESK-0292) may read it
later; until then a delegate still answers through the normal path.

Every change is reversible: an added line is taken back with Remove, and a removed line is kept in `.desk/delegations-removed.jsonl`
(local, never committed) under a short key, so Put back returns it exactly as it was. The web routes commit each change through the
store's git sync. Docs: docs/COMMANDS.md (`/settings` Delegations)."""

from __future__ import annotations

import datetime as dt
import json
import secrets
from pathlib import Path

from . import yamlio
from .secretscan import looks_secret

FILE = "delegations.yaml"
REMOVED = Path(".desk") / "delegations-removed.jsonl"
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
    key = secrets.token_hex(4)
    p = Path(home) / REMOVED
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"key": key, "row": gone}, ensure_ascii=False) + "\n")
    return {**gone, "key": key}


def removed(home: Path, key: str) -> dict | None:
    """The line removed under `key`, while it is not back on the list; None otherwise."""
    try:
        text = (Path(home) / REMOVED).read_text(encoding="utf-8")
    except OSError:
        return None
    for ln in reversed(text.splitlines()):
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if r.get("key") == key and isinstance(r.get("row"), dict):
            return None if r["row"] in load(home) else r["row"]
    return None


def put_back(home: Path, key: str) -> dict:
    """Return the line removed under `key` to the list, exactly as it was (its date and who said it kept)."""
    row = removed(home, key)
    if row is None:
        raise ValueError("That line is already back, or it can not be found. Nothing changed.")
    _write(home, load(home) + [row])
    return row
