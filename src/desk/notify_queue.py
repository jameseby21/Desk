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


CURSOR = Path(".desk") / "notify-queue.cursor"  # DESK-0289: bytes of the queue already handled; the queue itself stays append-only


def _cursor(home: Path) -> int:
    try:
        return int((Path(home) / CURSOR).read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        return 0


def read_counted(home: Path) -> tuple[list[dict], int, int]:
    """The items nobody has handled yet, the byte offset just past them (pass it to `advance` once they are handled), and how many
    complete lines could not be read. A corrupt line is skipped, never raised: one bad line must not hold every later one back."""
    p = Path(home) / FILE
    if not p.exists():
        return [], 0, 0
    data = p.read_bytes()
    start = min(_cursor(home), len(data))
    end = data.rfind(b"\n", start) + 1  # a line still being written is left for the next pass
    if end <= start:
        return [], start, 0
    items, bad = [], 0
    for raw in data[start:end].split(b"\n"):
        if not raw.strip():
            continue
        try:
            item = json.loads(raw.decode("utf-8"))
        except ValueError:  # JSONDecodeError and UnicodeDecodeError are both ValueErrors
            item = None
        if isinstance(item, dict):
            items.append(item)
        else:
            bad += 1
    return items, end, bad


def read_from_cursor(home: Path) -> tuple[list[dict], int]:
    """The readable items nobody has handled yet and the byte offset just past them; unreadable lines are skipped (see read_counted)."""
    items, end, _ = read_counted(home)
    return items, end


def advance(home: Path, offset: int) -> None:
    p = Path(home) / CURSOR
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(f"{int(offset)}\n".encode("ascii"))


def pending(home: Path) -> list[dict]:
    """The queued items not yet handled by `drew notify drain`."""
    return read_from_cursor(home)[0]
