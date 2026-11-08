"""Companies, the level above projects (DESK-0183): Ticket < Track < Project < Company < jeby overall.

Until DESK-0181 adds company profiles and a `company:` field, a company is the `area:` value of a project profile (the default
recorded on DESK-0183, 2026-11-08). A profile without `area:` lands in "Unsorted", which the board says out loud. This module is the
one place that rule lives; DESK-0181 replaces it here and nowhere else."""

from __future__ import annotations

import re

from .store import Store
from .ticket import slugify

UNSORTED = "Unsorted"
UNSORTED_NOTE = "projects with no area in their profile"
ALL = "*"  # every company at once (the old project-level board, kept for Expand all and old links)
_PAREN = re.compile(r"^(.*?)\s*\(([^)]*)\)\s*$")


def of(store: Store, project: str) -> str:
    """The company a project belongs to: its profile's `area:`, or Unsorted."""
    return str(store.project_profile(project)[0].get("area") or "").strip() or UNSORTED


def slug(name: str) -> str:
    return slugify(name, 60) or "unsorted"


def split(name: str) -> tuple[str, str]:
    """(title, subtitle): a trailing parenthetical becomes the muted subtitle. `Client work (JA Insights)` -> (`Client work`, `JA Insights`)."""
    m = _PAREN.match(name or "")
    if m and m.group(1).strip():
        return m.group(1).strip(), m.group(2).strip()
    return name, (UNSORTED_NOTE if name == UNSORTED else "")


def title(name: str) -> str:
    return split(name)[0]


def by_project(store: Store) -> dict[str, str]:
    """{project key: company name} for every project profile."""
    return {k: of(store, k) for k in store.projects()}


def by_slug(store: Store) -> dict[str, str]:
    """{company slug: company name}."""
    return {slug(n): n for n in by_project(store).values()}
