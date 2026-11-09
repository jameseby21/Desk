"""People (DESK-0044): who owners and assignees are, shown as a name and a colour.

`people.yaml` in the store lists them. A ticket stores the key (`chaz`, `main`, `code:jre`); the UI shows the display name (`main` is
Patch) with a colour. An unknown value still renders, as written. When the file is missing the built-in team below is used, so a
fresh store works; `drew people init` writes it out to edit.

External and private people (a lawyer, a doctor) live in `~/.claude/jeby-private/people.yaml` (or the file named by
DESK_PRIVATE_PEOPLE). It is read when it exists and never copied into the store or a repository: tickets hold only the key, and the
display name appears only on a machine that has that file. Entries of kind `external` are listed under "People outside the team"."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import yamlio

KINDS = ("person", "coworker", "agent", "code", "external")
GROUPS = [("People", ("person", "agent", "coworker")), ("Code sessions", ("code",)), ("People outside the team", ("external",))]
NEUTRAL = "#8A877D"
PALETTE = ["#C8F03C", "#7AA2F7", "#F7A072", "#B28DFF", "#4FD1C5", "#F6C453", "#FF8FB1", "#9AD066", "#6EC6FF", "#E5989B"]


@dataclass
class Person:
    key: str
    name: str
    kind: str = "person"
    role: str = ""
    colour: str = ""
    aliases: list[str] = field(default_factory=list)
    os_users: list[str] = field(default_factory=list)  # identity (DESK-0144): OS or git user names that are this person
    private: bool = False  # came from the private file

    @property
    def label(self) -> str:
        return f"{self.name} · {self.role}" if self.role else self.name


def _seed() -> list[Person]:
    out = [
        Person("james", "James", "person", "owner", PALETTE[0]),
        Person("main", "Patch", "agent", "main Claude Code session", PALETTE[1], ["patch"]),
        Person("chaz", "Chaz", "coworker", "Creative Director", PALETTE[2]),
        Person("em", "Em", "coworker", "Research", PALETTE[3]),
        Person("bri", "Bri", "coworker", "Assistant", PALETTE[4]),
        Person("joe", "Joe", "coworker", "Boring Stuff", PALETTE[5]),
        Person("lily", "Lily", "coworker", "Tech, Product, IT", PALETTE[6]),
        Person("bigd", "Big D", "coworker", "Security", PALETTE[7], ["big d", "big-d"]),
    ]
    for i, proj in enumerate(("jre", "plantriloquist", "jeby-us", "musicalgames", "algo", "appforge", "cosmology")):
        out.append(Person(f"code:{proj}", f"{proj} session", "code", "Claude Code in the repo", PALETTE[(i + 3) % len(PALETTE)], [proj]))
    return out


SEED = _seed()


def private_path() -> Path:
    env = os.environ.get("DESK_PRIVATE_PEOPLE")
    return Path(env).expanduser() if env else Path.home() / ".claude" / "jeby-private" / "people.yaml"


def _strs(v) -> list[str]:
    if v in (None, ""):
        return []
    return [str(x).strip() for x in (v if isinstance(v, list) else [v]) if str(x).strip()]


def _read(path: Path, private: bool = False) -> list[Person]:
    try:
        data = yamlio.load(path.read_text(encoding="utf-8"))
    except (OSError, yamlio.YAMLError):
        return []
    rows = data.get("people") if isinstance(data, dict) else data
    if isinstance(rows, dict):  # key: {name: ...} form
        rows = [{"key": k, **(v if isinstance(v, dict) else {"name": v})} for k, v in rows.items()]
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict) or not str(r.get("key") or "").strip():
            continue
        key = str(r["key"]).strip()
        kind = str(r.get("kind") or ("external" if private else "person")).strip().lower()
        out.append(Person(key, str(r.get("name") or key).strip(), kind if kind in KINDS else "person", str(r.get("role") or "").strip(),
                          str(r.get("colour") or r.get("color") or "").strip(), _strs(r.get("aliases")), _strs(r.get("os_users")), private))
    return out


class People:
    def __init__(self, people: list[Person]):
        self.list = people
        self.by_key = {p.key.lower(): p for p in people}
        self._names: dict[str, Person] = {}
        for p in people:  # a display name or alias finds the key; the key itself always wins
            for n in [p.name, *p.aliases]:
                self._names.setdefault(n.strip().lower(), p)

    def get(self, value: str) -> Person | None:
        v = " ".join(str(value or "").split()).lower()
        return self.by_key.get(v) or self._names.get(v)

    def resolve(self, value: str) -> str:
        """The key for a key, a display name or an alias (`Patch` -> `main`, `Big D` -> `bigd`); the text as written when unknown."""
        p = self.get(value)
        return p.key if p else " ".join(str(value or "").split())

    def display(self, value: str) -> str:
        p = self.get(value)
        return p.name if p else str(value or "")

    def colour(self, value: str) -> str:
        p = self.get(value)
        if p and p.colour:
            return p.colour
        return NEUTRAL

    def groups(self) -> list[tuple[str, list[Person]]]:
        """The dropdown: People, Code sessions, People outside the team, each in the order of people.yaml (the built-in team lists James, Patch, then the coworkers)."""
        out = []
        for label, kinds in GROUPS:
            ps = [p for p in self.list if p.kind in kinds]  # the order of people.yaml, which is the order James reads them in
            if ps:
                out.append((label, ps))
        return out

    def external_keys(self) -> set[str]:
        return {p.key for p in self.list if p.kind == "external"}


_CACHE: dict = {}


def _mtime(p: Path) -> tuple:
    """(mtime_ns, size): a file rewritten within one clock tick still changes size."""
    try:
        st = p.stat()
        return st.st_mtime_ns, st.st_size
    except OSError:
        return 0, 0


def load(home: Path | None) -> People:
    """The store's people.yaml (the built-in team when there is none) plus the private file's people. Cached by file times."""
    store_file = Path(home) / "people.yaml" if home else None
    priv = private_path()
    sig = (str(store_file), _mtime(store_file) if store_file else 0, str(priv), _mtime(priv))
    hit = _CACHE.get(sig[:1] + sig[2:3])
    if hit and hit[0] == sig:
        return hit[1]
    mine = _read(store_file) if store_file and store_file.is_file() else []
    people = mine or [Person(**p.__dict__) for p in SEED]
    have = {p.key.lower() for p in people}
    people += [p for p in _read(priv, private=True) if p.key.lower() not in have] if priv.is_file() else []
    out = People(people)
    _CACHE[sig[:1] + sig[2:3]] = (sig, out)
    return out


def _write_people(home: Path, rows: list[dict]) -> Path:
    p = Path(home) / "people.yaml"
    head = "# People: who owners and assignees are (DESK-0044). A ticket stores the key; the UI shows the name and colour.\n# os_users: the operating-system user names that are this person (DESK-0144); written when someone is named on first run.\n"
    p.write_text(head + yamlio.dump({"people": rows}, sort_keys=False, allow_unicode=True, default_flow_style=False, width=1000), encoding="utf-8", newline="\n")
    return p


def _rows(home: Path) -> list[dict]:
    """The people.yaml rows as plain dicts; the built-in team when the store has no file yet."""
    f = Path(home) / "people.yaml"
    people = _read(f) if f.is_file() else [Person(**p.__dict__) for p in SEED]
    out = []
    for p in people:
        r = {"key": p.key, "name": p.name, "kind": p.kind}
        if p.role:
            r["role"] = p.role
        r["colour"] = p.colour
        if p.aliases:
            r["aliases"] = list(p.aliases)
        if p.os_users:
            r["os_users"] = list(p.os_users)
        out.append(r)
    return out


def link_os_user(home: Path, key: str, user: str) -> Path:
    """Record that the operating-system user `user` is the person `key` (first-run naming). Written to the store's people.yaml."""
    rows = _rows(home)
    row = next((r for r in rows if r["key"].lower() == key.lower()), None)
    if row is None:
        raise KeyError(key)
    for r in rows:  # one user is one person
        if r is not row and r.get("os_users"):
            r["os_users"] = [u for u in r["os_users"] if u.lower() != user.lower()]
            if not r["os_users"]:
                r.pop("os_users")
    row["os_users"] = sorted({*row.get("os_users", []), user})
    return _write_people(home, rows)


def add_person(home: Path, key: str, name: str, kind: str = "person", os_user: str = "") -> Path:
    key = re.sub(r"[^a-z0-9:_-]+", "-", key.lower()).strip("-")
    if not key or not name.strip():
        raise ValueError("A person needs a key and a display name.")
    rows = _rows(home)
    if any(r["key"].lower() == key for r in rows):
        raise ValueError(f"{key} is already in people.yaml.")
    row = {"key": key, "name": name.strip(), "kind": kind if kind in KINDS else "person", "colour": PALETTE[len(rows) % len(PALETTE)]}
    if os_user:
        row["os_users"] = [os_user]
    return _write_people(home, rows + [row])


def seed_text() -> str:
    lines = ["# People: who owners and assignees are (DESK-0044). A ticket stores the key; the UI shows the name and colour.",
             "# kind: person, coworker, agent, code (a Claude Code session in a repo) or external. `main` is shown as Patch.",
             "# External people (a lawyer, a doctor) belong in ~/.claude/jeby-private/people.yaml, not here.", "people:"]
    for p in SEED:
        lines.append(f"  - key: {p.key}")
        lines.append(f"    name: {p.name}")
        lines.append(f"    kind: {p.kind}")
        if p.role:
            lines.append(f"    role: {p.role}")
        lines.append(f'    colour: "{p.colour}"')
        if p.aliases:
            lines.append(f"    aliases: [{', '.join(p.aliases)}]")
    return "\n".join(lines) + "\n"


def write_seed(home: Path, force: bool = False) -> Path:
    p = Path(home) / "people.yaml"
    if p.exists() and not force:
        raise FileExistsError(f"{p} already exists; edit it, or pass --force to replace it with the built-in team")
    p.write_text(seed_text(), encoding="utf-8", newline="\n")
    return p
