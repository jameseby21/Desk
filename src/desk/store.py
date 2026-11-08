"""The ticket store: a directory with desk.toml, tickets/, projects/, intake/."""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
import subprocess
from pathlib import Path

import yaml

from .config import DEFAULT_TOML, CONFIG_NAME, load_config
from .ticket import SIZES, Ticket, new_ticket, slugify

_ID = re.compile(r"^(?P<key>[A-Z0-9][A-Z0-9-]*)-(?P<num>\d{4,})$")
_PROFILE_FM = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.S)
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
                val = yaml.safe_load(raw)
            except yaml.YAMLError:
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
        meta = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        meta = None
    if not isinstance(meta, dict):
        meta = _loose_frontmatter(m.group(1))
    return meta, m.group(2)


def join_profile(meta: dict, body: str) -> str:
    fm = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, default_flow_style=None, width=1000).rstrip("\n")
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

    def delegated(self, ticket: Ticket) -> bool:
        ap = self.config["approval"]
        types = ap.get("delegated_types", [])
        max_size = ap.get("delegated_max_size", "s")
        size_ok = SIZES.index(str(ticket.meta.get("size", "l"))) <= SIZES.index(str(max_size))
        return ticket.meta.get("type") in types and size_ok

    # -- projects ----------------------------------------------------------
    def projects(self) -> dict[str, Path]:
        if not self.projects_dir.exists():
            return {}
        return {p.stem: p for p in sorted(self.projects_dir.glob("*.md"))}

    def project_profile(self, key: str) -> tuple[dict, str]:
        """(frontmatter mapping, body) of projects/<key>.md; ({}, "") when there is no profile."""
        path = self.projects().get(key)
        if not path:
            return {}, ""
        return split_profile(path.read_text(encoding="utf-8"))

    def brand_for(self, project: str) -> dict | None:
        """{'mark_url', 'accent', 'family'} for a project whose profile has a `brand:` block, else None.
        mark_url is None when the mark file is missing; the block still counts as present."""
        meta, _ = self.project_profile(project)
        b = meta.get("brand")
        if isinstance(b, str):
            try:
                b = yaml.safe_load(b)
            except yaml.YAMLError:
                b = None
        if not isinstance(b, dict):
            return None
        mark = str(b.get("mark") or "").replace("\\", "/")
        url = None
        if mark and (self.home / mark).is_file():
            root = "assets/brand/"
            url = "/brand/" + (mark[len(root):] if mark.startswith(root) else mark)
        return {"mark_url": url, "mark": mark, "accent": str(b.get("accent") or ""), "family": str(b.get("family") or "")}

    def project_title(self, key: str) -> str:
        meta, body = self.project_profile(key)
        for line in body.splitlines():
            if line.startswith("# "):
                return line[2:].strip()
        if meta.get("name"):
            return str(meta["name"])
        return key

    def key_for(self, project: str) -> str:
        """The ticket-id prefix for a project: its profile's `prefix:` frontmatter field, else the key uppercased."""
        meta, _ = self.project_profile(project)
        val = str(meta.get("prefix") or "").strip()
        return val.upper() if val else project.upper()

    def aliases_for(self, project: str) -> list[str]:
        """Old keys and old prefixes a project answers to (`aliases:` in its profile)."""
        meta, _ = self.project_profile(project)
        v = meta.get("aliases")
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, list):
            return []
        return [str(x).strip() for x in v if str(x).strip()]

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

    def tickets(self) -> list[Ticket]:
        out = []
        for p in self.ticket_paths():
            out.append(Ticket.load(p))
        out.sort(key=lambda t: t.id)
        return out

    def by_id(self) -> dict[str, Ticket]:
        return {t.id: t for t in self.tickets()}

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

    def create(self, title: str, project: str, type_: str = "slice", **kw) -> Ticket:
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

    def sync_before(self) -> None:
        if self.config["git"].get("sync"):
            self._git("pull", "--rebase", "--autostash")

    def sync_after(self, message: str) -> None:
        if self.config["git"].get("sync"):
            self._git("add", "-A")
            self._git("commit", "-m", message)
            self._git("push")
