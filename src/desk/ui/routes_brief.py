"""Pages of the daily loop's reading side (DESK-0288): /brief, /brief/week, /flow, /repeats, /company and /company/<key>, /deadlines.ics,
plus the Today tiles (lane balance and review debt) and the ticket page's flow time, offered to templates as globals.

Nothing here writes: the brief's buttons post to the decide route that already exists. Registered from app.py with one line."""

from __future__ import annotations

import datetime as dt
import html

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from .. import brief as bf, flow as flowmod, ics, review as rv

FLOW_DAYS = 30
FLOW_ROWS = 40
SHOWN = 12  # decision tiles drawn on /brief; the rest are one link away (a store can hold hundreds)


def register(app, templates, ctx, store) -> None:
    router = APIRouter()

    def today_tiles(s) -> dict:
        ts = s.tickets()
        today = dt.date.today()
        return {"balance": bf.balance(s, ts, today), "debt": bf.review_debt(s, ts, today)}

    templates.env.globals.update(brief_tiles=today_tiles, flow_of=lambda s, t: flowmod.view(s, t))

    @router.get("/brief", response_class=HTMLResponse)
    def brief_page(request: Request):
        s = store()
        b = bf.build_brief(s)
        today = dt.date.today()
        return templates.TemplateResponse(request, "brief.html", ctx(request, s, b=b, s=s, long_today=f"{today.strftime('%A, %B')} {today.day}",
                                                                   review_t=rv.find(s, rv.monday_of(today)), shown=SHOWN))

    @router.get("/brief/week", response_class=HTMLResponse)
    def brief_week(request: Request, week: str = ""):
        s = store()
        try:
            day = dt.date.fromisoformat(week) if week else dt.date.today()
        except ValueError:
            day = dt.date.today()
        monday = rv.monday_of(day)
        w = bf.weekly(s, monday, min(dt.date.today(), monday + dt.timedelta(days=6)) if monday <= dt.date.today() else monday)
        return templates.TemplateResponse(request, "brief_week.html", ctx(request, s, w=w, s=s, review_t=rv.find(s, monday),
                                                                        prev_week=(monday - dt.timedelta(days=7)).isoformat(),
                                                                        next_week=(monday + dt.timedelta(days=7)).isoformat()))

    @router.get("/flow", response_class=HTMLResponse)
    def flow_page(request: Request):
        s = store()
        since = (dt.date.today() - dt.timedelta(days=FLOW_DAYS)).isoformat()
        ts = [t for t in s.tickets() if str(t.meta.get("updated") or "")[:10] >= since]  # anything that moved in the window
        rows = []
        for t in ts:
            v = flowmod.view(s, t)
            if v["total"]:
                rows.append((t, v))
        waiting = sorted((r for r in rows if not r[1]["closed"] and r[1]["parts"]["waiting"] > 0), key=lambda r: -r[1]["parts"]["waiting"])[:FLOW_ROWS]
        closed = sorted((r for r in rows if r[1]["closed"]), key=lambda r: -r[1]["parts"]["waiting"])[:FLOW_ROWS]
        sums = {p: sum(v["parts"][p] for _t, v in rows if v["closed"]) for p in flowmod.PARTS}
        whole = sum(sums.values())
        summary = [{"label": flowmod.LABELS[p], "part": p, "pct": round(sums[p] * 100 / whole) if whole else 0} for p in flowmod.PARTS]
        return templates.TemplateResponse(request, "flow.html", ctx(request, s, s=s, waiting=waiting, closed=closed, summary=summary,
                                                                  n_closed=sum(1 for r in rows if r[1]["closed"]), days=FLOW_DAYS))

    @router.get("/repeats", response_class=HTMLResponse)
    def repeats_page(request: Request, weeks: int = 8):
        s = store()
        weeks = min(max(weeks, 1), 52)
        return templates.TemplateResponse(request, "repeats.html", ctx(request, s, s=s, weeks=weeks, groups=bf.repeats_by_week(s, weeks)))

    @router.get("/company", response_class=HTMLResponse)
    def company_index(request: Request):
        s = store()
        return templates.TemplateResponse(request, "company.html", ctx(request, s, s=s, c=None, all_companies=bf.companies_list(s)))

    @router.get("/company/{key}", response_class=HTMLResponse)
    def company_one(request: Request, key: str):
        s = store()
        c = bf.company_page(s, key.strip().lower())
        if c is None:
            return HTMLResponse(f"<p>No company called {html.escape(key)}. <a href=\"/company\">See every company</a>.</p>", status_code=404)
        return templates.TemplateResponse(request, "company.html", ctx(request, s, s=s, c=c, all_companies=[]))

    @router.get("/deadlines.ics")
    def deadlines_ics(request: Request, all: int = 0):
        s = store()
        body = ics.calendar(s.tickets(), s.owner, base_url=str(request.base_url), everyone=bool(all))
        return Response(body.encode("utf-8"), media_type="text/calendar; charset=utf-8",
                        headers={"Content-Disposition": "inline; filename=desk-deadlines.ics", "Cache-Control": "no-cache"})

    app.include_router(router)
