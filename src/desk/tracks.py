"""Tracks: the level between a project and its tickets (project > track > ticket > details).

A ticket may carry `track: <name>` in its frontmatter. A track may have a description file at
`tracks/<project>/<slug>.md` (frontmatter: title, kind, starts, ends, goal). `desk migrate track` assigns tracks
from an ordered list of rules in `tracks/rules.yaml`: the first rule that matches a ticket wins."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from . import yamlio

from .store import Store, split_profile
from .ticket import Ticket, slugify

KINDS = ["sprint", "milestone", "epic", "theme"]
UNTRACKED = "No track"
RULES_PATH = Path("tracks") / "rules.yaml"
CONDITIONS = {"project", "tags", "source", "title_prefix", "created_before", "status"}


class RulesError(ValueError):
    pass


def track_of(t: Ticket) -> str:
    return str(t.meta.get("track") or "").strip()


def track_slug(name: str) -> str:
    return slugify(name, 60) if name else "untracked"


def _as_list(v) -> list[str]:
    return [str(x) for x in v] if isinstance(v, list) else [str(v)]


def load_rules(path: Path) -> list[dict]:
    if not path.is_file():
        raise RulesError(f"No rules file at {path}")
    try:
        data = yamlio.load(path.read_text(encoding="utf-8"))
    except yamlio.YAMLError as e:
        raise RulesError(f"{path}: not valid YAML ({e})") from e
    rules = data.get("rules") if isinstance(data, dict) else data
    if not isinstance(rules, list):
        raise RulesError(f"{path}: expected a `rules:` list")
    out = []
    for i, r in enumerate(rules, 1):
        if not isinstance(r, dict) or not str(r.get("set") or "").strip():
            raise RulesError(f"{path}: rule {i} needs a `set:` track name")
        when = r.get("when") or {}
        if not isinstance(when, dict) or set(when) - CONDITIONS:
            raise RulesError(f"{path}: rule {i} `when:` may only use {', '.join(sorted(CONDITIONS))}")
        out.append({"when": when, "set": str(r["set"]).strip()})
    return out


def matches(t: Ticket, when: dict) -> bool:
    """Every condition in `when` must hold (an empty `when` always matches, which makes a catch-all)."""
    for key, want in when.items():
        if key == "project" and t.project not in _as_list(want):
            return False
        if key == "tags" and not set(_as_list(want)) & set(t.tags):
            return False
        if key == "source" and not any(w in str(t.meta.get("source") or "") for w in _as_list(want)):
            return False
        if key == "title_prefix" and not any(str(t.meta.get("title") or "").startswith(w) for w in _as_list(want)):
            return False
        if key == "created_before" and not str(t.meta.get("created") or "9999")[:10] < str(want)[:10]:
            return False
        if key == "status" and t.status not in _as_list(want):
            return False
    return True


def assign(t: Ticket, rules: list[dict]) -> str | None:
    for r in rules:
        if matches(t, r["when"]):
            return r["set"]
    return None


def plan(tickets: list[Ticket], rules: list[dict]) -> list[tuple[Ticket, str]]:
    """(ticket, track) for every ticket that has no track yet and matches a rule."""
    out = []
    for t in tickets:
        if track_of(t):
            continue
        name = assign(t, rules)
        if name:
            out.append((t, name))
    return out


def counts(pairs: list[tuple[Ticket, str]]) -> list[tuple[str, int]]:
    c = Counter(name for _, name in pairs)
    return sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))


def _yaml_value(name: str) -> str:
    return yamlio.dump({"track": name}, allow_unicode=True, width=10000, default_flow_style=False).rstrip("\n")


def write_track(t: Ticket, name: str) -> bool:
    """Add `track:` to the ticket's frontmatter by inserting one line; nothing else changes, `updated` included."""
    path = t.path
    text = path.read_bytes().decode("utf-8")
    m = re.match(r"(---\r?\n)(.*?)(\r?\n---\r?\n)", text, re.S)
    if not m or re.search(r"^track:", m.group(2), re.M):
        return False
    nl = "\r\n" if "\r\n" in m.group(3) else "\n"
    lines = m.group(2).split(nl)
    at = next((i for i, ln in enumerate(lines) if ln.startswith("created:")), len(lines))
    lines.insert(at, _yaml_value(name))
    path.write_bytes((m.group(1) + nl.join(lines) + m.group(3) + text[m.end():]).encode("utf-8"))
    return True


# -- descriptions -------------------------------------------------------------------

def track_info(store: Store, project: str, name: str) -> dict:
    """Description from tracks/<project>/<slug>.md, else {} (the track still shows, from its tickets)."""
    p = store.home / "tracks" / project / f"{track_slug(name)}.md"
    if not p.is_file():
        return {}
    meta, body = split_profile(p.read_text(encoding="utf-8"))
    meta = {k: str(v) for k, v in meta.items()}
    meta["body"] = body
    return meta


# -- the Projects page ------------------------------------------------------------------

def pct(done: int, total: int) -> int:
    return round(100 * done / total) if total else 0


def hierarchy(store: Store, tickets: list[Ticket]) -> list[dict]:
    """Project > track > ticket, every level in score order (DESK-0136): a project's or track's `score` is the priority rollup of its
    open tickets (the max ranks; sum and mean shown), `repeat_done` counts its done repeatable-process solutions (DESK-0214)."""
    from . import companies, priority
    from .review import ask_of
    from .ticket import STATUSES

    sc = priority.scores(store)
    rows = []
    for key in store.projects():
        mine = [t for t in tickets if t.project == key]
        meta, _ = store.project_profile(key)
        by_track: dict[str, list[Ticket]] = {}
        for t in mine:
            by_track.setdefault(track_of(t), []).append(t)
        tracks = []
        for name, ts in by_track.items():
            info = track_info(store, key, name) if name else {}
            done = len([t for t in ts if t.status == "done"])
            tracks.append({
                "name": name or UNTRACKED, "slug": track_slug(name), "kind": info.get("kind", ""), "goal": info.get("goal", ""),
                "starts": info.get("starts", ""), "ends": info.get("ends", ""),
                "total": len(ts), "done": done, "open": len([t for t in ts if t.is_open]), "pct": pct(done, len(ts)),
                "score": priority.rollup(sc, ts), "repeat_done": priority.repeatable_done(ts),
                "tickets": [{"t": t, "ask": ask_of(t), "score": sc.of(t)} for t in sc.sort(ts)],
            })
        tracks.sort(key=lambda tr: (tr["score"].key, tr["name"] == UNTRACKED, tr["name"].lower()))
        done = len([t for t in mine if t.status == "done"])
        co = companies.of(store, key)  # DESK-0181: the company (the profile's company:, else its area:)
        rows.append({"key": key, "title": store.project_title(key), "area": co if co != companies.UNSORTED else "", "prefix": store.key_for(key), "aliases": store.aliases_for(key), "total": len(mine), "done": done,
                     "open": len([t for t in mine if t.is_open]), "pct": pct(done, len(mine)), "tracks": tracks,
                     "score": priority.rollup(sc, mine), "repeat_done": priority.repeatable_done(mine),
                     "counts": {st: len([t for t in mine if t.status == st]) for st in STATUSES}})
    rows.sort(key=lambda r: (r["score"].key, r["key"]))
    return rows


def track_chips(tickets: list[Ticket]) -> list[tuple[str, str, int]]:
    c = Counter(track_of(t) for t in tickets if track_of(t))
    return [(track_slug(n), n, k) for n, k in sorted(c.items(), key=lambda kv: kv[0].lower())]
