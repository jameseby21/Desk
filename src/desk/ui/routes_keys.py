"""Keyboard and palette (DESK-0290): palette actions on the open ticket, saved views, display settings (theme and type size).

One code path: the palette never writes anything itself. `GET /palette/actions/<ID>` lists what can be done to the open ticket as
(route, form fields, a fresh one-time token), and the palette posts those fields to that route, exactly the post the page's own
button makes: Approve and Send back go to /ticket/<ID>/decide, Assign to /ticket/<ID>/assign, and Snooze, End snooze, the lane moves
and Park to /ticket/<ID>/lane with the fields of DESK-0283's lane menu (lane, reason, snooze=1w or clear).

The lane route belongs to DESK-0283 (lanes), built in parallel. Until it is merged, `register` adds a stand-in that reads exactly the
same fields; it steps aside when DESK-0283's module is present or the route already exists, so the merged app has one lane route.
Every write runs inside `activity.batch(...)` and answers with the batch id and a plain sentence (the undo contract of DESK-0284).

Theme (system, light, dark) and type size (small, normal, large) are per device: two cookies the server reads to write
`data-theme` and `data-size` on <html>, so the page is painted in the right theme the first time (no flash)."""

from __future__ import annotations

import datetime as dt
import re
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
        add("unsnooze", "End snooze", base + "/lane", {"snooze": "clear"}, "Brings it back to Today and Needs me now.", keywords="wake")
    else:
        until = today + dt.timedelta(days=SNOOZE_DAYS)
        add("snooze", "Snooze 1 week", base + "/lane", {"snooze": "1w"}, f"Off Today and Needs me until {until.isoformat()}.",
            keywords="snooze 1w week hide")
    cur = lanes.lane_of(t)
    for lane in ("now", "planned", "later"):
        if lane != cur:
            add(f"lane-{lane}", f"Move to {LANE_WORDS[lane]}", base + "/lane", {"lane": lane}, LANE_HINT[lane], keywords="lane")
    if cur != "parked":
        add("park", "Park", base + "/lane", {"lane": "parked"}, "Decided not now. Nothing is deleted.", words="Why park it? One line.",
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

    def answer(request, ok: bool, message: str, back: str, batch: str = "", code: int = 200, stamp: str = "", extra=None):
        """JSON for the palette and in-place forms; a redirect with `undo` and `msg` (the DESK-0284 toast) for a plain form. `extra` is
        a function giving more JSON fields, called only for a JSON answer (so a plain form issues no tokens it cannot use)."""
        if wants_json(request):
            if not ok:
                return JSONResponse({"error": message, "token": tokens.issue()}, status_code=code)
            return JSONResponse({"ok": True, "message": message, "batch": batch, "stamp": stamp, **(extra() if extra else {})})
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

    def lane_write(request, ticket_id: str, token: str, change, done: str, back: str):
        if not tokens.use(token):
            return answer(request, False, "That form was already sent; reload the page.", back, code=409)
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
            return answer(request, False, str(e).strip("'\""), back, code=422)
        from . import events as ev

        return answer(request, True, msg, back, batch=bid, stamp=str(ev.ticket_stamp(s, t.id)))

    def post_lane(request: Request, ticket_id: str, token: str = Form(""), lane: str = Form(""), reason: str = Form(""),
                  snooze: str = Form(""), next: str = Form("")):
        """Stand-in for DESK-0283's lane route, reading its exact fields: lane (now, planned, later, parked, or auto for the usual
        lane), reason (parking needs one), snooze (1d, 1w, 2w, a date, or clear). One batch per post. Parking deletes nothing."""
        lane, snooze = lane.strip().lower(), snooze.strip().lower()

        def change(t):
            if not lane and not snooze:
                raise ValueError("Pick a lane or a snooze time.")
            bits = []
            if lane:
                if lane != "auto" and lane not in lanes.LANES:
                    raise ValueError("Pick Now, Planned, Later or Parked.")
                why = " ".join(reason.split())
                if lane == "parked" and not why:
                    raise ValueError("Say in a few words why it is parked.")
                if lane == "parked":
                    t.meta["park_reason"] = why[:200]
                else:
                    t.meta.pop("park_reason", None)
                if lane == "auto":
                    t.meta.pop("lane", None)
                else:
                    t.meta["lane"] = lane
                bits.append(f"{t.id} is parked. Nothing was deleted" if lane == "parked" else f"{t.id} is in the {LANE_WORDS[lanes.lane_of(t)]} lane")
            if snooze == "clear":
                t.meta.pop("snooze_until", None)
                bits.append(f"{t.id} is back on Today now")
            elif snooze:
                day = _snooze_day(snooze)
                t.meta["snooze_until"] = day.isoformat()
                bits.append(f"{t.id} is snoozed until {day.isoformat()}")
            return ". ".join(bits) + "."
        return lane_write(request, ticket_id, token, change, "lane", _local(next) or f"/ticket/{ticket_id}")

    have = {(r.path, m) for r in app.routes for m in getattr(r, "methods", None) or ()}
    if not lanes_package_present() and ("/ticket/{ticket_id}/lane", "POST") not in have:
        router.add_api_route("/ticket/{ticket_id}/lane", post_lane, methods=["POST"])

    # -- saved views ------------------------------------------------------------------
    @router.get("/views", response_class=HTMLResponse)
    def views_page(request: Request):
        s = store()
        return templates.TemplateResponse(request, "views.html", ctx(request, s, views=saved_views.load(s.home)))

    def undo_of(label: str, route: str, **fields):
        """What the in-place answer offers to reverse this change: the route and fields that put the views back, with a fresh token."""
        return lambda: {"undo": {"label": label, "url": route, "fields": {**fields, "token": tokens.issue()}}, "token": tokens.issue()}

    @router.post("/views")
    def post_view(request: Request, name: str = Form(""), url: str = Form(""), token: str = Form(""), at: str = Form("")):
        """Save the current board or search filter under a name; it is pinned in the header of every page. `at` puts a view back in
        its old place (the Undo of a removal)."""
        back = saved_views.clean_url(url) or "/views"
        if not tokens.use(token):
            return answer(request, False, "That form was already sent; reload the page.", back, code=409)
        s = store()
        nice = " ".join(name.split())
        old = next((v for v in saved_views.load(s.home) if v["name"].lower() == nice.lower()), None)
        try:
            saved_views.add(s.home, name, url, at=int(at) if at.strip().isdigit() else None)
        except ValueError as e:
            return answer(request, False, str(e), back, code=422)
        s.sync_after("desk: saved view")
        if old is None:
            msg = f"Put back the view {nice}." if at.strip() else f"Saved the view {nice}. It is in the header now."
            return answer(request, True, msg, back, extra=undo_of("Undo", "/views/remove", name=nice))
        return answer(request, True, f"Changed the view {nice} to this filter.", back, extra=undo_of("Undo", "/views", name=old["name"], url=old["url"]))

    @router.post("/views/remove")
    def post_view_remove(request: Request, name: str = Form(""), token: str = Form("")):
        if not tokens.use(token):
            return answer(request, False, "That form was already sent; reload the page.", "/views", code=409)
        s = store()
        views = saved_views.load(s.home)
        at = next((i for i, v in enumerate(views) if v["name"].lower() == " ".join(name.split()).lower()), None)
        if at is None or not saved_views.remove(s.home, name):
            return answer(request, False, f"There is no saved view called {name}.", "/views", code=404)
        s.sync_after("desk: removed view")
        gone = views[at]
        return answer(request, True, f"Removed the view {gone['name']}. Nothing else changed.", "/views",
                      extra=undo_of("Put it back", "/views", name=gone["name"], url=gone["url"], at=str(at)))

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


def lanes_package_present() -> bool:
    """True once DESK-0283's lane routes are part of this install: the palette then posts to its route and adds none of its own."""
    import importlib.util

    return importlib.util.find_spec("desk.ui.routes_lanes") is not None


def _snooze_day(text: str) -> dt.date:
    """A snooze date from the lane menu's choices (1d, 1w, 2w and the like) or a YYYY-MM-DD date; it must be after today."""
    m = re.fullmatch(r"(\d{1,3})([dw])", text)
    today = dt.date.today()
    try:
        day = today + dt.timedelta(days=int(m.group(1)) * (7 if m.group(2) == "w" else 1)) if m else dt.date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"'{text}' is not a date Desk knows. Use a date like 2026-11-22, or 1d, 1w, 2w.") from None
    if day <= today:
        raise ValueError("Pick a date after today.")
    return day


def _local(url: str) -> str:
    """A path on this Desk, never another site; '' when it is not one."""
    url = (url or "").split("#")[0]
    return url if re.fullmatch(r"/(?!/)[^\s\\]*", url) else ""


def _save_view_url(request) -> str:
    """The board or search address this page shows, when it can be saved as a view; '' elsewhere."""
    url = saved_views.clean_url(request.url.path + ("?" + request.url.query if request.url.query else ""))
    return url if "?" in url else ""  # a filter to save; a bare /board (or one that only carried a message) has none

