"""The daily brief and the numbers around it (DESK-0288; DESK-0282 contract, DESK-0289 sends its plain text).

`build_brief(store, today)` answers "what do I decide today": `decisions` (what needs the owner, from Needs me, snoozed tickets left
out, each with the answers the decide route takes), `deadlines` (hard deadlines in the next seven days, and missed ones), `shipped`
(tickets moved to done yesterday, per the event log), plus `shipped_week`, `balance` (where the open work sits: now, planned, later,
parked) and `review_debt` (tickets in review and the oldest one's age). `brief_text(brief)` is the plain text a notification sends: ids,
titles and counts only, never a ticket's Context or Report.

`weekly(store, monday, today)` is what the weekly review writes into its Report by itself: decided, shipped, slipped, the balance of
the work and one recommended first version to build (the oldest planned ticket tagged mvp, else the smallest planned one).
Docs: docs/COMMANDS.md (/brief, /brief/week, /company, /repeats, drew brief)."""

from __future__ import annotations

import datetime as dt
import re

from . import activity, asks, deadlines, flow, lanes, needs_me as nmod
from .ticket import CLOSED, PRIORITIES, SIZES, Ticket

WEEK = 7
TEXT_ROWS = 10  # rows per section in the plain text; the rest is "and N more"
_LINK = re.compile(r"https?://[^\s)>\]]+")
LINK_FIELDS = ("release", "release_url", "live", "live_url", "url", "pr", "pr_url")  # a ticket's own link to the live thing, first wins
NOT_STARTED = ("inbox", "triage", "ready")


# ---- small helpers -------------------------------------------------------------------------------------------------------------

def _now_for(today: dt.date) -> dt.datetime:
    return dt.datetime.now() if today == dt.date.today() else dt.datetime.combine(today, dt.time(12, 0))


def _date(v) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(v or "")[:10])
    except ValueError:
        return None


def _ts(e) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(e.ts)[:19])
    except ValueError:
        return None


def _title(t: Ticket) -> str:
    return str(t.meta.get("title") or "")


def _row(t: Ticket, **kw) -> dict:
    return {"id": t.id, "title": _title(t), "project": t.project, "status": t.status, **kw}


def snooze_href(ticket_id: str) -> str:
    """Where Snooze goes: the ticket's own snooze control (DESK-0283 draws it under the `snooze` anchor)."""
    return f"/ticket/{ticket_id}#snooze"


def status_moves(store, t: Ticket) -> list[tuple[dt.datetime, str, str]]:
    """[(when, old, new)] for every status change in the ticket's event log, oldest first."""
    out = []
    for e in activity.read(store.home, t.id):
        if e.change == "field" and e.field == "status" and _ts(e):
            out.append((_ts(e), e.old, e.new))
    return sorted(out)


def shipped_at(store, t: Ticket) -> dt.datetime | None:
    """When the ticket last moved to done, from the event log; `done_at` when the log has no such move."""
    if t.status != "done":
        return None
    moves = [w for w, _o, n in status_moves(store, t) if n == "done"]
    if moves:
        return moves[-1]
    try:
        return dt.datetime.fromisoformat(str(t.meta.get("done_at") or "")[:19])
    except ValueError:
        return None


def ship_link(t: Ticket) -> str:
    """The link to the live thing: a release, live or pull-request field, else the first pull-request or release link in Evidence,
    Report or Decision. '' when there is none."""
    for k in LINK_FIELDS:
        v = str(t.meta.get(k) or "").strip()
        if v.startswith(("http://", "https://")):
            return v
    for sec in ("Evidence", "Report", "Decision"):
        for m in _LINK.finditer(t.section(sec)):
            if re.search(r"/pull/\d+|/releases/", m.group()):
                return m.group().rstrip(".,;")
    return ""


def shipped(store, tickets: list[Ticket], first: dt.date, last: dt.date) -> list[dict]:
    """Tickets moved to done between `first` and `last` (both included), newest first, each with its link."""
    out = []
    for t in tickets:
        if t.status != "done" or (str(t.meta.get("updated") or "")[:10] < first.isoformat() and str(t.meta.get("done_at") or "")[:10] < first.isoformat()):
            continue  # a ticket done in the window was saved in the window, so older ones need no log read
        when = shipped_at(store, t)
        if when and first <= when.date() <= last:
            out.append(_row(t, when=when, link=ship_link(t)))
    return sorted(out, key=lambda r: (r["when"], r["id"]), reverse=True)


# ---- decisions -------------------------------------------------------------------------------------------------------------

def answers(store, t: Ticket, kind: str) -> list[dict]:
    """The buttons a decision tile carries, each a post the existing decide route accepts. A choose option needs only the click (the
    option's text is the answer); Approve, Send back and Answer set `needs_words`, so the Decision line quotes words the owner typed
    (rule 10: no fixed sentence is ever recorded as his words)."""
    ask = asks.ask_of(t)
    if ask and str(t.meta.get("owner") or "").lower() == store.owner.lower() and t.status in ("ready", "review"):
        if ask["kind"] == "choose":
            return [{"label": f"{lab}" if lab == text else f"{lab}: {text}", "action": "approve", "reply": "1", "option": str(i), "words": ""}
                    for i, (lab, text) in enumerate(ask["options"])]
        if ask["kind"] == "approve":
            return [{"label": "Approve", "action": "approve", "reply": "1", "option": "", "words": "", "needs_words": True},
                    {"label": "Send back", "action": "changes", "reply": "1", "option": "", "words": "", "needs_words": True}]
        return [{"label": "Answer", "action": "approve", "reply": "1", "option": "", "words": "", "needs_words": True}]
    if kind == "approve" and t.status == "review":
        return [{"label": "Approve", "action": "approve", "reply": "", "option": "", "words": "", "needs_words": True},
                {"label": "Send back", "action": "changes", "reply": "", "option": "", "words": "", "needs_words": True}]
    return []


def decisions(store, today: dt.date | None = None, tickets: list[Ticket] | None = None) -> list[dict]:
    """What needs the owner: the asks Needs me lists (same order: emergencies, overdue, due date, priority), then tickets blocked on him.
    Snoozed tickets are left out until their date."""
    today = today or dt.date.today()
    tickets = store.tickets() if tickets is None else tickets
    out = []
    for i in nmod.build(store, now=_now_for(today), tickets=tickets):
        if i.group != "ask" or lanes.is_snoozed(i.ticket, today):
            continue
        out.append(_row(i.ticket, ask=i.kind, chip=i.chip, why=i.why, overdue=i.overdue, emergency=i.emergency,
                        answers=answers(store, i.ticket, i.kind), open_href=f"/ticket/{i.id}#decision", snooze_href=snooze_href(i.id)))
    seen = {d["id"] for d in out}
    for t in tickets:
        if (t.id not in seen and t.status == "blocked" and flow.names_owner(str(t.meta.get("blocked_on") or ""), store.owner)
                and not lanes.is_snoozed(t, today)):
            out.append(_row(t, ask="unblock", chip="Unblock this", why=f"blocked on you: {t.meta.get('blocked_on')}", overdue=False,
                            emergency="emergency" in t.tags, answers=[], open_href=f"/ticket/{t.id}#decision", snooze_href=snooze_href(t.id)))
    return out


def deadlines_week(tickets: list[Ticket], today: dt.date) -> list[dict]:
    """Open tickets with a hard deadline in the next seven days, or already missed, soonest first."""
    out = []
    for t in deadlines.immediate(tickets, today, days=WEEK - 1):
        n = deadlines.days_left(t, today)
        out.append(_row(t, date=deadlines.of(t)[0], words=deadlines.words(n), missed=n < 0))
    return out


# ---- balance and review debt ---------------------------------------------------------------------------------------------

def _age(t: Ticket, today: dt.date) -> int:
    c = _date(t.meta.get("created"))
    return (today - c).days if c else 0


def created_closed(store, tickets: list[Ticket], today: dt.date, days: int = WEEK) -> tuple[int, int]:
    """(created, closed) in the last `days` days (today included). Closed comes from the event log (a status change to done or
    rejected); only tickets that are closed now and were saved in the window are read. Created is the ticket's `created` date, the
    same day its `created` event carries, so no log needs reading for it."""
    first = today - dt.timedelta(days=days - 1)
    made = closed = 0
    for t in tickets:
        c = _date(t.meta.get("created"))
        made += bool(c and first <= c <= today)
        if t.status not in CLOSED or str(t.meta.get("updated") or "")[:10] < first.isoformat():
            continue
        closed += any(e.change == "field" and e.field == "status" and e.new in CLOSED and _ts(e) and first <= _ts(e).date() <= today
                      for e in activity.read(store.home, t.id))
    return made, closed


def balance(store, tickets: list[Ticket], today: dt.date | None = None) -> dict:
    """Where the open work sits. `counts` per lane; `now_oldest` (id, days since created); `planned_week` (planned tickets due by
    Sunday, overdue included); `later_week` (later tickets recorded since Monday); `created` and `closed` in the last seven days."""
    today = today or dt.date.today()
    monday = today - dt.timedelta(days=today.weekday())
    sunday = monday + dt.timedelta(days=6)
    open_ = [t for t in tickets if t.is_open]
    by = {ln: [t for t in open_ if lanes.lane_of(t) == ln] for ln in lanes.LANES}
    counts = {ln: len(v) for ln, v in by.items()}
    total = sum(counts.values())
    oldest = max(by["now"], key=lambda t: (_age(t, today), t.id), default=None)

    def due(t):
        ds = [d for d in (_date(t.meta.get("review_by")), deadlines.of(t)[0]) if d]
        return min(ds) if ds else None

    planned_week = sum(1 for t in by["planned"] if (d := due(t)) and d <= sunday)
    later_week = sum(1 for t in by["later"] if (c := _date(t.meta.get("created"))) and monday <= c <= today)
    made, closed = created_closed(store, tickets, today)
    segs = [{"lane": ln, "count": counts[ln], "pct": round(counts[ln] * 100 / total, 1) if total else 0} for ln in lanes.LANES]
    return {"counts": counts, "total": total, "segments": segs, "now_oldest": (oldest.id, _age(oldest, today), _title(oldest)) if oldest else None,
            "planned_week": planned_week, "later_week": later_week, "created": made, "closed": closed,
            "net": made - closed}


def review_debt(store, tickets: list[Ticket], today: dt.date | None = None) -> dict:
    """Tickets in review and the oldest one's age in days since it entered review (the log; `updated` when the log has no move)."""
    today = today or dt.date.today()
    rows = []
    for t in tickets:
        if t.status != "review":
            continue
        moves = [w for w, _o, n in status_moves(store, t) if n == "review"]
        since = moves[-1].date() if moves else (_date(t.meta.get("updated")) or today)
        rows.append(((today - since).days, t))
    rows.sort(key=lambda r: (-r[0], r[1].id))
    old = rows[0] if rows else None
    return {"count": len(rows), "oldest_days": old[0] if old else 0, "oldest_id": old[1].id if old else "",
            "oldest_title": _title(old[1]) if old else "", "level": "high" if old and old[0] >= 7 else "mid" if old and old[0] >= 3 else "low"}


# ---- the brief -----------------------------------------------------------------------------------------------------------

def build_brief(store, today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    tickets = store.tickets()
    yesterday = today - dt.timedelta(days=1)
    monday = today - dt.timedelta(days=today.weekday())
    ds = decisions(store, today, tickets)
    dl = deadlines_week(tickets, today)
    sh = shipped(store, tickets, yesterday, yesterday)
    return {"date": today.isoformat(), "decisions": ds, "deadlines": dl, "shipped": sh,
            "shipped_week": shipped(store, tickets, monday, today),
            "snoozed": sum(1 for t in tickets if t.is_open and lanes.is_snoozed(t, today)),
            "balance": balance(store, tickets, today), "review_debt": review_debt(store, tickets, today),
            "counts": {"decisions": len(ds), "deadlines": len(dl), "shipped": len(sh)}}


def _n(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def brief_text(brief: dict) -> str:
    """Plain text for a notification: the date, counts, and `ID: title` lines. Nothing from a ticket's body."""
    ds, dl, sh = brief.get("decisions", []), brief.get("deadlines", []), brief.get("shipped", [])
    lines = [f"Desk brief {brief.get('date', '')}: {_n(len(ds), 'decision')}, {_n(len(dl), 'deadline')} this week, {len(sh)} shipped yesterday"]
    for head, rows in (("Decide", ds), ("Deadlines", dl), ("Shipped yesterday", sh)):
        if not rows:
            continue
        lines.append(f"{head} ({len(rows)}):")
        lines += [f"- {r['id']}: {r['title']}" for r in rows[:TEXT_ROWS]]
        if len(rows) > TEXT_ROWS:
            lines.append(f"- and {len(rows) - TEXT_ROWS} more")
    return "\n".join(lines)


# ---- the weekly review -----------------------------------------------------------------------------------------------------

def mvp_pick(tickets: list[Ticket]) -> Ticket | None:
    """One recommended first version to build: the oldest open planned ticket tagged mvp, else the smallest planned one (size, then
    priority, then age). Tickets not started yet come first; review-week tickets never count."""
    planned = [t for t in tickets if t.is_open and lanes.lane_of(t) == "planned" and "review-week" not in t.tags and t.status not in ("review", "approved")]
    fresh = [t for t in planned if t.status in NOT_STARTED] or planned
    tagged = [t for t in fresh if "mvp" in [x.lower() for x in t.tags]]
    if tagged:
        return min(tagged, key=lambda t: (_date(t.meta.get("created")) or dt.date.max, t.id))

    def rank(v, order):
        return order.index(v) if v in order else len(order)

    return min(fresh, key=lambda t: (rank(t.meta.get("size"), SIZES), rank(t.meta.get("priority"), PRIORITIES), _date(t.meta.get("created")) or dt.date.max, t.id), default=None)


def _decided_on(t: Ticket, first: dt.date, last: dt.date) -> bool:
    for line in t.section("Decision").splitlines():
        m = re.match(r"^\s*-\s*(\d{4}-\d{2}-\d{2})\b", line)
        if m and (d := _date(m.group(1))) and first <= d <= last:
            return True
    return False


def weekly(store, monday: dt.date, today: dt.date | None = None) -> dict:
    """The week of `monday` up to `today` (or Sunday): decided, shipped, slipped, the balance and one recommended first version.
    `today` is the real date: a past week stops at its Sunday (`last`), and only dates that have passed count as slipped."""
    today = today or dt.date.today()
    last = min(monday + dt.timedelta(days=6), today)
    tickets = [t for t in store.tickets() if "review-week" not in t.tags]
    decided = [_row(t) for t in tickets if _decided_on(t, monday, last)]
    slipped = []
    for t in tickets:
        if not t.is_open or t.status == "approved":
            continue
        for what, d in (("review by", _date(t.meta.get("review_by"))), ("deadline", deadlines.of(t)[0])):
            if d and monday <= d <= last and d < today:
                slipped.append(_row(t, what=what, date=d))
                break
    pick = mvp_pick(tickets)
    return {"monday": monday, "last": last, "decided": decided, "shipped": shipped(store, tickets, monday, last),
            "slipped": sorted(slipped, key=lambda r: (r["date"], r["id"])), "balance": balance(store, tickets, last),
            "mvp": _row(pick, size=pick.meta.get("size"), tagged="mvp" in [x.lower() for x in pick.tags]) if pick else None}


def _ids(rows: list[dict], limit: int = 15) -> str:
    if not rows:
        return "none"
    shown = ", ".join(f"{r['id']} {r['title']}" + (f" ({r['link']})" if r.get("link") else "") for r in rows[:limit])
    return shown + (f" and {len(rows) - limit} more" if len(rows) > limit else "")


def week_text(w: dict) -> str:
    """The Report block the weekly review writes by itself, as Markdown lines."""
    b = w["balance"]
    c = b["counts"]
    old = f"; oldest now: {b['now_oldest'][0]}, {b['now_oldest'][1]} days" if b["now_oldest"] else ""
    m = w["mvp"]
    pick = (f"{m['id']} {m['title']} (size {m['size']}, " + ("tagged mvp, the oldest planned" if m["tagged"] else "the smallest planned") + ")") if m else "none: nothing is planned"
    return "\n".join([
        f"**This week by itself** (filled from the store, {w['monday'].isoformat()} to {w['last'].isoformat()})",
        "",
        f"- Decided: {len(w['decided'])}: {_ids(w['decided'])}",
        f"- Shipped: {len(w['shipped'])}: {_ids(w['shipped'])}",
        f"- Slipped (a review-by date or deadline passed, still open): {len(w['slipped'])}: {_ids(w['slipped'])}",
        f"- Where the work sits: now {c['now']}, planned {c['planned']}, later {c['later']}, parked {c['parked']}{old}; "
        f"created {b['created']}, closed {b['closed']} in the last 7 days",
        f"- Recommended first version to build (MVP): {pick}",
    ])


# ---- other pages ---------------------------------------------------------------------------------------------------------------

def repeats_by_week(store, weeks: int = 8, today: dt.date | None = None) -> list[dict]:
    """Repeated asks (the owner asked the same thing twice on a ticket within seven days, `drew repeats`), grouped by the week of the
    second ask, newest week first, over the last `weeks` weeks. Closed tickets count too: a repeat that was answered is still a cause."""
    from . import repeats

    today = today or dt.date.today()
    since = today - dt.timedelta(days=today.weekday()) - dt.timedelta(weeks=weeks - 1)
    by: dict[dt.date, list] = {}
    for t in store.tickets():
        r = repeats.repeated_ask(t, store.owner)
        if r and r.second.date() >= since:
            mon = r.second.date() - dt.timedelta(days=r.second.weekday())
            by.setdefault(mon, []).append({"id": t.id, "title": _title(t), "status": t.status, "first": r.first, "second": r.second, "text": r.text})
    return [{"monday": k, "rows": sorted(v, key=lambda x: x["second"], reverse=True)} for k, v in sorted(by.items(), reverse=True)]


def company_page(store, slug: str, today: dt.date | None = None) -> dict | None:
    """One company on one page: its first-version (MVP) status, Now items, the last thing shipped and the next decision. None when no
    project belongs to a company with that slug."""
    from . import companies

    today = today or dt.date.today()
    of = companies.by_project(store)
    name = next((n for n in of.values() if companies.slug(n) == slug), None)
    if name is None:
        return None
    projects = sorted(k for k, n in of.items() if n == name)
    ts = [t for t in store.tickets() if t.project in projects]
    mvp = [t for t in ts if "mvp" in [x.lower() for x in t.tags]]
    done = [t for t in mvp if t.status == "done"]
    now_items = sorted((t for t in ts if t.is_open and lanes.lane_of(t) == "now"), key=lambda t: (str(t.meta.get("priority") or "p9"), t.id))
    recent = sorted((t for t in ts if t.status == "done"), key=lambda t: (str(t.meta.get("done_at") or t.meta.get("updated") or ""), t.id), reverse=True)[:10]
    ships = sorted((r for t in recent if (w := shipped_at(store, t)) for r in [_row(t, when=w, link=ship_link(t))]), key=lambda r: (r["when"], r["id"]), reverse=True)
    ds = decisions(store, today, ts)
    return {"name": name, "title": companies.title(name), "slug": slug, "projects": [(k, store.project_title(k)) for k in projects],
            "open": sum(1 for t in ts if t.is_open), "mvp_total": len(mvp), "mvp_done": len(done),
            "mvp_open": sorted((_row(t) for t in mvp if t.is_open), key=lambda r: r["id"])[:8],
            "now": [_row(t) for t in now_items[:8]], "now_count": len(now_items), "last_ship": ships[0] if ships else None,
            "next_decision": ds[0] if ds else None, "decisions": len(ds)}


def companies_list(store) -> list[tuple[str, str, int]]:
    """[(slug, title, open tickets)] for /company."""
    from . import companies

    of = companies.by_project(store)
    open_by: dict[str, int] = {}
    for t in store.tickets():
        if t.is_open:
            n = of.get(t.project) or companies.UNSORTED
            open_by[n] = open_by.get(n, 0) + 1
    return sorted(((companies.slug(n), companies.title(n), open_by.get(n, 0)) for n in set(of.values())), key=lambda x: x[1].lower())
