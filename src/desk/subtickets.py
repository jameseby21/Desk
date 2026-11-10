"""Subtickets (DESK-0208): a small piece of a bigger ticket, such as "have Chaz create an icon for this feature".

A subticket names its main ticket in frontmatter `parent: <ID>` (any project). There is no children field: a main ticket's subtickets
are the tickets that name it, worked out here. One level is enough; a main ticket that is itself part of another is a validate warning.

A subticket is tracked, assigned and updated like any ticket, but it does not clutter the boards: the board, the Projects page, the
company rows and Today's counts show main tickets only, and each main ticket carries its subtickets' progress ("Subtasks: 1 of 3
done", the next open one and who holds it). The people who hold a subticket still see it in their own views (Needs me, In hand,
search, the coworker entry, `drew list --assignee`), labelled "part of <ID>: <title>". A main ticket cannot move to done while one
of its subtickets is open. A ticket whose parent is missing stays on the boards, so nothing disappears; `drew validate` names it.
Docs: docs/COMMANDS.md (drew sub)."""

from __future__ import annotations

from dataclasses import dataclass

from .ticket import CLOSED, PRIORITIES, Ticket

LABEL_TITLE = 60  # characters of the main ticket's title in a "part of" label


def parent_of(t: Ticket) -> str:
    """The id a ticket names in `parent:`, as written (trimmed, upper case); '' for a main ticket."""
    v = t.meta.get("parent")
    return str(v).strip().upper() if isinstance(v, str) else ""


def _order(t: Ticket) -> tuple:
    p = str(t.meta.get("priority") or "")
    return (PRIORITIES.index(p) if p in PRIORITIES else len(PRIORITIES), str(t.meta.get("review_by") or "9999")[:10], t.id)


@dataclass
class Rollup:
    """A main ticket's subtickets in one line. Let-go subtickets count in neither number, the same as the company row's done share."""

    done: int
    total: int
    open: int
    next: Ticket | None  # the open subticket to do first: priority, then review-by, then id

    @property
    def text(self) -> str:
        return f"Subtasks: {self.done} of {self.total} done"

    @property
    def next_who(self) -> str:
        return " ".join(str(self.next.meta.get("assignee") or "").split()) if self.next is not None else ""


class Family:
    """Who is part of what, over one list of tickets (normally every ticket in the store)."""

    def __init__(self, tickets: list[Ticket]):
        self.by_id = {t.id: t for t in tickets}
        self.kids: dict[str, list[Ticket]] = {}
        for t in tickets:
            p = parent_of(t)
            if p and p != t.id and p in self.by_id:
                self.kids.setdefault(p, []).append(t)
        for v in self.kids.values():
            v.sort(key=lambda t: t.id)

    def __bool__(self) -> bool:
        return bool(self.kids)

    def is_sub(self, t: Ticket) -> bool:
        """True when the ticket names an existing main ticket other than itself."""
        p = parent_of(t)
        return bool(p) and p != t.id and p in self.by_id

    def parent(self, t: Ticket) -> Ticket | None:
        return self.by_id.get(parent_of(t)) if self.is_sub(t) else None

    def mains(self, tickets: list[Ticket]) -> list[Ticket]:
        """The tickets that are not part of another: what boards and counts show."""
        if not self.kids:
            return list(tickets)
        return [t for t in tickets if not self.is_sub(t)]

    def subs(self, t: Ticket) -> list[Ticket]:
        """A main ticket's subtickets, oldest id first."""
        return list(self.kids.get(t.id, []))

    def rollup(self, t: Ticket) -> Rollup | None:
        kids = self.kids.get(t.id)
        if not kids:
            return None
        live = [k for k in kids if k.status != "rejected"]
        opened = sorted((k for k in kids if k.status not in CLOSED), key=_order)
        return Rollup(sum(1 for k in live if k.status == "done"), len(live), len(opened), opened[0] if opened else None)

    def label(self, t: Ticket) -> str:
        """`part of DESK-0183: Company rows` for a subticket, '' for a main ticket."""
        p = self.parent(t)
        return label_for(p) if p is not None else ""


def label_for(parent: Ticket) -> str:
    title = " ".join(str(parent.meta.get("title") or "").split())
    return f"part of {parent.id}: {title[:LABEL_TITLE]}{'…' if len(title) > LABEL_TITLE else ''}"


def open_subs(tickets: list[Ticket], t: Ticket) -> list[Ticket]:
    """The subtickets of `t` that are still open (not done, not let go)."""
    return [k for k in Family(tickets).subs(t) if k.status not in CLOSED]


def refusal(t: Ticket, opened: list[Ticket], who=lambda k: k) -> str:
    """The done rule's words: the main ticket, then each open subticket with its status and holder."""
    def one(k: Ticket) -> str:
        holder = " ".join(str(k.meta.get("assignee") or "").split())
        return f"{k.id} ({k.status.replace('_', ' ')}" + (f", {who(holder)}" if holder else "") + ")"

    n = len(opened)
    return (f"{t.id} cannot be done while {'its subticket' if n == 1 else f'{n} of its subtickets'} {'is' if n == 1 else 'are'} still open: "
            + ", ".join(one(k) for k in opened) + (". Finish it or let it go first." if n == 1 else ". Finish them or let them go first."))


def chain_errors(tickets: list[Ticket]) -> tuple[list[str], list[str]]:
    """(errors, warnings) across the store: a parent chain that loops back is an error (named once per loop); a main ticket that is
    itself part of another is a warning (one level is enough); a main ticket done or approved while a subticket is open is a warning."""
    by_id = {t.id: t for t in tickets}
    errors: list[str] = []
    warnings: list[str] = []
    looped: set[str] = set()
    for t in tickets:
        if not parent_of(t) or t.id in looped:
            continue
        seen, cur = [t.id], parent_of(t)
        while cur and cur in by_id:
            if cur in seen:
                loop = seen[seen.index(cur):] + [cur]
                if len(set(loop)) > 1 and not looped & set(loop):  # a ticket that names itself is validate_ticket's error, said once there
                    errors.append(f"{loop[0]}: parent chain loops back ({' → '.join(loop)}); a subticket names its main ticket, never its own subticket")
                looped |= set(loop)
                break
            seen.append(cur)
            cur = parent_of(by_id[cur])
    fam = Family(tickets)
    for t in tickets:
        p = fam.parent(t)
        if p is not None and t.id not in looped and fam.is_sub(p) and p.id not in looped:
            warnings.append(f"{t.id}: its main ticket {p.id} is itself part of {parent_of(p)}; one level is enough, so name {parent_of(p)} as the parent")
        if t.status in ("done", "approved") and t.id not in looped:
            opened = [k for k in fam.subs(t) if k.status not in CLOSED]
            if opened:
                warnings.append(f"{t.id}: is {t.status} but {len(opened)} subticket{'s are' if len(opened) != 1 else ' is'} still open ("
                                + ", ".join(f"{k.id} {k.status}" for k in opened) + ")")
    return errors, warnings
