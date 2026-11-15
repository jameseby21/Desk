"""Lanes in the web UI (DESK-0283): the lane chip, the lane bar on Today and the board, and the routes that change a lane or a snooze.

The view choices (a lane filter, 'only my lane', 'show all') are kept per device in two cookies, so the phone and the desk can differ.
`narrow()` is the one hook the Today and board routes call: it leaves out snoozed tickets, tickets outside the chosen lane, and (on
Today only) subtickets and agent-only tickets that carry no ask for the owner and are not his to hold. Rough week on Today means lane
Now only. On Today nothing narrows Immediate Action Required (a hard deadline in the window or missed) or an overdue ask.
A lane or snooze change is a store write: it runs inside one activity batch and answers with the undo contract (DESK-0284)."""

from __future__ import annotations

import datetime as dt
import html
import re
import secrets
from dataclasses import dataclass, field
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from markupsafe import Markup

from .. import activity, deadlines, lanes, needs_me as nmod, people as peoplemod
from ..rules import RuleError

LANE_COOKIE = "desk_lane"
SHOW_COOKIE = "desk_show"
YEAR = 31536000
_LOCAL = re.compile(r"^/(?!/)[^\s\\]*$")  # a path on this Desk, never another site
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})  # Desk serves on 127.0.0.1 only (standards rule 80, item 10)
NOT_LOCAL = "Desk changes lanes from this computer only. Open Desk at http://127.0.0.1 and try again."
SNOOZE_CHOICES = (("1d", "1 day"), ("1w", "1 week"), ("2w", "2 weeks"), ("next-monday", "Next Monday"))


@dataclass
class LaneView:
    """What the lane bar says on this page: the lane shown, whether a rough week forced it, and what was left out."""

    page: str
    lane: str = ""
    forced: bool = False
    show_all: bool = False
    snoozed: list = field(default_factory=list)
    hidden: int = 0  # subtickets and agent-only tickets left off Today
    other_lanes: int = 0
    here: str = "/"


def prefs(request: Request) -> tuple[str, bool]:
    lane = str(request.cookies.get(LANE_COOKIE) or "").lower()
    return (lane if lane in lanes.LANES else ""), request.cookies.get(SHOW_COOKIE) == "all"


def _holds(t, owner: str) -> bool:
    """The owner is the ticket's owner or its assignee: it is his own work in hand, wherever it sits in the family."""
    return any(" ".join(str(t.meta.get(k) or "").split()).lower() == owner for k in ("owner", "assignee"))


def narrow(request: Request, s, tickets: list, page: str) -> list:
    """The tickets this page draws after the lane filter, snooze and (Today) the hide-by-default rule; the counts of what was left out
    go to the lane bar through the request."""
    lane, show_all = prefs(request)
    forced = page == "today" and request.query_params.get("cap") == "rough"
    if forced:
        lane = "now"
    today = dt.date.today()
    now = dt.datetime.now()
    pe = peoplemod.load(s.home)
    owner = s.owner.lower()
    view = LaneView(page, lane, forced, show_all, here=_here(request))
    on_today = page == "today"
    pinned = {t.id for t in deadlines.immediate(tickets, today)} if on_today else set()  # Immediate Action Required is never narrowed
    kept = []
    for t in tickets:
        if lanes.is_snoozed(t, today):
            view.snoozed.append(t)
            continue
        if t.id in pinned:
            kept.append(t)
            continue
        hide = on_today and not show_all and (lanes.is_sub(t) or lanes.is_agent_only(t, pe)) and not _holds(t, owner)
        off_lane = bool(lane) and lanes.lane_of(t) != lane
        ask = nmod.ask_of(s, t, owner, now) if on_today and (hide or off_lane) else None  # only asked when it decides something
        if hide and ask is None:
            view.hidden += 1
            continue
        if off_lane and not (ask is not None and ask.overdue):  # an overdue ask is Now whatever its lane
            view.other_lanes += 1
            continue
        kept.append(t)
    view.snoozed.sort(key=lambda t: (lanes.snooze_until(t), t.id))
    request.scope.setdefault("state", {})["lanes"] = view
    return kept


def _host_of(value: str) -> str | None:
    try:
        return urlsplit(value if "//" in value else "//" + value).hostname
    except ValueError:
        return None


def local_only(request: Request) -> None:
    """Refuse a request whose Host or Origin is not this computer. A DNS-rebinding page reaches 127.0.0.1 under its own name, so its
    Host gives it away; a page on another site sends its own Origin. Either way no key is handed out and nothing is written."""
    host = _host_of(request.headers.get("host", ""))
    origin = request.headers.get("origin")
    if host not in LOCAL_HOSTS or (origin is not None and _host_of(origin) not in LOCAL_HOSTS):
        raise HTTPException(status_code=403, detail=NOT_LOCAL)


def _here(request: Request) -> str:
    """This page's address without the one-off undo and message params, so a view switch does not show an old message again."""
    q = "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in request.query_params.multi_items() if k not in ("undo", "msg", "error"))
    return request.url.path + ("?" + q if q else "")


def _safe_next(v: str, default: str) -> str:
    v = (v or "").split("#")[0]
    return v if _LOCAL.match(v) else default


def _with(url: str, **params) -> str:
    q = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items() if v)
    return url + (("&" if "?" in url else "?") + q if q else "")


def _day(d: dt.date) -> str:
    return f"{d.strftime('%b')} {d.day}"


# -- what the templates draw -------------------------------------------------------------

def chip(t, menu: bool = True) -> Markup:
    """The lane chip: the lane name, dashed when it is derived. With `menu`, a click or Enter opens the lane menu (lanes.js)."""
    lane = lanes.lane_of(t)
    set_ = bool(lanes.explicit_lane(t))
    label = lanes.LABELS[lane]
    tip = f"Lane: {label}" + ("" if set_ else " (worked out, not set)") + (". Click to change it" if menu else "")
    if lane == "parked" and t.meta.get("park_reason"):
        tip += f". Parked because: {t.meta['park_reason']}"
    attrs = f' role="button" tabindex="0" data-lane-id="{html.escape(t.id)}" aria-haspopup="menu"' if menu else ""
    cls = f"lane-chip lane-{lane}" + ("" if set_ else " derived")
    return Markup(f'<span class="{cls}" title="{html.escape(tip)}" aria-label="{html.escape(tip)}"{attrs}>{label}</span>')


def marks(t, cap: str = "") -> Markup:
    """What a row or tile carries: the lane chip, 'Back from snooze' for three days after a snooze ends, and on a rough week the
    smallest step."""
    out = str(chip(t))
    if lanes.back_from_snooze(t):
        out += f'<span class="lane-back" title="Snoozed until {html.escape(str(lanes.snooze_until(t)))}">Back from snooze</span>'
    step = str(t.meta.get("smallest_step") or "").strip()
    if cap == "rough" and step:
        out += f'<span class="tr-step">Smallest step: {html.escape(step)}</span>'
    return Markup(out)


def info(t) -> dict:
    d = lanes.snooze_until(t)
    return {"lane": lanes.lane_of(t), "label": lanes.LABELS[lanes.lane_of(t)], "set": bool(lanes.explicit_lane(t)),
            "snoozed": lanes.is_snoozed(t), "until": _day(d) if d else "", "reason": str(t.meta.get("park_reason") or ""),
            "back": lanes.back_from_snooze(t)}


def install(app, templates, store, tokens) -> None:
    """Register the routes and the template helpers (one line in create_app)."""
    templates.env.globals.update(lane_chip=chip, lane_marks=marks, lane_info=info, lane_names=lanes.LABELS, snooze_choices=SNOOZE_CHOICES,
                                 lane_view=lambda request: request.scope.get("state", {}).get("lanes"))
    app.include_router(router(store, tokens))


def router(store, tokens) -> APIRouter:
    r = APIRouter(dependencies=[Depends(local_only)])  # every lane route: the view switch, the menu's key and the change

    def js(request: Request) -> bool:
        return "application/json" in request.headers.get("accept", "")

    @r.post("/lanes/view")
    def set_view(lane: str = Form(""), show: str = Form(""), next: str = Form("/")):
        """The per-device view: a lane filter ('' for all lanes) and 'show all'. Nothing in the store changes."""
        lane = lane.strip().lower()
        resp = RedirectResponse(_safe_next(next, "/"), status_code=303)
        resp.set_cookie(LANE_COOKIE, lane if lane in lanes.LANES else "", max_age=YEAR, path="/", samesite="lax")
        resp.set_cookie(SHOW_COOKIE, "all" if show == "all" else "", max_age=YEAR, path="/", samesite="lax")
        return resp

    @r.get("/ticket/{ticket_id}/lane")
    def lane_get(ticket_id: str):
        """The lane menu's data, with a fresh one-time key for the change."""
        try:
            t = store().get(ticket_id)
        except KeyError:
            return JSONResponse({"error": f"No ticket {ticket_id}."}, status_code=404)
        return JSONResponse({"id": t.id, **info(t), "lanes": [[k, v] for k, v in lanes.LABELS.items()],
                             "snooze": [list(x) for x in SNOOZE_CHOICES], "token": tokens.issue()}, headers={"Cache-Control": "no-store"})

    @r.post("/ticket/{ticket_id}/lane")
    def lane_post(request: Request, ticket_id: str, token: str = Form(""), lane: str = Form(""), reason: str = Form(""),
                  snooze: str = Form(""), next: str = Form("")):
        """Change a ticket's lane (auto = back to the derived lane) and/or its snooze (a date, 1d, 1w, 2w, next-monday, or clear).
        One batch, so Undo puts back everything this click changed."""
        back = _safe_next(next, f"/ticket/{ticket_id}")
        if not tokens.use(token):
            msg = "That button was already used, so nothing more was saved. Reload the page and try again."
            return JSONResponse({"error": msg, "token": tokens.issue()}, status_code=409) if js(request) else RedirectResponse(_with(back, error=msg), status_code=303)
        s = store()
        lane, snooze = lane.strip().lower(), snooze.strip().lower()
        try:
            t = s.get(ticket_id)
            if not lane and not snooze:
                raise ValueError("Pick a lane or a snooze time.")
            until = None if snooze in ("", "clear") else lanes.parse_when(snooze)
            bid = "b-" + secrets.token_hex(6)
            s.sync_before()
            with activity.batch(bid):
                if lane:
                    t = lanes.set_lane(s, t, "" if lane == "auto" else lane, reason)
                if snooze:
                    t = lanes.set_snooze(s, s.get(t.id), until)
            s.sync_after(f"desk: lane {t.id}")
        except KeyError:
            msg = f"No ticket {ticket_id}."
            return JSONResponse({"error": msg, "token": tokens.issue()}, status_code=404) if js(request) else RedirectResponse(_with("/", error=msg), status_code=303)
        except (RuleError, ValueError) as e:
            return JSONResponse({"error": str(e), "token": tokens.issue()}, status_code=422) if js(request) else RedirectResponse(_with(back, error=str(e)), status_code=303)
        t = s.get(ticket_id)
        msg = _sentence(t, lane, snooze, until)
        if js(request):
            return JSONResponse({"ok": True, "message": msg, "batch": bid, "id": t.id, **info(t)})
        return RedirectResponse(_with(back, undo=bid, msg=msg), status_code=303)

    return r


def _sentence(t, lane: str, snooze: str, until: dt.date | None) -> str:
    bits = []
    if lane == "parked":
        bits.append(f"{t.id} is parked. Nothing was deleted")
    elif lane == "auto":
        bits.append(f"{t.id} is back in its usual lane ({lanes.LABELS[lanes.lane_of(t)]})")
    elif lane:
        bits.append(f"{t.id} is in the {lanes.LABELS[lane]} lane")
    if snooze:
        bits.append(f"{t.id} is back on Today now" if until is None else f"{t.id} is snoozed until {_day(until)}")
    return ". ".join(bits) + "."
