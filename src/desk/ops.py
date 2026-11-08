"""Operations shared by the CLI and the UI. Every write goes through `move`."""

from __future__ import annotations

import datetime as dt
import shutil
import zipfile
from pathlib import Path

from .rules import RuleError, check_transition
from .store import Store
from .ticket import Ticket, slugify, today

PRIVATE_NAMES = {"private_context.md"}
PRIVATE_PARTS = {"jeby-private"}


def _stamp(actor: str, what: str) -> str:
    return f"- {today()} · {actor} · {what}"


def move(store: Store, t: Ticket, new: str, *, actor: str | None = None, reason: str | None = None, note: str | None = None) -> Ticket:
    check_transition(t, new, store, actor=actor, reason=reason)
    old = t.status
    if new == "blocked":
        t.meta["blocked_from"] = old
    t.meta["status"] = new
    if note:
        t.append_line("Comments", _stamp(actor or "desk", note))
    store.save(t)
    return t


def new(store: Store, title: str, project: str, type_: str = "slice", **kw) -> Ticket:
    if project not in store.projects() and store.projects():
        raise RuleError(f"Unknown project `{project}`. Add projects/{project}.md first.")
    t = store.create(title, project, type_, **kw)
    store.save(t)
    return t


def claim(store: Store, t: Ticket, owner: str | None = None) -> Ticket:
    who = owner or t.project
    check_transition(t, "claimed", store, actor=who)
    t.meta["owner"] = who
    t.meta.setdefault("branch", f"{t.project}/{t.id}-{slugify(str(t.meta.get('title', '')), 32)}")
    if not t.meta.get("branch"):
        t.meta["branch"] = f"{t.project}/{t.id}-{slugify(str(t.meta.get('title', '')), 32)}"
    return move(store, t, "claimed", actor=who)


def start(store: Store, t: Ticket) -> Ticket:
    return move(store, t, "in_progress")


def report(store: Store, t: Ticket, text: str) -> Ticket:
    if not text.strip():
        raise RuleError("A report needs words: what changed, how it was verified, the commands run.")
    t.set_section("Report", text)
    if t.status in ("claimed", "ready"):
        # a session may skip `start`; the report still counts as work done
        t.meta["status"] = "in_progress"
    return move(store, t, "review")


def decide(store: Store, t: Ticket, approve: bool, reason: str, actor: str | None = None) -> Ticket:
    actor = actor or "main"
    new = "approved" if approve else "changes_requested"
    check_transition(t, new, store, actor=actor, reason=reason)
    t.append_line("Decision", f'{_stamp(actor, new)} · "{reason.strip()}"')
    return move(store, t, new, actor=actor, reason=reason)


def done(store: Store, t: Ticket, commit: str | None = None) -> Ticket:
    check_transition(t, "done", store)
    if commit:
        t.append_line("Decision", _stamp("desk", f"merged · {commit}"))
    return move(store, t, "done")


def block(store: Store, t: Ticket, on: str, reason: str, actor: str | None = None) -> Ticket:
    if not on.strip():
        raise RuleError("Stuck needs a name: who or which ticket is it waiting on?")
    if len(reason.strip()) < 4:
        raise RuleError("Stuck needs a reason.")
    t.meta["blocked_on"] = on.strip()
    return move(store, t, "blocked", actor=actor, note=f"blocked on {on.strip()}: {reason.strip()}")


def unblock(store: Store, t: Ticket, actor: str | None = None, to: str | None = None) -> Ticket:
    if t.status != "blocked":
        raise RuleError(f"{t.id} is not blocked")
    target = to or str(t.meta.get("blocked_from") or "ready")
    t.meta["status"] = target
    t.meta["blocked_on"] = ""
    t.meta.pop("blocked_from", None)
    t.append_line("Comments", _stamp(actor or "desk", f"unblocked → {target}"))
    store.save(t)
    return t


def reject(store: Store, t: Ticket, reason: str, actor: str | None = None) -> Ticket:
    check_transition(t, "rejected", store, actor=actor, reason=reason)
    t.append_line("Decision", f'{_stamp(actor or "main", "rejected")} · "{reason.strip()}"')
    return move(store, t, "rejected", actor=actor, reason=reason)


def comment(store: Store, t: Ticket, text: str, actor: str | None = None) -> Ticket:
    if not text.strip():
        raise RuleError("An empty comment says nothing.")
    t.append_line("Comments", _stamp(actor or "desk", text.strip()))
    store.save(t)
    return t


def _private(path: Path) -> bool:
    if path.name.lower() in PRIVATE_NAMES:
        return True
    return any(part.lower() in PRIVATE_PARTS for part in path.parts)


def intake(store: Store, source: Path, topic: str, project: str, actor: str = "main") -> tuple[Ticket, Path]:
    """Copy a hand-off folder or zip into intake/<date>-<topic>/ and open an intake ticket."""
    source = Path(source)
    if not source.exists():
        raise RuleError(f"{source} does not exist")
    dest = store.intake_dir / f"{today()}-{slugify(topic, 40)}"
    if dest.exists():
        raise RuleError(f"{dest} already exists")

    if source.is_file() and source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            bad = [n for n in names if _private(Path(n))]
            if bad:
                raise RuleError("Refused: the package contains private files: " + ", ".join(bad[:5]))
            dest.mkdir(parents=True)
            for n in names:
                target = (dest / n).resolve()
                if dest.resolve() not in target.parents:
                    raise RuleError(f"Refused: unsafe path in zip: {n}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(n) as src, target.open("wb") as out:
                    shutil.copyfileobj(src, out)
    elif source.is_dir():
        files = [p for p in source.rglob("*") if p.is_file()]
        bad = [p for p in files if _private(p.relative_to(source)) or _private(p)]
        if bad:
            raise RuleError("Refused: the package contains private files: " + ", ".join(str(p.relative_to(source)) for p in bad[:5]))
        shutil.copytree(source, dest)
    else:
        if _private(source):
            raise RuleError(f"Refused: {source.name} is private")
        dest.mkdir(parents=True)
        shutil.copy2(source, dest / source.name)

    rel = dest.relative_to(store.home).as_posix()
    t = store.create(f"Intake: {topic}", project, "intake", owner=actor, priority="p2", size="s", source=rel)
    t.set_section("Context", f"Research hand-off received {today()} from `{source.name}`, copied to `{rel}/`.")
    t.set_section("Outcome", "The package is read, the in-scope items are confirmed with the owner, and slices exist for each.")
    t.set_section("Acceptance", "- [ ] Package read end to end\n- [ ] In-scope items confirmed with the owner\n- [ ] One slice ticket per deliverable, each with `source` pointing into the intake folder")
    t.set_section("Out of scope", "Doing the work the package describes. This ticket only slices it.")
    store.save(t)
    return t, dest


def load_report_file(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def week_ago() -> str:
    return (dt.date.today() - dt.timedelta(days=7)).isoformat()
