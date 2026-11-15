"""A calendar feed of hard deadlines and review-by dates (DESK-0288, idea 45), served at /deadlines.ics.

RFC 5545: lines end in CRLF, long lines fold at 75 octets with a CRLF and one space, text escapes backslash, semicolon, comma and
newline, and every event has a UID that stays the same from one fetch to the next (`<ID>-deadline@desk` or `<ID>-review-by@desk`), so a
calendar updates an event instead of adding a second one. Each date is an all-day event. Only open tickets are listed (done, rejected
and approved tickets wait on nobody). Review-by dates are the owner's own tickets unless everyone's are asked for, so the owner's
calendar holds his dates and not every agent's. Docs: docs/COMMANDS.md (/deadlines.ics)."""

from __future__ import annotations

import datetime as dt

from . import deadlines
from .ticket import Ticket

CRLF = "\r\n"


def escape(text: str) -> str:
    return (str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n"))


def fold(line: str) -> str:
    """Split a content line into pieces of at most 75 octets (UTF-8), never inside a character."""
    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        limit = 75 if not out else 74  # a continuation line starts with one space, which counts
        if size + n > limit:
            out.append(cur)
            cur, size = "", 0
        cur += ch
        size += n
    out.append(cur)
    return (CRLF + " ").join(out)


def items(tickets: list[Ticket], owner: str, everyone: bool = False) -> list[dict]:
    """[{uid, date, summary, id, kind}] sorted by date then id."""
    out = []
    for t in tickets:
        if t.status in deadlines.DONE_WITH:
            continue
        title = str(t.meta.get("title") or "")
        d, kind = deadlines.of(t)
        if d and kind == "hard":
            out.append({"uid": f"{t.id}-deadline@desk", "date": d, "summary": f"Deadline: {t.id} {title}", "id": t.id, "kind": "deadline"})
        rb = deadlines.parse(t.meta.get("review_by"))
        if rb and (everyone or str(t.meta.get("owner") or "").strip().lower() == owner.lower()):
            out.append({"uid": f"{t.id}-review-by@desk", "date": rb, "summary": f"Review by: {t.id} {title}", "id": t.id, "kind": "review-by"})
    return sorted(out, key=lambda x: (x["date"], x["id"], x["kind"]))


def calendar(tickets: list[Ticket], owner: str, base_url: str = "", everyone: bool = False, now: dt.datetime | None = None) -> str:
    stamp = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//jeby//Desk deadlines//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             "X-WR-CALNAME:Desk deadlines"]
    for it in items(tickets, owner, everyone):
        lines += ["BEGIN:VEVENT", f"UID:{it['uid']}", f"DTSTAMP:{stamp}", f"DTSTART;VALUE=DATE:{it['date']:%Y%m%d}",
                  f"DTEND;VALUE=DATE:{it['date'] + dt.timedelta(days=1):%Y%m%d}", f"SUMMARY:{escape(it['summary'])}", "TRANSP:TRANSPARENT"]
        if base_url:
            lines.append(f"URL:{base_url.rstrip('/')}/ticket/{it['id']}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "".join(fold(x) + CRLF for x in lines)
