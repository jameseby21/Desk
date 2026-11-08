"""Lifecycle rules. Every transition and `desk validate` go through here."""

from __future__ import annotations

import datetime as dt
import re
from typing import TYPE_CHECKING

from . import people
from .store import id_parts
from .ticket import CLOSED, ID_LISTS_LOOSE, ID_LISTS_STRICT, PRIORITIES, REQUIRED, LEGACY_TYPES, SIZES, STATUSES, TYPES, Ticket, canon_type, parse_checks, validate_steps

if TYPE_CHECKING:
    from .store import Store

OWNER_ROLES = {"main", "james"}  # the roles that may throw a ticket into `ready`
MIN_WORDS = 4
TITLE_MAX = 70  # longer titles get a warning from `desk validate`
REVIEW_MAX_DAYS = 7  # a ticket untouched in review this long gets a warning


class RuleError(Exception):
    """A refused transition, with a plain reason."""


# Allowed moves; `blocked` and `rejected` are reachable from any open status.
ALLOWED = {
    "inbox": {"triage", "ready"},
    "triage": {"ready", "inbox"},
    "ready": {"claimed", "triage"},
    "claimed": {"in_progress", "ready"},
    "in_progress": {"review"},
    "review": {"approved", "changes_requested"},
    "changes_requested": {"in_progress"},
    "approved": {"done"},
    "blocked": set(),  # unblock restores blocked_from
    "done": set(),
    "rejected": set(),
}


def _date_ok(v) -> bool:
    try:
        dt.date.fromisoformat(str(v)[:10])
        return True
    except (TypeError, ValueError):
        return False


def _datetime_ok(v) -> bool:
    """An ISO date or an ISO timestamp."""
    try:
        dt.datetime.fromisoformat(str(v))
        return True
    except (TypeError, ValueError):
        return _date_ok(v) and len(str(v)) == 10


def validate_ticket(t: Ticket, store: "Store", known_ids: set[str] | None = None) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for one ticket's frontmatter and section invariants."""
    errors: list[str] = []
    warnings: list[str] = []
    m = t.meta
    where = t.id or (t.path.name if t.path else "ticket")

    for k in REQUIRED:
        if k not in m or m[k] in (None, ""):
            errors.append(f"{where}: missing required field `{k}`")
    if errors:
        return errors, warnings

    parts = id_parts(str(m["id"]))
    if not parts:
        errors.append(f"{where}: id must look like KEY-0001")
    elif parts[0] not in store.prefixes_for(str(m["project"])):
        errors.append(f"{where}: id prefix {parts[0]} does not match project `{m['project']}` (prefix or aliases)")
    if m["type"] in LEGACY_TYPES:
        warnings.append(f"{where}: legacy type `{m['type']}`; run drew migrate type")
    for field_, allowed in (("type", TYPES + list(LEGACY_TYPES)), ("status", STATUSES), ("priority", PRIORITIES), ("size", SIZES)):
        if m[field_] not in allowed:
            errors.append(f"{where}: {field_} `{m[field_]}` is not one of {', '.join(allowed)}")
    for field_ in ("created", "updated", "review_by"):
        if m.get(field_) not in (None, "") and not _date_ok(m[field_]):
            errors.append(f"{where}: {field_} must be a YYYY-MM-DD date")
    for field_ in ("depends_on", "blocks", "related", "docs_to_update", "dod"):
        if field_ in m and m[field_] is not None and not isinstance(m[field_], list):
            errors.append(f"{where}: {field_} must be a list")
    if "tags" in m and m["tags"] not in (None, ""):
        if not (isinstance(m["tags"], list) and all(isinstance(x, str) and x.strip() for x in m["tags"])):
            errors.append(f"{where}: tags must be a list of strings")
    if "track" in m and (not isinstance(m["track"], str) or not m["track"].strip()):
        errors.append(f"{where}: track must be a non-empty string when present")
    if "sprint" in m and (not isinstance(m["sprint"], str) or not m["sprint"].strip()):
        errors.append(f"{where}: sprint must be a non-empty string when present")
    for field_ in ("assignee", "assigned_by", "handoff", "cloned_from", "evidence"):
        if field_ in m and m[field_] not in (None, "") and not isinstance(m[field_], str):
            errors.append(f"{where}: {field_} must be a string")
    if "assigned_at" in m and m["assigned_at"] not in (None, "") and not _datetime_ok(m["assigned_at"]):
        errors.append(f"{where}: assigned_at must be an ISO date or timestamp")
    if "done_at" in m and m["done_at"] not in (None, "") and not _datetime_ok(m["done_at"]):
        errors.append(f"{where}: done_at must be an ISO date or timestamp (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)")
    if len(str(m["title"])) > TITLE_MAX:
        warnings.append(f"{where}: title is {len(str(m['title']))} characters; keep it under {TITLE_MAX}")
    if "confidence" in m and m["confidence"] not in (None, ""):
        try:
            c = float(m["confidence"])
            if not 0 <= c <= 1:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"{where}: confidence must be between 0 and 1")
    if "effort_hours" in m and m["effort_hours"] not in (None, "", []):
        eh = m["effort_hours"]
        if not (isinstance(eh, list) and len(eh) == 2 and all(isinstance(x, (int, float)) for x in eh) and eh[0] <= eh[1]):
            errors.append(f"{where}: effort_hours must be [low, high]")

    projects = store.projects()
    if projects and str(m["project"]) not in projects:
        errors.append(f"{where}: unknown project `{m['project']}` (no projects/{m['project']}.md)")
    elif not projects:
        warnings.append(f"{where}: no projects/ directory yet, project `{m['project']}` unchecked")

    ids = known_ids if known_ids is not None else {x.id for x in store.tickets()}
    for field_ in ID_LISTS_STRICT:
        for dep in (m.get(field_) or []) if isinstance(m.get(field_), list) else []:
            if str(dep) not in ids:
                errors.append(f"{where}: {field_} `{dep}` does not exist")
            elif str(dep) == t.id:
                errors.append(f"{where}: {field_} itself")
    for field_ in ID_LISTS_LOOSE:
        for rel in (m.get(field_) or []) if isinstance(m.get(field_), list) else []:
            if str(rel) not in ids:
                warnings.append(f"{where}: {field_} `{rel}` does not exist")
    if m.get("cloned_from") and str(m["cloned_from"]) not in ids:
        warnings.append(f"{where}: cloned_from `{m['cloned_from']}` does not exist")

    status = m["status"]
    if status == "review" and not t.section("Report"):
        errors.append(f"{where}: status `review` needs a non-empty Report")
    if status in ("approved", "done") and not re.search(r"·\s*approved\s*·", t.section("Decision")):
        errors.append(f"{where}: status `{status}` needs an approval line in Decision")
    if status == "changes_requested" and not re.search(r"·\s*changes_requested\s*·", t.section("Decision")):
        errors.append(f"{where}: status `changes_requested` needs a send-back line in Decision")
    if status == "blocked" and not str(m.get("blocked_on") or "").strip():
        errors.append(f"{where}: status `blocked` needs `blocked_on`")
    if status == "rejected" and not t.section("Decision"):
        errors.append(f"{where}: status `rejected` needs a reason in Decision")
    if status == "review" and _date_ok(m.get("updated")):
        since = (dt.date.today() - dt.date.fromisoformat(str(m["updated"])[:10])).days
        if since > REVIEW_MAX_DAYS:
            warnings.append(f"{where}: in review for {since} days (untouched since {str(m['updated'])[:10]}); decide or send back")
    if status == "review" and not t.validate_steps():
        warnings.append(f"{where}: in review with no How to validate (legacy ticket); write the steps")
    if status in ("ready", "in_progress", "review") and not validate_steps(t.sections.get("Tests") or ""):
        warnings.append(f"{where}: no Tests section text on a {status} ticket; name the automated test, or the steps a person performs and what they must see (`drew migrate tests` seeds old tickets)")
    if m["size"] == "l" and status not in ("inbox", "triage"):
        warnings.append(f"{where}: size `l` should be split before leaving triage")
    if canon_type(m["type"]) in ("feature", "upgrade") and status in ("ready", "claimed", "in_progress") and not t.acceptance():
        warnings.append(f"{where}: a ready {m['type']} should have Acceptance checkboxes")
    legacy = sum(1 for name in ("Acceptance", "How to validate", "Prompt", "Evidence") for c in parse_checks(t.sections.get(name) or "") if c.legacy)
    if legacy:
        warnings.append(f"{where}: {legacy} inline `not accepted` line(s) (the older form, still read); `drew migrate checks --dry-run` shows the quoted-reason form")
    return errors, warnings


def validate_store(store: "Store") -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    tickets = store.tickets()
    ids = [t.id for t in tickets]
    seen: set[str] = set()
    for t in tickets:
        if t.id in seen:
            errors.append(f"{t.id}: duplicate id ({t.path.name if t.path else ''})")
        seen.add(t.id)
        if t.path and not t.path.name.startswith(t.id + "-"):
            warnings.append(f"{t.id}: file name {t.path.name} does not start with the id")
    known = set(ids)
    by_id = {t.id: t for t in tickets}
    for key in store.projects():
        if store.brand_for(key) is None:
            warnings.append(f"projects/{key}.md: no brand block (mark, accent, family); run `drew brand-check`")
    req = store.assignee_required_from
    for t in tickets:
        e, w = validate_ticket(t, store, known)
        errors += e
        warnings += w
        if t.is_open and not " ".join(str(t.meta.get("assignee") or "").split()):  # blank is James's "not yet scanned" marker (DESK-0167)
            (errors if req and dt.date.today() >= req else warnings).append(f"{t.id}: no assignee (James has not scanned this)")
        from .docs import dead_links  # local import: docs depends on ticket only, keep rules import-light

        for link, tried in dead_links(store, t):
            warnings.append(f"{t.id}: doc link `{link}`: dead link (tried: {', '.join(tried)})")
        if t.status == "done":
            for dep in t.meta.get("depends_on") or []:
                d = by_id.get(str(dep))
                if d and d.status != "done":
                    errors.append(f"{t.id}: is done but depends on {dep} which is {d.status}")
    from . import sprints as sprintmod

    known_sprints = sprintmod.names(store)
    for t in tickets:  # DESK-0147: a sprint field must name a sprint in sprints.yaml
        w = sprintmod.unknown_warning(t, known_sprints)
        if w:
            warnings.append(w)
    pe = people.load(store.home)
    for t in tickets:  # DESK-0151: user-facing work is designed by Chaz first
        w = ui_design_warning(t, by_id, pe)
        if w:
            warnings.append(w)

    from . import repeats as repeatmod

    for r in repeatmod.repeats(store, tickets):  # DESK-0158: repetition is an emergency
        warnings.append(r.message(store.owner))
    for t, at in repeatmod.stale_handoffs(store, tickets):
        warnings.append(repeatmod.stale_message(store, t, at))

    from .dispatch import undispatched, warning as undispatched_warning  # local import: dispatch reads people and the store

    for t, hours in undispatched(store, tickets=tickets):  # DESK-0166: ready, unblocked, Patch's, nobody started
        warnings.append(undispatched_warning(store, t, hours))
    return errors, warnings


_DESIGN_NONE = re.compile(r"^\s*design:\s*none\s*-\s*\S", re.I | re.M)


def ui_design_warning(t: Ticket, by_id: dict, pe) -> str | None:
    """A ready, claimed or in-progress ticket with `ui` in its dod needs a ticket assigned to Chaz among its depends_on or related
    (the design), unless it is a bug, is itself Chaz's, or its Context says `design: none - <reason>`."""
    if t.status not in ("ready", "claimed", "in_progress") or "ui" not in [str(x) for x in (t.meta.get("dod") or [])]:
        return None
    if canon_type(str(t.meta.get("type"))) == "bug" or pe.resolve(str(t.meta.get("assignee") or "")) == "chaz" or _DESIGN_NONE.search(t.section("Context")):
        return None
    for ref in [*(t.meta.get("depends_on") or []), *(t.meta.get("related") or [])]:
        other = by_id.get(str(ref))
        if other is not None and pe.resolve(str(other.meta.get("assignee") or "")) == "chaz":
            return None
    return (f"{t.id}: ui work with no Chaz design ticket linked (user-facing work is designed by Chaz first); add a ticket assigned to chaz to "
            "depends_on or related, or write `design: none - <reason>` in Context")


def _words(reason: str | None) -> str:
    reason = (reason or "").strip()
    if len(reason) < MIN_WORDS:
        raise RuleError("Decisions need words: give a reason of at least four characters.")
    return reason


def check_transition(t: Ticket, new: str, store: "Store", *, actor: str | None = None, reason: str | None = None) -> None:
    """Raise RuleError if `t` may not move to `new`."""
    cur = t.status
    if new not in STATUSES:
        raise RuleError(f"`{new}` is not a status")
    if new == cur:
        raise RuleError(f"{t.id} is already {cur}")
    if cur in CLOSED:
        raise RuleError(f"{t.id} is {cur}; closed tickets do not move")
    if new in ("blocked", "rejected"):
        if new == "blocked" and not str(t.meta.get("blocked_on") or "").strip():
            raise RuleError("Stuck needs a name: set blocked_on to the ticket or person it waits on.")
        if new == "rejected":
            _words(reason)
        return
    if cur == "blocked":
        raise RuleError(f"{t.id} is blocked; use `desk unblock` first")
    allowed = ALLOWED.get(cur, set())
    if canon_type(t.meta.get("type")) == "decision" and cur == "ready":
        allowed = allowed | {"approved", "changes_requested"}  # a decision ticket is answered where it stands; nothing is built, so no review step
    if new not in allowed:
        raise RuleError(f"{t.id}: {cur} → {new} is not an allowed move")
    if new == "ready" and str(t.meta.get("owner")) not in OWNER_ROLES and (actor or "") not in OWNER_ROLES:
        raise RuleError("Only main or the owner throws a ticket into `ready`.")
    if new == "claimed":
        who = actor or str(t.meta.get("owner"))
        if who in OWNER_ROLES:
            raise RuleError("A project session claims a ticket, not main or the owner. Pass --owner <project or name>.")
    if new == "review" and not t.section("Report"):
        raise RuleError("`review` needs a non-empty Report. Use `desk report`.")
    if new == "review" and not t.validate_steps():
        raise RuleError("Write How to validate first: the steps that confirm the Outcome, before the work is handed over.")
    if new in ("approved", "changes_requested"):
        _words(reason)
        if new == "approved":
            who = actor or "main"
            if who != store.owner and not store.delegated(t):
                raise RuleError(
                    f"Only {store.owner} approves this ticket (type {t.meta.get('type')}, size {t.meta.get('size')} is not delegated)."
                )
    if new == "done":
        by_id = store.by_id()
        for dep in t.meta.get("depends_on") or []:
            d = by_id.get(str(dep))
            if d is None or d.status != "done":
                raise RuleError(f"{t.id} cannot be done: depends on {dep} which is {d.status if d else 'missing'}")
