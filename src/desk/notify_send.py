"""What leaves this computer, and when (DESK-0289): the privacy filter, the daily budget, quiet hours, and the senders behind
`drew notify test | brief | drain`.

The privacy filter is an allow-list. A notification carries ticket ids, titles, counts, dates and the brief's plain text, and
nothing else: never a ticket's Context, Report, comments or files. Every title (and every queued line) passes the private-term scan
from the owner's private folder and the secret-shape scan; a hit is replaced by `(title withheld)` and never sent. When the
private-term scan cannot be loaded, titles are left out and only ids go, and the message says so.

Budget and quiet hours: past `daily_budget` messages a day, or inside quiet hours, a queued item is held and folded into the next
brief. An urgent item goes out during quiet hours only when `urgent_bypasses_quiet = true`. The brief and `drew notify test` are
asked for by a person and do not count against the budget. Docs: docs/NOTIFICATIONS.md."""

from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import brief as briefmod, notify_channels as nc, notify_queue, secretscan
from .config import load_config

DEFAULTS = {
    "channels": ["ntfy", "smtp"],  # the paid adapters (postmark, twilio, pushover) are off until named here
    "daily_budget": 10,            # queued messages a day; the rest wait for the next brief (DESK-0201 uses the same default)
    "quiet_start": "21:00",        # no queued messages from here ...
    "quiet_end": "07:00",          # ... to here; they fold into the next brief ("" for either turns quiet hours off)
    "urgent_bypasses_quiet": False,
    "brief_time": "07:30",         # what `drew notify schedule --print` uses for the morning brief
    "drain_minutes": 15,
    "ntfy_server": nc.DEFAULT_SERVER,
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
}
STATE = Path(".desk") / "notify-state.json"
WITHHELD = "(title withheld)"
# the store's own id shape (store._ID: a prefix that may hold hyphens, such as JEBY-US, then 4+ digits), bounded so that inside free
# text it never starts or ends in the middle of a longer id
ID_RX = re.compile(r"(?<![A-Za-z0-9-])[A-Z0-9][A-Z0-9-]*-\d{4,}(?![A-Za-z0-9])")
ALLOWED_ITEM_KEYS = {"id", "title", "ask", "date", "status", "due", "count", "kind"}
MAX_LINE = 160
KNOWN_KINDS = frozenset({"alert", "brief", "digest-line", "note"})  # the queue contract's kinds; any other kind goes as "note"


def settings(home: Path) -> dict:
    st = dict(DEFAULTS)
    st.update(load_config(Path(home)).get("notify") or {})
    return st


# -- the private-term scan -------------------------------------------------------------------

def _scanner_path() -> Path:
    env = os.environ.get("DESK_PRIVATE_SCAN", "").strip()
    return Path(env).expanduser() if env else Path.home() / ".claude" / "jeby-private" / "private_scan.py"


def _denylist_path() -> Path:
    env = os.environ.get("JEBY_DENYLIST", "").strip()
    return Path(env).expanduser() if env else Path.home() / ".claude" / "jeby-private" / "denylist.txt"


@dataclass
class Scanner:
    """The owner's private-term matcher. `ready` is False when it could not be loaded; then titles are never sent."""
    patterns: list
    ready: bool
    why: str = ""

    def hit(self, text: str) -> bool:
        return any(rx.search(text or "") for _, rx in self.patterns)


def load_scanner() -> Scanner:
    path = _scanner_path()
    try:
        spec = importlib.util.spec_from_file_location("desk_private_scan", path)
        if spec is None or spec.loader is None or not path.exists():
            return Scanner([], False, "the private-term scan was not found")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        pats = mod.load(_denylist_path())
    except Exception:  # noqa: BLE001 - a broken scanner means ids only, never a crash and never titles
        return Scanner([], False, "the private-term scan could not be loaded")
    if not pats:
        return Scanner([], False, "the private-term list is empty or missing")
    return Scanner(pats, True)


# -- the filter -------------------------------------------------------------------------------

def clean_text(text: str, scan: Scanner) -> str | None:
    """One line that may be sent, or None: one line, at most 160 characters, no private term, nothing shaped like a secret."""
    line = " ".join(str(text or "").split())
    if not line or not scan.ready or scan.hit(line) or secretscan.looks_secret(line):
        return None
    return line[:MAX_LINE]


def clean_title(title: str, scan: Scanner) -> str:
    return clean_text(title, scan) or WITHHELD


def clean_brief(brief: dict, scan: Scanner) -> dict:
    """The brief with only allowed fields, every string scanned; titles become `(title withheld)` on a hit or with no scanner."""
    out = {}
    for k, v in brief.items():
        if isinstance(v, list):
            rows = []
            for row in v:
                if not isinstance(row, dict):
                    continue
                r = {}
                for kk, vv in row.items():
                    if kk not in ALLOWED_ITEM_KEYS:
                        continue
                    if kk == "id":
                        r[kk] = str(vv) if ID_RX.fullmatch(str(vv)) else "?"
                    elif isinstance(vv, (int, float)) and not isinstance(vv, bool):
                        r[kk] = vv
                    elif kk == "title":
                        r[kk] = clean_title(str(vv), scan)
                    else:
                        r[kk] = clean_text(str(vv), scan) or ""
                rows.append(r)
            out[k] = rows
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = v
        elif isinstance(v, dict) and all(isinstance(x, int) and not isinstance(x, bool) for x in v.values()):
            out[k] = {str(kk): x for kk, x in v.items() if re.fullmatch(r"[a-z_]{1,30}", str(kk))}  # counts by plain key
        elif isinstance(v, (str, dt.date)) and k in ("date",):
            out[k] = str(v)
    return out


def final_pass(text: str, scan: Scanner) -> str:
    """Last look at the whole message: a line with a private term or a secret shape becomes its ids alone."""
    lines = []
    for ln in str(text).splitlines():
        if (scan.ready and scan.hit(ln)) or secretscan.looks_secret(ln):
            ids = ID_RX.findall(ln)
            ln = ("- " + ", ".join(ids) + " " + WITHHELD) if ids else WITHHELD
        lines.append(ln)
    return "\n".join(lines)


def item_line(item: dict, scan: Scanner, titles: dict[str, str]) -> str:
    """A queued item as the one line that may be sent: its own text when it passes, else its ids with their (scanned) titles."""
    ids = [i for i in (item.get("ids") or []) if ID_RX.fullmatch(str(i))]
    text = clean_text(item.get("text", ""), scan)
    if text:
        return text
    if ids:
        return "; ".join(f"{i}: {clean_title(titles.get(i, ''), scan)}" if titles.get(i) and scan.ready else i for i in ids)
    kind = str(item.get("kind") or "")
    return f"1 {kind if kind in KNOWN_KINDS else 'note'} (text withheld)"  # a writer's kind is never echoed, even as a plain word


# -- state: budget used today and the items held for the next brief ---------------------------

def load_state(home: Path) -> dict:
    p = Path(home) / STATE
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(home: Path, state: dict) -> None:
    p = Path(home) / STATE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes((json.dumps(state, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def used_today(state: dict, today: dt.date) -> int:
    return int((state.get("sent") or {}).get(today.isoformat(), 0))


def _hm(s: str) -> dt.time | None:
    try:
        h, m = str(s).split(":")
        return dt.time(int(h), int(m))
    except (ValueError, TypeError):
        return None


def in_quiet_hours(now: dt.datetime, st: dict) -> bool:
    a, b = _hm(st.get("quiet_start", "")), _hm(st.get("quiet_end", ""))
    if a is None or b is None or a == b:
        return False
    t = now.time()
    return a <= t < b if a < b else (t >= a or t < b)


# -- sending ----------------------------------------------------------------------------------

def active_channels(st: dict, sec: dict, only: str | None = None) -> list[nc.Channel]:
    on = {str(c).strip().lower() for c in (st.get("channels") or [])}
    chans = [c for c in nc.all_channels(st) if c.name in on and c.ready(sec)]
    return [c for c in chans if c.name == only] if only else chans


def _scrub(text: str, sec: dict) -> str:
    """A failure detail never carries a secret value (rule 80)."""
    out = str(text)
    for v in sec.values():
        if v and len(v) >= 4:
            out = out.replace(v, nc.mask(v))
    return out


def deliver(note: nc.Note, chans: list[nc.Channel], sec: dict, post=nc.http_post, smtp=nc.smtp_connect) -> list[nc.Result]:
    out = []
    for c in chans:
        try:
            out.append(c.send(note, sec, post=post, smtp=smtp))
        except Exception as e:  # noqa: BLE001 - one channel failing never stops the others
            out.append(nc.Result(c.name, False, _scrub(f"failed: {type(e).__name__}: {e}", sec)))
    return out


class _BriefStore:
    """The store as the brief contract reads it: the base brief.py calls `store.all()`, which Store names `tickets()`."""

    def __init__(self, store):
        self._s = store

    def __getattr__(self, name):
        return getattr(self._s, name)

    def all(self):
        fn = getattr(self._s, "all", None)
        return fn() if callable(fn) else self._s.tickets()


def _titles(store, ids) -> dict[str, str]:
    by = store.by_id()
    return {i: str(by[i].meta.get("title") or "") for i in ids if i in by}


def brief_note(store, today: dt.date | None = None, scan: Scanner | None = None, state: dict | None = None) -> nc.Note:
    """The morning brief as it would be sent: the cleaned brief text, then the held items folded in."""
    today = today or dt.date.today()
    scan = scan or load_scanner()
    state = load_state(store.home) if state is None else state
    cleaned = clean_brief(briefmod.build_brief(_BriefStore(store), today), scan)
    try:
        body = briefmod.brief_text(cleaned)
    except (KeyError, TypeError, ValueError):  # the brief's owner (DESK-0288) reads a field the filter dropped: counts and ids still go
        body = f"Desk brief {today.isoformat()}: " + ", ".join(f"{len(v)} {k}" for k, v in cleaned.items() if isinstance(v, list))
    held = state.get("held") or []
    if held:
        all_ids = sorted({i for it in held for i in it.get("ids") or []})
        titles = _titles(store, all_ids)
        body += f"\nHeld since the last brief: {len(held)}"
        body += "".join("\n- " + item_line(it, scan, titles) for it in held[:10])
        if len(held) > 10:
            body += f"\n- and {len(held) - 10} more"
    if not scan.ready:
        body += f"\nTitles left out: {scan.why}."
    return nc.Note(f"Desk brief {today.isoformat()}", final_pass(body, scan))


def send_brief(store, dry_run=False, now: dt.datetime | None = None, post=nc.http_post, smtp=nc.smtp_connect) -> tuple[nc.Note, list[nc.Result], list[str]]:
    now = now or dt.datetime.now()
    st, sec, state = settings(store.home), nc.read_secrets(), load_state(store.home)
    note = brief_note(store, now.date(), state=state)
    chans = active_channels(st, sec)
    names = [c.name for c in chans]
    if dry_run or not chans:
        return note, [], names
    results = deliver(note, chans, sec, post, smtp)
    if any(r.ok for r in results):
        state["held"] = []  # folded into this brief
        state["last_brief"] = now.replace(microsecond=0).isoformat()
        save_state(store.home, state)
    return note, results, names


def send_test(home: Path, only: str | None = None, post=nc.http_post, smtp=nc.smtp_connect) -> tuple[nc.Note, list[nc.Result], list[str]]:
    st, sec = settings(home), nc.read_secrets()
    chans = active_channels(st, sec, only)
    note = nc.Note("Desk test", f"Desk test: notifications work ({dt.datetime.now():%Y-%m-%d %H:%M}).")
    return note, deliver(note, chans, sec, post, smtp) if chans else [], [c.name for c in chans]


@dataclass
class DrainReport:
    sent: int = 0
    held: int = 0
    failed: int = 0
    skipped: int = 0  # queue lines that could not be read; stepped over so they never block the rest
    reason: str = ""
    lines: list | None = None


def drain(store, now: dt.datetime | None = None, dry_run=False, post=nc.http_post, smtp=nc.smtp_connect, progress=None) -> DrainReport:
    """Send what the queue holds, through the filter, the budget and quiet hours; mark it handled. Held items wait for the next brief."""
    now = now or dt.datetime.now()
    home = store.home
    st, sec, state = settings(home), nc.read_secrets(), load_state(home)
    items, end, bad = notify_queue.read_counted(home)
    rep = DrainReport(lines=[], skipped=bad)
    if not items:
        rep.reason = "The queue is empty." + (f" {bad} unreadable line(s) skipped." if bad else "")
        if bad and not dry_run:
            notify_queue.advance(home, end)
        return rep
    chans = active_channels(st, sec)
    if not chans and not dry_run:
        rep.reason = "No channel is on yet, so nothing was sent and the queue is kept. Run `drew notify pair` first."
        return rep
    scan = load_scanner()
    titles = _titles(store, sorted({i for it in items for i in it.get("ids") or []}))
    quiet = in_quiet_hours(now, st)
    budget = int(st.get("daily_budget") or 0)
    used = used_today(state, now.date())
    held = list(state.get("held") or [])
    def say(line: str) -> None:
        rep.lines.append(line)
        if progress:
            progress(len(rep.lines), len(items), line)

    for it in items:
        urgent = bool(it.get("urgent"))
        if quiet and not (urgent and st.get("urgent_bypasses_quiet")):
            held.append(it)
            rep.held += 1
            say(f"held (quiet hours): {item_line(it, scan, titles)}")
            continue
        if used >= budget:
            held.append(it)
            rep.held += 1
            say(f"held (daily budget of {budget} used): {item_line(it, scan, titles)}")
            continue
        line = final_pass(item_line(it, scan, titles), scan)
        note = nc.Note("Desk alert" if urgent else "Desk", line, urgent)
        if dry_run:
            say(f"would send: {line}")
            used += 1
            rep.sent += 1
            continue
        results = deliver(note, chans, sec, post, smtp)
        if any(r.ok for r in results):
            used += 1
            rep.sent += 1
            say(f"sent: {line}")
        else:
            held.append(it)
            rep.failed += 1
            say(f"not sent, kept for the next brief: {line} ({'; '.join(r.detail for r in results)})")
    if not dry_run:
        state["sent"] = {now.date().isoformat(): used}  # only today's count matters
        state["held"] = held
        save_state(home, state)
        notify_queue.advance(home, end)
    return rep


def status(store) -> dict:
    home = store.home
    st, sec, state = settings(home), nc.read_secrets(), load_state(home)
    on = {str(c).strip().lower() for c in (st.get("channels") or [])}
    chans = []
    for c in nc.all_channels(st):
        chans.append({"name": c.name, "category": c.category, "service": c.service, "free": c.free, "listed": c.name in on,
                      "ready": c.ready(sec), "missing": c.missing(sec), "keys": {k: nc.mask(sec.get(k, "")) for k in c.needs}})
    scan = load_scanner()
    queued, _, unreadable = notify_queue.read_counted(home)
    return {"channels": chans, "secrets_file": str(nc.env_path()), "secrets_file_exists": nc.env_path().exists(),
            "quiet": f"{st.get('quiet_start') or '-'} to {st.get('quiet_end') or '-'}", "urgent_bypasses_quiet": bool(st.get("urgent_bypasses_quiet")),
            "budget": int(st.get("daily_budget") or 0), "used_today": used_today(state, dt.date.today()),
            "queued": len(queued), "unreadable": unreadable, "held": len(state.get("held") or []), "scan_ready": scan.ready, "scan_why": scan.why,
            "brief_time": st.get("brief_time"), "last_brief": state.get("last_brief", "")}


def schedule_commands(home: Path, st: dict, python: str | None = None) -> list[str]:
    """The two schtasks lines (morning brief, drain every N minutes). Printed for the owner to run; Desk never installs them.
    pythonw.exe runs without a console window, so nothing flashes on screen every 15 minutes."""
    import sys

    exe = Path(python or sys.executable)
    w = exe.with_name("pythonw.exe")
    runner = str(w if w.exists() else exe)
    base = f'\\"{runner}\\" -m desk.cli --home \\"{Path(home)}\\" notify'
    return [
        f'schtasks /Create /F /TN "Desk\\Notify brief" /SC DAILY /ST {st.get("brief_time") or "07:30"} /TR "{base} brief"',
        f'schtasks /Create /F /TN "Desk\\Notify drain" /SC MINUTE /MO {int(st.get("drain_minutes") or 15)} /TR "{base} drain"',
    ]
