"""/analytics (DESK-0336): flow, people, waiting, speed, live monitoring and the meta-analysis results, all counted from the store's own
files by desk.analytics. /analytics/live.json is the small route the live block polls every 30 seconds. Nothing here writes."""

from __future__ import annotations

import datetime as dt
import html

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from markupsafe import Markup

from .. import analytics as an


def register(app, templates, ctx, store) -> None:
    router = APIRouter()

    def linked(text, ids) -> Markup:
        """Escaped text with every id of a ticket that exists turned into a link to it."""
        out = []
        for piece, is_id in an.link_ids(str(text or ""), ids):
            out.append(f'<a class="tid" href="/ticket/{html.escape(piece)}">{html.escape(piece)}</a>' if is_id else html.escape(piece))
        return Markup("".join(out))

    templates.env.globals.update(an_link=linked, an_idle=an.idle_sentence)

    @router.get("/analytics", response_class=HTMLResponse)
    def analytics_page(request: Request):
        s = store()
        data = an.build(s)
        data["live"] = an.live(s)  # the same payload the poll returns, so the first paint and the refreshes agree
        return templates.TemplateResponse(request, "analytics.html", ctx(request, s, s=s, a=data, ids=data["ids"], live_url="/analytics/live.json", refresh_s=30))

    @router.get("/analytics/live.json")
    def analytics_live():
        return JSONResponse(an.live(store()), headers={"Cache-Control": "no-store"})

    app.include_router(router)
