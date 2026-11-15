"""The notification queue (DESK-0282 contract; DESK-0289 drains it, DESK-0292 and others write to it).

Anything that wants to tell the owner something appends one line here instead of sending: the sender applies the privacy filter,
the budget and quiet hours in one place. The file lives under `.desk/` (gitignored), one JSON object per line."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

FILE = Path(".desk") / "notify-queue.jsonl"


def enqueue(home: Path, kind: str, text: str, ids: tuple[str, ...] | list[str] = (), urgent: bool = False, source: str = "") -> dict:
    """Queue one notification: kind (brief, alert, digest-line), one plain line of text, the ticket ids it is about."""
    item = {"ts": dt.datetime.now().replace(microsecond=0).isoformat(), "kind": kind, "text": text, "ids": list(ids),
            "urgent": bool(urgent), "source": source}
    p = Path(home) / FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
    return item


def pending(home: Path) -> list[dict]:
    p = Path(home) / FILE
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
