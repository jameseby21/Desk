"""The daily loop's settings on /settings (DESK-0282): one page holds every switch the loop's packages added.

Theme and type size (DESK-0290), Motivation (DESK-0293) and Delegations (DESK-0285) bring their own sections. This module adds the two
that had no place on the page:

- Lanes on this device (DESK-0283): Only my lane, the lane filter and Show all. They post to DESK-0283's own `POST /lanes/view`, so
  there is one write path; the choices are cookies on this device and nothing in the store changes.
- Phone and email (DESK-0289): quiet hours, the daily budget, the brief time and which channels are on. Read from desk.toml `[notify]`
  and the queue state; shown, never written here (the file is the one owner; `drew notify status` prints the same).

No secret value is read into the page: a channel shows only whether it is on and ready. Docs: docs/DAILY-LOOP.md, docs/COMMANDS.md."""

from __future__ import annotations

import datetime as dt

from .. import notify_channels as nc, notify_queue, notify_send as ns
from . import routes_lanes


def notify_view(home) -> dict:
    """What the Notifications section says: the settings in force and the queue's counts, with no key or address in it."""
    st = ns.settings(home)
    try:
        sec = nc.read_secrets()
    except OSError:
        sec = {}
    listed = [str(c).strip().lower() for c in (st.get("channels") or []) if str(c).strip()]
    ready = {c.name for c in ns.active_channels(st, sec)}
    state = ns.load_state(home)
    quiet = bool(st.get("quiet_start") and st.get("quiet_end"))
    return {"quiet": quiet, "quiet_start": st.get("quiet_start") or "", "quiet_end": st.get("quiet_end") or "",
            "urgent_bypasses_quiet": bool(st.get("urgent_bypasses_quiet")), "budget": int(st.get("daily_budget") or 0),
            "used_today": ns.used_today(state, dt.date.today()), "brief_time": st.get("brief_time") or "",
            "channels": [{"name": n, "ready": n in ready} for n in listed],
            "queued": len(notify_queue.pending(home)), "held": len(state.get("held") or [])}


def lane_prefs(request) -> dict:
    """This device's lane choices, as the lane bar on Today reads them (cookies; DESK-0283)."""
    lane, show_all = routes_lanes.prefs(request)
    return {"lane": lane, "show_all": show_all}


def install(templates, store) -> None:
    """Template helpers for settings.html (one line in create_app). No route: the forms post to the packages' own routes."""
    templates.env.globals.update(notify_view=lambda: notify_view(store().home), lane_prefs=lane_prefs)
