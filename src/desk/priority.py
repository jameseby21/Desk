"""The priority score (DESK-0135, DESK-0136, DESK-0137, DESK-0214; DECISIONS 54 and 90): one number from 0 to 100 on every open
ticket, computed when the store is read and never stored. The inputs are the record: the fields Desk already holds, the project
profile, the track, the comments. A person sets at most two fields (`value` 1 to 5, else the hand-set priority stands in; and a
`deadline`). Weights come from four levels, the nearest winning: built-in defaults, the store's desk.toml `[priority]`, the
`weights:` map in tracks/rules.yaml, and a project profile's `priority:` block. People terms come only from a private TOML file
outside the repo, named by `[priority] private_weights`; a missing file means no people terms.

Every list sorts by the score; rollups (track, project, person, company, store) rank by the highest score in the group and show
the sum (load) and the mean (health). Docs: docs/PRIORITY.md (the formula, every signal and the defaults)."""

from __future__ import annotations

import copy
import datetime as dt
import math
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import yamlio
from .asks import ask_of
from .comments import ANSWERERS
from .ops import _COMMENT_LINE, assignee_of
from .store import Store
from .ticket import CLOSED, PRIORITIES, Ticket, canon_type

BALL = ("claimed", "in_progress", "review", "changes_requested")  # a ball in play: quiet days make it rise
NOT_STARTED = ("inbox", "triage", "ready", "blocked")  # quiet days make it sink
_ASKED = re.compile(r"^james-asked-(\d+)x$")
_WITH = "with:"

DEFAULTS: dict = {
    "base": 10,
    "value_step": 6,  # points per value point above 1: value 5 gives 24
    "value_default": 3,
    "value_from_priority": {"p0": 5, "p1": 4, "p2": 3, "p3": 2},
    "date_points": 20,  # a deadline today; 0 at the window's edge
    "date_window_days": 21,
    "date_late_per_day": 1,
    "date_cap": 25,
    "date_kinds": {"hard": 1.0, "soft": 0.75, "sprint": 0.5, "review_by": 0.5},
    "unblocks_per_log": 4,  # 4 x log2(1 + open dependents)
    "unblocks_cap": 20,
    "types": {"bug": 8, "release": 8, "decision": 6, "feature": 4, "upgrade": 3, "task": 3, "research": 3, "docs": 2, "intake": 2},
    "asks": {"approve": 2, "choose": 2, "answer": 1, "provide": 1},
    "type_cap": 10,
    "status": {"approved": 10, "review": 8, "changes_requested": 8, "in_progress": 6, "claimed": 4, "ready": 0, "triage": -5, "inbox": -10, "blocked": -15},
    "open_dependencies": -10,  # ready, but something it depends on is still open
    "owner_waiting": 15,  # a comment by the store owner nobody has answered
    "age_per_day": 0.2,
    "age_cap": 8,
    "stuck_after_days": 7,
    "stuck_per_day": 0.4,
    "stuck_cap": 8,
    "sink_per_day": 0.25,  # after [stale] days
    "sink_cap": 8,
    "effort": [[1, 6], [3, 3], [6, 0], [12, -3]],  # [hours up to, points]
    "effort_more": -6,
    "confidence_default": 0.7,
    "size_hours": {"xs": 1, "s": 3, "m": 6, "l": 12},
    "project_status": {"active": 6, "new": 2, "pending": 0, "exploring": 0, "parked": -10, "dormant": -10},
    "tracks": {"Go-live": 6, "First real use": 3, "Backlog": -3, "Wishlist": -6},
    "project_track_cap": 15,
    "owner_load": -4,
    "tags": {"health": 30, "repeatable-process": 8},
    "no_clients": -20,
    "no_clients_types": ["feature", "upgrade"],
    "enabler_exempt": True,
    "people_cap": 30,
    "emergency_band": 90,
    "ordinary_cap": 89,
    "private_weights": "",
}
TAG_WORDS = {"health": "health comes first", "repeatable-process": "solves a repeatable process", "emergency": "an emergency"}
STATUS_WORDS = {"approved": "approved, waiting for the merge", "review": "in review", "changes_requested": "sent back", "in_progress": "in progress",
                "claimed": "claimed", "ready": "ready", "triage": "in triage", "inbox": "still in the inbox", "blocked": "blocked"}
TYPE_WORDS = {"bug": "a bug", "release": "a release", "decision": "a decision", "feature": "a feature", "upgrade": "an upgrade", "task": "a thing to do",
              "research": "research", "docs": "documentation", "intake": "intake"}
PRIORITY_WORDS = {"p0": "top priority, set by hand", "p1": "high priority, set by hand", "p2": "normal priority, set by hand", "p3": "low priority, set by hand"}  # DESK-0137: plain words, never a code
ASK_WORDS = {"approve": "an approval", "choose": "a choice", "answer": "an answer", "provide": "something to provide"}
LEVELS = ("default", "store", "track rules", "project")


# -- weights ---------------------------------------------------------------------------

@dataclass
class Weights:
    """The effective weights for one ticket, and for every leaf which level set it (default, store, track rules, project)."""

    values: dict
    levels: dict  # leaf path ("tags.health", "base") -> level name

    def get(self, name: str, sub: str | None = None, default=None):
        v = self.values.get(name)
        if sub is None:
            return v if v is not None else default
        if isinstance(v, dict):
            return v.get(sub, default)
        return default

    def level(self, name: str, sub: str | None = None) -> str:
        return self.levels.get(f"{name}.{sub}" if sub is not None else name) or self.levels.get(name) or "default"


def _as_block(v) -> dict:
    """A `priority:` block from a profile: a mapping, or a hand-written inline mapping string."""
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v.strip().startswith("{"):
        try:
            got = yamlio.load(v)
        except yamlio.YAMLError:
            return {}
        return got if isinstance(got, dict) else {}
    return {}


def _layer(values: dict, levels: dict, over: dict, name: str) -> None:
    for k, v in (over or {}).items():
        if k not in DEFAULTS and k not in ("private_weights",):
            continue  # unknown keys are ignored, so a typo never breaks scoring (it shows in `drew explain` as unused)
        if isinstance(DEFAULTS.get(k), dict) and isinstance(v, dict):
            cur = dict(values.get(k) or {})
            for sk, sv in v.items():
                cur[str(sk)] = sv
                levels[f"{k}.{sk}"] = name
            values[k] = cur
        else:
            values[k] = v
            levels[k] = name


def track_weights(home: Path) -> dict:
    """The `weights:` map of tracks/rules.yaml (track name -> points), {} without one."""
    p = Path(home) / "tracks" / "rules.yaml"
    if not p.is_file():
        return {}
    try:
        data = yamlio.load(p.read_text(encoding="utf-8"))
    except (OSError, yamlio.YAMLError):
        return {}
    w = data.get("weights") if isinstance(data, dict) else None
    return {str(k): v for k, v in w.items()} if isinstance(w, dict) else {}


def weights_for(store: Store, project: str, _tw: dict | None = None) -> Weights:
    """Defaults, then desk.toml [priority], then the track rules' weights map, then the project profile's priority block."""
    values = copy.deepcopy(DEFAULTS)
    levels: dict = {}
    _layer(values, levels, store.config.get("priority") or {}, "store")
    tw = track_weights(store.home) if _tw is None else _tw
    if tw:
        _layer(values, levels, {"tracks": tw}, "track rules")
    meta, _ = store.project_profile(project)
    _layer(values, levels, _as_block(meta.get("priority")), "project")
    return Weights(values, levels)


def private_people(store: Store) -> dict:
    """{key: {"boost": n, "projects": [...], "words": "..."}} from the private weights file; {} when it is not named or not there.
    The file lives outside the repo; nothing of it is printed anywhere but the words a person chose for the card."""
    name = str((store.config.get("priority") or {}).get("private_weights") or "")
    if not name:
        return {}
    p = Path(os.path.expandvars(name)).expanduser()
    if not p.is_file():
        return {}
    try:
        with p.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    out = {}
    for key, row in (data.get("people") or {}).items():
        if not isinstance(row, dict):
            continue
        try:
            boost = float(row.get("boost", 0))
        except (TypeError, ValueError):
            continue
        projects = row.get("projects") or []
        out[str(key).lower()] = {"boost": boost, "projects": [str(x) for x in projects] if isinstance(projects, list) else [], "words": str(row.get("words") or "")}
    return out


# -- the score -------------------------------------------------------------------------

@dataclass
class Term:
    key: str  # machine name of the signal
    points: float
    words: str  # plain words for a card
    level: str  # which level set the weight


@dataclass
class Score:
    id: str
    score: int | None  # None on a closed ticket
    raw: float
    emergency: bool
    terms: list[Term] = field(default_factory=list)
    dependents: int = 0
    nearest: str = "9999"  # the nearest date, for ties
    hours: float = 0.0
    created: str = ""
    pulled: bool = False  # in the band because an emergency depends on it, not an emergency itself

    def reasons(self, n: int = 3) -> list[str]:
        """The top reasons in plain words: the terms that moved the score the most, the emergency first."""
        out = (["an emergency waits on it"] if self.pulled else ["an emergency"]) if self.emergency else []
        for t in sorted((t for t in self.terms if t.points and t.key != "base"), key=lambda t: (-abs(t.points), t.key)):
            if t.words not in out:
                out.append(t.words)
        return out[:n]

    def why(self, n: int = 3) -> str:
        return " · ".join(self.reasons(n))

    @property
    def key(self) -> tuple:
        """Sort key, highest first: score, raw, open dependents, the nearest date, fewer hours, older, id."""
        return (-(self.score if self.score is not None else -1), -self.raw, -self.dependents, self.nearest, self.hours, self.created, self.id)


def _date(v) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(v or "")[:10])
    except ValueError:
        return None


def _first_word(text: str) -> str:
    m = re.match(r"\s*([A-Za-z]+)", str(text or ""))
    return m.group(1).lower() if m else ""


def _asked_times(t: Ticket) -> int:
    n = 0
    for tag in t.tags:
        m = _ASKED.match(tag)
        if m:
            n = max(n, int(m.group(1)))
    return n


def is_emergency(t: Ticket) -> bool:
    return "emergency" in t.tags or _asked_times(t) >= 2


def owner_waiting(t: Ticket, owner: str, answerers=ANSWERERS) -> bool:
    """A comment by the owner with no later line by an answerer and no change to the ticket on a later day (comments.entries' rule)."""
    rows = []
    for line in t.section("Comments").splitlines():
        m = _COMMENT_LINE.match(line)
        if m:
            rows.append((m.group(1), m.group(2), m.group(3).strip().lower()))
    if not rows:
        return False
    names = {a.lower() for a in answerers}
    last_day = max(r[0] for r in rows)
    upd = str(t.meta.get("updated") or "")[:10]
    if upd and upd > last_day:
        return False
    for i, (d, hm, who) in enumerate(rows):
        if who == owner.lower() and not any(rows[j][2] in names and (rows[j][0], rows[j][1], j) > (d, hm, i) for j in range(len(rows))):
            return True
    return False


def people_keys(t: Ticket) -> set[str]:
    """The people a ticket is tied to: owner, assignee, associated, and `with:<key>` tags."""
    keys = {str(t.meta.get("owner") or ""), assignee_of(t)}
    assoc = t.meta.get("associated")
    if isinstance(assoc, list):
        keys |= {str(x) for x in assoc}
    keys |= {tag[len(_WITH):] for tag in t.tags if tag.startswith(_WITH)}
    return {k.strip().lower() for k in keys if k and k.strip()}


class Scores:
    """Every ticket of a store scored once, over one list of tickets (normally all of them)."""

    def __init__(self, store: Store, tickets: list[Ticket], today: dt.date | None = None):
        self.store = store
        self.today = today or dt.date.today()
        self.by_id = {t.id: t for t in tickets}
        self.owner = store.owner
        self.stale_days = store.stale_days
        self._tw = track_weights(store.home)
        self._weights: dict[str, Weights] = {}
        self.people = private_people(store)
        rev: dict[str, set[str]] = {}
        for t in tickets:
            for d in t.meta.get("depends_on") or []:
                rev.setdefault(str(d), set()).add(t.id)
            for b in t.meta.get("blocks") or []:
                rev.setdefault(t.id, set()).add(str(b))
        self._rev = rev
        self.balls = {assignee_of(t).lower() for t in tickets if t.status in ("claimed", "in_progress") and assignee_of(t)}
        band = {t.id for t in tickets if t.is_open and is_emergency(t)}
        self.direct = set(band)
        todo = list(band)
        while todo:  # an emergency pulls its open dependencies into the band with it (DESK-0134 question 4, recommended)
            t = self.by_id.get(todo.pop())
            for d in (t.meta.get("depends_on") or []) if t else []:
                dep = self.by_id.get(str(d))
                if dep is not None and dep.is_open and dep.id not in band:
                    band.add(dep.id)
                    todo.append(dep.id)
        self.band = band
        self.scores: dict[str, Score] = {t.id: self.one(t) for t in tickets}

    def weights(self, project: str) -> Weights:
        w = self._weights.get(project)
        if w is None:
            w = self._weights[project] = weights_for(self.store, project, self._tw)
        return w

    def dependents(self, t: Ticket) -> int:
        """Open tickets that wait on this one, followed transitively."""
        seen, todo = set(), [t.id]
        while todo:
            for n in self._rev.get(todo.pop(), ()):
                if n not in seen and n != t.id and (x := self.by_id.get(n)) is not None and x.is_open:
                    seen.add(n)
                    todo.append(n)
        return len(seen)

    def of(self, t: Ticket) -> Score:
        s = self.scores.get(t.id)
        return s if s is not None else self.one(t)

    def key(self, t: Ticket) -> tuple:
        return self.of(t).key

    def sort(self, tickets: list[Ticket]) -> list[Ticket]:
        return sorted(tickets, key=self.key)

    # -- one ticket -----------------------------------------------------------------
    def one(self, t: Ticket) -> Score:
        w = self.weights(t.project)
        created = str(t.meta.get("created") or "")[:10]
        if not t.is_open:
            return Score(t.id, None, 0.0, False, [], 0, "9999", 0.0, created)
        terms: list[Term] = []
        add = lambda key, pts, words, level: terms.append(Term(key, round(float(pts), 2), words, level))  # noqa: E731
        add("base", w.get("base"), "every ticket starts here", w.level("base"))

        # value: the field, else the hand-set priority
        pr = str(t.meta.get("priority") or "")
        try:
            value = int(t.meta.get("value"))
            vwords = f"worth {value} of 5"
            vlevel = w.level("value_step")
        except (TypeError, ValueError):
            value = w.get("value_from_priority", pr)
            vwords = PRIORITY_WORDS.get(pr, "no priority set")
            vlevel = w.level("value_from_priority", pr) if value is not None else w.level("value_default")
            if value is None:
                value = w.get("value_default")
        value = max(1, min(5, int(value)))
        add("value", (value - 1) * w.get("value_step"), vwords, vlevel)

        # the nearest date: deadline by kind, review_by at half
        best, bwords, nearest = 0.0, "", "9999"
        dl = _date(t.meta.get("deadline"))
        kind = str(t.meta.get("deadline_kind") or "hard") if dl else ""
        for d, k in ((dl, kind), (_date(t.meta.get("review_by")), "review_by")):
            if d is None:
                continue
            days = (d - self.today).days
            weight = float(w.get("date_kinds", k, 0.5))
            pts = 0.0 if days >= w.get("date_window_days") else (w.get("date_points") * (1 - days / w.get("date_window_days")) if days >= 0 else min(w.get("date_cap"), w.get("date_points") + w.get("date_late_per_day") * -days))
            pts *= weight
            label = {"hard": _due_words(days), "soft": "soft date " + _in_words(days), "sprint": "sprint ends " + _in_words(days), "review_by": f"review by {d.strftime('%b')} {d.day}"}[k if k in ("hard", "soft", "sprint", "review_by") else "hard"]
            if k == "review_by" and dl is None:
                nearest = min(nearest, d.isoformat())
            elif k != "review_by":
                nearest = min(nearest, d.isoformat())
            if pts > best or not bwords:
                best, bwords = pts, label
        if bwords:
            add("date", best, bwords, w.level("date_points"))

        # unblocks
        n = self.dependents(t)
        if n:
            add("unblocks", min(w.get("unblocks_cap"), w.get("unblocks_per_log") * math.log2(1 + n)), f"unblocks {n} ticket{'s' if n != 1 else ''}", w.level("unblocks_per_log"))

        # type and ask
        ty = canon_type(str(t.meta.get("type") or ""))
        pts = float(w.get("types", ty, 0))
        words = TYPE_WORDS.get(ty, ty or "untyped")
        ask = ask_of(t)
        if ask:
            pts += float(w.get("asks", ask["kind"], 0))
            words = f"{ASK_WORDS.get(ask['kind'], 'a decision')} for {self._who(t)}"
        add("type", min(pts, w.get("type_cap")), words, w.level("types", ty))

        # lifecycle
        st = t.status
        add("state", w.get("status", st, 0), STATUS_WORDS.get(st, st), w.level("status", st))
        open_deps = [d for d in (t.meta.get("depends_on") or []) if (x := self.by_id.get(str(d))) is not None and x.is_open]
        if st == "ready" and open_deps:
            add("open_dependencies", w.get("open_dependencies"), f"waiting on {len(open_deps)} open dependenc{'ies' if len(open_deps) != 1 else 'y'}", w.level("open_dependencies"))

        # the owner is waiting for an answer
        if owner_waiting(t, self.owner):
            add("owner_waiting", w.get("owner_waiting"), f"{self.owner.capitalize()} asked and nobody answered", w.level("owner_waiting"))

        # age and quiet days
        cd = _date(created)
        if cd is not None:
            age = max(0, (self.today - cd).days)
            if age:
                add("age", min(w.get("age_cap"), w.get("age_per_day") * age), f"{age} day{'s' if age != 1 else ''} old", w.level("age_per_day"))
        ud = _date(t.meta.get("updated"))
        if ud is not None:
            quiet = max(0, (self.today - ud).days)
            if st in BALL and quiet > w.get("stuck_after_days"):
                add("stuck", min(w.get("stuck_cap"), w.get("stuck_per_day") * (quiet - w.get("stuck_after_days"))), f"stuck for {quiet} days", w.level("stuck_per_day"))
            elif st in NOT_STARTED and quiet > self.stale_days:
                add("sinking", -min(w.get("sink_cap"), w.get("sink_per_day") * (quiet - self.stale_days)), f"untouched for {quiet} days", w.level("sink_per_day"))

        # effort with confidence
        hours, sure = self._hours(t, w)
        pts = w.get("effort_more")
        for upto, p in sorted((list(x) for x in w.get("effort")), key=lambda x: x[0]):
            if hours <= upto:
                pts = p
                break
        add("effort", pts, f"about {_hours_words(hours)}" + ("" if sure else ", uncertain"), w.level("effort"))

        # project and track weight, capped together
        meta, _ = self.store.project_profile(t.project)
        try:
            p_pts = float(meta.get("weight"))
            pw = f"project weight {p_pts:g}"
            plevel = "project"
        except (TypeError, ValueError):
            word = _first_word(meta.get("status"))
            p_pts = float(w.get("project_status", word, 0))
            pw = f"project {word}" if word else "project status unknown"
            plevel = w.level("project_status", word)
        track = str(t.meta.get("track") or "").strip()
        k_pts = float(w.get("tracks", track, 0)) if track else 0.0
        cap = w.get("project_track_cap")
        total = max(-cap, min(cap, p_pts + k_pts))
        k_pts = total - p_pts
        if p_pts:
            add("project", p_pts, pw, plevel)
        if k_pts:
            add("track", k_pts, f"track {track}", w.level("tracks", track))

        # James's rules (DECISIONS 90): tags, no clients, people
        for tag in t.tags:
            pts = w.get("tags", tag)
            if pts is not None and tag != "emergency":
                add(f"tag:{tag}", pts, TAG_WORDS.get(tag, f"tagged {tag}"), w.level("tags", tag))
        if ty in (w.get("no_clients_types") or []):
            clients = _int(meta.get("clients"), 0)
            monet = str(meta.get("monetization") or "none").strip().lower()
            enabler = str(meta.get("enabler") or "").strip().lower() in ("true", "yes", "1")
            if clients <= 0 or monet != "active":
                if enabler and w.get("enabler_exempt"):
                    add("no_clients", 0, "an enabler: exempt from the no-clients rule", w.level("enabler_exempt"))
                else:
                    add("no_clients", w.get("no_clients"), "no clients yet: feature work waits", w.level("no_clients"))
        if self.people:
            best_p, best_words = 0.0, ""
            for key in people_keys(t):
                row = self.people.get(key)
                if row and (not row["projects"] or t.project in row["projects"]) and row["boost"] > best_p:
                    best_p, best_words = row["boost"], row["words"] or "a person who comes first"
            if best_p:
                add("people", min(best_p, w.get("people_cap")), best_words, "private file")

        # one ball at a time
        who = assignee_of(t).lower()
        emergency = t.id in self.band
        if st == "ready" and who and who in self.balls and not emergency:
            add("owner_load", w.get("owner_load"), f"{self._who(t)} already holds a ball", w.level("owner_load"))

        raw = sum(x.points for x in terms)
        if emergency:
            score = min(99, int(w.get("emergency_band")) + min(9, round(raw / 10)))
        else:
            score = max(0, min(int(w.get("ordinary_cap")), round(raw)))
        return Score(t.id, score, round(raw, 2), emergency, terms, n, nearest, hours, created, emergency and t.id not in self.direct)

    def _who(self, t: Ticket) -> str:
        who = assignee_of(t) or str(t.meta.get("owner") or "")
        return who.capitalize() if who else "nobody"

    def _hours(self, t: Ticket, w: Weights) -> tuple[float, bool]:
        eh = t.meta.get("effort_hours")
        try:
            conf = float(t.meta.get("confidence"))
        except (TypeError, ValueError):
            conf = float(w.get("confidence_default"))
        conf = max(0.1, min(1.0, conf))
        if isinstance(eh, list) and len(eh) == 2:
            try:
                return (float(eh[0]) + float(eh[1])) / 2 / conf, conf >= 0.6
            except (TypeError, ValueError):
                pass
        return float(w.get("size_hours", str(t.meta.get("size") or ""), 6)), True


def _int(v, default: int) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def _due_words(days: int) -> str:
    if days < 0:
        return f"missed by {-days} day{'s' if days != -1 else ''}"
    return "due today" if days == 0 else f"due in {days} day{'s' if days != 1 else ''}"


def _in_words(days: int) -> str:
    if days < 0:
        return f"{-days} day{'s' if days != -1 else ''} ago"
    return "today" if days == 0 else f"in {days} day{'s' if days != 1 else ''}"


def _hours_words(h: float) -> str:
    if h < 1.5:
        return "an hour"
    return f"{round(h)} hours"


# -- the cache: one scoring per version of the store -------------------------------

_CACHE: dict[Path, tuple[tuple, Scores]] = {}


def _mt(p: Path) -> int:
    try:
        return p.stat().st_mtime_ns
    except OSError:
        return 0


def _dir_stamp(d: Path, suffix: str = ".md") -> tuple[int, int]:
    """(count, latest mtime) of the files in one directory from a single listing: on Windows the listing carries the times, so a
    1,500-ticket store is stamped in a few milliseconds instead of 1,500 stat calls."""
    n, latest = 0, _mt(d)
    try:
        with os.scandir(d) as it:
            for e in it:
                if e.name.endswith(suffix) and e.is_file():
                    n += 1
                    latest = max(latest, e.stat().st_mtime_ns)
    except OSError:
        pass
    return n, latest


def stamp(store: Store) -> tuple:
    """What a new score would depend on: the day, every ticket file, desk.toml, the profiles, the track rules and the private file."""
    priv = str((store.config.get("priority") or {}).get("private_weights") or "")
    return (dt.date.today().isoformat(), *_dir_stamp(store.tickets_dir), _mt(store.home / "desk.toml"), *_dir_stamp(store.projects_dir),
            _mt(store.home / "tracks" / "rules.yaml"), _mt(Path(os.path.expandvars(priv)).expanduser()) if priv else 0)


def scores(store: Store, tickets: list[Ticket] | None = None) -> Scores:
    """The store's scores, computed once per version of the store (the stamp) and shared by every page and command in the process."""
    st = stamp(store)
    hit = _CACHE.get(store.home)
    if hit is not None and hit[0] == st:
        return hit[1]
    sc = Scores(store, tickets if tickets is not None else store.tickets())
    _CACHE[store.home] = (st, sc)
    return sc


def sort(store: Store, tickets: list[Ticket]) -> list[Ticket]:
    return scores(store).sort(tickets)


# -- rollups (DESK-0136) ------------------------------------------------------------

@dataclass
class Rollup:
    """One group's numbers on the same scale as a ticket: the max ranks, the sum is the load, the mean is the health."""

    n: int
    max: int
    sum: int
    mean: float
    top: Ticket | None
    top3: int  # the sum of the three best, the tie rule between groups
    vals: tuple = ()  # every score, best first, so a larger group (a company) can merge rollups

    @property
    def key(self) -> tuple:
        return (-self.max, -self.top3, -self.sum)

    @property
    def text(self) -> str:
        return f"score {self.max} · load {self.sum} · mean {self.mean:g}" if self.n else "no open tickets"


def rollup(sc: Scores, tickets: list[Ticket]) -> Rollup:
    live = [(sc.of(t), t) for t in tickets if t.is_open]
    live = [(s, t) for s, t in live if s.score is not None]
    if not live:
        return Rollup(0, 0, 0, 0.0, None, 0, ())
    live.sort(key=lambda st: st[0].key)
    vals = [s.score for s, _ in live]
    return Rollup(len(vals), vals[0], sum(vals), round(sum(vals) / len(vals), 1), live[0][1], sum(vals[:3]), tuple(vals))


def by_group(sc: Scores, tickets: list[Ticket], group) -> list[tuple[str, Rollup]]:
    """(group name, rollup) for every group `group(t)` names, best first."""
    buckets: dict[str, list[Ticket]] = {}
    for t in tickets:
        buckets.setdefault(str(group(t) or ""), []).append(t)
    rows = [(k, rollup(sc, ts)) for k, ts in buckets.items()]
    rows.sort(key=lambda kv: (kv[1].key, kv[0].lower()))
    return rows


def repeatable_done(tickets: list[Ticket]) -> int:
    """Done tickets tagged repeatable-process: the solutions to repeatable, periodic processes a project has shipped (DESK-0214)."""
    return sum(1 for t in tickets if t.status == "done" and "repeatable-process" in t.tags)


# -- `drew explain` -------------------------------------------------------------------

def explain(store: Store, t: Ticket, sc: Scores | None = None) -> str:
    sc = sc or scores(store)
    s = sc.of(t)
    w = sc.weights(t.project)
    out = [f"{t.id}  {str(t.meta.get('title') or '')[:70]}"]
    if s.score is None:
        out.append(f"No score: the ticket is {t.status}. Closed tickets are not ranked.")
        return "\n".join(out)
    band = f"emergency band {w.get('emergency_band')} to 99" if s.emergency else f"ordinary band 0 to {w.get('ordinary_cap')}"
    out.append(f"Score {s.score} of 100 ({band}) · raw {s.raw:g}")
    out.append("Why: " + (s.why() or "nothing moves it"))
    out.append("")
    out.append(f"{'points':>8}  {'signal':<24} {'in words':<48} weight set at")
    for term in s.terms:
        out.append(f"{term.points:>+8.1f}  {term.key:<24} {term.words[:48]:<48} {term.level}")
    out.append(f"{'=':>8}  raw {s.raw:g}" + (f" → {w.get('emergency_band')} + min(9, round(raw / 10)) = {s.score}" if s.emergency else f" → clamped 0 to {w.get('ordinary_cap')} = {s.score}"))
    out.append("")
    srcs = [f"store desk.toml [priority]: {'present' if store.config.get('priority') else 'absent'}",
            f"track rules weights: {'present' if sc._tw else 'absent'}",
            f"project profile priority block: {'present' if _as_block(store.project_profile(t.project)[0].get('priority')) else 'absent'}",
            f"private people file: {'present' if sc.people else 'absent'} (never printed)"]
    out.append("Levels: default → store → track rules → project; the nearest wins. " + " · ".join(srcs))
    return "\n".join(out)
