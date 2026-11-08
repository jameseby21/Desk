"""Store documents shown inside the app: path-safe lookup, the glossary, "Mentioned in" backlinks, owning-doc links
(read from a project's own docs/doc-ownership.yaml) and dead-link checks. Nothing here copies or generates a file:
every document is read from where it lives, at request time."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

from . import yamlio

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

KNOWN_REPOS = {"jre": "jeby Real Estate", "musicalgames": "Games/MusicalGames", "algo": "Algo", "temujin": "Temujin",
               "desk": "desk", "standards": "standards", "jeby-us": "jeby-us"}


def repo_dir(store, project: str) -> str:
    """The project's repo folder inside the store: `repo:` in its profile, else the first path of a `| Repo |` table row
    (cut at ` →` or `(`; its first segment is tried too), else a known folder for the project. Empty when none exists."""
    meta, body = store.project_profile(project)
    raw = str(meta.get("repo") or "")
    if not raw:
        m = re.search(r"^\|\s*Repo\s*\|\s*([^|\n]+)", body, re.M)
        raw = m.group(1) if m else ""
    raw = raw.split("→")[0].split("->")[0].split("(")[0].strip().strip("`").strip("/").strip()
    cands = [raw, raw.split("/")[0].strip()] if raw else []
    if project in KNOWN_REPOS:
        cands.append(KNOWN_REPOS[project])
    for c in cands:
        if c and ".." not in c and (store.home / c).is_dir():
            return c
    return ""


def resolve_for(store, project: str, path: str) -> tuple[str | None, list[str]]:
    """Resolve a doc path written in a ticket: first the store root, then the project's repo folder. Returns
    (store-relative path with any #anchor kept, or None) and the store-relative paths tried (anchor dropped)."""
    base, sep, anchor = (path or "").strip().replace("\\", "/").partition("#")
    base = base.strip("/")
    tried = [base]
    repo = repo_dir(store, project) if project else ""
    if repo and not base.startswith(repo + "/"):
        tried.append(f"{repo}/{base}")
    for c in tried:
        if resolve_doc(store.home, c):
            return c + sep + anchor, tried
    return None, tried


DOD_DIR = "standards/definition-of-done"
# Where a project that keeps its own Definition of Done is expected to have it, when its profile does not say (`dod_doc:`).
DOD_CONVENTIONS = ("docs/guides/DEFINITION_OF_DONE.md", "docs/DEFINITION_OF_DONE.md")


def dod_sources(store, t: Ticket) -> dict:
    """Where "Done means" comes from, by reference: nothing is copied into the ticket.
    {"kind": "project" | "standards", "items": [{"label", "path" (store-relative, or None when missing), "tried"}], "note"}.
    A project profile's `dod_doc:` (path in its repo, or the store) overrides the shared modules; without it a repo file at a
    conventional path (docs/guides/DEFINITION_OF_DONE.md) is used; otherwise each module in the ticket's `dod:` comes from
    standards/definition-of-done/<module>.md."""
    meta, _ = store.project_profile(t.project)
    declared = str(meta.get("dod_doc") or "").strip()
    note = ""
    if declared:
        res, tried = resolve_for(store, t.project, declared)
        if res:
            return {"kind": "project", "items": [{"label": f"{store.project_title(t.project)} Definition of Done", "path": res.split("#", 1)[0], "tried": tried}], "note": ""}
        note = f"Profile says dod_doc: {declared}, but no such file (tried {', '.join(tried)}). Showing the shared modules."
    else:
        for conv in DOD_CONVENTIONS:
            res, tried = resolve_for(store, t.project, conv) if repo_dir(store, t.project) else (None, [])
            if res:
                return {"kind": "project", "items": [{"label": f"{store.project_title(t.project)} Definition of Done", "path": res, "tried": tried}], "note": ""}
    items = []
    for mod in (t.meta.get("dod") or []):
        rel = f"{DOD_DIR}/{mod}.md"
        items.append({"label": str(mod), "path": rel if resolve_doc(store.home, rel) else None, "tried": [rel]})
    return {"kind": "standards", "items": items, "note": note}


def split_sources(src: str) -> list[str]:
    """A `source:` value may hold several paths separated by `;` or `,`."""
    return [p.strip() for p in re.split(r"[;,]", str(src or "")) if p.strip()]


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
        data = yamlio.load(f.read_text(encoding="utf-8"))
    except yamlio.YAMLError:
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


def dead_links(store, t: Ticket) -> list[tuple[str, list[str]]]:
    """(path, tried) for doc paths a ticket links to (in its body or `source:`) that resolve nowhere: not under the store
    root, not under the project's repo folder. Only paths that start in a folder of the store count, and placeholders such
    as `projects/<key>.md` are ignored."""
    tops = {p.name for p in store.home.iterdir() if p.is_dir() and not p.name.startswith(".")} | set(DOC_DIRS)
    text = _FENCE.sub("", body_text(t))
    cands = [m.group(0) for m in DOC_RE.finditer(text)] + [m.group(1) for m in _MDLINK.finditer(text)]
    cands = [c for c in cands if c.split("/", 1)[0] in tops]
    for src in split_sources(t.meta.get("source")):  # an explicit source: is checked whatever its first folder
        if src.split("#", 1)[0].lower().endswith(".md") and "/" in src:
            cands.append(src)
    dead, seen = [], set()
    for c in cands:
        if c.startswith(("http://", "https://", "/")) or any(ch in c for ch in "<>*{}$"):
            continue
        res, tried = resolve_for(store, t.project, c)
        if res is None and c not in seen:
            seen.add(c)
            dead.append((c, tried))
    return dead
