"""The daily brief (DESK-0282 contract; DESK-0288 owns and replaces this minimal version, DESK-0289 sends its plain text).

`build_brief(store, today)` returns a dict with at least `decisions` (list of {id, title, ask}), `deadlines` (list of {id, title,
date}) and `shipped` (list of {id, title}); `brief_text(brief)` returns the plain text a notification sends: ids, titles and counts
only, never a ticket's Context or Report."""

from __future__ import annotations

import datetime as dt


def build_brief(store, today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    decisions = []
    for t in store.all():
        if t.is_open and str(t.meta.get("blocked_on", "")).strip().lower() == "james":
            decisions.append({"id": t.id, "title": str(t.meta.get("title", "")), "ask": ""})
    return {"date": today.isoformat(), "decisions": decisions, "deadlines": [], "shipped": []}


def brief_text(brief: dict) -> str:
    lines = [f"Desk brief {brief.get('date', '')}: {len(brief.get('decisions', []))} decision(s) waiting"]
    lines += [f"- {d['id']}: {d['title']}" for d in brief.get("decisions", [])[:10]]
    return "\n".join(lines)
