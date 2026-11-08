"""Ticket sections as HTML: markdown-it-py (CommonMark + tables + strikethrough) with task lists,
links that open in a new tab, `assets/<ID>/<file>` images served by the UI, and fenced ```mermaid blocks
left for the browser to draw. Raw HTML in a ticket is escaped, never passed through."""

from __future__ import annotations

import html

from markdown_it import MarkdownIt
from mdit_py_plugins.tasklists import tasklists_plugin

_md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
_md.enable(["table", "strikethrough"])
_md.use(tasklists_plugin, enabled=False)  # checkboxes render disabled; the file is the source of truth


def _render_link_open(self, tokens, idx, options, env):
    tok = tokens[idx]
    href = tok.attrGet("href") or ""
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


_md.add_render_rule("link_open", _render_link_open)
_md.add_render_rule("image", _render_image)
_md.add_render_rule("fence", _render_fence)


def render(text: str) -> str:
    """Markdown to HTML for one ticket section."""
    return _md.render(text or "")


def has_mermaid(text: str) -> bool:
    return "```mermaid" in (text or "")
