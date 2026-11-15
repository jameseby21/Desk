"""Links to attachments and to files on this computer, in rendered ticket text (DESK-0291).

* `[label](file:///C:/Users/you/Documents/plan.pdf)`, `[label](<C:/Users/you/Documents/my plan.pdf>)`, and a bare absolute path in
  the text (C:\\Users\\you\\Downloads\\report.docx, or file:///C:/...) become links to /local?path=..., which Desk serves only after
  checking the path (localfiles.check). A path inside `code` becomes a link when the code is the whole path.
* Every attachment link (/assets/<ID>/<file>) and every local link carries data-preview (the side panel) and data-card (the hover card),
  read by preview.js and hovercard.js. Without script the link still opens the file in a new tab.

Only the link is written here; nothing is checked or read from disk while rendering."""

from __future__ import annotations

import re
from urllib.parse import quote, unquote

from markdown_it.token import Token

_EXT = r"(?:pdf|md|markdown|txt|log|csv|tsv|json|ya?ml|toml|docx|xlsx|pptx|png|jpe?g|gif|webp|svg|mp4|webm|mov|mp3|wav|zip|py|html?)"
# A Windows path: with a known extension it may hold spaces (C:\My Files\plan v2.pdf); without one it ends at the first space.
BARE = re.compile(r"(?<![\w/\\])(?:file:///)?[A-Za-z]:[\\/](?:"
                  r"(?:[^\\/:*?\"<>|\r\n`]+[\\/])*[^\\/:*?\"<>|\r\n`]*?\." + _EXT + r"(?!\w|\.\w)"
                  r"|[^\s<>\"|*?`]*[^\s<>\"|*?`.,;:)\]'])", re.I)
_DRIVE = re.compile(r"^(?:file:///)?[A-Za-z]:[\\/]", re.I)
_ASSET = re.compile(r"^/assets/([A-Za-z0-9-]+)/([^/?#]+)$")


def local_target(href: str) -> str | None:
    """The absolute path a link points at when it names a file on this computer, else None."""
    h = unquote(href or "").strip()
    if h.lower().startswith("file:///") and _DRIVE.match(h):
        return h[8:]
    if _DRIVE.match(h) and not h.lower().startswith("file:"):
        return h
    return None


def validate_link(original):
    """markdown-it refuses file: links; allow the file:///C:/ form (it is rewritten to /local and checked there)."""
    def check(url: str) -> bool:
        return bool(_DRIVE.match(unquote(url.strip()))) or original(url)
    return check


def decorate(tok: Token) -> None:
    """Give an attachment or local-file link its preview and hover-card addresses (a local link's href becomes /local?path=...)."""
    href = tok.attrGet("href") or ""
    m = _ASSET.match(href)
    if m:
        tok.attrSet("data-preview", f"/preview/{m.group(1)}/{m.group(2)}")
        tok.attrSet("data-card", f"/card/asset/{m.group(1)}/{m.group(2)}")
        return
    path = local_target(href)
    if path is None:
        return
    q = quote(path.replace("\\", "/"), safe="/:")
    tok.attrSet("href", f"/local?path={q}")
    tok.attrSet("data-preview", f"/local/preview?path={q}")
    tok.attrSet("data-card", f"/card/local?path={q}")
    tok.attrSet("class", ((tok.attrGet("class") or "") + " local-link").strip())


def _link(target: str, children: list[Token]) -> list[Token]:
    lo = Token("link_open", "a", 1)
    lo.attrs = {"href": target}
    return [lo, *children, Token("link_close", "a", -1)]


def _text(s: str) -> Token:
    t = Token("text", "", 0)
    t.content = s
    return t


def local_paths(state) -> None:
    """Core rule: a bare absolute Windows path (or file:/// address) in plain text or a whole code span becomes a link."""
    for block in state.tokens:
        if block.type != "inline" or not block.children:
            continue
        out, depth = [], 0
        for tok in block.children:
            if tok.type == "link_open":
                depth += 1
            elif tok.type == "link_close":
                depth -= 1
            if depth or tok.type not in ("text", "code_inline"):
                out.append(tok)
                continue
            if tok.type == "code_inline":
                m = BARE.fullmatch(tok.content.strip())
                out += _link(tok.content.strip(), [tok]) if m else [tok]
                continue
            pos = 0
            for m in BARE.finditer(tok.content):
                if m.start() > pos:
                    out.append(_text(tok.content[pos:m.start()]))
                out += _link(m.group(0), [_text(m.group(0))])
                pos = m.end()
            if pos == 0:
                out.append(tok)
            elif pos < len(tok.content):
                out.append(_text(tok.content[pos:]))
        block.children = out
