"""Brand check: which projects have no `brand:` block or no mark file, and optionally ask Chaz for them."""

from __future__ import annotations

from . import ops
from .rules import RuleError
from .store import Store
from .ticket import CLOSED

BRAND_PROJECT = "brand"
TITLE_MAX = 60


def missing(store: Store) -> list[tuple[str, str]]:
    """[(project key, reason)] for projects with no brand block or whose mark file is missing."""
    out = []
    for key in store.projects():
        b = store.brand_for(key)
        if b is None:
            out.append((key, "no brand block"))
        elif not b["mark_url"]:
            out.append((key, f"mark file missing ({b['mark'] or 'no mark path'})"))
    return out


def title_for(store: Store, key: str) -> str:
    return f"Provide: mark and accent for {store.project_title(key)}"[:TITLE_MAX].rstrip()


def create(store: Store, assignee: str = "chaz") -> tuple[list[str], list[str]]:
    """Open one ticket per missing project in project `brand`. Returns (created ids, skipped ids already open)."""
    if BRAND_PROJECT not in store.projects():
        raise RuleError(f"No project profile `{BRAND_PROJECT}` (projects/{BRAND_PROJECT}.md); add it first.")
    open_titles = {str(t.meta.get("title")): t.id for t in store.tickets()
                   if t.project == BRAND_PROJECT and t.status not in CLOSED}
    created, skipped = [], []
    for key, reason in missing(store):
        title = title_for(store, key)
        if title in open_titles:
            skipped.append(open_titles[title])
            continue
        t = ops.new(store, title, BRAND_PROJECT, "chore", owner="main", assignee=assignee)
        t.set_section("Context", f"Project `{key}` has {reason}. Cards and ticket pages show the project's mark and accent.")
        t.set_section("Acceptance", f"- [ ] Mark file at assets/brand/{key}/mark.svg\n"
                                    f"- [ ] Accent colour (hex) and font family chosen\n"
                                    f"- [ ] `brand:` block added to projects/{key}.md")
        store.save(t)
        open_titles[title] = t.id
        created.append(t.id)
    return created, skipped
