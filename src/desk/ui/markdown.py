"""Ticket sections as HTML: markdown-it-py (CommonMark + tables + strikethrough) with task lists,
links that open in a new tab, `assets/<ID>/<file>` images served by the UI, and fenced ```mermaid blocks
left for the browser to draw. Raw HTML in a ticket is escaped, never passed through."""

from __future__ import annotations

import html
import re
from urllib.parse import quote, unquote

from markdown_it import MarkdownIt
from markdown_it.token import Token

from ..docs import DOC_FULL, DOC_RE, slug, vscode_url
from mdit_py_plugins.tasklists import tasklists_plugin

_md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
_md.enable(["table", "strikethrough"])
_md.use(tasklists_plugin, enabled=False)  # checkboxes render disabled; the file is the source of truth


ID_RE = re.compile(r"(?<![A-Za-z0-9-])[A-Z]{2,10}-\d{4}(?!\d)")
_CHECKBOX = re.compile(r'<input class="task-list-item-checkbox"[^>]*>')
_COMMENT_LINE = re.compile(r"^[ 	]*<!--.*?-->[ 	]*$", re.M)


_WIKI = re.compile(r"\[\[([^\[\]\n]{1,80})\]\]")


def _text(content: str) -> Token:
    t = Token("text", "", 0)
    t.content = content
    return t


def _spans(content: str, env: dict) -> list[tuple[int, int, str, object]]:
    """Non-overlapping (start, end, kind, data) link candidates in one run of plain text, left to right."""
    cands = []
    known = env.get("known") or {}
    for m in ID_RE.finditer(content):
        if m.group(0) in known:
            cands.append((m.start(), m.end(), "id", m.group(0)))
    exists = env.get("docs")
    if exists:
        for m in DOC_RE.finditer(content):
            if exists(m.group(0).split("#", 1)[0]):
                cands.append((m.start(), m.end(), "doc", m.group(0)))
    if env.get("glossary_on"):
        for m in _WIKI.finditer(content):
            cands.append((m.start(), m.end(), "wiki", m.group(1).strip()))
        rx = env.get("gloss_rx")
        if rx:
            for m in rx.finditer(content):
                cands.append((m.start(), m.end(), "term", m.group(0)))
    cands.sort(key=lambda c: (c[0], -(c[1] - c[0])))
    out, end = [], 0
    for c in cands:
        if c[0] >= end:
            out.append(c)
            end = c[1]
    return out


def _link(href: str, label: str, cls: str, **attrs) -> list[Token]:
    lo = Token("link_open", "a", 1)
    lo.attrs = {"href": href, **attrs, "class": cls}
    return [lo, _text(label), Token("link_close", "a", -1)]


def _edit_link(env: dict, rel: str) -> list[Token]:
    """The second affordance next to every doc link: open the same file in VS Code."""
    path = env["docs"](rel.split("#", 1)[0])
    if not path:
        return []
    t = Token("html_inline", "", 0)
    t.content = f'<a class="vsc" href="{html.escape(vscode_url(path))}" title="Edit in VS Code">Edit in VS Code</a>'
    return [_text(" "), t]


def _term_html(shown: str, term: str, definition: str, target: str = "") -> Token:
    e = html.escape
    t = Token("html_inline", "", 0)
    t.content = (f'<span class="gloss" tabindex="0">{e(shown)}<span class="pop" role="tooltip"><b>{e(term)}</b> {e(definition)} '
                 f'<a href="{html.escape(target or "/doc/glossary.md#" + slug(term))}">More</a></span></span>')
    return t


def _autolink(state):
    """Core rule: known ticket ids, store paths that exist, `[[Term]]` and the first use of a glossary term per page become
    links or popovers. Unknown ids and missing files stay plain text."""
    env = state.env
    gl = env.get("glossary") or {}
    env["glossary_on"] = bool(gl)
    if gl and "gloss_rx" not in env:
        terms = sorted((v[0] for v in gl.values()), key=len, reverse=True)
        env["gloss_rx"] = re.compile(r"(?<![\w-])(?:%s)(?![\w-])" % "|".join(re.escape(x) for x in terms), re.I)
    if not (env.get("known") or env.get("docs") or gl):
        return
    seen = env.setdefault("seen", set())
    for block in state.tokens:
        if block.type != "inline" or not block.children:
            continue
        out, in_link, edits = [], 0, []
        for tok in block.children:
            if tok.type == "link_open":
                in_link += 1
                href = unquote(tok.attrGet("href") or "")
                if env.get("docs") and DOC_FULL.fullmatch(href) and env["docs"](href.split("#", 1)[0]):
                    tok.attrSet("href", "/doc/" + quote(href, safe="/#"))
                    edits.append(href)
                else:
                    edits.append("")
            elif tok.type == "link_close":
                in_link -= 1
                out.append(tok)
                last = edits.pop() if edits else ""
                if last:
                    out += _edit_link(env, last)
                continue
            if tok.type == "code_inline" and not in_link and env.get("docs") and DOC_FULL.fullmatch(tok.content)                     and env["docs"](tok.content.split("#", 1)[0]):
                out += _link("/doc/" + quote(tok.content, safe="/#"), tok.content, "docref") + _edit_link(env, tok.content)
                continue
            if tok.type != "text" or in_link:
                out.append(tok)
                continue
            pos = 0
            for start, end, kind, data in _spans(tok.content, env):
                if kind == "term":
                    key = str(data).lower()
                    if key in seen or key not in gl:
                        continue
                    seen.add(key)
                if start > pos:
                    out.append(_text(tok.content[pos:start]))
                if kind == "id":
                    info = env["known"][data]
                    out += _link(f"/ticket/{data}", str(data), "tid", title=str(info[0]), **{"data-status": str(info[1])})
                elif kind == "doc":
                    out += _link("/doc/" + quote(str(data), safe="/#"), str(data), "docref") + _edit_link(env, str(data))
                elif kind == "wiki":
                    entry = gl.get(str(data).lower())
                    href = entry[2] if entry and len(entry) > 2 and entry[2] else f"/doc/glossary.md#{slug(str(data))}"
                    out += _link(href, str(data), "gloss-link")
                else:
                    term, definition, *rest = gl[str(data).lower()]
                    out.append(_term_html(str(data), term, definition, rest[0] if rest else ""))
                pos = end
            if pos < len(tok.content):
                out.append(_text(tok.content[pos:]))
        block.children = out


def _render_link_open(self, tokens, idx, options, env):
    tok = tokens[idx]
    href = tok.attrGet("href") or ""
    if href.startswith(("/ticket/", "/doc/")):
        return self.renderToken(tokens, idx, options, env)
    if href.startswith("assets/"):
        tok.attrSet("href", "/" + href)
    tok.attrSet("target", "_blank")
    tok.attrSet("rel", "noopener noreferrer")
    return self.renderToken(tokens, idx, options, env)


def _render_image(self, tokens, idx, options, env):
    tok = tokens[idx]
    src = tok.attrGet("src") or ""
    if src.startswith("assets/"):
        tok.attrSet("src", "/" + src)
    tok.attrSet("loading", "lazy")
    return self.image(tokens, idx, options, env)


def _render_fence(self, tokens, idx, options, env):
    tok = tokens[idx]
    lang = (tok.info or "").strip().split(" ", 1)[0].lower()
    if lang == "mermaid":
        return f'<pre class="mermaid">{html.escape(tok.content)}</pre>\n'
    return self.fence(tokens, idx, options, env)


_md.core.ruler.push("ticket_autolink", _autolink)
_md.add_render_rule("link_open", _render_link_open)
_md.add_render_rule("image", _render_image)
_md.add_render_rule("fence", _render_fence)


def _render_heading_open(self, tokens, idx, options, env):
    inline = tokens[idx + 1] if idx + 1 < len(tokens) else None
    if env.get("ids") and inline is not None and inline.type == "inline":
        tokens[idx].attrSet("id", slug(inline.content))
    return self.renderToken(tokens, idx, options, env)


_md.add_render_rule("heading_open", _render_heading_open)


def render(text: str, *, known: dict | None = None, check: dict | None = None, actions=None,
           docs=None, glossary: dict | None = None, seen: set | None = None, ids: bool = False) -> str:
    """Markdown to HTML for one ticket section.

    known:   {ticket id: (title, status)}; those ids become links.
    check:   {"id": ticket id, "section": name}; checkbox lines become real checkboxes that POST to /ticket/<id>/check.
    docs:    fn(store-relative path) -> bool; existing paths like docs/X.md become links to /doc/<path>.
    glossary: {lowercase term: (Term, definition)}; the first use of each term in `seen` gets a popover, `[[Term]]` always links.
    actions: optional fn(id, status) -> html appended to a checkbox line that links to a known ticket (review weeks).
    """
    text = text or ""
    out = _md.render(_COMMENT_LINE.sub("", text), {"known": known or {}, "docs": docs, "glossary": glossary or {},
                                                  "seen": seen if seen is not None else set(), "ids": ids})
    if not check:
        return out
    from ..ticket import check_lines

    n = len(check_lines(text))
    if len(_CHECKBOX.findall(out)) != n:  # cannot map boxes to lines safely: leave them read-only
        return out
    counter = iter(range(n))

    def box(m):
        i = next(counter)
        on = "checked" in m.group(0)
        return (f'<form class="chk" method="post" action="/ticket/{check["id"]}/check">'
                f'<input type="hidden" name="token" value="{check["token"]() if check.get("token") else ""}">'
                f'<input type="hidden" name="section" value="{html.escape(check["section"])}">'
                f'<input type="hidden" name="index" value="{i}">'
                f'<input type="hidden" name="checked" value="{0 if on else 1}">'
                f'<input class="task-list-item-checkbox" type="checkbox"{" checked" if on else ""} onchange="this.form.submit()"></form>')

    out = _CHECKBOX.sub(box, out)
    if actions and known:
        def li(m):
            ids = re.findall(r'href="/ticket/([A-Z0-9-]+)"', m.group(0))
            extra = actions(ids[0], known[ids[0]][1]) if ids and ids[0] in known else ""
            return m.group(0)[:-5] + extra + "</li>"
        out = re.sub(r'<li class="task-list-item">.*?</li>', li, out, flags=re.S)
    return out


def has_mermaid(text: str) -> bool:
    return "```mermaid" in (text or "")
