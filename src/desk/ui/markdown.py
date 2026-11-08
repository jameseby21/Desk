"""Ticket sections as HTML: markdown-it-py (CommonMark + tables + strikethrough) with task lists,
links that open in a new tab, `assets/<ID>/<file>` images served by the UI, and fenced ```mermaid blocks
left for the browser to draw. Raw HTML in a ticket is escaped, never passed through."""

from __future__ import annotations

import html
import re

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.tasklists import tasklists_plugin

_md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
_md.enable(["table", "strikethrough"])
_md.use(tasklists_plugin, enabled=False)  # checkboxes render disabled; the file is the source of truth


ID_RE = re.compile(r"(?<![A-Za-z0-9-])[A-Z]{2,10}-\d{4}(?!\d)")
_CHECKBOX = re.compile(r'<input class="task-list-item-checkbox"[^>]*>')
_COMMENT_LINE = re.compile(r"^[ 	]*<!--.*?-->[ 	]*$", re.M)


def _autolink(state):
    """Core rule: a known ticket id in plain text becomes a link to its page. Unknown ids stay text."""
    known = state.env.get("known")
    if not known:
        return
    for block in state.tokens:
        if block.type != "inline" or not block.children:
            continue
        out, in_link = [], 0
        for tok in block.children:
            if tok.type == "link_open":
                in_link += 1
            elif tok.type == "link_close":
                in_link -= 1
            if tok.type != "text" or in_link or not ID_RE.search(tok.content):
                out.append(tok)
                continue
            pos = 0
            for m in ID_RE.finditer(tok.content):
                info = known.get(m.group(0))
                if not info:
                    continue
                if m.start() > pos:
                    t = Token("text", "", 0)
                    t.content = tok.content[pos:m.start()]
                    out.append(t)
                lo = Token("link_open", "a", 1)
                lo.attrs = {"href": f"/ticket/{m.group(0)}", "title": str(info[0]), "data-status": str(info[1]), "class": "tid"}
                t = Token("text", "", 0)
                t.content = m.group(0)
                out += [lo, t, Token("link_close", "a", -1)]
                pos = m.end()
            if pos < len(tok.content):
                t = Token("text", "", 0)
                t.content = tok.content[pos:]
                out.append(t)
        block.children = out


def _render_link_open(self, tokens, idx, options, env):
    tok = tokens[idx]
    href = tok.attrGet("href") or ""
    if href.startswith("/ticket/"):
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


def render(text: str, *, known: dict | None = None, check: dict | None = None, actions=None) -> str:
    """Markdown to HTML for one ticket section.

    known:   {ticket id: (title, status)}; those ids become links.
    check:   {"id": ticket id, "section": name}; checkbox lines become real checkboxes that POST to /ticket/<id>/check.
    actions: optional fn(id, status) -> html appended to a checkbox line that links to a known ticket (review weeks).
    """
    text = text or ""
    out = _md.render(_COMMENT_LINE.sub("", text), {"known": known or {}})
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
