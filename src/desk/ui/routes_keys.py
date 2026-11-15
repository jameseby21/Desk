"""Keyboard and palette (DESK-0290): palette actions on the open ticket, saved views, display settings (theme and type size).

One code path: the palette never writes anything itself. `GET /palette/actions/<ID>` lists what can be done to the open ticket as
(route, form fields, a fresh one-time token), and the palette posts those fields to that route, exactly the post the page's own
button makes: Approve and Send back go to /ticket/<ID>/decide, Assign to /ticket/<ID>/assign, Snooze and the lane moves to
/ticket/<ID>/snooze and /ticket/<ID>/lane.

The lane and snooze routes belong to DESK-0283 (lanes). Its package is built in parallel; until it is merged, `register` adds the two
routes here, and only when no route with that path exists yet, so the integrated app keeps a single route per action either way.
Every write runs inside `activity.batch(...)` and answers with the batch id and a plain sentence (the undo contract of DESK-0284).

Theme (system, light, dark) and type size (small, normal, large) are per device: two cookies the server reads to write
`data-theme` and `data-size` on <html>, so the page is painted in the right theme the first time (no flash)."""

from __future__ import annotations

import datetime as dt
import secrets
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from markupsafe import Markup

from .. import activity, lanes, people as peoplemod, saved_views
from ..rules import RuleError

THEMES = ("system", "light", "dark")
SIZES = ("small", "normal", "large")
COOKIE_AGE = 60 * 60 * 24 * 400  # about a year: a device keeps its choice
LANE_WORDS = {"now": "Now", "planned": "Planned", "later": "Later", "parked": "Parked"}
LANE_HINT = {"now": "Urgent: it goes to the front.", "planned": "Planned for this sprint.", "later": "Kept as an idea, not built yet."}
SNOOZE_DAYS = 7


def display_prefs(request) -> dict:
    """This device's theme and type size, from its cookies; anything unknown is the default."""
    c = getattr(request, "cookies", {}) or {}
    theme = c.get("desk_theme", "system")
    size = c.get("desk_size", "normal")
    return {"theme": theme if theme in THEMES else "system", "size": size if size in SIZES else "normal"}


def display_attrs(request) -> Markup:
    """The attributes for <html>: written by the server so the first paint is already in the chosen theme and size."""
    p = display_prefs(request)
    out = f' data-size="{p["size"]}"'
    if p["theme"] != "system":  # system: no attribute, the colour-scheme media query decides
        out = f' data-theme="{p["theme"]}"' + out
    return Markup(out)


def new_batch() -> str:
    return "b-" + secrets.token_hex(6)


def palette_actions(t, people, issue, today: dt.date | None = None) -> list[dict]:
    """What the palette can do to ticket `t`, each as the route and the form fields its page button posts. `issue` makes a token."""
    today = today or dt.date.today()
    base = f"/ticket/{t.id}"
    out: list[dict] = []

    def add(key, label, url, fields, hint, words="", words_field="words", keywords=""):
        out.append({"key": key, "label": label, "url": url, "fields": {**fields, "token": issue()}, "hint": hint,
                    "words": words, "words_field": words_field, "keywords": (keywords + " " + label).lower().strip()})

    if t.status == "review":
        add("approve", "Approve", base + "/decide", {"action": "approve"}, "Records your words in Decision and moves it to approved.",
            words="Why approve? Your words go in the Decision.", keywords="yes ok accept ship")
        add("changes", "Send back", base + "/decide", {"action": "changes"}, "Records your words and moves it to changes requested.",
            words="Why send it back? Your words go in the Decision.", keywords="no reject changes")
    if lanes.is_snoozed(t, today):
        add("unsnooze", "End snooze", base + "/snooze", {"until": ""}, "Brings it back to Today and Needs me now.", keywords="wake")
    else:
        until = today + dt.timedelta(days=SNOOZE_DAYS)
        add("snooze", "Snooze 1 week", base + "/snooze", {"until": until.isoformat()}, f"Off Today and Needs me until {until.isoformat()}.",
            keywords="snooze 1w week hide")
    cur = lanes.lane_of(t)
    for lane in ("now", "planned", "later"):
        if lane != cur:
            add(f"lane-{lane}", f"Move to {LANE_WORDS[lane]}", base + "/lane", {"to": lane}, LANE_HINT[lane], keywords="lane")
    if cur != "parked":
        add("park", "Park", base + "/lane", {"to": "parked"}, "Decided not now. Nothing is deleted.", words="Why park it? One line.",
            words_field="reason", keywords="lane park not now")
    now = str(t.meta.get("assignee") or "").strip().lower()
    for _group, ps in people.groups():
        for p in ps:
            if p.key.lower() != now:
                add(f"assign-{p.key}", f"Assign to {p.name}", base + "/assign", {"to": p.key}, "Saves the assignee and who assigned it.",
                    keywords="assign give " + p.key + " " + (p.role or ""))
    return out


def register(app, templates, store, ctx) -> None:
    """Add the routes and the template helpers. Called once from create_app, after the other routes, so a route another package
    already added for the same path (DESK-0283's lane and snooze) is kept and this one is skipped."""
    templates.env.globals.update(display_attrs=display_attrs, display_prefs=display_prefs,
                                 saved_views=lambda: saved_views.load(store().home), save_view_url=_save_view_url)
    router = APIRouter()
    tokens = app.state.tokens

    def wants_json(request: Request) -> bool:
        return "application/json" in request.headers.get("accept", "")

    def answer(request, ok: bool, message: str, back: str, batch: str = "", code: int = 200, stamp: str = ""):
        """JSON for the palette and in-place forms; a redirect with `undo` and `msg` (the DESK-0284 toast) for a plain form."""
        if wants_json(request):
            if not ok:
                return JSONResponse({"error": message, "token": tokens.issue()}, status_code=code)
            return JSONResponse({"ok": True, "message": message, "batch": batch, "stamp": stamp})
        sep = "&" if "?" in back else "?"
        if not ok:
            return RedirectResponse(f"{back}{sep}error={quote(message)}", status_code=303)
        return RedirectResponse(f"{back}{sep}" + (f"undo={batch}&" if batch else "") + f"msg={quote(message)}", status_code=303)

    @router.get("/palette/actions/{ticket_id}")
    def actions(ticket_id: str):
        """The open ticket's palette actions (route, fields and a one-time token each), its title and lane."""
        s = store()
        try:
            t = s.get(ticket_id)
        except KeyError:
            return JSONResponse({"error": f"No ticket {ticket_id}."}, status_code=404)
        acts = palette_actions(t, peoplemod.load(s.home), tokens.issue)
        return JSONResponse({"id": t.id, "title": str(t.meta.get("title") or ""), "status": t.status, "lane": lanes.lane_of(t), "actions": acts},
                            headers={"Cache-Control": "no-store"})

    def lane_write(request, ticket_id: str, token: str, change, done: str):
        if not tokens.use(token):
            return answer(request, False, "That form was already sent; reload the page.", f"/ticket/{ticket_id}", code=409)
        s = store()
        bid = new_batch()
        try:
            t = s.get(ticket_id)
            s.sync_before()
            with activity.batch(bid):
                msg = change(t)
                s.save(t)
            s.sync_after(f"desk: {done} {t.id}")
        except KeyError:
            return answer(request, False, f"No ticket {ticket_id}.", "/", code=404)
        except (RuleError, ValueError) as e:
            return answer(request, False, str(e).strip("'\""), f"/ticket/{ticket_id}", code=422)
        from . import events as ev

        return answer(request, True, msg, f"/ticket/{t.id}", batch=bid, stamp=str(ev.ticket_stamp(s, t.id)))

    def post_lane(request: Request, ticket_id: str, to: str = Form(""), reason: str = Form(""), token: str = Form("")):
        """Move a ticket to a lane (now, planned, later, parked). Parking takes a one-line reason and deletes nothing."""
        to = to.strip().lower()

        def change(t):
            if to not in lanes.LANES:
                raise ValueError("Pick Now, Planned, Later or Parked.")
            if to == "parked" and len(reason.strip()) < 3:
                raise ValueError("Say in a few words why it is parked.")
            t.meta["lane"] = to
            if to == "parked":
                t.meta["park_reason"] = " ".join(reason.split())[:200]
            else:
                t.meta.pop("park_reason", None)
            return f"{t.id} is parked. Nothing was deleted." if to == "parked" else f"{t.id} is now marked {LANE_WORDS[to]}."
        return lane_write(request, ticket_id, token, change, "lane")

    def post_snooze(request: Request, ticket_id: str, until: str = Form(""), token: str = Form("")):
        """Snooze a ticket until a date (YYYY-MM-DD or 1w); an empty date ends the snooze."""
        def change(t):
            u = until.strip().lower()
            if not u:
                t.meta.pop("snooze_until", None)
                return f"{t.id} is back on Today and Needs me."
            day = dt.date.today() + dt.timedelta(days=SNOOZE_DAYS) if u in ("1w", "week") else dt.date.fromisoformat(u)
            if day <= dt.date.today():
                raise ValueError("Pick a date after today.")
            t.meta["snooze_until"] = day.isoformat()
            return f"{t.id} is snoozed until {day.isoformat()}."
        return lane_write(request, ticket_id, token, change, "snooze")

    have = {(r.path, m) for r in app.routes for m in getattr(r, "methods", None) or ()}
    if ("/ticket/{ticket_id}/lane", "POST") not in have:
        router.add_api_route("/ticket/{ticket_id}/lane", post_lane, methods=["POST"])
    if ("/ticket/{ticket_id}/snooze", "POST") not in have:
        router.add_api_route("/ticket/{ticket_id}/snooze", post_snooze, methods=["POST"])

    # -- saved views ------------------------------------------------------------------
    @router.get("/views", response_class=HTMLResponse)
    def views_page(request: Request):
        s = store()
        return templates.TemplateResponse(request, "views.html", ctx(request, s, views=saved_views.load(s.home)))

    @router.post("/views")
    def post_view(request: Request, name: str = Form(""), url: str = Form(""), token: str = Form("")):
        """Save the current board or search filter under a name; it is pinned in the header of every page."""
        back = saved_views.clean_url(url) or "/views"
        if not tokens.use(token):
            return answer(request, False, "That form was already sent; reload the page.", back, code=409)
        s = store()
        try:
            saved_views.add(s.home, name, url)
        except ValueError as e:
            return answer(request, False, str(e), back, code=422)
        s.sync_after("desk: saved view")
        return answer(request, True, f"Saved the view {' '.join(name.split())}. It is in the header now.", back)

    @router.post("/views/remove")
    def post_view_remove(request: Request, name: str = Form(""), token: str = Form("")):
        if not tokens.use(token):
            return answer(request, False, "That form was already sent; reload the page.", "/views", code=409)
        s = store()
        if not saved_views.remove(s.home, name):
            return answer(request, False, f"There is no saved view called {name}.", "/views", code=404)
        s.sync_after("desk: removed view")
        return answer(request, True, f"Removed the view {name}. Nothing else changed.", "/views")

    # -- display settings -------------------------------------------------------------
    @router.post("/settings/display")
    def post_display(request: Request, theme: str = Form("system"), size: str = Form("normal")):
        """Theme and type size for this device only: kept in two cookies, read by the server on every page."""
        if theme not in THEMES or size not in SIZES:
            return answer(request, False, "Pick a theme and a type size from the list.", "/settings", code=400)
        msg = "Saved for this device."
        r = JSONResponse({"ok": True, "message": msg}) if wants_json(request) else RedirectResponse("/settings?saved=1", status_code=303)
        r.set_cookie("desk_theme", theme, max_age=COOKIE_AGE, path="/", samesite="lax")
        r.set_cookie("desk_size", size, max_age=COOKIE_AGE, path="/", samesite="lax")
        return r

    app.include_router(router)


def _save_view_url(request) -> str:
    """The board or search address this page shows, when it can be saved as a view; '' elsewhere."""
    url = request.url.path + ("?" + request.url.query if request.url.query else "")
    return saved_views.clean_url(url) if request.url.query else ""

