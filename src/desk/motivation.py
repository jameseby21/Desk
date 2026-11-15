"""Motivation (DESK-0293): the decided-today count, the daily goal and its streak, the focus timer switch and the empty-state reward.

Decided today, precisely: the tickets whose LAST status change made by the store owner on that day moved them into one of DECIDED
(approved, changes_requested, rejected, done). It is read from the activity log (events/<ID>.jsonl), never stored, so it cannot drift:
a ticket approved and then put back (an undo writes a later status event) does not count, a ticket approved and then closed counts
once, and a change made by anyone else (an agent, a rule, a coworker) never counts. Only a confirmed owner counts (decision 110): a
change only claimed under the owner's name (`--as`, DESK_ACTOR, a device cookie, the owner assumed in a browser nobody is named in)
has `confirmed` false on its event and is not the owner's decision.

The streak is the run of days that met the goal, counted back from yesterday, plus today once today meets it. A Saturday or Sunday
below the goal neither breaks the run nor adds to it (a free weekend is not a failure); a weekday below the goal ends it.

Settings live per person in `.desk/motivation.toml` (beside `.desk/settings.toml`, which style.save rewrites whole, so they are kept in
their own file). Docs: docs/COMMANDS.md (In the UI: Motivation)."""

from __future__ import annotations

import datetime as dt
import json
import os
import tomllib
from pathlib import Path

from . import activity, lanes

DECIDED = ("approved", "changes_requested", "rejected", "done")
AT_WORK = ("claimed", "in_progress")
DEFAULTS = {"focus": True, "focus_minutes": 25, "goal": 5, "count": True, "celebrate": True}
MINUTES = (5, 120)  # the focus timer's allowed length
GOAL_MAX = 50  # a goal of 0 turns the goal and the streak off; the count stays
LOOKBACK = 366  # days the streak reads back at most
FILE = "motivation.toml"

_cache: dict[tuple[str, str], tuple[int, int, list]] = {}  # (events file, owner) -> (mtime_ns, size, [(day, ts, ticket, new status, by owner)])


# -- settings -------------------------------------------------------------------------------

def settings_path(home: Path) -> Path:
    return Path(home) / ".desk" / FILE


def load(home: Path) -> dict:
    """The Motivation switches, with the defaults for anything missing or out of range."""
    out = dict(DEFAULTS)
    try:
        raw = tomllib.loads(settings_path(home).read_text(encoding="utf-8")).get("motivation", {})
    except (OSError, tomllib.TOMLDecodeError):
        return out
    for k in ("focus", "count", "celebrate"):
        if isinstance(raw.get(k), bool):
            out[k] = raw[k]
    m = raw.get("focus_minutes")
    if isinstance(m, int) and not isinstance(m, bool) and MINUTES[0] <= m <= MINUTES[1]:
        out["focus_minutes"] = m
    g = raw.get("goal")
    if isinstance(g, int) and not isinstance(g, bool) and 0 <= g <= GOAL_MAX:
        out["goal"] = g
    return out


def save(home: Path, *, focus: bool, focus_minutes: int, goal: int, count: bool, celebrate: bool) -> dict:
    """Write the switches; a length or goal out of range is refused in plain words and nothing is written."""
    if not MINUTES[0] <= int(focus_minutes) <= MINUTES[1]:
        raise ValueError(f"A focus timer runs {MINUTES[0]} to {MINUTES[1]} minutes. Nothing was saved.")
    if not 0 <= int(goal) <= GOAL_MAX:
        raise ValueError(f"The daily goal is 0 (off) to {GOAL_MAX}. Nothing was saved.")
    p = settings_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    b = lambda v: "true" if v else "false"  # noqa: E731
    text = (f"[motivation]\nfocus = {b(focus)}\nfocus_minutes = {int(focus_minutes)}\ngoal = {int(goal)}\n"
            f"count = {b(count)}\ncelebrate = {b(celebrate)}\n")
    p.write_bytes(text.encode("utf-8"))
    return load(home)


# -- decided per day, from the event log ----------------------------------------------------

def _status_rows(p: Path, owner: str, st: os.stat_result) -> list:
    key = (str(p), owner)
    hit = _cache.get(key)
    if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    rows = []
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        text = ""
    for ln in text.splitlines():
        if '"status"' not in ln:  # cheap skip: only status changes matter here
            continue
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if d.get("change") != "field" or d.get("field") != "status":
            continue
        ts = str(d.get("ts") or "")
        try:
            day = dt.date.fromisoformat(ts[:10])
        except ValueError:
            continue
        mine = d.get("confirmed") is True and (d.get("kind") == "owner" or str(d.get("actor") or "").lower() == owner)
        rows.append((day, ts, str(d.get("ticket") or p.stem).upper(), str(d.get("new") or ""), mine))
    _cache[key] = (st.st_mtime_ns, st.st_size, rows)
    return rows


def daily_counts(home: Path, owner: str, since: dt.date) -> dict[dt.date, int]:
    """{day: tickets decided by the owner that day} for every day from `since` on. A log file not written since `since` is skipped
    without being opened, and a file read before is reused until it changes, so a page can ask on every load."""
    owner = (owner or "").lower()
    folder = Path(home) / activity.DIR
    floor = dt.datetime.combine(since, dt.time()).timestamp()
    last: dict[tuple[dt.date, str], tuple[str, str]] = {}  # (day, ticket) -> (ts, status) of the owner's last status change that day
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return {}
    for e in entries:
        if not e.name.endswith(".jsonl"):
            continue
        try:
            st = e.stat()
        except OSError:
            continue
        if st.st_mtime < floor:
            continue
        for day, ts, tid, new, mine in _status_rows(Path(e.path), owner, st):
            if not mine or day < since:
                continue
            k = (day, tid)
            if k not in last or ts >= last[k][0]:
                last[k] = (ts, new)
    out: dict[dt.date, int] = {}
    for (day, _tid), (_ts, new) in last.items():
        if new in DECIDED:
            out[day] = out.get(day, 0) + 1
    return out


def streak(counts: dict[dt.date, int], today: dt.date, goal: int) -> int:
    """Days in a row that met the goal (see the module note: weekends below the goal are skipped, not breaks)."""
    if goal <= 0:
        return 0
    n = 1 if counts.get(today, 0) >= goal else 0
    day = today - dt.timedelta(days=1)
    for _ in range(LOOKBACK):
        if counts.get(day, 0) >= goal:
            n += 1
        elif day.weekday() < 5:
            break
        day -= dt.timedelta(days=1)
    return n


def state(home: Path, owner: str, today: dt.date | None = None, cfg: dict | None = None) -> dict:
    """What the header shows: today's count, the goal, whether it is met, the streak, and one plain sentence."""
    today = today or dt.date.today()
    cfg = cfg or load(home)
    goal = cfg["goal"]
    counts = daily_counts(home, owner, today - dt.timedelta(days=LOOKBACK if goal else 0))
    n = counts.get(today, 0)
    s = streak(counts, today, goal)
    if goal:
        text = f"{n} of {goal} decided today" + (" · goal met" if n >= goal else "")
        if s:
            text += f" · {s}-day streak"
    else:
        text = f"{n} decided today"
    return {"decided": n, "goal": goal, "met": bool(goal) and n >= goal, "streak": s, "text": text, "day": today.isoformat()}


# -- focus and the empty state --------------------------------------------------------------

def focus_ok(t) -> bool:
    """A Start focus button is offered on an open ticket in the Now lane."""
    return bool(t is not None and t.is_open and lanes.lane_of(t) == "now")


def _since_words(days: int) -> str:
    return "since today" if days <= 0 else ("for 1 day" if days == 1 else f"for {days} days")


def agents_at_work(store, today: dt.date | None = None, limit: int = 8, tickets=None) -> dict:
    """What agents are doing while nothing needs the owner: claimed and in-progress tickets held by someone else, each with who holds
    it and how long it has been in that status (from the event log, else the ticket's `updated` date), most recent first. `tickets`
    is the page's own list when it is filtered (Today's Company, Track, Sprint, Tag and Type), so the list follows the filters."""
    today = today or dt.date.today()
    owner = (store.owner or "").lower()
    rows = []
    for t in (store.tickets() if tickets is None else tickets):
        if t.status not in AT_WORK:
            continue
        who = str(t.meta.get("assignee") or t.meta.get("owner") or "").strip()
        if not who or who.lower() == owner:
            continue
        since = None
        for e in reversed(activity.read(store.home, t.id)):
            if e.change == "field" and e.field == "status" and e.new == t.status:
                try:
                    since = dt.date.fromisoformat(e.ts[:10])
                except ValueError:
                    since = None
                break
        if since is None:
            try:
                since = dt.date.fromisoformat(str(t.meta.get("updated") or "")[:10])
            except ValueError:
                since = today
        days = max(0, (today - since).days)
        rows.append({"id": t.id, "title": str(t.meta.get("title") or ""), "who": who, "status": t.status, "days": days,
                     "age": _since_words(days)})
    rows.sort(key=lambda r: (r["days"], r["id"]))
    return {"rows": rows[:limit], "more": max(0, len(rows) - limit), "total": len(rows)}
