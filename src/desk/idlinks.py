"""One filter for ticket ids (DESK-0334): wherever Desk shows text, an id that exists in the store is a link to that ticket.

James (2026-11-19): "Why the fuck am I still seeing a reference to a ticket WITHOUT A GODDAMNED LINK". Before this module, ids were linked
by the markdown renderer in a ticket's body sections and nowhere else. Now there is one rule, written once here:

- an id that exists in the store becomes `<a class="tid" href="/ticket/ID" title="<its title>" data-status="<its status>">ID</a>`;
- inside another link (a whole row or card is one link, and an anchor cannot hold an anchor) it becomes
  `<span class="tid" role="link" tabindex="0" data-href="/ticket/ID" ...>`, which static/idlinks.js turns into a click and a key press;
- an id that does not exist stays plain, wrapped in `<span class="tid-missing" title="No such ticket">`;
- never inside code (a backtick span, <code>, <pre>), an existing link to that same ticket, a URL, a file name, a <script>, <style>,
  <textarea>, <title>, <option>, <select>, <button> or <svg>, and never in an attribute.

The three doors share this module: the Jinja filter `tid` (plain text in, HTML out), the response pass (`link_html`, every page, so a
template that forgets the filter is still covered), and static/idlinks.js (text drawn by scripts), which implements the same rule in
JavaScript against the `/ids` endpoint. `index(store)` is the set of ids that exist, kept until a ticket file changes."""

from __future__ import annotations

import re
from collections.abc import Mapping

from markupsafe import escape

# An id: two to ten capitals, a dash, four digits, not glued to other word characters. (A slash is not "glued": `A-0001/B-0002` is two ids.)
ID_RE = re.compile(r"(?<![A-Za-z0-9_-])[A-Z]{2,10}-\d{4}(?![\dA-Za-z_]|-[A-Za-z0-9])")
_FILE_EXT = r"(?:md|png|jpe?g|gif|webp|yaml|yml|json|py|html?|txt|pdf|csv|js|css|toml|log)"
# spans of plain text that are never touched: a backtick span, a URL, a Windows path, a file name
_PROTECTED = re.compile(r"`[^`\n]*`|\S{0,300}://\S*|\bwww\.\S+|[A-Za-z]:\\\S*|\S{0,300}[\\]\S*|\S{1,300}\." + _FILE_EXT + r"\b")  # bounded: a long unbroken token must not make this quadratic
_TAG = re.compile(r"(<!--.*?-->|<[^>]*>)", re.S)
_OURS = re.compile(r'\bclass\s*=\s*"tid(?:-missing)?"')
_TAG_NAME = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9-]*)")
_HREF = re.compile(r'''\bhref\s*=\s*["']?/ticket/([A-Za-z]+-\d{4})''')
SKIP = frozenset({"code", "pre", "script", "style", "textarea", "title", "option", "select", "button", "svg", "kbd", "samp", "noscript", "template"})
VOID = frozenset({"br", "hr", "img", "input", "meta", "link", "source", "wbr", "area", "base", "col", "embed", "param", "track"})
MISSING_TITLE = "No such ticket"


def _info(known, tid):
    v = known.get(tid)
    if v is None:
        return None
    if isinstance(v, (tuple, list)):
        return (str(v[0]) if len(v) > 0 else "", str(v[1]) if len(v) > 1 else "")
    return (str(v), "")


def _wrap(tid: str, known: Mapping, nested: bool, censor=None) -> str:
    info = _info(known, tid)
    if info is None:
        return f'<span class="tid-missing" title="{MISSING_TITLE}">{tid}</span>'
    title, status = escape(censor(info[0]) if censor else info[0]), escape(info[1])  # the profanity filter covers the title in a hover too
    if nested:
        return f'<span class="tid" role="link" tabindex="0" data-href="/ticket/{tid}" title="{title}" data-status="{status}">{tid}</span>'
    return f'<a class="tid" href="/ticket/{tid}" title="{title}" data-status="{status}">{tid}</a>'


def link_text(text: str, known: Mapping | None, *, nested: bool = False, own: str = "", censor=None) -> str:
    """Plain text in, safe HTML out: the text escaped, every id that exists linked, every id that does not marked. `nested` is for text that
    will sit inside a link; `own` is an id that must stay as it is (the ticket the surrounding link already points at)."""
    text = "" if text is None else str(text)
    if not known or not ID_RE.search(text):
        return str(escape(text))
    prot = [(m.start(), m.end()) for m in _PROTECTED.finditer(text)]
    out, pos = [], 0
    for m in ID_RE.finditer(text):
        if any(a <= m.start() < b for a, b in prot):
            continue
        out.append(str(escape(text[pos:m.start()])))
        out.append(escape(m.group(0)) if m.group(0) == own else _wrap(m.group(0), known, nested, censor))
        pos = m.end()
    out.append(str(escape(text[pos:])))
    return "".join(out)


def link_html(html: str, known: Mapping | None, censor=None) -> str:
    """The same rule over markup: only text between tags is looked at; the tag stack says where it may not be touched."""
    if not known or not html or not ID_RE.search(html):
        return html
    parts = _TAG.split(html)
    skip: list[str] = []  # open tags that forbid linking (code, script, ...)
    anchors: list[str] = []  # the ticket each open <a> points at, or "-" for an <a> that points anywhere else (its text is left alone)
    ours = False  # the text that follows is the id inside a link this pass (or the markdown renderer) already made
    for i in range(0, len(parts), 2):  # even parts are text, odd parts are tags (the split keeps them)
        if i:
            tag = parts[i - 1]
            m = _TAG_NAME.match(tag)
            ours = False
            if m and not tag.startswith("<!--"):
                closing, name = bool(m.group(1)), m.group(2).lower()
                if closing:
                    if name == "a" and anchors:
                        anchors.pop()
                    elif name in SKIP and name in skip:
                        del skip[len(skip) - 1 - skip[::-1].index(name):]
                elif name not in VOID and not tag.endswith("/>"):
                    if name == "a":
                        h = _HREF.search(tag)
                        anchors.append(h.group(1).upper() if h else "-")
                    elif name == "span" and _OURS.search(tag):
                        ours = True
                    elif name in SKIP:
                        skip.append(name)
        txt = parts[i]
        if ours or skip or "-" in anchors or not txt or not ID_RE.search(txt):
            continue
        parts[i] = _link_escaped(txt, known, nested=bool(anchors), own=anchors[-1] if anchors else "", censor=censor)
    return "".join(parts)


def _link_escaped(txt: str, known: Mapping, *, nested: bool, own: str, censor=None) -> str:
    """Text that is already HTML-escaped (a text node of a page): ids hold no characters that escaping changes, so it is scanned as it is."""
    prot = [(m.start(), m.end()) for m in _PROTECTED.finditer(txt)]
    out, pos = [], 0
    for m in ID_RE.finditer(txt):
        if any(a <= m.start() < b for a, b in prot):
            continue
        out.append(txt[pos:m.start()])
        out.append(m.group(0) if m.group(0) == own else _wrap(m.group(0), known, nested, censor))
        pos = m.end()
    out.append(txt[pos:])
    return "".join(out)


# ---- the set of ids that exist ---------------------------------------------------------------------------------------------

_FRONT = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.S)
_FILES: dict = {}  # path -> (mtime_ns, size, id, title, status): a file is read again only when it changed


def _scalar(raw: str):
    """The value of a one-line front-matter scalar written by Desk (single-quoted, or plain), or None when it needs a real YAML parse."""
    raw = raw.strip()
    if raw.startswith("'") and raw.endswith("'") and len(raw) >= 2:
        return raw[1:-1].replace("''", "'")
    if raw and raw[0] not in "\"'|>[{&*!%@`#" and " #" not in raw and ": " not in raw:
        return raw
    return None


_KEYS = re.compile(r"^(id|title|status):[ 	]*(.*)$", re.M)


def _head(path) -> tuple[str, str, str]:
    """(id, title, status) from the front matter of one ticket file; the body is never parsed (the full parse is most of a page's cost).
    The three keys are read from their own lines; anything that is not a plain one-line value goes through the YAML parser."""
    from . import yamlio

    with open(path, encoding="utf-8") as fh:
        text = fh.read(32768)
    m = _FRONT.match(text)
    if not m:
        return "", "", ""
    fm = m.group(1)
    found = {}
    for k in _KEYS.finditer(fm):
        nxt = fm[k.end() + 1:k.end() + 2]
        v = None if nxt in (" ", "	") else _scalar(k.group(2))  # a line that continues on the next one needs the parser
        if v is None:
            found = None
            break
        found[k.group(1)] = v
    if found is None or "id" not in found:
        meta = yamlio.load(fm) or {}
        meta = meta if isinstance(meta, dict) else {}
        return str(meta.get("id") or ""), str(meta.get("title") or ""), str(meta.get("status") or "")
    return found.get("id", ""), found.get("title", ""), found.get("status", "")


def index(store) -> dict[str, tuple[str, str]]:
    """{id: (title, status)} for every ticket. One stat per ticket file, and a read of only the front matter of a file that changed since
    the last call, so a busy store (agents saving all day) costs a few milliseconds per page, not a parse of every ticket."""
    known: dict[str, tuple[str, str]] = {}
    live = set()
    for p in store.ticket_paths():
        try:
            st = p.stat()
        except OSError:
            continue
        live.add(p)
        hit = _FILES.get(p)
        if hit is None or hit[0] != st.st_mtime_ns or hit[1] != st.st_size:
            try:
                hit = (st.st_mtime_ns, st.st_size, *_head(p))
            except (OSError, ValueError, UnicodeDecodeError):
                continue
            _FILES[p] = hit
        if hit[2]:
            known[hit[2]] = (hit[3], hit[4])
    for gone in [p for p in _FILES if p not in live and str(p).startswith(str(store.tickets_dir))]:
        del _FILES[gone]
    return known


# ---- one lookup per request --------------------------------------------------------------------------------------------------

import contextvars  # noqa: E402  (kept beside the code that uses it)

_HOLDER: contextvars.ContextVar[dict | None] = contextvars.ContextVar("desk_idlinks", default=None)


def begin_request() -> None:
    """Called by the response pass at the start of a request: the first `current()` of the request reads the index, the rest reuse it
    (a card draws dozens of ids, and the stamp that proves the cache is fresh costs one stat per ticket)."""
    _HOLDER.set({})


def current(store) -> dict[str, tuple[str, str]]:
    holder = _HOLDER.get()
    if holder is None:
        return index(store)
    key = str(store.home)
    if key not in holder:
        holder[key] = index(store)
    return holder[key]


class ResponsePass:
    """Pure ASGI middleware: every text/html response is run through `link_html` before it leaves, so a page, a fragment or a hover card
    that prints an id without the Jinja filter is still linked. Streams (server-sent events), JSON and files pass untouched. A failure
    here never breaks a page: the page goes out as it was built, and the failure is logged."""

    def __init__(self, app, find_store, find_censor=None):
        self.app = app
        self.find_store = find_store
        self.find_censor = find_censor  # store -> the profanity filter for this store, or None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        begin_request()
        held: dict = {"start": None, "chunks": []}

        async def send_wrapped(message):
            if message["type"] == "http.response.start":
                heads = {k.lower(): v for k, v in message.get("headers", [])}
                if heads.get(b"content-type", b"").lower().startswith(b"text/html") and b"content-encoding" not in heads:
                    held["start"] = message
                    return
                return await send(message)
            if held["start"] is not None and message["type"] == "http.response.body":
                held["chunks"].append(message.get("body", b""))
                if message.get("more_body"):
                    return
                body = b"".join(held["chunks"])
                try:
                    st = self.find_store()
                    body = link_html(body.decode("utf-8"), current(st), self.find_censor(st) if self.find_censor else None).encode("utf-8")
                except Exception:  # noqa: BLE001 - the page matters more than its links
                    import logging

                    logging.getLogger("desk.idlinks").exception("ticket ids were not linked on %s", scope.get("path"))
                start = held["start"]
                heads = [(k, v) for k, v in start["headers"] if k.lower() != b"content-length"] + [(b"content-length", str(len(body)).encode())]
                held["start"] = None
                await send({**start, "headers": heads})
                return await send({"type": "http.response.body", "body": body})
            return await send(message)

        await self.app(scope, receive, send_wrapped)
