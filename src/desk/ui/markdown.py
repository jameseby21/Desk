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
_TICK = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#141414" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>'
_CROSS = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" stroke-width="3.6" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>'


_WIKI = re.compile(r"\[\[([^\[\]\n]{1,80})\]\]")


def _text(content: str) -> Token:
    t = Token("text", "", 0)
    t.content = content
    return t


def _res(env: dict, text: str) -> str | None:
    """The store-relative path (anchor kept) a doc reference in the text points at, or None when it resolves nowhere."""
    fn = env.get("resolve")
    if fn:
        return fn(text)
    exists = env.get("docs")
    return text if exists and exists(text.split("#", 1)[0]) else None


def _spans(content: str, env: dict) -> list[tuple[int, int, str, object]]:
    """Non-overlapping (start, end, kind, data) link candidates in one run of plain text, left to right."""
    cands = []
    known = env.get("known") or {}
    for m in ID_RE.finditer(content):
        if m.group(0) in known:
            cands.append((m.start(), m.end(), "id", m.group(0)))
    if env.get("docs"):
        for m in DOC_RE.finditer(content):
            if _res(env, m.group(0)):
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
                res = _res(env, href) if env.get("docs") and DOC_FULL.fullmatch(href) else None
                if res:
                    tok.attrSet("href", "/doc/" + quote(res, safe="/#"))
                    edits.append(res)
                else:
                    edits.append("")
            elif tok.type == "link_close":
                in_link -= 1
                out.append(tok)
                last = edits.pop() if edits else ""
                if last:
                    out += _edit_link(env, last)
                continue
            if tok.type == "code_inline" and not in_link and env.get("docs") and DOC_FULL.fullmatch(tok.content):
                res = _res(env, tok.content)
                if res:
                    out += _link("/doc/" + quote(res, safe="/#"), tok.content, "docref") + _edit_link(env, res)
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
                    res = _res(env, str(data)) or str(data)
                    out += _link("/doc/" + quote(res, safe="/#"), str(data), "docref") + _edit_link(env, res)
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


def _censor_text(state):
    """Core rule, after autolinking: plain text nodes pass through env["censor"]. Code spans, fences and link targets are other
    token types or attributes, so they are never touched."""
    fn = state.env.get("censor")
    if not fn:
        return
    for block in state.tokens:
        if block.type == "inline" and block.children:
            for tok in block.children:
                if tok.type == "text":
                    tok.content = fn(tok.content)


def _thumb_links(state):
    """Core rule: an uploaded image (assets/<ID>/<file>) that is not already inside a link opens its full size on click."""
    for block in state.tokens:
        if block.type != "inline" or not block.children:
            continue
        out, depth = [], 0
        for tok in block.children:
            if tok.type == "link_open":
                depth += 1
            elif tok.type == "link_close":
                depth -= 1
            if tok.type == "image" and not depth and (tok.attrGet("src") or "").startswith("assets/"):
                a = Token("link_open", "a", 1)
                a.attrSet("href", tok.attrGet("src"))
                a.attrSet("class", "thumb")
                out += [a, tok, Token("link_close", "a", -1)]
            else:
                out.append(tok)
        block.children = out


_md.core.ruler.push("ticket_autolink", _autolink)
_md.core.ruler.push("thumb_links", _thumb_links)
_md.core.ruler.push("censor_text", _censor_text)
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
           docs=None, resolve=None, glossary: dict | None = None, seen: set | None = None, ids: bool = False, censor=None) -> str:
    """Markdown to HTML for one ticket section.

    known:   {ticket id: (title, status)}; those ids become links.
    check:   {"id": ticket id, "section": name}; checkbox lines become real checkboxes that POST to /ticket/<id>/check.
    docs:    fn(store-relative path) -> Path or None; existing paths like docs/X.md become links to /doc/<path>.
    resolve: fn(path as written) -> store-relative path (anchor kept) or None; lets a project-relative path link to its real file.
    glossary: {lowercase term: (Term, definition)}; the first use of each term in `seen` gets a popover, `[[Term]]` always links.
    censor:  optional fn(str) -> str applied to plain text nodes (the profanity filter); code is never touched.
    actions: optional fn(id, status) -> html appended to a checkbox line that links to a known ticket (review weeks).
    """
    text = text or ""
    from ..ops import press
    from ..ticket import DEFAULT_SEVERITY, SEVERITIES, parse_checks

    checks = parse_checks(text)
    by_first = {c.first: c for c in checks}
    kept, i = [], 0
    src = text.split("\n")
    while i < len(src):  # what markdown sees: the line's words only. Byline, quoted reason and evidence lines are drawn below.
        c = by_first.get(i)
        if c is None:
            kept.append(src[i])
            i += 1
            continue
        kept.append(f"{c.head}{'x' if c.state == '!' else c.state}{c.mid}{c.text}")  # a `[!]` goes through the task-list plugin as done
        kept += c.extra
        i = c.last + 1
    out = _md.render(_COMMENT_LINE.sub("", "\n".join(kept)), {"known": known or {}, "docs": docs, "resolve": resolve, "glossary": glossary or {},
                                                              "seen": seen if seen is not None else set(), "ids": ids, "censor": censor})
    failed = any(c.state == "!" for c in checks)
    if not check and not failed:
        return out
    n = len(checks)
    if len(_CHECKBOX.findall(out)) != n:  # cannot map boxes to lines safely: leave them read-only
        return out
    counter = iter(range(n))
    pending: dict[str, str] = {}  # marker -> what to show under the line whose checkbox carries that marker
    e = html.escape

    def box(m):
        i = next(counter)
        c = checks[i]
        st = c.state
        under = ""
        if st == "!":
            why = (censor(c.reason) if censor else c.reason)
            under = _reason_html(c, why, editable=bool(check))
        elif c.evidence:
            under = f'<div class="ev-count">{_shots(len(c.evidence))}</div>'
        if not check:
            if st == "!":
                pending[f"@@NR{i}@@"] = under
                return f'<span class="chk-failed" role="img" aria-label="not accepted">&#10007;</span>@@NR{i}@@'
            return m.group(0)
        sec, tid = e(check["section"]), e(check["id"])
        what = e(" ".join(c.text.split())[:120])
        tok = lambda: check["token"]() if check.get("token") else ""  # noqa: E731

        def head():  # every form has its own one-time token
            return (f'<form class="chk post-once" method="post" action="/ticket/{tid}/check"><input type="hidden" name="token" value="{tok()}">'
                    f'<input type="hidden" name="section" value="{sec}"><input type="hidden" name="index" value="{i}">')

        done = (f'{head()}<input type="hidden" name="state" value="{press(st, "done")[0]}"><button type="submit" class="hit" data-act="done" '
                f'aria-pressed="{"true" if st == "x" else "false"}" aria-label="Done: {what}"><span class="box{" on-done" if st == "x" else ""}">{_TICK}</span></button></form>')
        if st == "!":
            na = (f'{head()}<input type="hidden" name="state" value="{press(st, "no")[0]}"><button type="submit" class="hit" data-act="no" aria-pressed="true" '
                  f'aria-label="Not accepted: {what}"><span class="box on-no">{_CROSS}</span></button></form>')
        else:
            na = (f'<button type="button" class="hit" data-act="no" aria-pressed="false" aria-label="Not accepted: {what}" aria-expanded="false">'
                  f'<span class="box">{_CROSS}</span></button>')
        sev = c.severity or DEFAULT_SEVERITY
        pills = "".join(f'<label class="sevpill"><input type="radio" name="severity" value="{v}"{" checked" if v == sev else ""}><span>{lab}</span></label>'
                        for v, lab in zip(SEVERITIES, ("Must fix", "Should", "Nit")))
        cancel = ('<button type="submit" class="btn small" name="state" value=" " formnovalidate data-act="cancel-clear">Cancel</button>' if st == "x"
                  else '<button type="button" class="btn small" data-act="cancel">Cancel</button>')
        panel = (f'<div class="nr-panel" hidden data-line="{i}">{head()}<label>Why isn&#39;t this accepted?'
                 f'<textarea name="reason" rows="2" required placeholder="Say what is missing or wrong. Paste a screenshot if it helps." data-attach="/ticket/{tid}/attach" data-token="{tok()}">{e(c.reason) if st == "!" else ""}</textarea></label>'
                 f'<div class="shots" hidden></div>'
                 f'<div class="nr-err" role="alert"></div><div class="sevs" role="radiogroup" aria-label="How serious">{pills}</div>'
                 f'<div class="row nr-actions">{cancel}<button type="submit" class="btn small ink" name="state" value="!">Save reason</button></div></form></div>')
        pending[f"@@NR{i}@@"] = under + panel
        return f'<span class="chk2">{done}{na}</span>@@NR{i}@@'

    out = _CHECKBOX.sub(box, out)
    out = _place_reasons(out, pending)
    if check and n:
        first = "Acceptance line" if check["section"] == "Acceptance" else "Line"
        out = f'<div class="chk-head" aria-hidden="true"><span>{first}</span><span>Done</span><span>Not accepted</span></div>' + out
    if actions and known:
        def li(m):
            ids = re.findall(r'href="/ticket/([A-Z0-9-]+)"', m.group(0))
            extra = actions(ids[0], known[ids[0]][1]) if ids and ids[0] in known else ""
            return m.group(0)[:-5] + extra + "</li>"
        out = re.sub(r'<li class="task-list-item">.*?</li>', li, out, flags=re.S)
    if check:  # a line can be tabbed to; D, N and Esc act on it (static/checks.js)
        out = out.replace('<li class="task-list-item">', '<li class="task-list-item" tabindex="0">')
    return out


def summary_html(text: str) -> str:
    """The line and the bar above an Acceptance list: `2 of 4 done · 1 not accepted · 1 open`, lime for done, red for not accepted.
    Empty when the section has no checkbox lines. The page and the JSON reply of a box click both come from here."""
    from ..ticket import check_counts

    c = check_counts(text)
    if not c["total"]:
        return ""
    pct = lambda k: round(100 * c[k] / c["total"])  # noqa: E731
    return (f'<span class="chk-line"><b>{c["done"]} of {c["total"]} done</b> · {c["no"]} not accepted · {c["open"]} open</span>'
            f'<div class="chk-bar" aria-hidden="true"><span class="b-done" style="width:{pct("done")}%"></span><span class="b-no" style="width:{pct("no")}%"></span></div>')


def _shots(n: int) -> str:
    return f"{n} screenshot{'' if n == 1 else 's'}"


def _clock(when: str) -> str:
    """`2026-11-08T03:41` as `3:41 AM` (with the date when it is not today)."""
    import datetime as dt

    try:
        d = dt.datetime.fromisoformat(when)
    except ValueError:
        return when
    t = d.strftime("%I:%M %p").lstrip("0")
    return t if d.date() == dt.date.today() else f"{d.strftime('%b')} {d.day}, {t}"


def _reason_html(c, why: str, editable: bool) -> str:
    """The saved reason under a Not accepted line: severity, the reason, who, when, evidence count and an Edit link."""
    from ..ticket import DEFAULT_SEVERITY

    e = html.escape
    sev = c.severity or DEFAULT_SEVERITY
    label = {"must fix": "Must fix", "should": "Should", "nit": "Nit"}[sev]
    meta = [x for x in (f"{c.who[:1].upper()}{c.who[1:]} · {_clock(c.when)}" if c.who and c.when else "", _shots(len(c.evidence)) if c.evidence else "") if x]
    return (f'<div class="nr-reason" data-sev="{sev}"><span class="sev">{label}</span> <span class="why">{e(why) if why else "Not accepted"}</span>'
            + (f'<span class="nr-meta">{e(" · ".join(meta))}</span>' if meta else "")
            + ('<button type="button" class="linkbtn" data-act="edit">Edit</button>' if editable else "") + "</div>")


def _place_reasons(out: str, pending: dict) -> str:
    """Move each reason marker to the end of its own list item (before `</li>` or a nested list), as a red line under the text."""
    for mark, block in pending.items():
        i = out.find(mark)
        if i < 0:
            continue
        out = out[:i] + out[i + len(mark):]
        m = re.compile(r"</li>|<ul|<ol").search(out, i)
        j = m.start() if m else len(out)
        out = out[:j] + block + out[j:]
    return out


def has_mermaid(text: str) -> bool:
    return "```mermaid" in (text or "")
