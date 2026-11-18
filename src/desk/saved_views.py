"""Saved views (DESK-0290): a named board or search filter, pinned in the header of every page.

They live in the store as `views.yaml` (a list of `{name, url}`), so every device that reads the store sees the same views and the
store's sync commits them like a ticket. Only Desk's own pages are kept: a URL must start with /board or /search, so a view can never
point anywhere else. Removing a view deletes its line and nothing more."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

from . import yamlio

FILE = "views.yaml"
MAX_VIEWS = 12
MAX_NAME = 40
PAGES = ("/board", "/search")
_NAME = re.compile(r"^[^\x00-\x1f<>]{1,%d}$" % MAX_NAME)


def path(home: Path) -> Path:
    return Path(home) / FILE


def load(home: Path) -> list[dict]:
    """The saved views in their order; an unreadable or missing file is no views (never an error on every page)."""
    p = path(home)
    try:
        data = yamlio.load(p.read_text(encoding="utf-8")) if p.is_file() else None
    except Exception:
        return []
    out = []
    for row in data or []:
        if isinstance(row, dict) and row.get("name") and clean_url(str(row.get("url") or "")):
            out.append({"name": str(row["name"]).strip()[:MAX_NAME], "url": clean_url(str(row["url"]))})
    return out[:MAX_VIEWS]


ONE_OFF = ("undo", "msg", "error")  # a write's message in the address (the DESK-0284 toast); a view keeps the filter, not the message


def clean_url(url: str) -> str:
    """The URL when it is one of Desk's filter pages (path and query only, one-off message parameters left out), else ''."""
    u = urlsplit((url or "").strip())
    if u.scheme or u.netloc or u.path not in PAGES:
        return ""
    q = "&".join(p for p in u.query.split("&") if p and p.split("=", 1)[0] not in ONE_OFF)
    return u.path + ("?" + q if q else "")


def _write(home: Path, views: list[dict]) -> None:
    text = "# Saved views (DESK-0290): pinned in Desk's header. Edit here or on /views.\n" + (
        yamlio.dump(views, sort_keys=False, allow_unicode=True, default_flow_style=False, width=1000) if views else "[]\n")
    path(home).write_bytes(text.encode("utf-8"))


def add(home: Path, name: str, url: str, at: int | None = None) -> list[dict]:
    """Save `url` under `name`; the same name again replaces that view in place. A new view goes last, or at position `at` (how
    Undo puts a removed view back where it was). Raises ValueError with a plain sentence."""
    name = " ".join((name or "").split())
    url = clean_url(url)
    if not name or not _NAME.match(name):
        raise ValueError(f"Give the view a name of 1 to {MAX_NAME} characters.")
    if not url:
        raise ValueError("Only a board or search page can be saved as a view.")
    views = load(home)
    for v in views:
        if v["name"].lower() == name.lower():
            v["name"], v["url"] = name, url
            break
    else:
        if len(views) >= MAX_VIEWS:
            raise ValueError(f"There are already {MAX_VIEWS} saved views. Remove one on the Views page first.")
        views.insert(len(views) if at is None else max(0, min(at, len(views))), {"name": name, "url": url})
    _write(home, views)
    return views


def remove(home: Path, name: str) -> bool:
    """Delete the view called `name`; True when one was removed."""
    views = load(home)
    keep = [v for v in views if v["name"].lower() != " ".join((name or "").split()).lower()]
    if len(keep) == len(views):
        return False
    _write(home, keep)
    return True
