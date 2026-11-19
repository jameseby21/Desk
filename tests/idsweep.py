"""DESK-0334 test helper: find the ticket ids a rendered page shows as plain text where a link was due.

`bare_ids(html, existing)` walks the page the way a browser does (html.parser, with the stack of open tags) and returns every
`(id, snippet)` where an id that exists in the store sits in a text node that is none of: inside a link-like element (`a.tid`,
`span.tid[data-href]`, the plain-with-title `span.tid-missing`), inside code or a pre block, a script or style, a title, a textarea, an
option or a select, or inside the one link that points at that very ticket (a row or card links itself). It is deliberately a different
implementation from desk.idlinks (which is regex over the tag stream), so a bug in one does not hide in the other."""

from __future__ import annotations

import re
from html.parser import HTMLParser

ID = re.compile(r"(?<![A-Za-z0-9-])[A-Z]{2,10}-\d{4}(?!\d|-[A-Za-z0-9])")  # an id followed by a slug is a branch or file name (bsa/BSA-0091-group-formulas), not a mention
SKIP_TAGS = {"code", "pre", "script", "style", "title", "textarea", "option", "select", "svg", "kbd", "button", "noscript"}
VOID = {"br", "hr", "img", "input", "meta", "link", "source", "wbr", "area", "base", "col", "embed", "param", "track"}


class _Walker(HTMLParser):
    def __init__(self, existing):
        super().__init__(convert_charrefs=True)
        self.existing = existing
        self.stack: list[tuple[str, dict]] = []
        self.bare: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append((tag, dict(attrs)))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return

    def _covered(self, tid: str) -> bool:
        for tag, a in self.stack:
            cls = (a.get("class") or "").split()
            if tag in SKIP_TAGS or "tid" in cls or "tid-missing" in cls:
                return True
            if tag == "a" and re.search(rf"/ticket/{re.escape(tid)}(?![0-9])", a.get("href") or ""):
                return True
        return False

    def handle_data(self, data):
        if not data.strip():
            return
        # a protected token: a URL or a file name keeps its id plain on purpose
        prot = [(m.start(), m.end()) for m in re.finditer(r"\S*://\S*|\S+\.(?:md|png|jpg|jpeg|gif|yaml|yml|json|py|html|txt|pdf|csv|js|css)\b|`[^`\n]*`", data)]
        for m in ID.finditer(data):
            if m.group(0) not in self.existing or any(a <= m.start() < b for a, b in prot):
                continue
            if not self._covered(m.group(0)):
                self.bare.append((m.group(0), data[max(0, m.start() - 30): m.end() + 30].strip()))


def bare_ids(html: str, existing) -> list[tuple[str, str]]:
    w = _Walker(set(existing))
    w.feed(html)
    w.close()
    return w.bare


def linked_ids(html: str) -> set[str]:
    """The ids a page links (a.tid and span.tid[data-href])."""
    return set(re.findall(r'<a [^>]*class="tid"[^>]*href="/ticket/([A-Z]+-\d{4})"', html)) | set(re.findall(r'<a [^>]*href="/ticket/([A-Z]+-\d{4})"[^>]*class="tid"', html)) | \
        set(re.findall(r'<span [^>]*class="tid"[^>]*data-href="/ticket/([A-Z]+-\d{4})"', html))
