"""Companies, the level above projects (DESK-0183, DESK-0181): Ticket < Track < Project < Company < jeby overall.

A project's company is the `company:` key in its profile, shown by the name desk.toml [hierarchy.companies] gives it (hierarchy.py);
an undeclared key shows as written and `drew validate` says so. A profile without `company:` falls back to its `area:` (the default
recorded on DESK-0183, 2026-11-08), and one with neither lands in "Unsorted", which the board says out loud. This module is the one
place that rule lives."""

from __future__ import annotations

import re

from . import hierarchy
from .store import Store
from .ticket import slugify

UNSORTED = "Unsorted"
UNSORTED_NOTE = "projects with no company or area in their profile"
ALL = "*"  # every company at once (the old project-level board, kept for Expand all and old links)
_PAREN = re.compile(r"^(.*?)\s*\(([^)]*)\)\s*$")


def of(store: Store, project: str) -> str:
    """The company a project belongs to: the shown name of its profile's `company:`, else its `area:`, else Unsorted."""
    key = hierarchy.company_key(store, project)
    if key:
        return hierarchy.company_names(store).get(key, key)
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
