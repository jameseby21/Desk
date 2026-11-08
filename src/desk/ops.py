"""Operations shared by the CLI and the UI. Every write goes through `move`."""

from __future__ import annotations

import datetime as dt
import platform
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from .rules import RuleError, check_transition
from .store import Store, join_profile, split_profile
from .ticket import Ticket, slugify, today

PRIVATE_NAMES = {"private_context.md"}
PRIVATE_PARTS = {"jeby-private"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
_KEY = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


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
    t.meta["done_at"] = dt.datetime.now().replace(microsecond=0).isoformat()  # local time, for combos
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


# ---- projects ----------------------------------------------------------------

def rename_project(store: Store, old: str, new: str, *, prefix: str | None = None, name: str | None = None,
                   actor: str = "desk") -> tuple[Path, list[Ticket]]:
    """Rename projects/<old>.md to <new>.md, record the old key and prefix as aliases, and point every
    ticket of the project at the new key. Ids never change; the old prefix stays valid through `aliases:`."""
    projects = store.projects()
    if old not in projects:
        raise RuleError(f"No project `{old}` (no projects/{old}.md).")
    if not _KEY.match(new):
        raise RuleError("A project key is lowercase letters, digits and hyphens, like `learninglabs`.")
    if new in projects or (store.projects_dir / f"{new}.md").exists():
        raise RuleError(f"Project `{new}` already exists; pick another key or merge by hand.")
    if prefix is not None and not re.match(r"^[A-Za-z0-9][A-Za-z0-9-]*$", prefix):
        raise RuleError("A prefix is letters, digits and hyphens, like `LEARN`.")

    old_path = projects[old]
    meta, body = split_profile(old_path.read_text(encoding="utf-8"))
    old_prefix = store.key_for(old)
    new_prefix = prefix.upper() if prefix else old_prefix

    aliases = [str(a) for a in (meta.get("aliases") or []) if str(a).strip()]
    for a in (old, old_prefix):
        if a not in aliases and a.upper() != new_prefix and a != new:
            aliases.append(a)
    ordered: dict = {"key": new}
    if new_prefix != new.upper():
        ordered["prefix"] = new_prefix
    if name:
        ordered["name"] = name
    elif meta.get("name"):
        ordered["name"] = meta["name"]
    ordered["aliases"] = aliases
    for k, v in meta.items():
        if k not in ordered and k not in ("key", "prefix", "name", "aliases"):
            ordered[k] = v
    if name:
        lines = body.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("# "):
                lines[i] = f"# {name}"
                break
        else:
            lines.insert(0, f"# {name}")
        body = "\n".join(lines) + ("\n" if body.endswith("\n") else "")
    new_path = store.projects_dir / f"{new}.md"
    new_path.write_text(join_profile(ordered, body), encoding="utf-8", newline="\n")
    old_path.unlink()

    changed: list[Ticket] = []
    for t in store.tickets():
        if t.project != old:
            continue
        t.meta["project"] = new
        t.append_line("Comments", _stamp(actor, f"project renamed {old} -> {new}"))
        store.save(t)
        changed.append(t)
    return new_path, changed


# ---- attachments -------------------------------------------------------------

def attach(store: Store, t: Ticket, source: Path, caption: str | None = None, actor: str | None = None) -> Path:
    """Copy a file into tickets/assets/<ID>/ and add an image or link line to the ticket's Attachments."""
    source = Path(source)
    if not source.is_file():
        raise RuleError(f"{source} is not a file")
    dest_dir = store.ticket_assets_dir(t.id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe = _SAFE_NAME.sub("-", source.name).strip("-.") or "file"
    dest = dest_dir / safe
    n = 2
    while dest.exists():
        dest = dest_dir / f"{Path(safe).stem}-{n}{Path(safe).suffix}"
        n += 1
    shutil.copy2(source, dest)
    text = (caption or source.name).strip().replace("]", ")")
    rel = f"assets/{t.id}/{dest.name}"
    line = f"![{text}]({rel})" if dest.suffix.lower() in IMAGE_SUFFIXES else f"[{text}]({rel})"
    t.append_line("Attachments", line)
    store.save(t)
    return dest


def capture_screen(dest: Path) -> Path:
    """Take a PNG of the whole screen into `dest`. Windows, macOS and Linux (import or gnome-screenshot)."""
    dest = Path(dest)
    system = platform.system()
    if system == "Windows":
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
            "$b = [System.Windows.Forms.SystemInformation]::VirtualScreen; "
            "$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height; "
            "$g = [System.Drawing.Graphics]::FromImage($bmp); "
            "$g.CopyFromScreen($b.Left, $b.Top, 0, 0, $bmp.Size); "
            f"$bmp.Save('{str(dest).replace(chr(39), chr(39) * 2)}', [System.Drawing.Imaging.ImageFormat]::Png); "
            "$g.Dispose(); $bmp.Dispose()"
        )
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
    elif system == "Darwin":
        cmd = ["screencapture", "-x", str(dest)]
    elif system == "Linux":
        if shutil.which("import"):
            cmd = ["import", "-window", "root", str(dest)]
        elif shutil.which("gnome-screenshot"):
            cmd = ["gnome-screenshot", "-f", str(dest)]
        else:
            raise RuleError("No screenshot tool found (install ImageMagick `import` or gnome-screenshot).")
    else:
        raise RuleError(f"Screen capture is not supported on {system}.")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuleError(f"Screen capture failed: {e}") from e
    if r.returncode != 0 or not dest.exists():
        raise RuleError(f"Screen capture failed: {(r.stderr or r.stdout).strip() or 'no image written'}")
    return dest


def snap(store: Store, t: Ticket, caption: str | None = None) -> Path:
    """Capture the screen and attach it as snap-<timestamp>.png."""
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    with tempfile.TemporaryDirectory(prefix="desk-snap-") as tmp:
        shot = capture_screen(Path(tmp) / f"snap-{stamp}.png")
        return attach(store, t, shot, caption or f"Screen {stamp}")
