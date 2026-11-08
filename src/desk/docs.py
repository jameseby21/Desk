"""Store documents shown inside the app: path-safe lookup, the glossary, "Mentioned in" backlinks, owning-doc links
(read from a project's own docs/doc-ownership.yaml) and dead-link checks. Nothing here copies or generates a file:
every document is read from where it lives, at request time."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

import yaml

from .ticket import Ticket

DOC_DIRS = ("docs", "projects", "intake", "standards", "tickets", "tracks")
PRIVATE_NAMES = {"private_context.md"}
PRIVATE_PARTS = {"jeby-private", "node_modules"}
# A store-relative Markdown path in running text (no spaces), optionally with a #anchor. It only becomes a link when the
# file exists, so the pattern can be generic; the clean left edge keeps it from matching inside URLs or longer paths.
DOC_RE = re.compile(r"(?<![\w/.:~-])(?:[A-Za-z0-9_-]+/)+[^\s)`<>]*?\.md(?:#[-\w]+)?(?![\w/])")
# The same, for a whole markdown href or code span (folder names may hold spaces, like `jeby Real Estate/docs/README.md`).
DOC_FULL = re.compile(r"[\w. -]+(?:/[\w. -]+)+\.md(?:#[-\w]+)?")
ID_IN_TEXT = r"(?<![A-Za-z0-9-]){}(?!\d)"
CODE_PATH = re.compile(r"(?<![\w/.:~-])((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.(?:py|ts|tsx|js|sql|html|css|yaml|yml|toml|sh|ps1))(?![\w/])")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def resolve_doc(home: Path, rel: str) -> Path | None:
    """The Markdown file `rel` inside the store (or a submodule folder of it), or None. Any top-level folder of the
    store is reachable; `..`, absolute paths, hidden folders, symlinks out of the store and private files are refused."""
    rel = (rel or "").replace("\\", "/").strip("/")
    if not rel or "\x00" in rel or not rel.lower().endswith(".md"):
        return None
    parts = rel.split("/")
    if any(p in ("", ".", "..") or p.startswith(".") or ":" in p for p in parts):
        return None
    if parts[-1].lower() in PRIVATE_NAMES or any(p.lower() in PRIVATE_PARTS for p in parts):
        return None
    root = Path(home).resolve()
    try:
        target = (root / rel).resolve()
        target.relative_to(root)
    except (ValueError, OSError):
        return None
    return target if target.is_file() else None


def vscode_url(path: Path, line: int | None = None) -> str:
    """Open a file in VS Code: vscode://file/<absolute path>, with :line when known."""
    return "vscode://file/" + quote(Path(path).resolve().as_posix(), safe="/:") + (f":{line}" if line else "")


def glossary(home: Path) -> dict[str, tuple]:
    """{lowercase term: (Term, plain-words definition, target)} from glossary.md: one `## Term` heading per entry.

    A project's own term (lot, provenance, vault) is not redefined here: an entry whose body points at the project's
    document ("See jeby Real Estate/docs/x.md#lot" or a markdown link) has that document as its target, so the popover
    and `[[Term]]` go to the owner's file. Other entries target the entry here."""
    p = Path(home) / "glossary.md"
    if not p.is_file():
        return {}
    out: dict[str, tuple] = {}
    term, buf = None, []
    link = re.compile(r"\[([^\]]*)\]\(([^)]+?\.md(?:#[-\w]+)?)\)")

    def flush():
        if not term:
            return
        text = " ".join(buf)
        target = ""
        m = link.search(text)
        if m and resolve_doc(home, m.group(2).split("#", 1)[0]):
            target = "/doc/" + quote(m.group(2), safe="/#")
        text = link.sub(lambda mm: mm.group(1), text)
        out[term.lower()] = (term, " ".join(text.split()), target)

    for line in p.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            flush()
            term, buf = line[3:].strip(), []
        elif term and not line.startswith("#"):
            buf.append(line)
    flush()
    return out


def body_text(t: Ticket) -> str:
    return "\n".join(t.section(name) for name in t.sections)


def mentioned_in(tickets: list[Ticket], *, ticket_id: str = "", path: str = "") -> list[Ticket]:
    """Tickets whose bodies mention a ticket id or a store-relative path (the ticket itself is left out)."""
    if ticket_id:
        pat = re.compile(ID_IN_TEXT.format(re.escape(ticket_id)))
    elif path:
        pat = re.compile(r"(?<![\w/.-])" + re.escape(path) + r"(?![\w/])")
    else:
        return []
    out = []
    for t in tickets:
        if t.id == ticket_id:
            continue
        if pat.search(body_text(t)):
            out.append(t)
    return sorted(out, key=lambda t: t.id)


# -- a project's own indexes (read, never generated) ----------------------------------------------

def repo_dir(store, project: str) -> str:
    """The project's repo folder inside the store: `repo:` in its profile, else the first column of a `| Repo |` table row.
    Empty when the profile names none or the folder is not in the store."""
    meta, body = store.project_profile(project)
    raw = str(meta.get("repo") or "")
    if not raw:
        m = re.search(r"^\|\s*Repo\s*\|\s*([^|\n]+)", body, re.M)
        raw = m.group(1) if m else ""
    raw = raw.split("→")[0].split("->")[0].strip().strip("`").strip("/")
    return raw if raw and (store.home / raw).is_dir() and ".." not in raw else ""


def glob_re(glob: str) -> re.Pattern:
    """A doc-ownership glob (`**`, `*`, `?`, `{a,b}`) as a regex over repo-relative posix paths."""
    out, i = [], 0
    while i < len(glob):
        c = glob[i]
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if glob.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "{":
            j = glob.find("}", i)
            if j > i:
                out.append("(?:" + "|".join(glob_re(a).pattern for a in glob[i + 1:j].split(",")) + ")")
                i = j + 1
                continue
            out.append(re.escape(c))
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("".join(out) + r"\Z")


def ownership_rules(store, project: str) -> tuple[str, list[dict]]:
    """(repo folder, rules) from the project's own docs/doc-ownership.yaml, or ("", [])."""
    repo = repo_dir(store, project)
    if not repo:
        return "", []
    f = store.home / repo / "docs" / "doc-ownership.yaml"
    if not f.is_file():
        return repo, []
    try:
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return repo, []
    rules = []
    for r in data if isinstance(data, list) else []:
        if isinstance(r, dict) and r.get("glob") and r.get("doc"):
            try:
                rules.append({"re": glob_re(str(r["glob"])), "doc": str(r["doc"]), "primary": bool(r.get("primary"))})
            except re.error:
                continue
    return repo, rules


def owning_doc(rules: list[dict], rel: str) -> str:
    """The document that owns a code path: the last matching primary rule, else the last matching rule."""
    hits = [r for r in rules if r["re"].match(rel)]
    prim = [r for r in hits if r["primary"]]
    pick = (prim or hits)[-1] if hits else None
    return pick["doc"] if pick else ""


def owning_docs(store, t: Ticket) -> list[dict]:
    """For a ticket: the owning doc of every `docs_to_update` path and of every code path its body mentions.
    Items: {path, doc (store-relative, may carry #anchor), exists}. Uses the project's own map; builds nothing."""
    repo, rules = ownership_rules(store, t.project)
    if not repo:
        return []
    paths = [str(p) for p in (t.meta.get("docs_to_update") or [])]
    paths += [m.group(1) for m in CODE_PATH.finditer(body_text(t))]
    out, seen = [], set()
    for p in paths:
        if p in seen:
            continue
        seen.add(p)
        base = p.split("#", 1)[0]
        if base.lower().endswith(".md"):
            doc = p
        else:
            doc = owning_doc(rules, base)
        if not doc:
            continue
        full = f"{repo}/{doc}"
        out.append({"path": p, "doc": full, "exists": resolve_doc(store.home, full.split("#", 1)[0]) is not None})
        if len(out) >= 15:
            break
    return out


def doc_index(store, project: str) -> str:
    """Store-relative link to a project's own documentation index: its docs/README.md, else README.md#documentation-tree."""
    repo = repo_dir(store, project)
    if not repo:
        return ""
    if resolve_doc(store.home, f"{repo}/docs/README.md"):
        return f"{repo}/docs/README.md"
    if resolve_doc(store.home, f"{repo}/README.md"):
        return f"{repo}/README.md#documentation-tree"
    return ""


# -- dead links (drift) ---------------------------------------------------------------------------

_FENCE = re.compile(r"```.*?```", re.S)
_MDLINK = re.compile(r"\]\(([^)\s]+?\.md(?:#[-\w]+)?)\)")


def dead_links(store, t: Ticket) -> list[str]:
    """Doc paths a ticket links to (in its body or `source:`) whose file does not exist. Only paths that start in a folder
    of the store count, and placeholders such as `projects/<key>.md` are ignored."""
    tops = {p.name for p in store.home.iterdir() if p.is_dir() and not p.name.startswith(".")} | set(DOC_DIRS)
    text = _FENCE.sub("", body_text(t))
    cands = [m.group(0) for m in DOC_RE.finditer(text)] + [m.group(1) for m in _MDLINK.finditer(text)]
    src = str(t.meta.get("source") or "").strip()
    if src.split("#", 1)[0].lower().endswith(".md") and "/" in src:
        cands.append(src)
    dead = []
    for c in cands:
        if c.startswith(("http://", "https://", "/")) or any(ch in c for ch in "<>*{}$"):
            continue
        if c.split("/", 1)[0] not in tops:
            continue
        if resolve_doc(store.home, c.split("#", 1)[0]) is None and c not in dead:
            dead.append(c)
    return dead
