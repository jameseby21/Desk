"""One-field capture (DESK-0286, closes DESK-0197's one-field idea): a sentence becomes an inbox ticket in five seconds.

Desk guesses the project (a `#key`, a ticket id, or a project's key, id prefix, alias or title named in the sentence), the type (bug, fix,
decide, research ... words) and the lane: Later unless the words urgent, now, today or asap appear, which put it in Now. The title is
the sentence cut to the title limit at a word; the whole sentence goes in Context when it was cut. An optional "MVP slice?" line is
stored as `smallest_step`. The ticket is written through `Store.create` and `Store.save`, the path `drew new` and the New ticket form
use, so it passes `drew validate` and the activity log records it.

Paste-to-split: `split_asks` turns a chat excerpt or an email into one proposed row per ask (a line, bullet or sentence with please,
can you, need, should, must, TODO or a question mark); only the rows a person ticks become tickets, all in one activity batch.
Files: `refused_name` names the file types a drop never accepts (programs and scripts), whatever the size.
Docs: docs/COMMANDS.md (In the UI: Capture)."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field

from . import assign_rules, people as peoplemod
from .rules import TITLE_MAX, RuleError
from .store import Store
from .ticket import Ticket, today

# Programs, scripts and shortcuts: a drop refuses them by extension, whatever their size (James's list, 2026-11-15).
REFUSED_EXTENSIONS = (".exe", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".msi", ".scr", ".com", ".lnk")
FALLBACK_PROJECTS = ("management",)  # where a sentence that names no project lands, unless desk.toml [capture] project says otherwise
MVP_LABEL = "MVP slice?"

# First match wins, in this order: a sentence with "fix" is a bug even when it also says "improve".
TYPE_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("bug", ("bug", "bugs", "broken", "fix", "fixes", "crash", "crashes", "error", "errors", "fails", "failing", "wrong")),
    ("decision", ("decide", "decision", "choose", "approve", "pick")),
    ("research", ("research", "investigate", "find out", "look into", "compare", "explore")),
    ("docs", ("docs", "document", "documentation", "readme", "write up")),
    ("release", ("release", "ship", "publish", "deploy")),
    ("upgrade", ("upgrade", "improve", "faster", "polish", "tidy")),
    ("task", ("call", "email", "pay", "book", "buy", "send", "schedule", "renew")),
)
URGENT_WORDS = ("urgent", "now", "today", "asap")
ASK_WORDS = ("please", "can you", "could you", "would you", "need", "needs", "should", "must", "todo")

_NEW = re.compile(r"^\s*new\s*:\s*", re.I)
_HASH = re.compile(r"(?<![\w#])#([a-z0-9][a-z0-9-]*)\b", re.I)
_ID = re.compile(r"\b([A-Z][A-Z0-9]*)-\d{2,}\b")
_BULLET = re.compile(r"^\s*(?:[-*+•]|\d+[.)]|\[[ xX]\])\s+")
_QUOTE = re.compile(r"^\s*(?:>\s*)+")
_SPEAKER = re.compile(r"^\s*(?:\[[^\]]{1,24}\]\s*)?[A-Za-z][\w .'-]{0,24}:\s+(?=\S)")  # "[10:42] Name: ..." or "Name: ..." in a chat log
_HEADER = re.compile(r"^\s*(from|sent|to|cc|bcc|date|subject|on .+ wrote)\s*:", re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
_LEAD = re.compile(r"^(?:(?:hey|hi|ok|okay|so|also|and|but|then)[,\s]+)*(?:please\s+|(?:can|could|would|will)\s+you\s+(?:please\s+)?|"
                   r"(?:should|shall|can|could)\s+we\s+|we\s+(?:need|should|must)\s+to\s+|we\s+(?:need|should|must)\s+|you\s+(?:need|should|must)\s+to\s+|you\s+(?:need|should|must)\s+|"
                   r"(?:i\s+)?need\s+(?:you\s+)?to\s+|todo\s*:?\s*)", re.I)


def words_in(text: str, words) -> bool:
    low = f" {' '.join(re.findall(r'[a-z0-9]+', text.lower()))} "
    return any(f" {w} " in low for w in words)


def batch_id() -> str:
    """A new activity batch id (DESK-0282 contract: b- and 12 hex digits)."""
    return "b-" + secrets.token_hex(6)


def strip_new(text: str) -> str:
    return _NEW.sub("", text or "", count=1)


def is_capture(text: str) -> bool:
    """True when the palette text starts with `new:`, the explicit capture prefix."""
    return bool(_NEW.match(text or ""))


def default_project(store: Store) -> str:
    keys = list(store.projects())
    want = str(store.config.get("capture", {}).get("project") or "").strip()
    for k in (want, *FALLBACK_PROJECTS):
        if k and k in keys:
            return k
    return keys[0] if keys else ""


def guess_project(store: Store, text: str) -> tuple[str, bool]:
    """(project key, named) for a sentence. `named` is False when nothing in the sentence named a project and the default was used.
    Strongest first: `#key`, then a ticket id, then a project's key, id prefix, alias or title as a whole word; ties go to the earliest."""
    keys = list(store.projects())
    low = (text or "").lower()
    best: tuple[int, int, str] | None = None  # (strength, -position, key)

    def offer(strength: int, pos: int, key: str):
        nonlocal best
        cand = (strength, -pos, key)
        if best is None or cand[:2] > best[:2]:
            best = cand

    for m in _HASH.finditer(text or ""):
        k = m.group(1).lower()
        if k in keys:
            offer(4, m.start(), k)
    prefixes = {}
    for k in keys:
        for p in store.prefixes_for(k):
            prefixes.setdefault(p.upper(), k)
    for m in _ID.finditer(text or ""):
        k = prefixes.get(m.group(1).upper())
        if k:
            offer(3, m.start(), k)
    for k in keys:
        names = {k, store.key_for(k).lower(), store.project_title(k).lower(), *(a.lower() for a in store.aliases_for(k))}
        for n in names:
            n = n.strip()
            if len(n) < 2:
                continue
            m = re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", low)
            if m:
                offer(2, m.start(), k)
    if best:
        return best[2], True
    return default_project(store), False


def guess_type(text: str) -> str:
    for type_, ws in TYPE_WORDS:
        if words_in(text, ws):
            return type_
    return "feature"


def guess_lane(text: str) -> str:
    """now when the sentence says urgent, now, today or asap; otherwise later (new ideas never jump the queue)."""
    return "now" if words_in(text, URGENT_WORDS) else "later"


def clean_sentence(text: str) -> str:
    """The capture text on one line: the `new:` prefix and `#project` marks taken out, spaces collapsed."""
    s = strip_new(text)
    s = _HASH.sub("", s)
    return " ".join(s.split())


def title_for(text: str, limit: int = TITLE_MAX) -> str:
    """The sentence as a title: first letter up, no closing full stop, cut at a word to fit the title limit."""
    s = clean_sentence(text).rstrip(" .")
    if not s:
        return ""
    s = s[0].upper() + s[1:]
    if len(s) <= limit:
        return s
    cut = s[:limit + 1].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return cut if len(cut) >= limit // 2 else s[:limit].rstrip()


@dataclass
class Guess:
    title: str
    project: str
    named: bool
    type: str
    lane: str
    text: str = ""

    def as_dict(self, store: Store | None = None) -> dict:
        d = {"title": self.title, "project": self.project, "named": self.named, "type": self.type, "lane": self.lane}
        if store is not None and self.project:
            d["project_title"] = store.project_title(self.project)
        return d


def guess(store: Store, text: str) -> Guess:
    s = clean_sentence(text)
    project, named = guess_project(store, strip_new(text))
    return Guess(title=title_for(text), project=project, named=named, type=guess_type(s), lane=guess_lane(s), text=s)


def create(store: Store, text: str, creator: str, *, mvp: str = "", project: str = "", type_: str = "", title: str = "", how: str = "palette") -> Ticket:
    """Write one inbox ticket for a captured sentence and return it. Raises RuleError in plain words when there is nothing to write.
    `project`, `type_` and `title` override the guesses (the paste page lets a person correct them)."""
    g = guess(store, text)
    title = " ".join((title or g.title).split())[:200]
    if not title:
        raise RuleError("Type a sentence first: what you want done, in your words.")
    project = project or g.project
    if not project:
        raise RuleError("Desk has no projects yet, so there is nowhere to put the ticket. Add projects/<key>.md first.")
    if project not in store.projects():
        raise RuleError(f"Desk has no project called {project}. Pick one from the list.")
    type_ = type_ or g.type
    pe = peoplemod.load(store.home)
    t = store.create(title, project, type_, priority="p2", size="s", owner="main", source=f"Captured from the {how}, {today()}")
    t.meta["creator"] = pe.resolve(creator) if creator else store.owner
    t.meta["lane"] = g.lane
    mvp = " ".join((mvp or "").split())
    if mvp:
        t.meta["smallest_step"] = mvp[:200]
    said = g.text
    if said and said.rstrip(" .").casefold() != title.rstrip(" .").casefold():  # only when the title lost words
        t.sections["Context"] = f"**Captured from the {how}.** The words as typed:\n\n> {said}\n"
    store.save(t)
    if not t.meta.get("assignee"):  # the same fill from assign.yaml that `drew new` and the New ticket form do
        try:
            assign_rules.apply_new(store, t)
        except assign_rules.RulesError:
            pass
    return t


def refused_name(filename: str) -> str:
    """A plain refusal when a dropped file's type is never accepted (programs and scripts), else an empty string."""
    name = (filename or "").strip().lower()
    for ext in REFUSED_EXTENSIONS:
        if name.endswith(ext):
            return (f"Desk does not take {ext} files: programs and scripts can run on this computer. "
                    "Zip it first, or attach a screenshot or a note instead.")
    return ""


# ---- paste to split ---------------------------------------------------------------------------------------------------------

@dataclass
class Proposed:
    title: str
    said: str
    project: str
    named: bool
    type: str
    lane: str
    extra: dict = field(default_factory=dict)


def is_ask(sentence: str) -> bool:
    s = sentence.strip()
    return bool(s) and ("?" in s or words_in(s, ASK_WORDS))


def tidy_ask(sentence: str) -> str:
    """An ask as a title: polite lead-ins (please, can you, we need to ...) and a closing please or question mark taken out."""
    s = " ".join(sentence.split()).strip(" -")
    for _ in range(2):
        s = _LEAD.sub("", s, count=1)
    s = re.sub(r"[\s,]*\bplease\b[\s?.!]*$", "", s, flags=re.I)
    s = s.rstrip(" ?.!")
    return title_for(s) if s else ""


def pieces(text: str) -> list[str]:
    """The sentences of a pasted text, one per line or bullet and then per sentence; mail headers, quotes marks and chat names dropped."""
    out: list[str] = []
    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        ln = raw.strip()
        if not ln or ln == "--" or _HEADER.match(ln):
            continue
        ln = _QUOTE.sub("", ln)
        ln = _BULLET.sub("", ln)
        if not ln.lower().startswith("todo"):  # "TODO: ..." is an ask, not a speaker's name
            ln = _SPEAKER.sub("", ln, count=1)
        ln = ln.strip()
        if ln:
            out.extend(x.strip() for x in _SENTENCE.split(ln) if x.strip())
    return out


def split_asks(store: Store, text: str) -> list[Proposed]:
    """One proposed ticket per ask in a pasted text, in the order they appear; a repeated ask is proposed once."""
    whole_project, whole_named = guess_project(store, text)
    seen: set[str] = set()
    rows: list[Proposed] = []
    for s in pieces(text):
        if not is_ask(s):
            continue
        title = tidy_ask(s)
        key = title.casefold()
        if not title or key in seen:
            continue
        seen.add(key)
        project, named = guess_project(store, s)
        if not named:
            project, named = whole_project, whole_named
        rows.append(Proposed(title=title, said=s, project=project, named=named, type=guess_type(s), lane=guess_lane(s)))
    return rows
