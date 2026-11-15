"""Read-only links to files on this computer (DESK-0291). A ticket may name a file by its absolute path; Desk serves it only after the
path is resolved (links and junctions followed) and found inside a folder the owner allows, and never when its name looks like a key,
a password store or a private folder. Every refusal is one plain sentence a person can act on.

Allowed folders: desk.toml `[files] allowed_roots` (a list; `~` and `%VAR%` are expanded). Default: the store itself, Documents and
Downloads. The name rules below always apply; folder names are checked below the allowed folder, so a store that sits under AppData
(a test folder) still works while AppData inside Documents does not."""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

# File names that are never served, whatever folder they sit in (case does not matter).
DENY_NAMES = (".env", ".env.*", "*.pem", "*.key", "*.pfx", "*.p12", "id_rsa*", "id_ed25519*", "id_ecdsa*", "*.kdbx", "*.kdb")
# Folder names that are never entered below an allowed folder.
DENY_DIRS = {".ssh", ".git", ".claude", "jeby-private", "secure docs", "appdata", ".gnupg", ".aws"}
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9]|conin\$|conout\$)(\..*)?$", re.I)  # Windows device names
_SHORT = re.compile(r"~\d")  # an 8.3 short name such as PROGRA~1
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
MAX_PATH_CHARS = 1000


class Refused(Exception):
    """The path is not served. `args[0]` is the plain sentence shown to the person; `status` is the HTTP code."""

    def __init__(self, reason: str, status: int = 403):
        super().__init__(reason)
        self.status = status


def default_roots(home: Path) -> list[Path]:
    user = Path(os.environ.get("USERPROFILE") or Path.home())
    return [Path(home), user / "Documents", user / "Downloads"]


def allowed_roots(home: Path, config: dict | None = None) -> list[Path]:
    """The folders Desk may open, resolved, that exist. desk.toml [files] allowed_roots replaces the default list."""
    raw = ((config or {}).get("files") or {}).get("allowed_roots")
    if isinstance(raw, str):
        raw = [raw]
    paths = [Path(os.path.expandvars(os.path.expanduser(str(p)))) for p in raw] if raw else default_roots(home)
    out: list[Path] = []
    for p in paths:
        try:
            r = Path(os.path.realpath(p))
        except (OSError, ValueError):
            continue
        if r.is_dir() and r not in out:
            out.append(r)
    return out


def _norm(p: Path | str) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


def _under(path: Path, root: Path) -> bool:
    a, b = _norm(path), _norm(root)
    try:
        return os.path.commonpath([a, b]) == b
    except ValueError:  # different drives
        return False


def denied_name(name: str) -> bool:
    low = name.lower()
    return any(fnmatch.fnmatch(low, pat) for pat in DENY_NAMES)


def _denied_below(path: Path, root: Path) -> str:
    """The reason a resolved path is refused by the name rules, or ''."""
    if denied_name(path.name):
        return "Desk never opens key, password or secret files."
    rel = Path(os.path.relpath(path, root)) if _under(path, root) else path
    for part in rel.parts:
        if part.lower() in DENY_DIRS:
            return f"Desk never opens files inside a folder named {part}."
    return ""


def clean(raw: str) -> str:
    """The path as written, without a file:/// prefix. Refuses shapes Windows reads in surprising ways, before anything touches the disk."""
    p = (raw or "").strip().strip('"')
    if not p:
        raise Refused("Give the full path of a file, such as C:/Users/you/Documents/notes.md.", 400)
    if len(p) > MAX_PATH_CHARS or any(ord(c) < 32 for c in p):
        raise Refused("That path is too long or holds hidden characters.", 400)
    if p.lower().startswith("file:"):
        rest = unquote(p[5:])
        if rest.startswith("///"):
            p = rest[3:] if _DRIVE.match(rest[3:]) else rest[2:]
        elif rest.startswith("//"):
            raise Refused("Desk only opens files on this computer, not on a network share.")
        else:
            p = rest
    if p.startswith(("\\\\", "//", "\\/", "/\\")):
        raise Refused("Desk only opens files on this computer, not on a network share or a device path.")
    for form in {p, unquote(p), unquote(unquote(p))}:
        if any(seg in ("..", ".") for seg in re.split(r"[\\/]", form)):
            raise Refused("Paths that step up a folder (..) are not accepted; give the full path.")
    if sys.platform == "win32" or _DRIVE.match(p):
        if not _DRIVE.match(p):
            raise Refused("Give the full path, starting with the drive letter, such as C:/Users/you/Documents/notes.md.", 400)
        if ":" in p[2:]:
            raise Refused("A colon may only follow the drive letter; hidden parts of a file are not served.")
    elif not p.startswith("/"):
        raise Refused("Give the full path, starting at the top folder.", 400)
    for seg in re.split(r"[\\/]", p)[1:]:
        if not seg:
            continue
        if seg != seg.rstrip(" ."):
            raise Refused("A folder or file name may not end in a dot or a space.")
        if _RESERVED.match(seg):
            raise Refused("That name is a device on Windows, not a file.")
        if _SHORT.search(seg):
            raise Refused("Short names such as PROGRA~1 are not accepted; use the full name.")
    return p


def check(home: Path, raw: str, config: dict | None = None, *, want: str = "file") -> Path:
    """The resolved path when Desk may open it (want='file': an existing file; 'any': a file or folder), else raise Refused."""
    p = clean(raw)
    roots = allowed_roots(home, config)
    if not roots:
        raise Refused("No allowed folders exist; set [files] allowed_roots in desk.toml.")
    asked = Path(os.path.abspath(p))
    try:
        real = Path(os.path.realpath(asked))
    except (OSError, ValueError):
        raise Refused("Desk could not read that path.", 404)
    root = next((r for r in roots if _under(real, r)), None)
    if root is None:
        if any(_under(asked, r) for r in roots):
            raise Refused("That path is a link that leads outside the folders Desk may open.")
        raise Refused("That file is outside the folders Desk may open. Add its folder to [files] allowed_roots in desk.toml.")
    reason = _denied_below(asked, next((r for r in roots if _under(asked, r)), root)) or _denied_below(real, root)
    if reason:
        raise Refused(reason)
    if want == "file" and not real.is_file():
        raise Refused("No file at that path.", 404)
    if want == "any" and not real.exists():
        raise Refused("Nothing at that path.", 404)
    return real


def launch(cmd: str) -> None:
    """Start Explorer. A module-level function so tests replace it and never open a window."""
    subprocess.Popen(cmd, close_fds=True)  # noqa: S603 - the path was checked and holds no quote


def reveal(path: Path) -> str:
    """Show the file selected in Windows Explorer (a folder opens). Returns the plain success sentence."""
    if sys.platform != "win32":
        raise Refused("Open in Explorer works on Windows only.", 501)
    if '"' in str(path):
        raise Refused("That path holds a quote mark, which Explorer cannot take.", 400)
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "explorer.exe")
    launch(f'"{exe}" /select,"{path}"' if path.is_file() else f'"{exe}" "{path}"')
    return f"Explorer is opening with {path.name} selected."
