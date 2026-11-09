"""Who is acting (DESK-0144). Every write records the actor, so two sessions writing the same ticket can be told apart.

An actor has a name, a kind (person, thread = a coworker thread, session = a Claude Code session, subagent), an instance (a session or
subagent id, or the machine for a person or a browser), a source (ui, cli, mcp) and how the identity was established. Establishing is
honest about its limits: the operating-system user or the git user mapped to a person in people.yaml is *confirmed*; a name only claimed
(`--as patch`, DESK_ACTOR, a device cookie, the owner assumed because nobody is named yet) is shown as *claimed*, never as confirmed.

CLI order: a subagent id, then a name given (`--as` or DESK_ACTOR), then a Claude Code shell (CLAUDECODE), then the OS user, then the
git user.name. UI order: the OS user the server runs as, then the device cookie; the store owner may Act as someone else, and the record
then reads `james (as chaz)`. A subagent is always itself (`subagent:<id>` with its parent), never Patch or James.

Environment a session or subagent can set so it is told apart: DESK_ACTOR, DESK_ACTOR_KIND (person, thread, session, subagent),
DESK_INSTANCE (a session id or a short name), DESK_SUBAGENT_ID, DESK_PARENT_SESSION. CLAUDE_CODE_SESSION_ID is read when the host sets it."""

from __future__ import annotations

import contextvars
import getpass
import json
import os
import platform
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import people as peoplemod

KINDS = ("person", "thread", "session", "subagent")
SOURCES = ("ui", "cli", "mcp")
CURRENT: contextvars.ContextVar["Actor | None"] = contextvars.ContextVar("desk_actor", default=None)
_KIND_OF = {"person": "person", "external": "person", "coworker": "thread", "agent": "session", "code": "session"}
PROTECTED = {"patch", "main", "james"}  # a subagent is never recorded as one of these
_TAG = re.compile(r"\s*<!-- by ([^>]*?) -->")
LOG = "writes.jsonl"


@dataclass(frozen=True)
class Actor:
    name: str
    kind: str = "person"
    instance: str = ""
    source: str = "cli"
    how: str = "claimed"
    confirmed: bool = False
    as_: str = ""  # the person the owner is acting as
    parent: str = ""  # a subagent's parent session

    def display(self) -> str:
        return f"{self.name} (as {self.as_})" if self.as_ else self.name

    def short(self) -> str:
        """`person · ui · os-user · confirmed`: kind, source, how it was established, and whether that is a check or a claim."""
        return f"{self.kind} · {self.source} · {self.how} · {'confirmed' if self.confirmed else 'claimed'}"

    def tag(self) -> str:
        parts = [f"kind={self.kind}", f"src={self.source}", f"via={self.how.replace(' ', '-')}", f"ok={int(self.confirmed)}"]
        if self.instance:
            parts.append(f"inst={_safe(self.instance)}")
        if self.as_:
            parts.append(f"as={_safe(self.as_)}")
        if self.parent:
            parts.append(f"parent={_safe(self.parent)}")
        return "<!-- by " + " ".join(parts) + " -->"

    def as_dict(self) -> dict:
        return {"actor": self.name, "kind": self.kind, "instance": self.instance, "source": self.source, "how": self.how,
                "confirmed": self.confirmed, "as": self.as_, "parent": self.parent}


def _safe(v: str) -> str:
    return re.sub(r"[^\w.:@-]+", "-", str(v)).strip("-")[:48]


def parse_tag(text: str) -> tuple[dict, str]:
    """({kind, src, via, ok, inst, as, parent}, text without the tag) for a comment's words; ({}, text) when there is no tag."""
    m = _TAG.search(text or "")
    if not m:
        return {}, text
    info = dict(re.findall(r"(\w+)=(\S*)", m.group(1)))
    return info, (text[:m.start()] + text[m.end():]).rstrip()


def describe(info: dict) -> str:
    """The short form for a parsed tag, or `not recorded` for a comment written before identities were kept."""
    if not info:
        return "not recorded"
    return f"{info.get('kind', '?')} · {info.get('src', '?')} · {info.get('via', '?').replace('-', ' ')} · {'confirmed' if info.get('ok') == '1' else 'claimed'}" + (
        f" · as {info['as']}" if info.get("as") else "") + (f" · parent {info['parent']}" if info.get("parent") else "")


# -- finding who ------------------------------------------------------------------------

def os_user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # no user database, or no login name
        return os.environ.get("USERNAME") or os.environ.get("USER") or ""


def git_user() -> str:
    try:
        r = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _person_for(pe: peoplemod.People, name: str) -> peoplemod.Person | None:
    n = (name or "").strip().lower()
    if not n:
        return None
    for p in pe.list:
        if n in {u.lower() for u in p.os_users} or n == p.key.lower() or n == p.name.lower() or n in {a.lower() for a in p.aliases}:
            return p
    return None


def _kind_of(p: peoplemod.Person | None, default: str) -> str:
    return _KIND_OF.get(p.kind, default) if p else default


def instance_here() -> str:
    return platform.node() or "this-machine"


def resolve_cli(store, claimed: str | None = None, env=None, user: str | None = None, git: str | None = None) -> Actor:
    """The actor behind a command line call, and how sure we are of it."""
    env = os.environ if env is None else env
    pe = peoplemod.load(store.home)
    kind_env = (env.get("DESK_ACTOR_KIND") or "").strip().lower()
    inst = env.get("DESK_INSTANCE") or env.get("CLAUDE_CODE_SESSION_ID") or env.get("CLAUDE_SESSION_ID") or ""
    sub = env.get("DESK_SUBAGENT_ID", "").strip()
    named = (claimed or env.get("DESK_ACTOR") or "").strip()
    how_named = "claimed" if claimed else "env-DESK_ACTOR"
    if sub or kind_env == "subagent":
        ident = sub or _safe(inst) or _safe(named) or "unknown"
        return Actor(f"subagent:{ident}", "subagent", ident, "cli", "agent-id", False, parent=env.get("DESK_PARENT_SESSION", "") or inst)
    if named:
        p = pe.get(named)
        kind = kind_env if kind_env in KINDS else _kind_of(p, "session" if named.lower() in PROTECTED - {"james"} else "person")
        return Actor(named, kind, inst or (instance_here() if kind == "person" else ""), "cli", how_named, False)
    if env.get("CLAUDECODE"):  # a Claude Code shell that did not say which session it is: not James, not confirmed
        return Actor("claude-code", "session", inst, "cli", "env-CLAUDECODE", False)
    u = os_user() if user is None else user
    p = _person_for(pe, u)
    if p:
        return Actor(p.key, _kind_of(p, "person"), instance_here(), "cli", "os-user", True)
    g = git_user() if git is None else git
    p = _person_for(pe, g)
    if p:
        return Actor(p.key, _kind_of(p, "person"), instance_here(), "cli", "git-user", True)
    return Actor(u or store.owner, "person", instance_here(), "cli", "os-user-unlisted", False)


def resolve_ui(store, cookies: dict, user: str | None = None) -> Actor:
    """The actor behind a browser request. The server runs on this machine as the OS user; a device cookie names someone who is not
    listed; with neither, the store owner is assumed and shown as claimed until someone is named."""
    pe = peoplemod.load(store.home)
    device = _safe(cookies.get("desk_device", "")) or "browser"
    p = _person_for(pe, os_user() if user is None else user)
    if p:
        base = Actor(p.key, _kind_of(p, "person"), device, "ui", "os-user", True)
    else:
        q = pe.get(cookies.get("desk_who", ""))
        if q:
            base = Actor(q.key, _kind_of(q, "person"), device, "ui", "device-cookie", False)
        else:
            base = Actor(store.owner, "person", device, "ui", "assumed-owner", False)
    act = (cookies.get("desk_act_as") or "").strip()
    if act and base.confirmed and base.name == store.owner and pe.get(act) and pe.get(act).key != base.name:
        return Actor(base.name, base.kind, base.instance, base.source, base.how, True, as_=pe.get(act).key)
    return base


def unnamed(actor: Actor) -> bool:
    return actor.how == "assumed-owner"


# -- the write log ----------------------------------------------------------------------

def record_write(home: Path, ticket_id: str) -> None:
    """One line per save in `.desk/writes.jsonl`: when, which ticket, and everything known about the actor."""
    a = CURRENT.get()
    if a is None:
        return
    try:
        p = Path(home) / ".desk" / LOG
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "ticket": ticket_id, **a.as_dict()}, ensure_ascii=False) + "\n")
    except OSError:
        pass  # the log is a record, never a reason to refuse a save


def read_log(home: Path, ticket: str | None = None) -> list[dict]:
    p = Path(home) / ".desk" / LOG
    out = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not ticket or row.get("ticket") == ticket:
                out.append(row)
    except OSError:
        pass
    return out
