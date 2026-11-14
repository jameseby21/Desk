"""The work hierarchy as data (DESK-0181, DESK-0251): company > project > track > ticket > subticket.

desk.toml holds the levels and the companies (key -> the name Desk shows):

    [hierarchy]
    levels = ["company", "project", "track", "ticket", "subticket"]

    [hierarchy.companies]
    jebylabs = "jebyLabs"

A project profile names its company by key (`company: jebylabs`); a ticket never names a company, only its project, so renaming a
company is one line in desk.toml (the shown name) or desk.toml plus the profiles that name it (the key), and no ticket changes.

`drew migrate hierarchy` writes that structure into a store and takes it back out: `company:` in each profile from a map
(tracks/companies.yaml), the [hierarchy] block in desk.toml, and each ticket's `track:` mapped to the vocabulary
(tracks/vocabulary.yaml) with the old text kept verbatim as `legacy_track:`. `--undo` puts every byte back. Every edit is a line edit
on the file's own bytes, so line endings, quoting and the order of the other lines never change."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import yamlio
from .ticket import canon_type

LEVELS = ("company", "project", "track", "ticket", "subticket")
FROM_TYPE = "from_type"
COMPANIES_PATH = Path("tracks") / "companies.yaml"
VOCABULARY_PATH = Path("tracks") / "vocabulary.yaml"
BEGIN = "# >>> hierarchy (DESK-0181): written by `drew migrate hierarchy --apply`; `--undo` removes this block. A rename is one line."
END = "# <<< hierarchy"
_KEY = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_PLAIN = re.compile(r"^[A-Za-z][A-Za-z0-9 ._()-]*$")


class HierarchyError(Exception):
    pass


# -- reading the hierarchy from desk.toml and the profiles ------------------------------------------------------------------------

def _table(store) -> dict:
    h = store.config.get("hierarchy")
    return h if isinstance(h, dict) else {}


def levels(store) -> list[str]:
    """The level words, broadest first; the default five when desk.toml does not say."""
    got = _table(store).get("levels")
    return [str(x) for x in got] if isinstance(got, list) and got else list(LEVELS)


def company_names(store) -> dict[str, str]:
    """{company key: the name Desk shows}, in the order desk.toml lists them."""
    got = _table(store).get("companies")
    return {str(k): str(v) for k, v in got.items()} if isinstance(got, dict) else {}


def company_key(store, project: str) -> str:
    """The `company:` key in the project's profile, or ''."""
    return str(store.project_profile(project)[0].get("company") or "").strip()


def profile_errors(store) -> list[str]:
    """A profile whose `company:` names a key desk.toml does not declare (the only way a company reference can dangle)."""
    names = company_names(store)
    out = []
    for key in store.projects():
        ck = company_key(store, key)
        if ck and ck not in names:
            out.append(f"projects/{key}.md: company `{ck}` is not declared in desk.toml [hierarchy.companies]")
    return out


# -- the owner-editable inputs of the migration -----------------------------------------------------------------------------------

@dataclass
class CompanyMap:
    names: dict[str, str]
    projects: dict[str, str]
    default: str


@dataclass
class Vocabulary:
    tracks: list[str]
    map: dict[str, str]
    from_type: dict[str, str] = field(default_factory=dict)

    def target(self, old: str, type_: str) -> str:
        new = self.map.get(old, "")
        return self.from_type.get(canon_type(type_), self.from_type.get(type_, "")) if new == FROM_TYPE else new


def _load(path: Path) -> dict:
    if not path.exists():
        raise HierarchyError(f"No {path}. Copy it from the Desk repo's docs/hierarchy/ or pass the path.")
    try:
        data = yamlio.load(path.read_text(encoding="utf-8"))
    except yamlio.YAMLError as e:
        raise HierarchyError(f"{path}: not valid YAML ({e})") from e
    if not isinstance(data, dict):
        raise HierarchyError(f"{path}: expected a mapping at the top")
    return data


def load_companies(path: Path) -> CompanyMap:
    data = _load(Path(path))
    names = {str(k): str(v) for k, v in (data.get("companies") or {}).items()}
    projects = {str(k): str(v) for k, v in (data.get("projects") or {}).items()}
    default = str(data.get("default") or "")
    bad = [k for k in names if not _KEY.match(k)]
    if bad:
        raise HierarchyError(f"{path}: company keys must be lower-case letters, digits and hyphens: {', '.join(bad)}")
    unknown = sorted({v for v in projects.values() if v not in names} | ({default} if default and default not in names else set()))
    if unknown:
        raise HierarchyError(f"{path}: these companies are used but not listed under companies: {', '.join(unknown)}")
    return CompanyMap(names, projects, default)


def load_vocabulary(path: Path) -> Vocabulary:
    data = _load(Path(path))
    tracks = [str(x) for x in data.get("tracks") or []]
    vmap = {str(k): str(v) for k, v in (data.get("map") or {}).items()}
    ft = {str(k): str(v) for k, v in (data.get("from_type") or {}).items()}
    bad = sorted({v for v in vmap.values() if v != FROM_TYPE and v not in tracks} | {v for v in ft.values() if v not in tracks})
    if bad:
        raise HierarchyError(f"{path}: these targets are not in tracks: {', '.join(bad)}")
    return Vocabulary(tracks, vmap, ft)


# -- line editing on the file's own bytes -----------------------------------------------------------------------------------------

def _lines(raw: str) -> list[str]:
    return raw.splitlines(keepends=True)


def _eol(raw: str) -> str:
    return "\r\n" if "\r\n" in raw else "\n"


def _fm_span(lines: list[str]) -> tuple[int, int] | None:
    """(first line inside, closing line) of the frontmatter, or None."""
    if not lines or lines[0].rstrip("\r\n") != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == "---":
            return 1, i
    return None


def _field(lines: list[str], span: tuple[int, int], name: str) -> int | None:
    for i in range(*span):
        if lines[i].startswith(name + ":"):
            return i
    return None


def _scalar(rest: str) -> str:
    """The value of a `key:<rest>` line as YAML reads it."""
    try:
        v = yamlio.load("v:" + rest)
    except yamlio.YAMLError:
        return rest.strip()
    return str((v or {}).get("v") or "") if isinstance(v, dict) else rest.strip()


def _text(value: str) -> str:
    return value if _PLAIN.match(value) else '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def add_company(raw: str, key: str) -> str:
    """`company: <key>` right after `area:` (or at the end of the frontmatter); a profile with none gets a frontmatter of one line."""
    eol, lines = _eol(raw), _lines(raw)
    span = _fm_span(lines)
    if span is None:
        return f"---{eol}company: {key}{eol}---{eol}" + raw
    if _field(lines, span, "company") is not None:
        return raw
    at = _field(lines, span, "area")
    at = at + 1 if at is not None else span[1]
    lines.insert(at, f"company: {key}{eol}")
    return "".join(lines)


def drop_company(raw: str) -> str:
    eol, lines = _eol(raw), _lines(raw)
    span = _fm_span(lines)
    if span is None:
        return raw
    if span[1] == 2 and lines[1].startswith("company:"):  # the one-line frontmatter add_company made: remove it whole
        return "".join(lines[3:])
    return "".join(x for i, x in enumerate(lines) if not (span[0] <= i < span[1] and x.startswith("company:")))


def retrack(raw: str, new: str) -> str:
    """`track: <new>` and, right after it, `legacy_track:` holding the old text byte for byte."""
    lines = _lines(raw)
    span = _fm_span(lines)
    i = _field(lines, span, "track") if span else None
    if i is None or _field(lines, span, "legacy_track") is not None:
        return raw
    body = lines[i][len("track:"):]
    rest, end = body.rstrip("\r\n"), body[len(body.rstrip("\r\n")):]
    lines[i:i + 1] = [f"track: {_text(new)}{end}", f"legacy_track:{rest}{end}"]
    return "".join(lines)


def untrack(raw: str) -> str:
    """Put `legacy_track:`'s text back on `track:` and remove the legacy line."""
    lines = _lines(raw)
    span = _fm_span(lines)
    j = _field(lines, span, "legacy_track") if span else None
    if j is None:
        return raw
    body = lines[j][len("legacy_track:"):]
    rest, end = body.rstrip("\r\n"), body[len(body.rstrip("\r\n")):]
    i = _field(lines, span, "track")
    if i is None:
        lines[j] = f"track:{rest}{end}"
    else:
        lines[i] = f"track:{rest}{lines[i][len(lines[i].rstrip(chr(13) + chr(10))):]}"
        del lines[j]
    return "".join(lines)


def toml_block(names: dict[str, str]) -> str:
    body = [BEGIN, "[hierarchy]", "levels = [" + ", ".join(f'"{x}"' for x in LEVELS) + "]", "", "[hierarchy.companies]"]
    body += [f'{k} = "{v}"' for k, v in names.items()] + [END]
    return "\n".join(body) + "\n"


def add_toml(raw: str, names: dict[str, str]) -> str:
    eol = _eol(raw)
    return raw + eol + toml_block(names).replace("\n", eol)


def drop_toml(raw: str) -> str:
    eol = _eol(raw)
    start = raw.find(eol + BEGIN)
    if start < 0:
        return raw
    stop = raw.find(END, start)
    if stop < 0:
        return raw
    stop = raw.find(eol, stop)
    return raw[:start] + (raw[stop + len(eol):] if stop >= 0 else "")


# -- the plan, apply and undo -----------------------------------------------------------------------------------------------------

@dataclass
class Plan:
    profiles: list[tuple[str, str]]  # (project key, company key)
    tickets: list[tuple[object, str, str]]  # (ticket, old track, new track)
    unmapped: dict[str, int]  # old track values with no row in the map, and how many tickets carry each
    toml: bool  # desk.toml gets the [hierarchy] block
    kept: int  # tickets left alone (no track, already in the vocabulary, or already migrated)
    names: dict[str, str] = field(default_factory=dict)


def plan(store, cmap: CompanyMap, vocab: Vocabulary) -> Plan:
    profiles = []
    for key in store.projects():
        if not company_key(store, key):
            ck = cmap.projects.get(key, cmap.default)
            if ck:
                profiles.append((key, ck))
    tickets, unmapped, kept = [], {}, 0
    for t in store.tickets():
        old = str(t.meta.get("track") or "").strip()
        if not old or "legacy_track" in t.meta or old in vocab.tracks:
            kept += 1
            continue
        new = vocab.target(old, str(t.meta.get("type") or ""))
        if not new:
            unmapped[old] = unmapped.get(old, 0) + 1
            kept += 1
            continue
        tickets.append((t, old, new))
    return Plan(profiles, tickets, unmapped, not _table(store), kept, cmap.names)


def apply(store, p: Plan) -> tuple[int, int, bool]:
    """(profiles written, tickets written, desk.toml written)."""
    np_ = nt = 0
    for key, ck in p.profiles:
        path = store.projects_dir / f"{key}.md"
        raw = path.read_bytes().decode("utf-8")
        new = add_company(raw, ck)
        if new != raw:
            path.write_bytes(new.encode("utf-8"))
            np_ += 1
    for t, _old, new_track in p.tickets:
        raw = t.path.read_bytes().decode("utf-8")
        new = retrack(raw, new_track)
        if new != raw:
            t.path.write_bytes(new.encode("utf-8"))
            nt += 1
    wrote_toml = False
    if p.toml:
        path = store.home / "desk.toml"
        path.write_bytes(add_toml(path.read_bytes().decode("utf-8"), p.names).encode("utf-8"))
        wrote_toml = True
    return np_, nt, wrote_toml


def undo_plan(store) -> tuple[list[Path], list[Path], bool]:
    """(profiles with a company: line, tickets with legacy_track:, desk.toml holds the migration's block)."""
    profiles = [p for p in store.projects().values() if drop_company(p.read_bytes().decode("utf-8")) != p.read_bytes().decode("utf-8")]
    tickets = [t.path for t in store.tickets() if t.path is not None and "legacy_track" in t.meta]
    toml = store.home / "desk.toml"
    raw = toml.read_bytes().decode("utf-8") if toml.exists() else ""
    return profiles, tickets, drop_toml(raw) != raw


def undo(store) -> tuple[int, int, bool]:
    profiles, tickets, has_toml = undo_plan(store)
    for path, fn in [(p, drop_company) for p in profiles] + [(p, untrack) for p in tickets]:
        raw = path.read_bytes().decode("utf-8")
        path.write_bytes(fn(raw).encode("utf-8"))
    if has_toml:
        path = store.home / "desk.toml"
        path.write_bytes(drop_toml(path.read_bytes().decode("utf-8")).encode("utf-8"))
    return len(profiles), len(tickets), has_toml
