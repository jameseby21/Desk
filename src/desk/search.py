"""Search (DESK-0113): an FTS5 trigram index over the tickets, a small filter grammar, typo tolerance, results grouped by project.

The markdown files stay the source of truth. The index lives in `.desk/search.sqlite` (derived, gitignored) and is refreshed
incrementally: a ticket is re-read only when its file's (mtime, size) changed. A trigram index matches inside words and ids
(`DESK-00`, `0038`, `chedul` all find what they should); a term the index cannot find is retried with its trigrams and
scored by similarity, which is the typo tolerance.

Grammar (any order, free words and filters mixed; `field:"two words"`, `a,b` is either, a leading `-` negates):
  project: status: type: tag: ask: owner: assignee: track: sprint: priority: parent: is:stale|open|closed  before:DATE  after:DATE
`before:` and `after:` compare the ticket's `updated` date. `parent:DESK-0183` finds that main ticket's subtickets, and a subticket's hit
carries `part_of` ("part of DESK-0183: <title>", DESK-0208)."""

from __future__ import annotations

import datetime as dt
import difflib
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .subtickets import LABEL_TITLE, parent_of
from .ticket import CLOSED, PRIORITIES, Ticket

FIELDS = ("project", "status", "type", "tag", "ask", "owner", "assignee", "track", "sprint", "priority", "parent", "is", "before", "after")
BODY_CHARS = 2000
CANDIDATES = 500
PER_GROUP = 12
SNIPPETS = 30  # only the first hits shown get a snippet; the rest are title lines
_TOKEN = re.compile(r'(-?)(\w+):("([^"]*)"|\S*)|"([^"]*)"|(\S+)')
_FILE_ID = re.compile(r"^(.+?-\d{4,})-")


@dataclass
class Query:
    terms: list[str] = field(default_factory=list)
    filters: list[tuple[str, list[str], bool]] = field(default_factory=list)  # (field, any-of values, negated)
    raw: str = ""

    @property
    def empty(self) -> bool:
        return not self.terms and not self.filters


def parse(text: str) -> Query:
    q = Query(raw=text or "")
    for m in _TOKEN.finditer(text or ""):
        neg, key, _val, quoted, phrase, word = m.groups()
        if key and key.lower() in FIELDS:
            value = quoted if quoted is not None else (m.group(3) or "")
            vals = [v.strip() for v in value.split(",") if v.strip()] if quoted is None else [quoted.strip()]
            if vals:
                q.filters.append((key.lower(), vals, bool(neg)))
            continue
        piece = phrase if phrase is not None else (word if word is not None else m.group(0))
        if piece and piece.strip():
            q.terms.append(piece.strip())
    return q


def _fts_quote(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


@dataclass
class Hit:
    id: str
    title: str
    project: str
    status: str
    priority: str
    type: str
    assignee: str
    snippet: str
    fuzzy: bool = False
    part_of: str = ""  # DESK-0208: `part of DESK-0183: <title>` when the hit is a subticket

    def as_json(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Result:
    query: Query
    total: int
    groups: list[tuple[str, int, list[Hit]]]  # (project, count in the candidates, hits shown)
    fuzzy: bool = False
    capped: bool = False

    def as_json(self) -> dict:
        return {"query": self.query.raw, "total": self.total, "fuzzy": self.fuzzy, "capped": self.capped,
                "groups": [{"project": p, "count": n, "hits": [h.as_json() for h in hs]} for p, n, hs in self.groups]}


def ask_value(t: Ticket) -> str:
    from .asks import ask_of

    a = ask_of(t)
    if a:
        return a["kind"]
    return "review" if t.status == "review" else ""


def row_for(t: Ticket, sig: str = "") -> dict:
    m = t.meta
    body = "\n".join(t.section(s) for s in ("Context", "Outcome", "Acceptance", "Comments") if t.section(s))
    return {"id": t.id, "title": str(m.get("title") or ""), "project": t.project, "status": t.status, "type": str(m.get("type") or ""),
            "priority": str(m.get("priority") or "p3"), "owner": str(m.get("owner") or "").lower(),
            "assignee": " ".join(str(m.get("assignee") or "").split()).lower(), "track": str(m.get("track") or "").lower(),
            "sprint": str(m.get("sprint") or "").lower(), "tags": " " + " ".join(x.lower() for x in t.tags) + " ",
            "ask": ask_value(t), "updated": str(m.get("updated") or "")[:10], "sig": sig, "parent": parent_of(t),
            "body": (" ".join(body.split()))[:BODY_CHARS]}


COLS = ["id", "title", "project", "status", "type", "priority", "owner", "assignee", "track", "sprint", "tags", "ask", "updated", "sig", "parent"]  # body lives in the fts table only


class Index:
    """The search index. `path=None` keeps it in memory (tests, the benchmark)."""

    def __init__(self, path: Path | None = None, stale_days: int = 21, resolve=None):
        self.path = path
        self.stale_days = stale_days
        self.resolve = resolve or (lambda f, v: v)  # (field, value) -> value; people.py maps display names to keys
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path) if path else ":memory:", check_same_thread=False, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.checked = 0.0
        self._schema()

    def _schema(self) -> None:
        with self.lock:
            ver = self.db.execute("PRAGMA user_version").fetchone()[0]
            if ver != 5:  # 5: the parent column (DESK-0208); an older index is rebuilt from the files
                self.db.executescript("DROP TABLE IF EXISTS docs; DROP TABLE IF EXISTS fts; DROP TABLE IF EXISTS vwords; DROP TABLE IF EXISTS vfts;")
            self.db.executescript(
                "CREATE TABLE IF NOT EXISTS docs (rowid INTEGER PRIMARY KEY, id TEXT UNIQUE, title TEXT, project TEXT, status TEXT, type TEXT,"
                " priority TEXT, owner TEXT, assignee TEXT, track TEXT, sprint TEXT, tags TEXT, ask TEXT, updated TEXT, sig TEXT, parent TEXT, pri INTEGER);"
                "CREATE INDEX IF NOT EXISTS docs_pri ON docs (pri);"
                "CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(id, title, body, tokenize='trigram');"
                "CREATE TABLE IF NOT EXISTS vwords (word TEXT PRIMARY KEY);"
                "CREATE VIRTUAL TABLE IF NOT EXISTS vfts USING fts5(word, tokenize='trigram');"
                "PRAGMA user_version = 5;")

    # -- writing ---------------------------------------------------------------------
    def upsert(self, rows: list[dict]) -> None:
        with self.lock, self.db:
            for r in rows:
                old = self.db.execute("SELECT rowid FROM docs WHERE id = ?", (r["id"],)).fetchone()
                if old:
                    self.db.execute("DELETE FROM fts WHERE rowid = ?", (old[0],))
                    self.db.execute("DELETE FROM docs WHERE rowid = ?", (old[0],))
                pri = (4 if r["status"] in CLOSED else 0) + (PRIORITIES.index(r["priority"]) if r["priority"] in PRIORITIES else 3)  # 0 = open p0 ... 7 = closed p3
                cur = self.db.execute(f"INSERT INTO docs ({','.join(COLS)}, pri) VALUES ({','.join('?' * (len(COLS) + 1))})", [r.get(c, "") for c in COLS] + [pri])
                self.db.execute("INSERT INTO fts (rowid, id, title, body) VALUES (?,?,?,?)", (cur.lastrowid, r["id"], r["title"], r["body"]))
                for w in set(re.findall(r"[a-z0-9]{4,}", r["title"].lower())):  # the words typos are corrected against
                    if self.db.execute("INSERT OR IGNORE INTO vwords (word) VALUES (?)", (w,)).rowcount:
                        self.db.execute("INSERT INTO vfts (word) VALUES (?)", (w,))

    def delete(self, ids: list[str]) -> None:
        with self.lock, self.db:
            for i in ids:
                old = self.db.execute("SELECT rowid FROM docs WHERE id = ?", (i,)).fetchone()
                if old:
                    self.db.execute("DELETE FROM fts WHERE rowid = ?", (old[0],))
                    self.db.execute("DELETE FROM docs WHERE rowid = ?", (old[0],))

    def refresh(self, store, force: bool = False, every: float = 0.0) -> int:
        """Bring the index in line with the ticket files; returns how many tickets were (re)read or dropped.
        With `every`, a check made less than that many seconds ago is skipped (the UI asks on each keystroke)."""
        now = time.monotonic()
        if not force and every and now - self.checked < every:
            return 0
        with self.lock:
            self.checked = now
            known = {r["id"]: r["sig"] for r in self.db.execute("SELECT id, sig FROM docs")}
            seen, rows = set(), []
            for p in store.ticket_paths():
                try:
                    st = p.stat()
                except OSError:
                    continue
                sig = f"{st.st_mtime_ns}:{st.st_size}:{p.name}"
                # the id is the file name up to its number; the file is read only when its signature changed
                m = _FILE_ID.match(p.name)
                tid = m.group(1) if m else None
                if tid in known and known[tid] == sig:
                    seen.add(tid)
                    continue
                try:
                    t = Ticket.load(p)
                except (OSError, ValueError):
                    continue
                seen.add(t.id)
                rows.append(row_for(t, sig))
            gone = [i for i in known if i not in seen]
            if rows:
                self.upsert(rows)
            if gone:
                self.delete(gone)
            return len(rows) + len(gone)

    def count(self) -> int:
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM docs").fetchone()[0]

    # -- reading ---------------------------------------------------------------------
    def _where(self, q: Query) -> tuple[list[str], list]:
        sql, args = [], []
        for key, vals, neg in q.filters:
            vals = [self.resolve(key, v) for v in vals]
            parts, pargs = [], []
            for v in vals:
                lv = v.lower()
                if key == "is":
                    cutoff = (dt.date.today() - dt.timedelta(days=self.stale_days)).isoformat()
                    closed = ",".join("?" * len(CLOSED))
                    if lv == "stale":
                        parts.append(f"(d.status NOT IN ({closed}) AND d.updated < ?)")
                        pargs += [*sorted(CLOSED), cutoff]
                    elif lv == "open":
                        parts.append(f"d.status NOT IN ({closed})")
                        pargs += sorted(CLOSED)
                    elif lv == "closed":
                        parts.append(f"d.status IN ({closed})")
                        pargs += sorted(CLOSED)
                    else:
                        parts.append("d.status = ?")  # is:review, is:blocked ...
                        pargs.append(lv)
                elif key == "before":
                    parts.append("d.updated < ?"); pargs.append(v[:10])
                elif key == "after":
                    parts.append("d.updated > ?"); pargs.append(v[:10])
                elif key == "tag":
                    parts.append("d.tags LIKE ?"); pargs.append(f"% {lv} %")
                elif key == "parent":
                    parts.append("d.parent = ?"); pargs.append(v.strip().upper())
                elif key in ("project", "status", "type", "priority", "owner", "assignee", "track", "sprint", "ask"):
                    if key in ("track", "sprint"):  # a name: match inside it, so `track:first` finds "First real use"
                        parts.append(f"d.{key} LIKE ?"); pargs.append(f"%{lv}%")
                    else:
                        parts.append(f"d.{key} = ?"); pargs.append(lv)
            if parts:
                clause = "(" + " OR ".join(parts) + ")"
                sql.append(("NOT " if neg else "") + clause)
                args += pargs
        return sql, args

    def search(self, text: "str | Query", limit: int = PER_GROUP) -> Result:
        q = text if isinstance(text, Query) else parse(text)
        if q.empty:
            return Result(q, 0, [])
        with self.lock:
            where, args = self._where(q)
            short = [t for t in q.terms if len(t) < 3]
            long_ = [t for t in q.terms if len(t) >= 3]
            rows: list[tuple] = []
            fuzzy = False
            if long_:
                rows = [(r, False) for r in self._fetch([_fts_quote(t) for t in long_], short, where, args)]
                if len(rows) < 3:  # too few: correct each term against the words in titles, then search again
                    fixed = [self._close_words(t) for t in long_]
                    if all(fixed) and fixed != [[t.lower()] for t in long_]:
                        have = {r[0]["id"] for r in rows}
                        groups = ["(" + " OR ".join(_fts_quote(w) for w in ws) + ")" for ws in fixed]
                        rows += [(r, True) for r in self._fetch(groups, short, where, args) if r["id"] not in have]
                        fuzzy = any(f for _, f in rows)
            else:
                sql = "SELECT d.* FROM docs d WHERE 1=1" + "".join(f" AND {w}" for w in where)
                params = list(args)
                for t in short:
                    sql += " AND (d.id LIKE ? OR d.title LIKE ?)"
                    params += [f"%{t}%", f"%{t}%"]
                rows = [(r, False) for r in self._buckets(sql, params)]
        return self._shape(q, rows, limit, fuzzy, " AND ".join(_fts_quote(t) for t in long_))

    def _snip(self, rowid: int, match: str, fuzzy: bool) -> str:
        """The words around the match (marked [like this]) for one shown hit; the start of the text when nothing matched literally."""
        with self.lock:
            if match and not fuzzy:
                r = self.db.execute("SELECT snippet(fts, -1, '[', ']', '...', 10) FROM fts WHERE rowid = ? AND fts MATCH ?", (rowid, match)).fetchone()
                if r and r[0]:
                    return " ".join(str(r[0]).split())
            r = self.db.execute("SELECT substr(body, 1, 120) FROM fts WHERE rowid = ?", (rowid,)).fetchone()
            return " ".join(str(r[0] if r else "").split())

    def _fetch(self, match_parts: list[str], short: list[str], where: list[str], args: list):
        sql = ("SELECT d.* FROM fts JOIN docs d ON d.rowid = fts.rowid WHERE fts MATCH ?"
               + "".join(f" AND {w}" for w in where))
        params = [" AND ".join(match_parts), *args]
        for t in short:
            sql += " AND (d.id LIKE ? OR d.title LIKE ?)"
            params += [f"%{t}%", f"%{t}%"]
        return self._buckets(sql, params)

    def _buckets(self, sql: str, params: list) -> list:
        """Open p0 first, then open p1 ... closed p3, each bucket cut off as soon as CANDIDATES rows are in hand. Sorting every match
        would cost time in proportion to the number of tickets that match; this stops early when a word matches nearly everything."""
        out: list = []
        for bucket in range(8):
            out += self.db.execute(sql + f" AND d.pri = ? LIMIT {CANDIDATES - len(out)}", [*params, bucket]).fetchall()
            if len(out) >= CANDIDATES:
                break
        return out

    def _close_words(self, term: str) -> list[str]:
        """Words in ticket titles that look like `term` (a typo, a different ending): the term itself first when it is a word, then the
        closest spellings. Found through the vocabulary's own trigram index, so the cost does not grow with the number of tickets."""
        t = term.lower()
        grams = sorted({t[i:i + 3] for i in range(len(t) - 2)})[:40]
        if not grams:
            return []
        cands = [r[0] for r in self.db.execute("SELECT word FROM vfts WHERE vfts MATCH ? LIMIT 400", (" OR ".join(_fts_quote(g) for g in grams),))]
        scored = sorted(((difflib.SequenceMatcher(None, t, w, autojunk=False).ratio(), w) for w in cands if _similar(t, w)), reverse=True)
        return [w for _, w in scored[:6]]

    def _shape(self, q: Query, rows: list[tuple], limit: int, fuzzy: bool, match: str = "") -> Result:
        terms = [t.lower() for t in q.terms]
        idq = [_norm_id_fragment(t) for t in q.terms]

        def rank(item):
            r, was_fuzzy = item
            tid = r["id"].lower()
            exact = 0 if any(tid == i or tid == t for i, t in zip(idq, terms)) else 1 if any(tid.startswith(i) for i in idq if i) else 2 if any(t in tid for t in terms) else 3
            prio = PRIORITIES.index(r["priority"]) if r["priority"] in PRIORITIES else len(PRIORITIES)
            closed = 1 if r["status"] in CLOSED else 0
            titled = 0 if any(t in (r["id"] + " " + r["title"]).lower() for t in terms) else 1
            return (was_fuzzy, exact, titled, closed, prio, r["id"])

        rows = sorted(rows, key=rank)
        by: dict[str, list[Hit]] = {}
        shown = 0
        counts: dict[str, int] = {}
        for r, was_fuzzy in rows:
            counts[r["project"]] = counts.get(r["project"], 0) + 1
            hits = by.setdefault(r["project"], [])
            if len(hits) < limit:
                hits.append(Hit(r["id"], r["title"], r["project"], r["status"], r["priority"], r["type"], r["assignee"], self._snip(r["rowid"], match, was_fuzzy) if shown < SNIPPETS else "", was_fuzzy))
                shown += 1
        self._label_parts([h for hs in by.values() for h in hs], {r["id"]: r["parent"] for r, _ in rows if r["parent"]})
        order = sorted(by, key=lambda p: (-counts[p], p))  # biggest group first
        return Result(q, len(rows), [(p, counts[p], by[p]) for p in order], fuzzy, capped=len(rows) >= CANDIDATES)

    def _label_parts(self, hits: list[Hit], parents: dict[str, str]) -> None:
        """Give each shown subticket its `part of <ID>: <title>` label: one lookup for the main tickets of the hits shown."""
        want = sorted({parents[h.id] for h in hits if h.id in parents})
        if not want:
            return
        with self.lock:
            titles = {r[0]: " ".join(str(r[1] or "").split()) for r in
                      self.db.execute(f"SELECT id, title FROM docs WHERE id IN ({','.join('?' * len(want))})", want)}
        for h in hits:
            pid = parents.get(h.id)
            if pid and pid in titles:
                title = titles[pid]
                h.part_of = f"part of {pid}: {title[:LABEL_TITLE]}{'…' if len(title) > LABEL_TITLE else ''}"


def _norm_id_fragment(term: str) -> str:
    """`desk38` or `desk 38` -> `desk-0038`; `DESK-00` stays a prefix. Lowercase."""
    t = term.strip().lower()
    m = re.fullmatch(r"([a-z][a-z0-9]*)[-\s]?(\d{1,4})", t)
    return f"{m.group(1)}-{int(m.group(2)):04d}" if m and len(m.group(2)) < 4 else t


def _similar(term: str, word: str) -> bool:
    if term in word:
        return True
    if len(term) < 4:
        return False
    ratio = difflib.SequenceMatcher(None, term, word, autojunk=False).ratio()
    return ratio >= (0.8 if len(term) < 6 else 0.72)


def snippet_html(snip: str) -> str:
    """The snippet with the `[match]` marks turned into <mark> and everything else escaped."""
    import html

    out = html.escape(snip)
    return out.replace("[", "<mark>").replace("]", "</mark>")


def people_resolver(store):
    """Lets `assignee:Patch` and `owner:"Big D"` find the keys the tickets hold (main, bigd)."""
    from . import people

    pe = people.load(store.home)
    return lambda field, value: pe.resolve(value) if field in ("assignee", "owner") else value


def open_index(store, resolve=None) -> Index:
    resolve = resolve or people_resolver(store)
    return Index(store.index_path.with_name("search.sqlite"), stale_days=store.stale_days, resolve=resolve)
