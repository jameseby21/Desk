"""The repeated-ask detector (DESK-0158). Rule: repetition is an emergency.

Two things are noticed, so nothing waits on anyone's memory:
* a repeated ask: the store owner (James) comments twice on one ticket within seven days asking for the same outcome. The ticket becomes
  p0 with the tag `emergency` when the second comment is written, and `drew validate` warns while the pair stands;
* a stale hand-off: `blocked_on` names the owner and the ticket has not changed across two sweeps (the sweep times kept in
  `.desk/state`), so the hand-off may not be real.

"The same outcome" is judged on normalized text: lower case, no punctuation, no filler words; two comments match when they share at
least 60% of their content words (or one contains the other), and each has at least three. DESK-0142's normalizer is not on main yet, so
this module has its own; when that lands, `normalize` is the one place to swap."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from . import comments as cm
from .ops import _COMMENT_LINE, split_comment_kind
from .store import Store
from .ticket import CLOSED, Ticket

WINDOW = dt.timedelta(days=7)
MIN_WORDS = 3
SIMILAR = 0.6
STOP = set("a an and are as at be but by can do for from has have i if in is it its me my of on or so that the then this to up was we what when with you your please just also now still again".split())


def normalize(text: str) -> list[str]:
    """The content words of a comment: lower case, punctuation and the hidden kind marker removed, filler words dropped."""
    text = re.sub(r"<!--.*?-->", " ", text or "")
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP]


def same_ask(a: str, b: str) -> bool:
    wa, wb = set(normalize(a)), set(normalize(b))
    if len(wa) < MIN_WORDS or len(wb) < MIN_WORDS:
        return False
    return len(wa & wb) / min(len(wa), len(wb)) >= SIMILAR and len(wa & wb) / len(wa | wb) >= SIMILAR - 0.2


@dataclass
class Repeat:
    ticket: Ticket
    first: dt.datetime
    second: dt.datetime
    text: str

    def message(self, owner: str) -> str:
        return (f"{self.ticket.id}: repeated ask: {owner} asked for the same thing on {self.first:%Y-%m-%d %H:%M} and {self.second:%Y-%m-%d %H:%M} "
                f"(\"{self.text[:70]}\"); repetition is an emergency, answer it first")


def _owner_comments(t: Ticket, owner: str) -> list[tuple[dt.datetime, str]]:
    out = []
    for line in t.section("Comments").splitlines():
        m = _COMMENT_LINE.match(line)
        if not m or m.group(3).strip().lower() != owner.lower():
            continue
        try:
            when = dt.datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}")
        except ValueError:
            continue
        out.append((when, split_comment_kind(m.group(4))[1]))
    return out


def repeated_ask(t: Ticket, owner: str) -> Repeat | None:
    rows = _owner_comments(t, owner)
    for i, (wb, tb) in enumerate(rows):
        for wa, ta in rows[:i]:
            if abs(wb - wa) <= WINDOW and same_ask(ta, tb):
                return Repeat(t, wa, wb, " ".join(tb.split()))
    return None


def repeats(store: Store, tickets: list[Ticket] | None = None) -> list[Repeat]:
    return [r for t in (tickets if tickets is not None else store.tickets()) if t.is_open and (r := repeated_ask(t, store.owner))]


def escalate(store: Store, t: Ticket) -> bool:
    """Make a ticket with a repeated ask p0 with the tag `emergency`. True when something changed (the caller saves)."""
    changed = False
    if t.meta.get("priority") != "p0":
        t.meta["priority"] = "p0"
        changed = True
    tags = list(t.tags)
    if "emergency" not in tags:
        t.meta["tags"] = tags + ["emergency"]
        changed = True
    return changed


# -- stale hand-offs ---------------------------------------------------------------------

def sweep_times(home) -> list[dt.datetime]:
    out = []
    for v in cm._state(home).get("sweeps") or []:
        try:
            out.append(dt.datetime.fromisoformat(str(v)))
        except ValueError:
            pass
    return sorted(out)


def _changed_at(t: Ticket) -> dt.datetime | None:
    try:
        return dt.datetime.fromtimestamp(t.path.stat().st_mtime)
    except (OSError, AttributeError):
        return None


def stale_handoffs(store: Store, tickets: list[Ticket] | None = None) -> list[tuple[Ticket, dt.datetime]]:
    """(ticket, last changed) for open tickets whose `blocked_on` names the owner and that have not changed since before the
    second-to-last sweep, so at least two sweeps have looked at them and nothing happened."""
    sweeps = sweep_times(store.home)
    if len(sweeps) < 2:
        return []
    older = sweeps[-2]
    name = re.compile(rf"(?<![a-z0-9]){re.escape(store.owner.lower())}(?![a-z0-9])")
    out = []
    for t in tickets if tickets is not None else store.tickets():
        if t.status in CLOSED or not name.search(str(t.meta.get("blocked_on") or "").lower()):
            continue
        at = _changed_at(t)
        if at is not None and at < older:
            out.append((t, at))
    return out


def stale_message(store: Store, t: Ticket, at: dt.datetime) -> str:
    return f"{t.id}: waiting on {store.owner.capitalize()} since {at:%Y-%m-%d %H:%M}; is the hand-off real?"


def flags(store: Store) -> list[str]:
    """Every flag, repeated asks first, then stale hand-offs: what validate warns about and the sweep prints first."""
    tickets = store.tickets()
    return [r.message(store.owner) for r in repeats(store, tickets)] + [stale_message(store, t, at) for t, at in stale_handoffs(store, tickets)]
