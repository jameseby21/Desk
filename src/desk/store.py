"""The ticket store: a directory with desk.toml, tickets/, projects/, intake/."""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
import subprocess
from pathlib import Path

from .config import DEFAULT_TOML, CONFIG_NAME, load_config
from .ticket import SIZES, Ticket, new_ticket, slugify

_ID = re.compile(r"^(?P<key>[A-Z0-9][A-Z0-9-]*)-(?P<num>\d{4,})$")


def id_parts(ticket_id: str) -> tuple[str, int] | None:
    m = _ID.match(ticket_id or "")
    if not m:
        return None
    return m.group("key"), int(m.group("num"))


class Store:
    def __init__(self, home: Path):
        self.home = Path(home)
        self.config = load_config(self.home)
        self.tickets_dir = self.home / "tickets"
        self.projects_dir = self.home / "projects"
        self.intake_dir = self.home / "intake"
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

    def project_title(self, key: str) -> str:
        p = self.projects().get(key)
        if not p:
            return key
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                return line[2:].strip()
        return key

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

    def key_for(self, project: str) -> str:
        return project.upper()

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
