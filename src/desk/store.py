"""The ticket store: a directory with desk.toml, tickets/, projects/, intake/."""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

from . import yamlio

from .timing import timed_db
from .config import DEFAULT_TOML, CONFIG_NAME, load_config
from .ticket import SIZES, canon_type, Ticket, new_ticket, slugify

_ID = re.compile(r"^(?P<key>[A-Z0-9][A-Z0-9-]*)-(?P<num>\d{4,})$")
_PROFILE_FM = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.S)
_PROFILE_CACHE: dict = {}
_KV = re.compile(r"^([A-Za-z_][\w-]*):\s?(.*)$")


def id_parts(ticket_id: str) -> tuple[str, int] | None:
    m = _ID.match(ticket_id or "")
    if not m:
        return None
    return m.group("key"), int(m.group("num"))


def _loose_frontmatter(block: str) -> dict:
    """Profiles are written by hand: `name: Algo (a repo): notes` is not valid YAML but people mean it.
    One `key: value` per line; a `[a, b]` value is a list; everything else is a string."""
    out: dict = {}
    for line in block.splitlines():
        mm = _KV.match(line)
        if not mm:
            continue
        key, raw = mm.group(1), mm.group(2).strip()
        if raw.startswith("[") and raw.endswith("]"):
            try:
                val = yamlio.load(raw)
            except yamlio.YAMLError:
                val = [x.strip().strip("'\"") for x in raw[1:-1].split(",")]
            out[key] = [str(x) for x in val if str(x).strip()] if isinstance(val, list) else []
        else:
            out[key] = raw.strip("'\"") if len(raw) > 1 and raw[0] == raw[-1] and raw[0] in "'\"" else raw
    return out


def split_profile(text: str) -> tuple[dict, str]:
    """Split a project profile into (frontmatter mapping, markdown body)."""
    m = _PROFILE_FM.match(text)
    if not m:
        return {}, text
    try:
        meta = yamlio.load(m.group(1))
    except yamlio.YAMLError:
        meta = None
    if not isinstance(meta, dict):
        meta = _loose_frontmatter(m.group(1))
    return meta, m.group(2)


def join_profile(meta: dict, body: str) -> str:
    fm = yamlio.dump(meta, sort_keys=False, allow_unicode=True, default_flow_style=None, width=1000).rstrip("\n")
    fm = re.sub(r"^(\w+): '(\d{4}-\d{2}-\d{2})'$", r"\1: \2", fm, flags=re.M)
    return f"---\n{fm}\n---\n{body.lstrip(chr(10))}"


class Store:
    def __init__(self, home: Path):
        self.home = Path(home)
        self.config = load_config(self.home)
        self.tickets_dir = self.home / "tickets"
        self.projects_dir = self.home / "projects"
        self.intake_dir = self.home / "intake"
        self.assets_dir = self.tickets_dir / "assets"
        self.brand_dir = self.home / "assets" / "brand"
        self.index_path = self.home / ".desk" / "index.sqlite"

    # -- setup -------------------------------------------------------------
    @classmethod
    def init(cls, home: Path, owner: str = "james") -> "Store":
        home = Path(home)
        home.mkdir(parents=True, exist_ok=True)
        cfg = home / CONFIG_NAME
        if not cfg.exists():
            cfg.write_text(DEFAULT_TOML.replace('owner = "james"', f'owner = "{owner}"'), encoding="utf-8")
        for d in ("tickets", "projects", "intake"):
            (home / d).mkdir(exist_ok=True)
        decisions = home / "DECISIONS.md"
        if not decisions.exists():
            decisions.write_text("# Decisions\n\nProcess decisions, dated, append-only. Newest at the bottom.\n", encoding="utf-8")
        gi = home / ".gitignore"
        lines = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
        for needed in (".desk/", "jeby-private/"):
            if needed not in lines:
                lines.append(needed)
        gi.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return cls(home)

    # -- config helpers ----------------------------------------------------
    @property
    def owner(self) -> str:
        return str(self.config["desk"]["owner"])

    @property
    def stale_days(self) -> int:
        return int(self.config["stale"]["days"])

    @property
    def combos(self) -> bool:
        return bool(self.config.get("life", {}).get("combos", False))

    @property
    def max_attach_mb(self) -> float:
        try:
            return float(self.config.get("attachments", {}).get("max_mb", 8))
        except (TypeError, ValueError):
            return 8.0

    def delegated(self, ticket: Ticket) -> bool:
        ap = self.config["approval"]
        types = {canon_type(x) for x in ap.get("delegated_types", [])}
        max_size = ap.get("delegated_max_size", "s")
        size_ok = SIZES.index(str(ticket.meta.get("size", "l"))) <= SIZES.index(str(max_size))
        return canon_type(str(ticket.meta.get("type"))) in types and size_ok

    # -- projects ----------------------------------------------------------
    def projects(self) -> dict[str, Path]:
        if not self.projects_dir.exists():
            return {}
        return {p.stem: p for p in sorted(self.projects_dir.glob("*.md"))}

    @timed_db
    def _profile(self, key: str):
        """The cache entry (signature, meta, body, memo) of projects/<key>.md, re-read only when its (mtime_ns, size) changed; None without a profile.
        The cache is per process and keyed by path, so the per-request Store objects of the UI share it."""
        if not key or "/" in key or "\\" in key:
            return None
        path = self.projects_dir / f"{key}.md"
        try:
            st = path.stat()
        except OSError:
            _PROFILE_CACHE.pop(path, None)
            return None
        sig = (st.st_mtime_ns, st.st_size)
        hit = _PROFILE_CACHE.get(path)
        if hit is None or hit[0] != sig:
            meta, body = split_profile(path.read_text(encoding="utf-8"))
            hit = (sig, meta, body, {})
            _PROFILE_CACHE[path] = hit
        return hit

    def project_profile(self, key: str) -> tuple[dict, str]:
        """(frontmatter mapping, body) of projects/<key>.md; ({}, "") when there is no profile. Shared with the cache: do not modify it."""
        hit = self._profile(key)
        return (hit[1], hit[2]) if hit else ({}, "")

    def _memo(self, key: str, name: str, compute):
        """A value derived from the profile alone, computed once per version of the file."""
        hit = self._profile(key)
        if hit is None:
            return compute({}, "")
        memo = hit[3]
        if name not in memo:
            memo[name] = compute(hit[1], hit[2])
        return memo[name]

    def brand_for(self, project: str) -> dict | None:
        """{'mark_url', 'accent', 'family'} for a project whose profile has a `brand:` block, else None.
        mark_url is None when the mark file is missing; the block still counts as present (the file is checked on every call)."""
        def parse(meta, _body):
            b = meta.get("brand")
            if isinstance(b, str):
                try:
                    b = yamlio.load(b)
                except yamlio.YAMLError:
                    b = None
            if not isinstance(b, dict):
                return None
            return str(b.get("mark") or "").replace("\\", "/"), str(b.get("accent") or ""), str(b.get("family") or "")

        got = self._memo(project, "brand", parse)
        if got is None:
            return None
        mark, accent, family = got
        url = None
        if mark and (self.home / mark).is_file():
            root = "assets/brand/"
            url = "/brand/" + (mark[len(root):] if mark.startswith(root) else mark)
        return {"mark_url": url, "mark": mark, "accent": accent, "family": family}

    def project_title(self, key: str) -> str:
        def title(meta, body):
            for line in body.splitlines():
                if line.startswith("# "):
                    return line[2:].strip()
            if meta.get("name"):
                return str(meta["name"])
            return key

        return self._memo(key, "title", title)

    def key_for(self, project: str) -> str:
        """The ticket-id prefix for a project: its profile's `prefix:` frontmatter field, else the key uppercased."""
        def prefix(meta, _body):
            val = str(meta.get("prefix") or "").strip()
            return val.upper() if val else project.upper()

        return self._memo(project, "key", prefix)

    def aliases_for(self, project: str) -> list[str]:
        """Old keys and old prefixes a project answers to (`aliases:` in its profile)."""
        def aliases(meta, _body):
            v = meta.get("aliases")
            if isinstance(v, str):
                v = [v]
            if not isinstance(v, list):
                return ()
            return tuple(str(x).strip() for x in v if str(x).strip())

        return list(self._memo(project, "aliases", aliases))

    def prefixes_for(self, project: str) -> set[str]:
        """Every id prefix that is valid for a project: the current prefix plus every alias, uppercased."""
        return {self.key_for(project), *(a.upper() for a in self.aliases_for(project))}

    def formerly(self, ticket: Ticket) -> str | None:
        """The ticket's id prefix when it differs from its project's current prefix (a renamed project), else None."""
        parts = id_parts(ticket.id)
        if not parts:
            return None
        return parts[0] if parts[0] != self.key_for(ticket.project) else None

    # -- tickets -----------------------------------------------------------
    def ticket_paths(self) -> list[Path]:
        if not self.tickets_dir.exists():
            return []
        return sorted(p for p in self.tickets_dir.glob("*.md") if p.is_file())

    @timed_db
    def tickets(self) -> list[Ticket]:
        out = []
        for p in self.ticket_paths():
            out.append(Ticket.load(p))
        out.sort(key=lambda t: t.id)
        return out

    @timed_db
    def by_id(self) -> dict[str, Ticket]:
        return {t.id: t for t in self.tickets()}

    @timed_db
    def get(self, ticket_id: str) -> Ticket:
        ticket_id = ticket_id.upper()
        for p in self.ticket_paths():
            if p.name.upper().startswith(ticket_id + "-") or p.stem.upper() == ticket_id:
                t = Ticket.load(p)
                if t.id.upper() == ticket_id:
                    return t
        # slow path: id inside a file whose name drifted
        for t in self.tickets():
            if t.id.upper() == ticket_id:
                return t
        raise KeyError(f"No ticket {ticket_id}")

    def next_id(self, project: str) -> str:
        key = self.key_for(project)
        n = 0
        for t in self.tickets():
            parts = id_parts(t.id)
            if parts and parts[0] == key:
                n = max(n, parts[1])
        return f"{key}-{n + 1:04d}"

    def path_for(self, ticket: Ticket) -> Path:
        return self.tickets_dir / f"{ticket.id}-{slugify(str(ticket.meta.get('title', '')))}.md"

    def ticket_assets_dir(self, ticket_id: str) -> Path:
        return self.assets_dir / ticket_id.upper()

    def create(self, title: str, project: str, type_: str = "feature", **kw) -> Ticket:
        t = new_ticket(self.next_id(project), title, project, type_, **kw)
        t.path = self.path_for(t)
        return t

    def save(self, ticket: Ticket, touch: bool = True) -> Path:
        from .rules import validate_ticket  # local import: rules depends on store

        if touch:
            ticket.meta["updated"] = dt.date.today().isoformat()
        errors, _ = validate_ticket(ticket, self)
        if errors:
            raise ValueError("; ".join(errors))
        if ticket.path is None:
            ticket.path = self.path_for(ticket)
        self.tickets_dir.mkdir(parents=True, exist_ok=True)
        return ticket.write()

    # -- derived -----------------------------------------------------------
    def is_stale(self, ticket: Ticket, on: dt.date | None = None) -> bool:
        if not ticket.is_open:
            return False
        try:
            updated = dt.date.fromisoformat(str(ticket.meta.get("updated"))[:10])
        except ValueError:
            return False
        on = on or dt.date.today()
        return (on - updated).days > self.stale_days

    def next_ready(self, project: str | None = None) -> Ticket | None:
        cands = [t for t in self.tickets() if t.status == "ready" and (project is None or t.project == project)]
        if not cands:
            return None
        cands.sort(key=lambda t: (str(t.meta.get("priority", "p3")), str(t.meta.get("created", "")), t.id))
        return cands[0]

    def index(self) -> Path:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        if self.index_path.exists():
            self.index_path.unlink()
        con = sqlite3.connect(self.index_path)
        con.execute(
            "CREATE TABLE tickets (id TEXT PRIMARY KEY, title TEXT, project TEXT, type TEXT, status TEXT,"
            " priority TEXT, size TEXT, owner TEXT, created TEXT, updated TEXT, stale INTEGER, path TEXT, body TEXT)"
        )
        rows = []
        for t in self.tickets():
            m = t.meta
            rows.append((t.id, m.get("title"), m.get("project"), m.get("type"), m.get("status"), m.get("priority"),
                         m.get("size"), m.get("owner"), str(m.get("created")), str(m.get("updated")),
                         int(self.is_stale(t)), str(t.path), "\n".join(t.section(s) for s in t.sections)))
        con.executemany("INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        con.commit()
        con.close()
        return self.index_path

    # -- git sync ----------------------------------------------------------
    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=self.home, capture_output=True, text=True)

    # Writes never commit on the spot. `sync_after` records that something is pending (.desk/sync-pending.json) and the
    # commit happens when the store has been idle for [git] debounce_seconds (default 30): in the UI by a timer, in the
    # CLI at the next start of any command, or at once with `drew sync`. The files are already on disk, so stopping
    # mid-debounce loses nothing; the pending note makes the next start commit them.
    background_flush = False  # the UI sets this: a timer commits after the idle time instead of waiting for the next start
    _timer: "threading.Timer | None" = None

    @property
    def sync_on(self) -> bool:
        return bool(self.config["git"].get("sync"))

    @property
    def debounce_seconds(self) -> float:
        try:
            return max(0.0, float(self.config["git"].get("debounce_seconds", 30)))
        except (TypeError, ValueError):
            return 30.0

    @property
    def _pending_path(self) -> Path:
        return self.home / ".desk" / "sync-pending.json"

    def pending(self) -> dict | None:
        try:
            return json.loads(self._pending_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def sync_before(self) -> None:
        if self.sync_on:
            self.flush()  # earlier work that has been idle long enough
            self._git("pull", "--rebase", "--autostash")

    def sync_after(self, message: str) -> None:
        if not self.sync_on:
            return
        pend = self.pending() or {"messages": []}
        pend["messages"] = (pend["messages"] + [message])[-500:]
        pend["count"] = int(pend.get("count", 0)) + 1
        pend["last"] = time.time()
        self._pending_path.parent.mkdir(parents=True, exist_ok=True)
        self._pending_path.write_text(json.dumps(pend), encoding="utf-8")
        self.schedule_flush()

    def schedule_flush(self) -> None:
        """In a long-running process (the UI), commit after the idle time. Each new write restarts the wait."""
        if not (Store.background_flush and self.sync_on and self.pending()):
            return
        if Store._timer is not None:
            Store._timer.cancel()
        Store._timer = threading.Timer(self.debounce_seconds + 0.05, self.flush)
        Store._timer.daemon = True
        Store._timer.start()

    def flush(self, force: bool = False) -> bool:
        """Commit and push what is pending when the store has been idle for the debounce time (or at once with force).
        Returns True when a commit was made."""
        pend = self.pending()
        if not pend or not self.sync_on:
            return False
        if not force and time.time() - float(pend.get("last", 0)) < self.debounce_seconds:
            return False
        msgs = pend.get("messages") or ["desk: sync"]
        n = int(pend.get("count", len(msgs)))
        message = msgs[0] if n == 1 else f"desk: {n} changes (first: {msgs[0].removeprefix('desk: ')}; last: {msgs[-1].removeprefix('desk: ')})"
        self._git("add", "-A")
        made = self._git("commit", "-m", message).returncode == 0
        self._git("push")
        try:
            self._pending_path.unlink()
        except OSError:
            pass
        return made

    def sync_now(self) -> tuple[bool, str]:
        """`drew sync`: pull, commit what is pending, push. (committed?, git's last word)"""
        if not self.sync_on:
            return False, "[git] sync is false in desk.toml; nothing to do"
        self._git("pull", "--rebase", "--autostash")
        made = self.flush(force=True)
        r = self._git("push")
        return made, (r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip() else "pushed"
