"""The Projects page filters (DESK-0207): Assigned to, Tag, Type and Company, each a set of values held in the address.

A filter narrows the tickets, and a project or a track is drawn only when at least one of its tickets is left. The number beside a
choice is the open tickets that choice would leave, given the other filters, so it equals the open tickets in the rows it draws.
Assigned to lists people by display name first, then code sessions as "<project> session" (people.People.facet: an assignee that is
a project key is that project's own Claude Code session). A company is the project's area (companies.py)."""

from __future__ import annotations

from collections import Counter

from . import boardfilter as bf, companies as compmod, ops, people as peoplemod
from .store import Store
from .ticket import Ticket

FACETS = (("assignee", "Assigned to", "people"), ("tag", "Tag", "tags"), ("type", "Type", "types"), ("company", "Company", "companies"))
NAMES = tuple(f for f, _label, _plural in FACETS)
_GROUPS = [g for g, _kinds in peoplemod.GROUPS]


class Facets:
    """The four filters over one store: what each ticket carries, what a selection keeps, and the choices with their open counts."""

    def __init__(self, store: Store, people: peoplemod.People):
        self.people = people
        self.projects = list(store.projects())
        self._company = {k: compmod.of(store, k) for k in self.projects}
        self._company_names = {compmod.slug(n): n for n in self._company.values()}
        self._who: dict[str, tuple[str, str, str]] = {}

    def who(self, value: str) -> tuple[str, str, str]:
        """(key, group, label) of an assignee as written; one lookup per distinct value."""
        if value not in self._who:
            self._who[value] = self.people.facet(value, self.projects)
        return self._who[value]

    def values(self, t: Ticket) -> dict[str, set[str]]:
        a = ops.assignee_of(t)
        return {"assignee": {self.who(a)[0]} if a else set(), "tag": set(bf.tags_of(t)), "type": {bf.type_of(t)} - {""},
                "company": {compmod.slug(self._company.get(t.project) or compmod.UNSORTED)}}

    def selection(self, assignee, tag, type_, company) -> dict[str, list[str]]:
        """The address's values: an assignee as written (`temujin`, `Patch`) becomes its key (`code:temujin`, `main`)."""
        who = []
        for a in bf.split(assignee):
            k = self.who(a)[0]
            if k not in who:
                who.append(k)
        return {"assignee": who, "tag": bf.split(tag), "type": [x.lower() for x in bf.split(type_)], "company": bf.split(company)}

    @staticmethod
    def keep(vals: dict[str, set[str]], want: dict[str, set[str]], skip: str = "") -> bool:
        return all(f == skip or not want[f] or vals[f] & want[f] for f in NAMES)

    def label(self, facet: str, value: str) -> str:
        if facet == "assignee":
            return self.who(value)[2]
        if facet == "type":
            return bf.type_label(value)
        if facet == "company":
            return compmod.title(self._company_names.get(value, value))
        return value

    def apply(self, tickets: list[Ticket], sel: dict) -> tuple[list[Ticket], dict]:
        """(the tickets the selection keeps, {facet: [(group heading, [Opt])]}). Only tickets of a known project count: the page draws no other."""
        known = set(self.projects)
        rows = [(t, self.values(t)) for t in tickets if t.project in known]
        want = {f: set(sel[f]) for f in NAMES}
        kept = [t for t, v in rows if self.keep(v, want)]
        opts = {}
        for f in NAMES:
            n_open: Counter = Counter()
            seen = set(want[f])
            for t, v in rows:
                if self.keep(v, want, f):
                    seen |= v[f]
                    for x in v[f]:
                        n_open[x] += t.is_open
            choices = [bf.Opt(x, self.label(f, x), n_open.get(x, 0), x in want[f]) for x in seen]
            opts[f] = self._group(f, choices)
        return kept, opts

    def _group(self, facet: str, choices: list) -> list[tuple[str, list]]:
        by_label = lambda o: (o.label.lower(), o.value)  # noqa: E731
        if facet != "assignee":
            return [("", sorted(choices, key=by_label))]
        groups: dict[str, list] = {}
        for o in choices:
            groups.setdefault(self.who(o.value)[1], []).append(o)
        return [(g, sorted(groups[g], key=by_label)) for g in _GROUPS if g in groups]
