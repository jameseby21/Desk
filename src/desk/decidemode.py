"""Decide mode (DESK-0285): one ask per screen with the worker's recommendation, the options as buttons with what happens next, and
an answer that needs no typed words when the owner agrees. Also batch approve from Needs me and the ask-me-once guard.

Every decision, whether from the ticket page's reply box, Decide mode or a batch, is written by `record`: one path to `ops.decide`.
The recommendation is read from what workers already write (`Chaz recommends: A; why`, `Recommendation: X`, an option marked
`(recommended)`); nothing is invented when none is written. Docs: docs/COMMANDS.md (`/decide`)."""

from __future__ import annotations

import datetime as dt
import re
import secrets
from dataclasses import dataclass

from . import asks as asksmod
from . import lanes
from . import needs_me as nmod
from . import ops
from .repeats import normalize
from .rules import RuleError, check_transition
from .store import Store
from .ticket import Ticket, canon_type

AGREE = "Agree with the recommendation"
NO_REC = "No recommendation recorded"
SNOOZE_DAYS = 7
DECIDE_KINDS = ("approve", "choose", "answer", "provide")  # a `do` item is a step to take, not a question; Decide mode leaves it out
REC_SECTIONS = ("Report", "Comments", "Context", "Prompt", "Outcome")  # where workers write a recommendation, most recent word first
_REC = re.compile(r"(?:\b(?P<who>[A-Z][A-Za-z-]*)\s+recommends|\b[Rr]ecommend(?:ation|ed)(?:\s+(?:answer|option|choice))?)\s*:\s*(?P<text>\S[^\n]*)")
_MARKED = re.compile(r"\s*\(recommended\)\s*", re.I)
_TAGS = re.compile(r"<!--.*?-->")
_COMMENT_HEAD = re.compile(r"^- \d{4}-\d{2}-\d{2}(?: \d{2}:\d{2})? · (?P<who>[^·]+?) · ")
_ORDINAL = {"first": 0, "second": 1, "third": 2, "fourth": 3, "fifth": 4}
_YES = ("approve", "yes", "ship", "merge", "go ahead", "accept")
_NO = ("send back", "no", "reject", "hold", "not yet", "wait")
_DECISION = re.compile(r'^- (\d{4}-\d{2}-\d{2}) · (.+?) · ([^·]+?) · "(.*)"\s*$')
_ASK_STOP = set("approve choose answer provide decide decision ticket option options yes no which should".split())
SIMILAR = 0.5  # share of the shorter text's content words that must also be in the other
MIN_SHARED = 2


def new_batch_id() -> str:
    """`b-<12 hex>`, the DESK-0282 batch id every event of one action carries."""
    return "b-" + secrets.token_hex(6)


# ---- the recommendation -------------------------------------------------------------------------------------------------------

@dataclass
class Recommendation:
    text: str  # what the worker recommends, as written
    why: str = ""  # the reason after it, as written
    by: str = ""  # who recommended it, when the line says
    where: str = ""  # the section it was read from
    option: int | None = None  # the option it points at (choose)
    action: str = ""  # approve or changes (approve asks)


def _split(text: str) -> tuple[str, str]:
    """`A; it reads at 16 px.` -> (`A`, `it reads at 16 px`): the choice, then the reason after the first `;`, sentence end or `because`."""
    text = " ".join(_TAGS.sub(" ", text).split()).strip()
    for sep in (";", ". ", " because "):
        if sep in text:
            a, b = text.split(sep, 1)
            return a.strip().rstrip(".,"), b.strip().rstrip(".")
    return text.rstrip("."), ""


def _option_for(choice: str, options: list[tuple[str, str]]) -> int | None:
    if not options or not choice:
        return None
    labels = [lab for lab, _ in options]
    m = re.match(r"(?:option\s+)?([A-Z])(?![A-Za-z0-9])", choice.strip(), re.I if choice.lower().startswith("option") else 0)
    if m and m.group(1).upper() in labels:
        return labels.index(m.group(1).upper())
    low = choice.lower()
    for word, i in _ORDINAL.items():
        if re.search(rf"\b{word}\b", low) and i < len(options):
            return i
    for i, (_, text) in enumerate(options):
        t = _MARKED.sub(" ", text).strip().lower()
        if t and len(low) >= 3 and (t in low or low in t):
            return i
    return None


def _action_for(choice: str) -> str:
    low = choice.lower().strip()
    if any(low.startswith(w) for w in _NO):
        return "changes"
    if any(low.startswith(w) for w in _YES):
        return "approve"
    return ""


def _lines(t: Ticket, section: str) -> list[tuple[str, str]]:
    """(line, author) pairs of a section; Comments newest first, with each comment's author."""
    out = []
    for ln in (t.section(section) or "").splitlines():
        m = _COMMENT_HEAD.match(ln) if section == "Comments" else None
        out.append((ln, m.group("who").strip() if m else ""))
    return list(reversed(out)) if section == "Comments" else out


def recommendation(t: Ticket, ask: dict | None = None) -> Recommendation | None:
    """The worker's recommendation for this ask, read from the ticket; None when nobody wrote one (the page then says so)."""
    ask = ask if ask is not None else (asksmod.ask_of(t) or {"kind": "approve", "options": []})
    options = ask.get("options") or []
    for i, (_, text) in enumerate(options):
        if _MARKED.search(text):
            return Recommendation(_MARKED.sub(" ", text).strip(), where="Options", option=i)
    for section in REC_SECTIONS:
        for line, author in _lines(t, section):
            if re.match(r"^\s*[-*]\s+\[[ xX!]\]", line):  # a checklist line ("- [ ] Recommendation: ...") is a task, not a recommendation
                continue
            m = _REC.search(line)
            if not m:
                continue
            choice, why = _split(m.group("text"))
            if not choice:
                continue
            rec = Recommendation(choice, why, (m.group("who") or author or "").strip(), section)
            if ask["kind"] == "choose":
                rec.option = _option_for(choice, options)
            elif ask["kind"] == "approve":
                rec.action = _action_for(choice)
            return rec
    return None


# ---- what happens next ------------------------------------------------------------------------------------------------------

def waiting_on(store: Store, t: Ticket) -> list[Ticket]:
    """Open tickets whose depends_on names this one: what an answer unblocks."""
    return [x for x in store.tickets() if x.is_open and t.id in [str(d) for d in (x.meta.get("depends_on") or [])]]


def next_steps(store: Store, t: Ticket, kind: str, today: dt.date | None = None) -> dict:
    """One plain sentence per button: what pressing it does. Keys: yes, no, snooze, and per option `options`."""
    today = today or dt.date.today()
    n = len(waiting_on(store, t))
    unblocks = f" {n} waiting ticket{'s' if n != 1 else ''} can then start." if n else ""
    decision = canon_type(t.meta.get("type")) == "decision"
    if decision:
        yes = "Your answer goes into the Decision section and the question closes." + unblocks
    else:
        yes = "Approved: the worker merges it and marks it done." + unblocks
    return {
        "yes": yes,
        "no": "Goes back to the worker with your reason; it returns when it is fixed.",
        "snooze": f"Leaves this list until {snooze_date(today):%a %b} {snooze_date(today).day}; nothing else changes.",
        "option": "Records this choice in the Decision section and closes the question." + unblocks,
    }


def snooze_date(today: dt.date | None = None) -> dt.date:
    return (today or dt.date.today()) + dt.timedelta(days=SNOOZE_DAYS)


# ---- the queue --------------------------------------------------------------------------------------------------------------

def ask_for(t: Ticket, kind: str) -> dict:
    """The asks dict (kind, options) for an item; a review approval on a ticket that is not a decision has no options."""
    a = asksmod.ask_of(t)
    return a if a else {"kind": kind, "options": []}


def queue(store: Store, now: dt.datetime | None = None) -> list[nmod.Item]:
    """The owner's asks in Needs me order, without `do` steps and without snoozed tickets."""
    now = now or dt.datetime.now()
    return [i for i in nmod.build(store, now=now) if i.group == "ask" and i.kind in DECIDE_KINDS and not lanes.is_snoozed(i.ticket, now.date())]


# ---- the words of an answer ---------------------------------------------------------------------------------------------------

def words(kind: str, ask: dict, rec: Recommendation | None, option: str, note: str, action: str) -> str:
    """The decision as it is written. Agreeing with a recommendation needs no typed words: the line says `Agree with the
    recommendation` and what it was. Anything else uses the person's own words; a refusal says what to type."""
    note = " ".join((note or "").split())
    if action == "changes":
        if len(note) < 4:
            raise ValueError("Say why you are sending it back (at least four characters), then press Enter.")
        return note
    if kind == "choose":
        if str(option).strip() == "":
            if note:
                return note
            raise ValueError("Pick an option first: press 1 to 9, or click one.")
        extra = note or (AGREE + "." if rec is not None and rec.option is not None and str(rec.option) == str(option).strip() else "")
        return asksmod.words_for(ask, str(option), extra)
    if note:
        return note
    if kind == "approve":
        return f"{AGREE}: {rec.text}." if rec else "Approved as written."
    if kind == "answer" and rec:
        return f"{AGREE}: {rec.text}."
    if kind == "provide":
        raise ValueError("Type what you are providing first (never a password or key).")
    raise ValueError("Type your answer first, then press Y.")


# ---- the one write path -----------------------------------------------------------------------------------------------------

def record(store: Store, t: Ticket, approve: bool, reason: str, actor: str | None = None, sync: bool = True) -> Ticket:
    """Every web decision is written here (the reply box, Decide mode and batch approve): the same rules, the same Decision line."""
    if sync:
        store.sync_before()
    t = ops.decide(store, t, approve=approve, reason=reason, actor=actor or store.owner)
    if sync:
        store.sync_after(f"desk: decide {t.id}")
    return t


def snooze(store: Store, t: Ticket, today: dt.date | None = None) -> dt.date:
    until = snooze_date(today)
    t.meta["snooze_until"] = until.isoformat()
    store.save(t)
    return until


# ---- batch approve ----------------------------------------------------------------------------------------------------------

@dataclass
class Planned:
    ticket: Ticket
    words: str = ""
    skip: str = ""  # why it is left for the owner; empty when it will be approved


def plan_batch(store: Store, ids: list[str], now: dt.datetime | None = None) -> list[Planned]:
    """The dry run: for each ticked id, the words it would be approved with, or why it is left for the owner. Nothing is written."""
    now = now or dt.datetime.now()
    who = store.owner.lower()
    out: list[Planned] = []
    seen = set()
    for raw in ids:
        tid = str(raw).strip().upper()
        if not tid or tid in seen:
            continue
        seen.add(tid)
        try:
            t = store.get(tid)
        except KeyError:
            continue
        item = nmod.ask_of(store, t, who, now)
        if item is None:
            out.append(Planned(t, skip="It is not waiting on you any more."))
            continue
        if item.kind == "do":
            out.append(Planned(t, skip="It is a step for you to do, not something to approve."))
            continue
        if item.kind == "provide":
            out.append(Planned(t, skip="It asks you to provide something; open it and type it."))
            continue
        ask = ask_for(t, item.kind)
        rec = recommendation(t, ask)
        try:
            if item.kind == "choose":
                if rec is None or rec.option is None:
                    raise ValueError("No recommended option is recorded; open it and pick one.")
                w = words("choose", ask, rec, str(rec.option), "", "approve")
            elif item.kind == "answer":
                if rec is None:
                    raise ValueError("No recommended answer is recorded; open it and answer in your words.")
                w = words("answer", ask, rec, "", "", "approve")
            else:
                if rec is not None and rec.action == "changes":
                    raise ValueError(f"The recommendation is to send it back ({rec.text}); open it to decide.")
                w = words("approve", ask, rec, "", "", "approve") if rec else "Approved in one batch from Needs me."
            check_transition(t, "approved", store, actor=store.owner, reason=w)
        except (RuleError, ValueError) as e:
            out.append(Planned(t, skip=str(e)))
            continue
        out.append(Planned(t, words=w))
    return out


def run_batch(store: Store, planned: list[Planned], batch_id: str) -> tuple[list[str], list[tuple[str, str]]]:
    """Approve every planned ticket inside one activity batch, so one Undo reverses them all. Returns (approved ids, [(id, why refused)])."""
    from . import activity

    done, refused = [], []
    store.sync_before()
    with activity.batch(batch_id):
        for p in planned:
            if p.skip:
                continue
            try:
                record(store, store.get(p.ticket.id), True, p.words, sync=False)
                done.append(p.ticket.id)
            except (KeyError, RuleError, ValueError) as e:
                refused.append((p.ticket.id, str(e)))
    if done:
        store.sync_after(f"desk: batch approve {', '.join(done)}")
    return done, refused


# ---- ask me once ------------------------------------------------------------------------------------------------------------

@dataclass
class Earlier:
    ticket: Ticket
    date: str
    what: str  # approved, changes_requested, direction, ...
    quote: str
    score: float


def _ask_words(t: Ticket) -> set[str]:
    title = re.sub(r"^\s*(approve|choose|answer|provide)\s*:\s*", "", str(t.meta.get("title") or ""), flags=re.I)
    opts = " ".join(text for _, text in asksmod.parse_options(t.section("Context")))
    return {w for w in normalize(f"{title} {opts}") if w not in _ASK_STOP and len(w) > 2}


def owner_decisions(store: Store, t: Ticket) -> list[tuple[Ticket, str, str, str]]:
    """(ticket, date, what, quote) for every Decision line the owner wrote, on every ticket but `t`."""
    out = []
    for x in store.tickets():
        if x.id == t.id:
            continue
        for ln in (x.section("Decision") or "").splitlines():
            m = _DECISION.match(ln)
            if m and nmod.same_person(m.group(2).strip(), store.owner):
                out.append((x, m.group(1), m.group(3).strip(), m.group(4).strip()))
    return out


def similar_answer(store: Store, t: Ticket) -> Earlier | None:
    """The owner's earlier answer that looks most like this ask (shared content words of the titles, options and his words), or None."""
    mine = _ask_words(t)
    if len(mine) < MIN_SHARED:
        return None
    best: Earlier | None = None
    for x, date, what, quote in owner_decisions(store, t):
        theirs = _ask_words(x) | {w for w in normalize(quote) if w not in _ASK_STOP and len(w) > 2}
        shared = mine & theirs
        if len(shared) < MIN_SHARED:
            continue
        score = len(shared) / min(len(mine), len(theirs))
        if score < SIMILAR:
            continue
        if best is None or (score, date) > (best.score, best.date):
            best = Earlier(x, date, what, quote, round(score, 2))
    return best
