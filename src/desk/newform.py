"""The New ticket form (DESK-0189): what the form offers, what it checks, and how it writes.

It writes through the same pieces as `drew new` (`Store.create` for the next id and the template sections, `Store.save` for the
validation and the file) so a ticket made in the browser and one made on the command line are the same kind of file. The form adds
what `drew new` has no flags for: creator, associated, track, sprint, Context, Outcome, Acceptance and How to validate lines.
Docs: docs/COMMANDS.md (In the UI)."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import assign_rules, deadlines, ops, people as peoplemod, sprints as sprintmod, tracks as trk
from .rules import RuleError
from .store import Store
from .ticket import PRIORITIES, SIZES, TYPE_HELP, TYPES, Ticket

# What the form posts. Every key is optional on the wire; `check` says which ones a ticket cannot do without.
KEYS = ("project", "title", "type", "priority", "size", "owner", "assignee", "associated", "track", "sprint", "deadline", "deadline_kind", "context", "outcome", "acceptance", "steps",
        "parent")  # DESK-0208: Part of, the main ticket a subticket belongs to
SAY = {
    "project": "Pick the project this ticket belongs to.",
    "title": "Give the ticket a title, a few words, starting with what you want done (Approve, Choose, Fix, Add).",
    "acceptance": "Write at least one Acceptance line: something a person can check to say this is done.",
}


@dataclass
class Options:
    """The dropdowns, read from the store at the moment the form is drawn."""

    projects: list[tuple[str, str]]
    types: list[tuple[str, str]]
    priorities: list[str]
    sizes: list[str]
    groups: list[tuple[str, list[peoplemod.Person]]]
    tracks: list[str]
    sprints: list[str]
    default_owner: str = "main"


def options(store: Store) -> Options:
    pe = peoplemod.load(store.home)
    tracks = sorted({trk.track_of(t) for t in store.tickets() if trk.track_of(t)}, key=str.lower)
    return Options(projects=[(k, store.project_title(k)) for k in store.projects()], types=[(t, TYPE_HELP.get(t, "")) for t in TYPES],
                   priorities=list(PRIORITIES), sizes=list(SIZES), groups=pe.groups(), tracks=tracks,
                   sprints=list(dict.fromkeys(s.name for s in sprintmod.load(store.home))))


def lines(text: str) -> list[str]:
    """One entry per non-blank line; a leading list marker or check box the person typed is dropped."""
    out = []
    for ln in (text or "").splitlines():
        ln = ln.strip()
        for mark in ("- [ ]", "- [x]", "* [ ]", "-", "*", "+"):
            if ln.startswith(mark + " ") or ln == mark:
                ln = ln[len(mark):].strip()
                break
        if ln:
            out.append(ln)
    return out


def clean(values: dict) -> dict:
    """The posted values trimmed, every key present. `associated` is a list of keys."""
    v = {k: str(values.get(k) or "").strip() for k in KEYS if k != "associated"}
    raw = values.get("associated") or []
    v["associated"] = [str(x).strip() for x in (raw if isinstance(raw, (list, tuple)) else [raw]) if str(x).strip()]
    for k in ("context", "outcome", "acceptance", "steps"):
        v[k] = str(values.get(k) or "").replace("\r\n", "\n").strip()
    v["title"] = " ".join(v["title"].split())
    return v


def check(store: Store, values: dict) -> dict[str, str]:
    """Plain-word problems by field; empty when the ticket can be written. Nothing is written here."""
    v = clean(values)
    pe = peoplemod.load(store.home)
    errors: dict[str, str] = {}
    if not v["project"]:
        errors["project"] = SAY["project"]
    elif v["project"] not in store.projects():
        errors["project"] = f"Desk has no project called {v['project']}. Pick one from the list."
    if not v["title"]:
        errors["title"] = SAY["title"]
    if v["type"] and v["type"] not in TYPES:
        errors["type"] = "That type is not one Desk knows. Pick one from the list."
    if v["priority"] and v["priority"] not in PRIORITIES:
        errors["priority"] = "Priority is p0, p1, p2 or p3."
    if v["size"] and v["size"] not in SIZES:
        errors["size"] = "Size is xs, s, m or l."
    for k, label in (("owner", "Owner"), ("assignee", "Assignee")):
        if v[k] and pe.get(v[k]) is None:
            errors[k] = f"{label} {v[k]} is not in the people list. Pick someone from the list."
    for who in v["associated"]:
        if pe.get(who) is None:
            errors["associated"] = f"{who} is not in the people list. Pick someone from the list."
    if v["sprint"] and sprintmod.norm(v["sprint"]) not in sprintmod.names(store):
        errors["sprint"] = f"There is no sprint called {v['sprint']}. Pick one from the list, or leave it empty."
    if v["deadline"]:
        try:
            deadlines.parse_arg(v["deadline"])
        except ValueError as e:
            errors["deadline"] = str(e)
    if v["deadline_kind"] and v["deadline_kind"] not in deadlines.KINDS:
        errors["deadline_kind"] = "Kind of deadline is hard, soft or sprint."
    if not lines(v["acceptance"]):
        errors["acceptance"] = SAY["acceptance"]
    if v["parent"]:
        try:
            ops.main_for_sub(store, v["parent"])
        except RuleError as e:
            errors["parent"] = str(e)
    return errors


def build(store: Store, values: dict, creator: str) -> Ticket:
    """The ticket the form describes, not yet saved: next id, template sections, creator, owner, assignee, associated."""
    v = clean(values)
    pe = peoplemod.load(store.home)
    t = store.create(v["title"], v["project"], v["type"] or "feature", priority=v["priority"] or "p2", size=v["size"] or "s",
                     owner=pe.resolve(v["owner"] or "main"), assignee=pe.resolve(v["assignee"]) if v["assignee"] else None)
    t.meta["creator"] = pe.resolve(creator) if creator else store.owner
    if v["associated"]:
        t.meta["associated"] = [pe.resolve(x) for x in v["associated"]]
    if v["track"]:
        t.meta["track"] = v["track"]
    if v["sprint"]:
        t.meta["sprint"] = next((s.name for s in sprintmod.load(store.home) if sprintmod.norm(s.name) == sprintmod.norm(v["sprint"])), v["sprint"])
    if v["deadline"]:
        t.meta["deadline"] = deadlines.parse_arg(v["deadline"]).isoformat()
        t.meta["deadline_kind"] = v["deadline_kind"] or "hard"
    if v["parent"]:
        t.meta["parent"] = ops.main_for_sub(store, v["parent"]).id
    if v["context"]:
        t.sections["Context"] = v["context"] + "\n"
    if v["outcome"]:
        t.sections["Outcome"] = v["outcome"] + "\n"
    t.sections["Acceptance"] = "".join(f"- [ ] {ln}\n" for ln in lines(v["acceptance"]))
    steps = lines(v["steps"])
    if steps:  # nothing typed keeps the template's hint, which `drew validate` and the review rule already read
        t.sections["How to validate"] = "".join(f"- {ln}\n" for ln in steps)
    return t


def create(store: Store, values: dict, creator: str) -> Ticket:
    """Check, build, validate and write. Raises ValueError with `.errors` (field -> sentence) when the form is not ready, and
    ValueError with the validator's own words when the finished file would not pass `drew validate`; nothing is written then."""
    errors = check(store, values)
    if errors:
        e = ValueError(" ".join(errors.values()))
        e.errors = errors  # type: ignore[attr-defined]
        raise e
    store.sync_before()
    t = build(store, values, creator)
    try:
        store.save(t)
    except (RuleError, ValueError) as err:
        e = ValueError(f"Desk checked the ticket and did not save it: {err}")
        e.errors = {"form": str(e)}  # type: ignore[attr-defined]
        raise e from err
    if not t.meta.get("assignee"):  # the same fill from assign.yaml that `drew new` does
        try:
            assign_rules.apply_new(store, t)
        except assign_rules.RulesError:
            pass
    store.sync_after(f"desk: new {t.id}")
    return t
