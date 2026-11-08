"""Restart detection. The running process remembers the build commit it imported (`_build.COMMIT`); the wheel also carries the
same commit in a `.build-stamp` file next to the templates. When the files on disk were replaced by a newer build, the two differ
and the UI says so instead of failing half-way with new templates on old code."""

from __future__ import annotations

from pathlib import Path

STAMP_NAME = ".build-stamp"


def _imported() -> str:
    try:
        from . import _build  # type: ignore[attr-defined]

        return str(getattr(_build, "COMMIT", "") or "")
    except ImportError:  # a checkout: no stamp, nothing to compare
        return ""


RUNNING = _imported()  # fixed when this process imported the package


def on_disk(templates_dir: Path) -> str:
    try:
        return (Path(templates_dir) / STAMP_NAME).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def stale(templates_dir: Path) -> bool:
    disk = on_disk(templates_dir)
    return bool(RUNNING and disk and disk != RUNNING)


STALE_TEXT = "Desk was updated on disk; restart drew-ui to load it"
