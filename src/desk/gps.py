"""GPS: Goal, Proof, Steps (DESK-0215; the `gps` skill, James's rule of 2026-11-09 after Sandeep Swadia).

A ticket passes when a reader who was not in the conversation can tell what it is for and how to know it is done:
- G: the Outcome opens with one sentence (after an optional "**Goal.**" marker or a bold lead) of at most `goal_max_words` words.
- P: at least one Acceptance checkbox line, and a How to validate that names something observable (run, open, prints, shows, lists,
  returns, see); a project may require more kinds of proof (`command`: a backticked span; `screenshot`: the word, or an image file).
- S: the Prompt holds the steps, and no step hides behind a handwaving word (HANDWAVING).

A project profile's `gps:` block (goal_max_words, proof_requires, extra_handwaving, allow) changes the defaults for its tickets.
`drew validate` prints one warning per ticket that fails, naming the failing letters and why. Scored: the tickets that have left triage
(ready, claimed, in_progress, review, changes_requested, approved, blocked), since a failing letter is fixed before work starts; inbox
and triage tickets are not sliced yet, and done and rejected tickets are history."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import readability
from .ticket import CLOSED, Ticket, validate_steps

if TYPE_CHECKING:
    from .store import Store

GOAL_MAX_WORDS = 30
HANDWAVING = ("handle", "deal with", "clean up", "improve", "optimize", "figure out", "as needed", "as appropriate", "etc.", "and so on",
              "various", "relevant", "somehow", "make sure it works", "TBD")
OBSERVABLE = ("run", "open", "prints", "shows", "lists", "returns", "see")
PROOF_KINDS = ("observable", "command", "screenshot")  # anything else in proof_requires is a word the Proof must contain
NOT_SCORED = CLOSED | {"inbox", "triage"}  # not sliced yet, or history

_GOAL_MARK = re.compile(r"^\*\*\s*Goal\s*[.:]?\s*\*\*\s*[.:]?\s*", re.I)
_OBSERVABLE = re.compile(r"\b(?:run|runs|ran|open|opens|opened|print|prints|printed|show|shows|shown|list|lists|listed|return|returns|returned|see|sees|saw)\b", re.I)
_CODE_SPAN = re.compile(r"`[^`\n]+`")
_SCREENSHOT = re.compile(r"screenshot|\.(?:png|jpe?g|webp|gif)\b", re.I)


@dataclass(frozen=True)
class Settings:
    goal_max_words: int = GOAL_MAX_WORDS
    proof_requires: tuple[str, ...] = ("observable",)
    handwaving: tuple[str, ...] = HANDWAVING


def _words_list(v) -> list[str]:
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    return []


def settings_from(block) -> Settings:
    """Settings from a profile's `gps:` mapping; anything missing or malformed keeps its default."""
    if not isinstance(block, dict):
        return Settings()
    max_words = GOAL_MAX_WORDS
    try:
        max_words = max(1, int(block.get("goal_max_words", GOAL_MAX_WORDS)))
    except (TypeError, ValueError):
        pass
    requires = tuple(_words_list(block.get("proof_requires"))) or ("observable",)
    allow = {x.lower().rstrip(".") for x in _words_list(block.get("allow"))}
    words = [w for w in HANDWAVING if w.lower().rstrip(".") not in allow]
    words += [w for w in _words_list(block.get("extra_handwaving")) if w.lower().rstrip(".") not in allow and w not in words]
    return Settings(max_words, requires, tuple(words))


def settings(store: "Store", project: str) -> Settings:
    meta, _body = store.project_profile(project)
    return settings_from(meta.get("gps") if isinstance(meta, dict) else None)


def goal_sentence(outcome: str) -> str | None:
    """The first sentence of the Outcome's opening paragraph, without the **Goal.** marker or bold marks; None when the Outcome does not
    open with prose."""
    for kind, text in readability.blocks(outcome or ""):
        if kind != "para":
            return None
        t = _GOAL_MARK.sub("", text.strip())
        t = t.replace("**", "").strip()
        if not t:
            return None
        masked = _CODE_SPAN.sub("x", t)
        masked = re.sub(r"\d+\.\d+", "n", masked)
        m = re.match(r"(.+?[.!?])[\"')\]]*(?:\s+(?=[A-Z0-9\"'(\[`])|$)", masked, re.S)
        return (m.group(1) if m else masked).strip()
    return None


def _pattern(phrase: str) -> re.Pattern:
    words = phrase.strip().rstrip(".").split()
    if not words:
        return re.compile(r"(?!x)x")
    body = r"\s+".join(re.escape(w) for w in words[:-1])
    last = re.escape(words[-1]) + r"(?:s|es|d|ed|ing)?"
    return re.compile(r"\b" + (body + r"\s+" if body else "") + last + r"\b", re.I)


def handwaving_in(text: str, words: tuple[str, ...] = HANDWAVING) -> list[str]:
    """The handwaving phrases found in the text, in list order; code spans are not steps and are skipped."""
    plain = _CODE_SPAN.sub(" ", text or "")
    return [w for w in words if _pattern(w).search(plain)]


def score(t: Ticket, st: Settings = Settings()) -> dict[str, str]:
    """{letter: why it fails} for G, P and S; an empty dict is a pass."""
    out: dict[str, str] = {}
    goal = goal_sentence(t.section("Outcome"))
    if not goal:
        out["G"] = "Outcome has no one-sentence Goal; open it with **Goal.** and one sentence"
    else:
        n = len(goal.split())
        if n > st.goal_max_words:
            out["G"] = f"Goal sentence is {n} words (limit {st.goal_max_words}); one sentence, or two tickets"
    acc = t.acceptance()
    steps = t.validate_steps()
    proof_text = steps + "\n" + "\n".join(x for _, x in acc)
    p: list[str] = []
    if not acc:
        p.append("no Acceptance checkbox line")
    if not steps:
        p.append("no How to validate line")
    else:
        for req in st.proof_requires:
            key = req.lower()
            if key == "observable" and not _OBSERVABLE.search(steps):
                p.append(f"How to validate names nothing observable ({', '.join(OBSERVABLE)})")
            elif key == "command" and not _CODE_SPAN.search(steps):
                p.append("How to validate names no command (a backticked span)")
            elif key == "screenshot" and not _SCREENSHOT.search(steps + "\n" + t.section("Evidence") + "\n" + t.section("Attachments")):
                p.append("no screenshot named in How to validate, Evidence or Attachments")
            elif key not in PROOF_KINDS and not re.search(r"\b" + re.escape(req) + r"\b", proof_text, re.I):
                p.append(f"Proof does not mention \"{req}\"")
    if p:
        out["P"] = "; ".join(p)
    prompt = validate_steps(t.section("Prompt"))
    if not prompt:
        out["S"] = "Prompt is empty; write the numbered steps"
    else:
        found = handwaving_in(prompt, st.handwaving)
        if found:
            out["S"] = "Prompt handwaves (" + ", ".join(f'"{w}"' for w in found) + "); name the action and its object"
    return out


def _letters(keys) -> str:
    ks = list(keys)
    return ks[0] if len(ks) == 1 else ", ".join(ks[:-1]) + " and " + ks[-1]


def warning(t: Ticket, store: "Store") -> str | None:
    """The `drew validate` line for one ticket past triage that fails GPS, or None."""
    if t.status in NOT_SCORED:
        return None
    fails = score(t, settings(store, t.project))
    if not fails:
        return None
    return f"{t.id}: GPS fails {_letters(fails)}: " + "; ".join(fails.values())
