"""Reading mode in the web UI (DESK-0287): `/changes`, and the `why_of` line rows on Today and Needs me carry.

Registered from `create_app` with one line (`routes_reading.install(...)`); the ticket page's part is `reading.ticket_view`, called
from the ticket route. Nothing here writes a ticket: the only write is the viewer's own last-seen marker under `.desk/state/`."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from jinja2 import pass_context

from .. import companies as compmod
from .. import reading


def install(app, templates, ctx, store, acting) -> None:
    router = APIRouter()

    @pass_context
    def why_of(c, t, mode: str = "row") -> str:
        """The short 'why am I seeing this' line for a row. `extra` (Needs me) leaves out the ask and the change, which its own
        reason line already says."""
        s, me = c.get("s"), c.get("me")
        if s is None or me is None:
            return ""
        names = c.get("_rd_names")
        if names is None:  # one cache per page, kept on the context's parent dict
            names = reading.Names(s.home)
            try:
                c.parent["_rd_names"] = names
            except (AttributeError, TypeError):
                pass
        viewer = reading.viewer_key(me)
        if mode == "extra":
            return reading.why_line(s, t, viewer, names=names, skip=("ask", "change", "held"), limit=2)
        return reading.why_line(s, t, viewer, names=names)

    templates.env.globals["why_of"] = why_of

    @router.get("/changes", response_class=HTMLResponse)
    def changes_page(request: Request, since: str = "", project: str = "", company: str = "", mine: int = 0, at: str = ""):
        s = store()
        viewer = reading.viewer_key(acting(request))
        error = ""
        dated = bool(since.strip()) and since.strip().lower() != "last"
        held = None if dated else reading._parse_ts(at)  # a filter on this page keeps the moment the page opened with
        try:
            start, words = (held, reading.since_words(held)) if held else reading.since_for(s.home, viewer, since)
        except ValueError as e:
            error = str(e)
            dated = False
            start, words = reading.since_for(s.home, viewer, "")
        offsets = None if dated else reading.changes_offsets(s.home, viewer, held)  # exact places in each log at the last visit
        groups = reading.changes(s, viewer, start, project=project, company=company, mine=bool(mine), offsets=offsets)
        everything = groups if not (project or company) else reading.changes(s, viewer, start, mine=bool(mine), offsets=offsets)
        if not dated and not held:
            reading.mark_changes(s.home, viewer)  # the next visit starts from now; a dated look or a filter leaves the marker alone
        comps = [(g.company_slug, g.company, g.count) for g in everything]
        projs = [(k, title, len(rows)) for g in everything for k, title, rows in g.projects]
        n = sum(g.count for g in groups)
        return templates.TemplateResponse(request, "changes.html", ctx(request, s, s=s, groups=groups, since_words=words, since=since if dated else "", at="" if dated else start.isoformat(),
                                                                       project=project, company=company, mine=bool(mine), comps=comps, projs=projs,
                                                                       n=n, error=error, week_ago=(dt.date.today() - dt.timedelta(days=7)).isoformat(),
                                                                       company_title=compmod.title))

    app.include_router(router)
