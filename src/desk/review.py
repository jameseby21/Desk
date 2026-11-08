"""Ticket review: a weekly ticket whose Acceptance lists every ticket that needs the owner's review, feedback or decision."""

from __future__ import annotations

import datetime as dt
import re
import statistics

from .store import Store
from .ticket import Ticket

GROUPS = ["Approve", "Choose", "Answer", "Provide", "Do", "Review"]
ASK_RE = re.compile(r"^(approve|choose|answer|provide|do)\b", re.I)
WAITING_QUEUE = {"ready", "triage", "inbox", "blocked"}
MOVED_ON = {"done", "approved", "changes_requested", "rejected"}
TAG = "review-week"

ACC_START, ACC_END = "<!-- review:start -->", "<!-- review:end -->"
KPI_START, KPI_END = "<!-- kpi:start -->", "<!-- kpi:end -->"
_ITEM = re.compile(r"^- \[( |x|X)\] ([A-Za-z0-9-]+-\d{4,}) · (.*)$")
_INITIAL = re.compile(r"<!-- initial: ([^>]*?) -->")
_DECISION_DATE = re.compile(r"^\s*-\s*(\d{4}-\d{2}-\d{2})\b", re.M)
_FAR = "9999-12-31"


class ReviewError(Exception):
    pass


def monday_of(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def parse_day(text: str | None) -> dt.date:
    if not text:
        return dt.date.today()
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        raise ReviewError(f"`{text}` is not a date; use YYYY-MM-DD.")


def _md(d) -> str:
    d = d if isinstance(d, dt.date) else dt.date.fromisoformat(str(d)[:10])
    return f"{d.strftime('%b')} {d.day:02d}"


def title_for(monday: dt.date) -> str:
    return f"Ticket review: week of {_md(monday)}"


def is_review_ticket(t: Ticket) -> bool:
    return TAG in t.tags


def week_of(t: Ticket) -> dt.date:
    return monday_of(dt.date.fromisoformat(str(t.meta["review_by"])[:10]))


def find(store: Store, monday: dt.date) -> Ticket | None:
    friday = (monday + dt.timedelta(days=4)).isoformat()
    for t in store.tickets():
        if is_review_ticket(t) and str(t.meta.get("review_by", ""))[:10] == friday:
            return t
    return None


def decision_dates(t: Ticket) -> list[dt.date]:
    out = []
    for m in _DECISION_DATE.finditer(t.section("Decision")):
        try:
            out.append(dt.date.fromisoformat(m.group(1)))
        except ValueError:
            pass
    return out


def decided_in_week(t: Ticket, monday: dt.date) -> bool:
    sunday = monday + dt.timedelta(days=6)
    return any(monday <= d <= sunday for d in decision_dates(t))


def ask_of(t: Ticket) -> str:
    m = ASK_RE.match(str(t.meta.get("title", "")).strip())
    return m.group(1).capitalize() if m else "Review"


def qualifies(t: Ticket, owner: str) -> bool:
    if is_review_ticket(t):
        return False
    if t.status == "review":
        return True
    if t.status in WAITING_QUEUE:
        title = str(t.meta.get("title", "")).strip()
        if str(t.meta.get("owner")) == owner and (t.meta.get("type") == "decision" or ASK_RE.match(title)):
            return True
        if t.status == "blocked" and owner.lower() in str(t.meta.get("blocked_on") or "").lower():
            return True
    return False


def _sort_key(t: Ticket):
    return (str(t.meta.get("review_by") or _FAR)[:10], t.id)


def _rest(t: Ticket) -> str:
    by = f" · by {_md(t.meta['review_by'])}" if t.meta.get("review_by") else ""
    steps = t.validate_steps()
    check = f" · validate: {steps.splitlines()[0].strip()[:120]}" if steps else ""
    failed = len(t.failed_checks())
    bad = f" · {failed} failed" if failed else ""
    return f"{t.meta.get('title')}{by}{bad}{check}"


def _days(t: Ticket, today: dt.date) -> int:
    try:
        return (today - dt.date.fromisoformat(str(t.meta.get("updated"))[:10])).days
    except ValueError:
        return 0


# ---- block parsing / rendering ------------------------------------------------

def _between(text: str, start: str, end: str) -> str | None:
    a, b = text.find(start), text.find(end)
    if a < 0 or b < a:
        return None
    return text[a + len(start):b]


def _replace_block(text: str, start: str, end: str, inner: str) -> str:
    block = f"{start}\n{inner.strip(chr(10))}\n{end}"
    a, b = text.find(start), text.find(end)
    if a >= 0 and b > a:
        return text[:a] + block + text[b + len(end):]
    return (text.rstrip("\n") + "\n\n" if text.strip() else "") + block


def parse_block(text: str) -> dict:
    """{'items': [(checked, id, rest, group)], 'initial': [ids] | None} from the Acceptance section."""
    inner = _between(text, ACC_START, ACC_END) or ""
    items, group = [], "Review"
    for line in inner.splitlines():
        m = re.match(r"^\*\*(.+?)\*\*\s*$", line.strip())
        if m:
            group = m.group(1).split(" \u00b7 ")[0]  # `**Review · chaz**` is the Review group, sub-headed by assignee
            continue
        m = _ITEM.match(line.strip())
        if m:
            items.append((m.group(1).lower() == "x", m.group(2), m.group(3), group))
    mi = _INITIAL.search(inner)
    initial = [x.strip() for x in mi.group(1).split(",") if x.strip()] if mi else None
    return {"items": items, "initial": initial}


def waiting_count(t: Ticket) -> int:
    return sum(1 for chk, *_ in parse_block(t.section("Acceptance"))["items"] if not chk)


def render_block(items: list, initial: list[str], assignees: dict | None = None) -> str:
    """The checkbox list. The Review group is sub-headed by assignee (`**Review · chaz**`) when any of its tickets has one."""
    assignees = assignees or {}
    lines = [f"<!-- initial: {','.join(initial)} -->"]
    groups = GROUPS + sorted({i[3] for i in items} - set(GROUPS))
    for g in groups:
        mine = [i for i in items if i[3] == g]
        if not mine:
            continue
        parts = [(g, mine)]
        if g == "Review" and any(assignees.get(i[1]) for i in mine):
            names = sorted({assignees.get(i[1]) or "" for i in mine}, key=lambda n: (n == "", n.lower()))
            parts = [(f"Review \u00b7 {n or 'unassigned'}", [i for i in mine if (assignees.get(i[1]) or "") == n]) for n in names]
        for head, rows in parts:
            lines.append(f"\n**{head}**\n")
            lines += [f"- [{'x' if c else ' '}] {i} · {r}" for c, i, r, _ in rows]
    return "\n".join(lines)


# ---- generation ---------------------------------------------------------------

def compute(store: Store, review: Ticket, today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    monday = week_of(review)
    owner = str(review.meta.get("owner") or store.owner)
    tickets = {t.id: t for t in store.tickets()}
    qual = {i for i, t in tickets.items() if i != review.id and qualifies(t, owner)}
    prev = parse_block(review.section("Acceptance"))
    existing = {i: (c, r, g) for c, i, r, g in prev["items"]}

    ids = sorted(set(existing) | qual, key=lambda i: _sort_key(tickets[i]) if i in tickets else (_FAR, i))
    items, added, newly_checked, resolved = [], 0, 0, 0
    for i in ids:
        t = tickets.get(i)
        was_checked = existing[i][0] if i in existing else False
        moved = bool(t) and (t.status in MOVED_ON or decided_in_week(t, monday))
        gone = i not in qual
        if gone:
            rest = re.sub(r"\s*\(resolved\)\s*$", "", existing[i][1]) + " (resolved)"
            group = ask_of(t) if t else existing[i][2]
            resolved += 1
        else:
            rest, group = _rest(t), ask_of(t)
        checked = was_checked or moved or gone
        added += i not in existing
        newly_checked += checked and not was_checked
        items.append((checked, i, rest, group))
    initial = prev["initial"] if prev["initial"] is not None else [i for _, i, _, _ in items]
    waiting = [it for it in items if not it[0]]
    ages = [_days(t, today) for t in tickets.values() if t.status == "review" and t.id != review.id]
    oldest = min(((-_days(tickets[i], today), i) for _, i, _, _ in waiting if i in tickets), default=None)
    oldest = (-oldest[0], oldest[1]) if oldest else None
    kpi = {
        "at_creation": len(initial),
        "added": len([1 for _, i, _, _ in items if i not in initial]),
        "decided": sum(1 for t in tickets.values() if t.id != review.id and decided_in_week(t, monday)),
        "waiting": len(waiting),
        "median_days": statistics.median(ages) if ages else None,
        "oldest": oldest,
    }
    return {"items": items, "initial": initial, "kpi": kpi, "added": added, "checked": newly_checked, "resolved": resolved}


def kpi_text(k: dict) -> str:
    med = "n/a" if k["median_days"] is None else f"{k['median_days']:g}"
    old = "none" if not k["oldest"] else f"{k['oldest'][1]}, {k['oldest'][0]} days"
    return "\n".join([
        f"- Waiting at creation: {k['at_creation']}",
        f"- Added during the week: {k['added']}",
        f"- Decided this week: {k['decided']}",
        f"- Still waiting: {k['waiting']}",
        f"- Median days in review: {med}",
        f"- Oldest waiting: {old}",
    ])


def ops_assignee(t: Ticket) -> str:
    return " ".join(str(t.meta.get("assignee") or "").split())


def apply(store: Store, review: Ticket, today: dt.date | None = None) -> dict:
    res = compute(store, review, today)
    review.set_section("Acceptance", _replace_block(review.section("Acceptance"), ACC_START, ACC_END, render_block(res["items"], res["initial"], {t.id: ops_assignee(t) for t in store.tickets()})))
    review.set_section("Report", _replace_block(review.section("Report"), KPI_START, KPI_END, kpi_text(res["kpi"])))
    store.save(review)
    return res


def create(store: Store, day: dt.date | None = None, project: str = "management", owner: str = "james") -> tuple[Ticket, dict]:
    monday = monday_of(day or dt.date.today())
    if project not in store.projects():
        raise ReviewError(f"No project profile `{project}` in this store (projects/{project}.md). Add it, or pass --project.")
    old = find(store, monday)
    if old:
        raise ReviewError(f"{old.id} is already the ticket review for the week of {_md(monday)}.")
    t = store.create(title_for(monday), project, "task", priority="p1", size="s", owner=owner, assignee=owner)
    t.meta.update(status="ready", tags=["management", TAG], review_by=(monday + dt.timedelta(days=4)).isoformat())
    store.save(t)
    res = apply(store, t)
    return t, res


# ---- stats ----------------------------------------------------------------------

def decisions_per_week(store: Store, weeks: int = 8, today: dt.date | None = None) -> dict:
    """{monday iso: tickets with a Decision line dated that week}, the last `weeks` weeks, oldest first."""
    this = monday_of(today or dt.date.today())
    out = {(this - dt.timedelta(weeks=n)).isoformat(): 0 for n in range(weeks - 1, -1, -1)}
    for t in store.tickets():
        for m in {monday_of(d).isoformat() for d in decision_dates(t)}:
            if m in out:
                out[m] += 1
    return out


def review_weeks(store: Store) -> dict:
    rw = [t for t in store.tickets() if is_review_ticket(t)]
    return {"created": len(rw), "closed": sum(1 for t in rw if not t.is_open)}
