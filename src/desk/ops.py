"""Operations shared by the CLI and the UI. Every write goes through `move`."""

from __future__ import annotations

import datetime as dt
import platform
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from . import identity, review
from .rules import RuleError, check_transition
from .secretscan import looks_secret, refusal as secret_refusal
from .store import Store, join_profile, split_profile
from .ticket import CLONED_SECTIONS, DEFAULT_SEVERITY, MIN_REASON, SEVERITIES, Ticket, parse_checks, reason_ok, render_check, slugify, today

PRIVATE_NAMES = {"private_context.md"}
PRIVATE_PARTS = {"jeby-private"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
_KEY = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _stamp(actor: str, what: str, at_time: bool = False, now: dt.datetime | None = None, decision: bool = False) -> str:
    """One record line. When the writer relays another person's words (`--relaying`, DESK-0261) the line says so beside the writer's own
    name, never as the relayed person: a Decision reads `<date> · patch, relaying james's words of <date> · approved`, a Comments line
    `<time> · patch · relaying james's words of <date>: <text>` (the person's key; `relay_names` shows the name on screen). Lines Desk writes itself (actor `desk`) are not relayed."""
    when = (now or dt.datetime.now()).strftime("%Y-%m-%d %H:%M") if at_time else today()
    ident = identity.CURRENT.get()
    if ident is not None and ident.relay_phrase and actor != "desk":
        if decision:
            actor = f"{actor}, {ident.relay_phrase}"
        else:
            what = f"{ident.relay_phrase}: {what}"
    return f"- {when} · {actor} · {what}"


_RELAYED = re.compile(r"\brelaying ([\w:.-]+)'s words of ")


def relay_names(text: str, pe) -> str:
    """View time only (DESK-0261): `relaying james's words of` -> `relaying James's words of`. The record keeps the key; a private
    person (the private people file) stays a key on screen too, as in DESK-0254."""
    def name(m):
        p = pe.get(m.group(1))
        return m.group(0) if p is None or p.private else f"relaying {p.name}'s words of "
    return _RELAYED.sub(name, text or "")


DUPLICATE_WINDOW = dt.timedelta(minutes=10)  # byte-for-byte repeats (case and spacing aside) by one author
NEAR_WINDOW = dt.timedelta(seconds=60)  # near repeats: punctuation, brackets, a trailing counter or a typo or two (DESK-0142)
DUP_NOTICE = "That comment is already on the ticket"
NEAR_NOTICE = "That is almost the same as a comment you just posted, so it was ignored; the earlier one is highlighted"
_COMMENT_LINE = re.compile(r"^- (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) · (.+?) · (.*)$")


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def move(store: Store, t: Ticket, new: str, *, actor: str | None = None, reason: str | None = None, note: str | None = None,
         authority: str | None = None) -> Ticket:
    """`authority` is whose permission the rules check when the writer relays another person's words (DESK-0261); the record keeps `actor`."""
    check_transition(t, new, store, actor=authority or actor, reason=reason)
    old = t.status
    if new == "blocked":
        t.meta["blocked_from"] = old
    t.meta["status"] = new
    if note:
        t.append_line("Comments", _stamp(actor or "desk", note))
    store.save(t)
    _acted(store, t, actor)
    return t


def _acted(store: Store, t: Ticket, actor: str | None) -> None:
    """The store's owner just acted on `t`: Needs me (DESK-0149) compares later changes against this moment."""
    from . import needs_me

    needs_me.record_action(store, t, actor)


def new(store: Store, title: str, project: str, type_: str = "feature", parent: str = "", **kw) -> Ticket:
    if project not in store.projects() and store.projects():
        raise RuleError(f"Unknown project `{project}`. Add projects/{project}.md first.")
    t = store.create(title, project, type_, **kw)
    if parent:  # DESK-0208: written before the first save, so `drew validate` and the activity log see a subticket from the start
        t.meta["parent"] = parent
    store.save(t)
    return t


def main_for_sub(store: Store, parent_id: str) -> Ticket:
    """The main ticket a new subticket will be part of, or a RuleError in plain words: it must exist, be open, and not be a subticket
    itself (one level is enough). Shared by `drew sub` and the New ticket form's Part of field (DESK-0208)."""
    from . import subtickets

    pid = " ".join(str(parent_id or "").split()).upper()
    try:
        main = store.get(pid)
    except KeyError:
        raise RuleError(f"There is no ticket {pid} to be part of. Check the id (drew list shows them).") from None
    up = subtickets.parent_of(main)
    if up:
        raise RuleError(f"{main.id} is itself part of {up}. One level is enough: make the new ticket part of {up}.")
    if not main.is_open:
        raise RuleError(f"{main.id} is {main.status}, so it takes no new subtickets. Make a new ticket instead.")
    return main


def sub(store: Store, parent_id: str, title: str, project: str | None = None, type_: str = "task", **kw) -> Ticket:
    """`drew sub` (DESK-0208): a subticket of `parent_id`, written by `new` like any ticket. It lands in the main ticket's project and
    takes its priority unless others are named."""
    main = main_for_sub(store, parent_id)
    kw["priority"] = kw.get("priority") or str(main.meta.get("priority") or "p2")
    return new(store, title, project or main.project, type_, parent=main.id, **kw)


def claim(store: Store, t: Ticket, owner: str | None = None) -> Ticket:
    who = owner or t.project
    check_transition(t, "claimed", store, actor=who)
    t.meta["owner"] = who
    t.meta.setdefault("branch", f"{t.project}/{t.id}-{slugify(str(t.meta.get('title', '')), 32)}")
    if not t.meta.get("branch"):
        t.meta["branch"] = f"{t.project}/{t.id}-{slugify(str(t.meta.get('title', '')), 32)}"
    return move(store, t, "claimed", actor=who)


def start(store: Store, t: Ticket) -> Ticket:
    return move(store, t, "in_progress")


def report(store: Store, t: Ticket, text: str) -> Ticket:
    if not text.strip():
        raise RuleError("A report needs words: what changed, how it was verified, the commands run.")
    t.set_section("Report", text)
    if t.status in ("claimed", "ready"):
        # a session may skip `start`; the report still counts as work done
        t.meta["status"] = "in_progress"
    return move(store, t, "review")


def decide(store: Store, t: Ticket, approve: bool, reason: str, actor: str | None = None) -> Ticket:
    actor = actor or "main"
    new = "approved" if approve else "changes_requested"
    ident = identity.CURRENT.get()
    # A relayed decision is the relayed person's words, so their authority is checked; the record still names the real writer.
    authority = ident.relaying if ident is not None and ident.relaying else None
    check_transition(t, new, store, actor=authority or actor, reason=reason)
    if (what := looks_secret(reason)):  # rule 80: a secret never lands in a ticket
        raise RuleError(secret_refusal(what))
    t.append_line("Decision", f'{_stamp(actor, new, decision=True)} · "{reason.strip()}"')
    return move(store, t, new, actor=actor, reason=reason, authority=authority)


CHECK_SECTIONS = ("Acceptance", "How to validate", "Prompt", "Evidence")


def press(cur: str, which: str) -> tuple[str, bool]:
    """What a click on a box does: (new state char, needs a reason). `cur` is ' ' open, 'x' done or '!' not accepted; `which` is
    'done' or 'no' (the Not accepted box). A box that is on clears; a box that is off sets itself and so clears the other.
    The six moves: open > done, done > open, open > not accepted, not accepted > open, done > not accepted, not accepted > done."""
    if which == "done":
        return (" " if cur == "x" else "x"), False
    if which == "no":
        return (" ", False) if cur == "!" else ("!", True)
    raise ValueError(f"A box is 'done' or 'no', not {which!r}.")


def toggle_check(store: Store, ticket_id: str, section: str, index: int, checked: "bool | str | None" = None, actor: str | None = None,
                 reason: str = "", severity: str = "", evidence: "tuple | list" = ()) -> Ticket:
    """Set the `index`-th checkbox line of `section`; only that line (and the quoted lines under it) changes.
    `checked` is True/False (done/open), a state char (' ', 'x', '!'), or None to flip open <-> done.
    Not accepted (`!`) REQUIRES a reason of 3 or more non-space characters and takes a severity (must fix, the default, should, nit).
    It is written as `- [!] text  <!-- by who when -->` with `> Not accepted (severity): reason` under it. Any other state drops
    the reason, severity, who and when; `> Evidence:` lines stay."""
    if section not in CHECK_SECTIONS:
        raise ValueError(f"Checkboxes can only be changed in {', '.join(CHECK_SECTIONS)}.")
    t = store.get(ticket_id)
    raw = t.sections.get(section) or ""
    checks = parse_checks(raw)
    if not 0 <= index < len(checks):
        raise ValueError(f"{t.id} {section} has no checkbox line {index}; reload the page.")
    c = checks[index]
    cur = c.state
    if checked is None:
        new = " " if cur == "x" else "x"
    elif isinstance(checked, bool):
        new = "x" if checked else " "
    elif checked in (" ", "x", "!"):
        new = checked
    else:
        raise ValueError("A checkbox is open, done (x) or not accepted (!).")
    if new == "!":
        reason = " ".join((reason or "").split())
        if not reason_ok(reason):
            raise ValueError(f"Not accepted needs a reason: say what is wrong, at least {MIN_REASON} characters.")
        sev = " ".join((severity or DEFAULT_SEVERITY).lower().split())
        if sev not in SEVERITIES:
            raise ValueError(f"The severity is one of: {', '.join(SEVERITIES)}.")
        c.reason, c.severity, c.legacy = reason, sev, False
        c.who = re.sub(r"[^\w.@-]+", "-", actor or "desk").strip("-") or "desk"
        c.when = dt.datetime.now().strftime("%Y-%m-%dT%H:%M")
    else:
        c.reason = c.severity = c.who = c.when = ""
        c.legacy = False
    c.evidence += [e for e in evidence if e and e not in c.evidence]
    c.state = new
    parts = raw.split("\n")
    parts[c.first:c.last + 1] = render_check(c)
    t.sections[section] = "\n".join(parts)
    store.save(t)
    _acted(store, t, actor)
    return t


# James's Definition of Done line for handing a ticket to someone; shown by `drew assign` and on the ticket page.
ASSIGN_DOD = "assignee named and notified; the hand-off record linked"


def assign(store: Store, t: Ticket, to: str, note: str = "", handoff: str = "", actor: str | None = None) -> Ticket:
    """Hand a ticket to someone: sets `assignee` and `assigned_at` (ISO timestamp), optionally `handoff` (a URL or Drive doc id for
    the hand-off record), and appends `- <date> · <actor> · assigned to <name>: <note>` to Comments."""
    to = " ".join((to or "").split())
    if not to:
        raise RuleError("assign needs a name: --to <name>")
    from . import people  # a display name (Patch, Big D) is stored as its key (main, bigd)

    to = people.load(store.home).resolve(to)
    t.meta["assignee"] = to
    t.meta["assigned_at"] = dt.datetime.now().replace(microsecond=0).isoformat()
    t.meta["assigned_by"] = actor or "desk"  # a person's name here ends "assigned by rule <name>" (DESK-0168)
    if handoff.strip():
        t.meta["handoff"] = handoff.strip()
    note = " ".join((note or "").split())
    t.append_line("Comments", _stamp(actor or "desk", f"assigned to {to}" + (f": {note}" if note else "")))
    store.save(t)
    return t


def unassign(store: Store, t: Ticket, actor: str | None = None) -> Ticket:
    """Back to blank: the ticket is "not yet scanned" again. Clears assignee, assigned_at and assigned_by and notes it in Comments."""
    old = assignee_of(t)
    for k in ("assignee", "assigned_at", "assigned_by"):
        t.meta.pop(k, None)
    if old:
        t.append_line("Comments", _stamp(actor or "desk", f"unassigned (was {old})"))
    store.save(t)
    return t


# ---- refine and split (DESK-0145) ------------------------------------------------------------

def refine(store: Store, t: Ticket, line: str, actor: str | None = None, review_by: str | None = None, reason: str = "") -> Ticket:
    """A refinement of the requirements, tracked: a dated Acceptance line `- [ ] 2026-11-08 refinement: ...` and a Comments line. A refinement
    may cost time, so `review_by` may move, but only with a reason, and the slip is written down. A change of scope is not a refinement:
    that goes through `split`."""
    line = " ".join((line or "").split())
    if not line:
        raise RuleError("A refinement needs words: what is being made clearer, in a line.")
    if t.status in ("done", "rejected"):
        raise RuleError(f"{t.id} is {t.status}; a closed ticket is not refined. Open a new ticket.")
    t.append_line("Acceptance", f"- [ ] {today()} refinement: {line}")
    note = f"refinement: {line}"
    if review_by:
        try:
            new = dt.date.fromisoformat(review_by[:10]).isoformat()
        except ValueError:
            raise RuleError(f"`{review_by}` is not a date (use YYYY-MM-DD).") from None
        why = " ".join((reason or "").split())
        if not why:
            raise RuleError("Moving review_by needs a reason: say why the refinement costs time.")
        old = str(t.meta.get("review_by") or "none")[:10]
        t.meta["review_by"] = new
        note += f" (review_by moved from {old} to {new}: {why})"
    t.append_line("Comments", _stamp(actor or "desk", note, at_time=True))
    store.save(t)
    return t


def split(store: Store, t: Ticket, title: str, size: str = "s", due: str | None = None, details: str = "", outcome: str = "",
          acceptance: "list[str] | None" = None, actor: str | None = None) -> Ticket:
    """A change of scope does not edit the ticket: it becomes a new ticket with its details and timeline, linked both ways (the new ticket
    depends_on the old one, the old one lists it under blocks), and the old ticket carries a prompt for the completion decision: done as scoped, or closed in
    favour of the new one. Returns the new ticket."""
    title = " ".join((title or "").split())
    if not title:
        raise RuleError("A split needs a title for the new ticket.")
    if size not in ("xs", "s", "m"):
        raise RuleError("The new ticket's size is xs, s or m (a larger one is split again before it starts).")
    if t.status in ("done", "rejected"):
        raise RuleError(f"{t.id} is {t.status}; a closed ticket is not split. Open a new ticket.")
    if due:
        try:
            due = dt.date.fromisoformat(due[:10]).isoformat()
        except ValueError:
            raise RuleError(f"`{due}` is not a date (use YYYY-MM-DD).") from None
    m = t.meta
    new = store.create(title, t.project, str(m.get("type", "feature")), priority=str(m.get("priority", "p2")), size=size,
                       owner=actor or str(m.get("owner") or "main"), dod=list(m.get("dod") or ["core"]), source=str(m.get("source") or ""))
    for k in ("tags", "assignee", "track", "sprint"):
        if m.get(k) not in (None, "", []):
            new.meta[k] = m[k]
    new.meta["split_from"] = t.id
    new.meta["depends_on"] = [t.id]
    if due:
        new.meta["review_by"] = due
    new.set_section("Context", f"Split from {t.id} on {today()}: scope that did not belong in its Outcome. It depends on {t.id}, which lists it under blocks." + ("\n\n" + details.strip() if details.strip() else ""))
    new.set_section("Outcome", outcome.strip() or "(state the outcome of this part of the scope)")
    new.set_section("Acceptance", "\n".join(f"- [ ] {a.strip()}" for a in (acceptance or []) if a.strip()) or "- [ ] (write the acceptance before it is thrown)")
    store.save(new)
    blocks = [str(x) for x in (t.meta.get("blocks") or [])]
    if new.id not in blocks:
        t.meta["blocks"] = blocks + [new.id]
    t.append_line("Comments", _stamp(actor or "desk", f"scope split out to {new.id}: {title}" + (f" (due {due})" if due else ""), at_time=True))
    t.append_line("Decision", _stamp("desk", f"split to {new.id}: completion decision needed. Close this ticket as scoped (it blocks {new.id}), or close it in favour of {new.id} (drew reject {t.id} with a reason)"))
    store.save(t)
    return new


def edit_outcome(store: Store, t: Ticket, text: str, actor: str | None = None) -> Ticket:
    """The Outcome is the ticket's promise. It can be rewritten freely until the ticket is thrown (inbox, triage); after that a different
    Outcome is a change of scope and goes through `split`."""
    text = (text or "").strip()
    if t.status not in ("inbox", "triage"):
        if " ".join(text.split()) == " ".join(t.section("Outcome").split()):
            return t
        raise RuleError(f"{t.id} is {t.status}: its Outcome is the promise made, so changing it is a change of scope. Use Split to make a new ticket with the details and a timeline, and decide how to complete this one.")
    t.set_section("Outcome", text)
    t.append_line("Comments", _stamp(actor or "desk", "outcome rewritten before the ticket was thrown", at_time=True))
    store.save(t)
    return t


def assignee_of(t: Ticket) -> str:
    return " ".join(str(t.meta.get("assignee") or "").split())


def done(store: Store, t: Ticket, commit: str | None = None) -> Ticket:
    check_transition(t, "done", store)
    if commit:
        t.append_line("Decision", _stamp("desk", f"merged · {commit}"))
    t.meta["done_at"] = dt.datetime.now().replace(microsecond=0).isoformat()  # local time, for combos
    return move(store, t, "done")


def block(store: Store, t: Ticket, on: str, reason: str, actor: str | None = None) -> Ticket:
    if not on.strip():
        raise RuleError("Blocked needs a name: say who or which ticket it is waiting on.")
    if len(reason.strip()) < 4:
        raise RuleError("Blocked needs a reason: say why it is blocked.")
    t.meta["blocked_on"] = on.strip()
    return move(store, t, "blocked", actor=actor, note=f"blocked on {on.strip()}: {reason.strip()}")


def unblock(store: Store, t: Ticket, actor: str | None = None, to: str | None = None) -> Ticket:
    if t.status != "blocked":
        raise RuleError(f"{t.id} is not blocked")
    target = to or str(t.meta.get("blocked_from") or "ready")
    t.meta["status"] = target
    t.meta["blocked_on"] = ""
    t.meta.pop("blocked_from", None)
    t.append_line("Comments", _stamp(actor or "desk", f"unblocked → {target}"))
    store.save(t)
    _acted(store, t, actor)
    return t


def reject(store: Store, t: Ticket, reason: str, actor: str | None = None) -> Ticket:
    check_transition(t, "rejected", store, actor=actor, reason=reason)
    t.append_line("Decision", f'{_stamp(actor or "main", "rejected", decision=True)} · "{reason.strip()}"')
    return move(store, t, "rejected", actor=actor, reason=reason)


class CommentResult:
    """What `comment` did: `added` is False when an identical or nearly identical recent comment made it a duplicate. `existing` is the
    index (in `comment_lines`) of the comment already there, `near` says it was a variant and not a repeat."""

    def __init__(self, ticket: Ticket, added: bool, existing: int | None = None, near: bool = False):
        self.ticket, self.added, self.existing, self.near = ticket, added, existing, near

    @property
    def message(self) -> str:
        if self.added:
            return ""
        return NEAR_NOTICE if self.near else DUP_NOTICE

    @property
    def id(self) -> str:
        return self.ticket.id

    @property
    def status(self) -> str:
        return self.ticket.status


COMMENT_KINDS = ("Question", "Suggestion", "Concern", "FYI")
_KIND_TAIL = re.compile(r"\s*<!-- kind: (Question|Suggestion|Concern|FYI) -->\s*$")


def split_comment_kind(rest: str) -> tuple[str | None, str]:
    """(kind or None, the words) for what follows `<when> · <who> · ` on a comment line. Comments written before kinds have none.
    The hidden `<!-- by ... -->` identity tag (DESK-0144) is not part of the words."""
    m = _KIND_TAIL.search(rest)
    kind, words = (m.group(1), rest[:m.start()]) if m else (None, rest)
    return kind, identity.parse_tag(words)[1]


def comment_identity(rest: str) -> dict:
    """The parsed identity tag of a comment line's tail ({} for a comment written before identities were kept)."""
    m = _KIND_TAIL.search(rest)
    return identity.parse_tag(rest[:m.start()] if m else rest)[0]


def _squash(text: str) -> str:
    """The comment with punctuation and brackets turned to spaces, case folded, spaces collapsed: `Dup test # 2 ()` is `dup test 2`."""
    return " ".join(re.sub(r"[^\w\s]|_", " ", text.casefold()).split())


def norm_comment(text: str) -> tuple[str, str]:
    """(normalized, normalized without its trailing counter). `Dup test # 2 ()` gives (`dup test 2`, `dup test`)."""
    full = _squash(text)
    return full, re.sub(r"(?:\s+\d+)+$", "", full)


def _distance(a: str, b: str, limit: int) -> int:
    """Edit distance between a and b, or limit + 1 as soon as it is certain to be more."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def near_same(a: str, b: str) -> bool:
    """True when two comments are the same after normalizing, or differ by at most 2 edits (1 under 8 characters, none under 5, so
    `ok` and `no` stay two comments). The trailing counter is ignored: `Dup test #3` is `Dup test #2` again."""
    (fa, ca), (fb, cb) = norm_comment(a), norm_comment(b)
    if not fa or not fb:
        return False
    for x, y in ((fa, fb), (ca, cb)):
        k = 2 if min(len(x), len(y)) >= 8 else 1 if min(len(x), len(y)) >= 5 else 0
        if x and y and _distance(x, y, k) <= k:
            return True
    return False


def _lines(t: Ticket):
    """(index in comment_lines, when, author, text) for each comment line of the ticket."""
    for i, line in enumerate(comment_lines(t)):
        m = _COMMENT_LINE.match(line)
        if m:
            try:
                yield i, dt.datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}"), m.group(3), split_comment_kind(m.group(4))[1]
            except ValueError:
                continue


def find_duplicate(t: Ticket, actor: str, text: str, now: dt.datetime | None = None) -> tuple[int, bool] | None:
    """(index of the comment already there, whether it is only a near repeat) when this person, or one named the same way (`james`,
    `James`, `james1`, `james_`), posted the same words within 10 minutes or nearly the same words within 60 seconds; else None.
    The kind does not matter."""
    from .people import same_person

    now = now or dt.datetime.now()
    near = None
    for i, when, who, body in _lines(t):
        if not same_person(who, actor):
            continue
        age = abs(now - when)
        if age <= DUPLICATE_WINDOW and _norm(body) == _norm(text):
            return i, False
        if near is None and age <= NEAR_WINDOW and near_same(body, text):
            near = i
    return (near, True) if near is not None else None


def is_duplicate_comment(t: Ticket, actor: str, text: str, now: dt.datetime | None = None) -> bool:
    return find_duplicate(t, actor, text, now) is not None


def canon_kind(kind: str | None) -> str:
    """The kind of a new comment: Question, Suggestion, Concern or FYI (any case); Question when none is given."""
    if not (kind or "").strip():
        return COMMENT_KINDS[0]
    for k in COMMENT_KINDS:
        if k.lower() == kind.strip().lower():
            return k
    raise ValueError(f"A comment is one of: {', '.join(COMMENT_KINDS[:-1])} or {COMMENT_KINDS[-1]}.")


def comment(store: Store, t: Ticket, text: str, actor: str | None = None, kind: str | None = None, now: dt.datetime | None = None) -> CommentResult:
    """Append a dated comment. It always has a kind (Question unless said otherwise), kept at the end of the line as `<!-- kind: X -->`."""
    kind = canon_kind(kind)
    if not text.strip():
        raise RuleError("An empty comment says nothing.")
    who = actor or "desk"
    dup = find_duplicate(t, who, text, now)  # DESK-0142: the same or nearly the same words by the same person, just posted
    if dup:
        return CommentResult(t, False, existing=dup[0], near=dup[1])
    ident = identity.CURRENT.get()
    extra = ""
    if ident is not None and ident.how == "verified":  # DESK-0259: signed with the actor's own token
        when = (now or dt.datetime.now()).strftime("%Y-%m-%d %H:%M")
        extra = "sig=" + identity.sign(who, t.id, when, text.strip(), os.environ.get("DESK_ACTOR_TOKEN", ""))
        now = dt.datetime.strptime(when, "%Y-%m-%d %H:%M")
    elif ident is not None and _session_signed_as_another(store, ident.instance, who):
        extra = "impersonation=suspected"
    tag = f" {ident.tag(extra)}" if ident is not None else ""  # who really wrote it: kind, source, how it was established, instance
    t.append_line("Comments", _stamp(who, f"{text.strip()}{tag} <!-- kind: {kind} -->", at_time=True, now=now))
    if who.lower() == store.owner.lower():
        from . import repeats  # DESK-0158: the owner asking for the same thing twice in a week makes the ticket p0 with the tag emergency

        if repeats.repeated_ask(t, store.owner):
            repeats.escalate(store, t)
    store.save(t)
    _acted(store, t, who)
    return CommentResult(t, True)


def _session_signed_as_another(store: Store, instance: str, who: str) -> bool:
    """DESK-0259: this session id already wrote a comment under a different author name, so writing as `who` may be someone's mimicry."""
    if not instance:
        return False
    inst = _safe_inst(instance)
    for t in store.tickets():
        for ln in comment_lines(t):
            m = _COMMENT_LINE.match(ln)
            if m and m.group(3).strip().lower() != who.lower() and comment_identity(m.group(4)).get("inst") == inst:
                return True
    return False


def _safe_inst(v: str) -> str:
    return identity._safe(v)


def comment_lines(t: Ticket) -> list[str]:
    """The lines of the Comments section, one per comment, in file order."""
    return [ln for ln in t.section("Comments").splitlines() if ln.strip()]


def locate_comment(t: Ticket, actor: str, text: str) -> int | None:
    """Index in `comment_lines` of the newest comment by `actor` with this text (whitespace and case normalized), or None."""
    want = (_norm(actor), _norm(text))
    for i in range(len(lines := comment_lines(t)) - 1, -1, -1):
        m = _COMMENT_LINE.match(lines[i])
        if m and (_norm(m.group(3)), _norm(split_comment_kind(m.group(4))[1])) == want:
            return i
    return None


def _private(path: Path) -> bool:
    if path.name.lower() in PRIVATE_NAMES:
        return True
    return any(part.lower() in PRIVATE_PARTS for part in path.parts)


def intake(store: Store, source: Path, topic: str, project: str, actor: str = "main") -> tuple[Ticket, Path]:
    """Copy a hand-off folder or zip into intake/<date>-<topic>/ and open an intake ticket."""
    source = Path(source)
    if not source.exists():
        raise RuleError(f"{source} does not exist")
    dest = store.intake_dir / f"{today()}-{slugify(topic, 40)}"
    if dest.exists():
        raise RuleError(f"{dest} already exists")

    if source.is_file() and source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            bad = [n for n in names if _private(Path(n))]
            if bad:
                raise RuleError("Refused: the package contains private files: " + ", ".join(bad[:5]))
            dest.mkdir(parents=True)
            for n in names:
                target = (dest / n).resolve()
                if dest.resolve() not in target.parents:
                    raise RuleError(f"Refused: unsafe path in zip: {n}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(n) as src, target.open("wb") as out:
                    shutil.copyfileobj(src, out)
    elif source.is_dir():
        files = [p for p in source.rglob("*") if p.is_file()]
        bad = [p for p in files if _private(p.relative_to(source)) or _private(p)]
        if bad:
            raise RuleError("Refused: the package contains private files: " + ", ".join(str(p.relative_to(source)) for p in bad[:5]))
        shutil.copytree(source, dest)
    else:
        if _private(source):
            raise RuleError(f"Refused: {source.name} is private")
        dest.mkdir(parents=True)
        shutil.copy2(source, dest / source.name)

    rel = dest.relative_to(store.home).as_posix()
    t = store.create(f"Intake: {topic}", project, "intake", owner=actor, priority="p2", size="s", source=rel)
    t.set_section("Context", f"Research hand-off received {today()} from `{source.name}`, copied to `{rel}/`.")
    t.set_section("Outcome", "The package is read, the in-scope items are confirmed with the owner, and slices exist for each.")
    t.set_section("Acceptance", "- [ ] Package read end to end\n- [ ] In-scope items confirmed with the owner\n- [ ] One slice ticket per deliverable, each with `source` pointing into the intake folder")
    t.set_section("Out of scope", "Doing the work the package describes. This ticket only slices it.")
    store.save(t)
    return t, dest


def load_report_file(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def week_ago() -> str:
    return (dt.date.today() - dt.timedelta(days=7)).isoformat()


# ---- projects ----------------------------------------------------------------

def rename_project(store: Store, old: str, new: str, *, prefix: str | None = None, name: str | None = None,
                   actor: str = "desk") -> tuple[Path, list[Ticket]]:
    """Rename projects/<old>.md to <new>.md, record the old key and prefix as aliases, and point every
    ticket of the project at the new key. Ids never change; the old prefix stays valid through `aliases:`."""
    projects = store.projects()
    if old not in projects:
        raise RuleError(f"No project `{old}` (no projects/{old}.md).")
    if not _KEY.match(new):
        raise RuleError("A project key is lowercase letters, digits and hyphens, like `learninglabs`.")
    if new in projects or (store.projects_dir / f"{new}.md").exists():
        raise RuleError(f"Project `{new}` already exists; pick another key or merge by hand.")
    if prefix is not None and not re.match(r"^[A-Za-z0-9][A-Za-z0-9-]*$", prefix):
        raise RuleError("A prefix is letters, digits and hyphens, like `LEARN`.")

    old_path = projects[old]
    meta, body = split_profile(old_path.read_text(encoding="utf-8"))
    old_prefix = store.key_for(old)
    new_prefix = prefix.upper() if prefix else old_prefix

    aliases = [str(a) for a in (meta.get("aliases") or []) if str(a).strip()]
    for a in (old, old_prefix):
        if a not in aliases and a.upper() != new_prefix and a != new:
            aliases.append(a)
    ordered: dict = {"key": new}
    if new_prefix != new.upper():
        ordered["prefix"] = new_prefix
    if name:
        ordered["name"] = name
    elif meta.get("name"):
        ordered["name"] = meta["name"]
    ordered["aliases"] = aliases
    for k, v in meta.items():
        if k not in ordered and k not in ("key", "prefix", "name", "aliases"):
            ordered[k] = v
    if name:
        lines = body.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("# "):
                lines[i] = f"# {name}"
                break
        else:
            lines.insert(0, f"# {name}")
        body = "\n".join(lines) + ("\n" if body.endswith("\n") else "")
    new_path = store.projects_dir / f"{new}.md"
    new_path.write_text(join_profile(ordered, body), encoding="utf-8", newline="\n")
    old_path.unlink()

    changed: list[Ticket] = []
    for t in store.tickets():
        if t.project != old:
            continue
        t.meta["project"] = new
        t.append_line("Comments", _stamp(actor, f"project renamed {old} -> {new}"))
        store.save(t)
        changed.append(t)
    return new_path, changed


# ---- attachments -------------------------------------------------------------

def attach(store: Store, t: Ticket, source: Path, caption: str | None = None, actor: str | None = None) -> Path:
    """Copy a file into tickets/assets/<ID>/ and add an image or link line to the ticket's Attachments."""
    source = Path(source)
    if not source.is_file():
        raise RuleError(f"{source} is not a file")
    dest_dir = store.ticket_assets_dir(t.id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe = _SAFE_NAME.sub("-", source.name).strip("-.") or "file"
    dest = dest_dir / safe
    n = 2
    while dest.exists():
        dest = dest_dir / f"{Path(safe).stem}-{n}{Path(safe).suffix}"
        n += 1
    shutil.copy2(source, dest)
    text = (caption or source.name).strip().replace("]", ")")
    rel = f"assets/{t.id}/{dest.name}"
    line = f"![{text}]({rel})" if dest.suffix.lower() in IMAGE_SUFFIXES else f"[{text}]({rel})"
    t.append_line("Attachments", line)
    store.save(t)
    return dest


def add_evidence(store: Store, ticket_id: str, section: str, index: int, rels: "list[str]") -> Ticket:
    """Add `> Evidence: <rel>` lines under the `index`-th checkbox line of `section` (a path already there is not added twice)."""
    if section not in CHECK_SECTIONS:
        raise ValueError(f"Evidence can only go on lines in {', '.join(CHECK_SECTIONS)}.")
    t = store.get(ticket_id)
    raw = t.sections.get(section) or ""
    checks = parse_checks(raw)
    if not 0 <= index < len(checks):
        raise ValueError(f"{t.id} {section} has no checkbox line {index + 1}.")
    c = checks[index]
    c.evidence += [r for r in rels if r and r not in c.evidence]
    parts = raw.split("\n")
    parts[c.first:c.last + 1] = render_check(c)
    t.sections[section] = "\n".join(parts)
    store.save(t)
    return t


def unattach(store: Store, t: Ticket, name: str) -> None:
    """Remove an uploaded file and its line under Attachments. Refuses a file that a comment, a report or an evidence line uses."""
    rel = f"assets/{t.id}/{name}"
    path = store.ticket_assets_dir(t.id) / name
    if not path.is_file():
        raise KeyError(name)
    if any(rel in (body or "") for sec, body in t.sections.items() if sec != "Attachments"):
        raise RuleError("That screenshot is already used by this ticket, so it stays.")
    lines = [ln for ln in (t.sections.get("Attachments") or "").split("\n") if rel not in ln]
    t.sections["Attachments"] = "\n".join(lines)
    store.save(t)
    path.unlink()


def capture_screen(dest: Path) -> Path:
    """Take a PNG of the whole screen into `dest`. Windows, macOS and Linux (import or gnome-screenshot)."""
    dest = Path(dest)
    system = platform.system()
    if system == "Windows":
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
            "$b = [System.Windows.Forms.SystemInformation]::VirtualScreen; "
            "$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height; "
            "$g = [System.Drawing.Graphics]::FromImage($bmp); "
            "$g.CopyFromScreen($b.Left, $b.Top, 0, 0, $bmp.Size); "
            f"$bmp.Save('{str(dest).replace(chr(39), chr(39) * 2)}', [System.Drawing.Imaging.ImageFormat]::Png); "
            "$g.Dispose(); $bmp.Dispose()"
        )
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
    elif system == "Darwin":
        cmd = ["screencapture", "-x", str(dest)]
    elif system == "Linux":
        if shutil.which("import"):
            cmd = ["import", "-window", "root", str(dest)]
        elif shutil.which("gnome-screenshot"):
            cmd = ["gnome-screenshot", "-f", str(dest)]
        else:
            raise RuleError("No screenshot tool found (install ImageMagick `import` or gnome-screenshot).")
    else:
        raise RuleError(f"Screen capture is not supported on {system}.")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuleError(f"Screen capture failed: {e}") from e
    if r.returncode != 0 or not dest.exists():
        raise RuleError(f"Screen capture failed: {(r.stderr or r.stdout).strip() or 'no image written'}")
    return dest


def snap(store: Store, t: Ticket, caption: str | None = None) -> Path:
    """Capture the screen and attach it as snap-<timestamp>.png."""
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    with tempfile.TemporaryDirectory(prefix="desk-snap-") as tmp:
        shot = capture_screen(Path(tmp) / f"snap-{stamp}.png")
        return attach(store, t, shot, caption or f"Screen {stamp}")


# ---- clone and stats -----------------------------------------------------------

def clone(store: Store, src: Ticket, title: str, project: str | None = None, actor: str = "main") -> Ticket:
    """A new ticket with the source's Context, Outcome, Acceptance and Prompt, and `cloned_from` set."""
    if not title.strip():
        raise RuleError("A clone needs a title.")
    project = project or src.project
    if project not in store.projects() and store.projects():
        raise RuleError(f"Unknown project `{project}`. Add projects/{project}.md first.")
    m = src.meta
    t = store.create(title.strip(), project, str(m.get("type", "slice")), priority=str(m.get("priority", "p2")),
                     size=str(m.get("size", "s")), owner=actor, dod=list(m.get("dod") or ["core"]), source=str(m.get("source") or ""))
    t.meta["cloned_from"] = src.id
    for k in ("tags", "assignee", "docs_to_update", "effort_hours", "smallest_step"):
        if m.get(k) not in (None, "", []):
            t.meta[k] = m[k]
    for name in CLONED_SECTIONS:
        body = src.section(name)
        if body:
            t.set_section(name, re.sub(r"^(\s*[-*]\s*)\[[xX]\]", r"\1[ ]", body, flags=re.M))  # boxes start unchecked
    store.save(t)
    return t


def _week(day: str) -> str:
    try:
        d = dt.date.fromisoformat(str(day)[:10])
    except ValueError:
        return "unknown"
    monday = d - dt.timedelta(days=d.weekday())
    return monday.isoformat()


def stats(store: Store, since: str | None = None, project: str | None = None) -> dict:
    """Counts James can quote: total, done, in review (built but not proven), open; by status, project, type;
    created and done per ISO week (Monday). `since` filters on created (and done_at for the done-per-week table)."""
    tickets = [t for t in store.tickets() if not project or t.project == project]
    if since:
        tickets = [t for t in tickets if str(t.meta.get("created", ""))[:10] >= since]
    by = lambda key: dict(sorted(_count(key(t) for t in tickets).items()))  # noqa: E731
    created_week = _count(_week(str(t.meta.get("created", ""))) for t in tickets)
    done_week = _count(_week(str(t.meta.get("done_at") or t.meta.get("updated", ""))) for t in tickets if t.status == "done")
    week = week_ago()
    by_assignee: dict = {}
    for t in tickets:
        row = by_assignee.setdefault(assignee_of(t) or "(unassigned)", {"open": 0, "done_this_week": 0})
        row["open"] += t.is_open
        row["done_this_week"] += t.status == "done" and str(t.meta.get("done_at") or t.meta.get("updated", ""))[:10] >= week
    return {
        "by_assignee": dict(sorted(by_assignee.items())),
        "total": len(tickets),
        "done": sum(t.status == "done" for t in tickets),
        "in_review": sum(t.status == "review" for t in tickets),
        "open": sum(t.is_open for t in tickets),
        "rejected": sum(t.status == "rejected" for t in tickets),
        "by_status": by(lambda t: t.status),
        "by_project": by(lambda t: t.project),
        "by_type": by(lambda t: str(t.meta.get("type", ""))),
        "created_per_week": dict(sorted(created_week.items())),
        "done_per_week": dict(sorted(done_week.items())),
        "since": since,
        "project": project,
        "decisions_per_week": review.decisions_per_week(store),
        "review_weeks": review.review_weeks(store),
    }


def _count(items) -> dict:
    out: dict = {}
    for x in items:
        out[x] = out.get(x, 0) + 1
    return out
