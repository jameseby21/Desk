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
import shutil
import subprocess
import tempfile
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

    def tag(self, extra: str = "") -> str:
        parts = [f"kind={self.kind}", f"src={self.source}", f"via={self.how.replace(' ', '-')}", f"ok={int(self.confirmed)}"]
        if self.instance:
            parts.append(f"inst={_safe(self.instance)}")
        if self.as_:
            parts.append(f"as={_safe(self.as_)}")
        if self.parent:
            parts.append(f"parent={_safe(self.parent)}")
        if extra:
            parts.append(extra)
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


# -- verified authors (DESK-0259, DESK-0262) ---------------------------------------------
# Asymmetric signatures, so nothing Desk stores can sign. Each actor holds its own SSH private key (DESK_ACTOR_KEY = the path to it, in
# that actor's environment only); Desk keeps only public keys, `<actor>.pub` in `DESK_KEYS_DIR` or `~/.desk/actor-keys/`. A comment is
# signed with `ssh-keygen -Y sign` over (actor, ticket, time, words) and checked with `ssh-keygen -Y verify` against the stored public key.
# Why ssh-keygen and not Ed25519 through the `cryptography` package: that package is not a Desk dependency, and OpenSSH ships with Windows
# 10/11, macOS and Linux, so nothing new is installed. The earlier scheme (an HMAC keyed by sha256(token), kept in the key directory) let
# anyone who could read that directory forge a verified sign-off (pass 3 review, desk-pr14).
# Issuing or replacing a key is for the store owner at their own terminal only (`may_issue`); every issue or rotation is logged.

NAMESPACE = "desk-comment"
_ARMOR = ("-----BEGIN SSH SIGNATURE-----", "-----END SSH SIGNATURE-----")
_SESSION_ENV = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "DESK_ACTOR", "DESK_SUBAGENT_ID", "DESK_ACTOR_KEY")


def keys_dir(env=None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("DESK_KEYS_DIR") or (Path.home() / ".desk" / "actor-keys"))


def _principal(actor: str) -> str:
    return _safe((actor or "").lower())


def _pub_file(actor: str, env=None) -> Path:
    return keys_dir(env) / (_principal(actor) + ".pub")


def _keygen() -> str:
    return shutil.which("ssh-keygen") or ""


def _run_keygen(args: list[str], data: bytes | None = None) -> subprocess.CompletedProcess | None:
    kg = _keygen()
    if not kg:
        return None
    try:
        return subprocess.run([kg, *args], input=data if data is not None else b"", capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None


def _pub_of(text: str) -> str:
    """`<type> <base64>` of an OpenSSH public key line, without its comment; '' when it is not one."""
    parts = (text or "").split()
    return f"{parts[0]} {parts[1]}" if len(parts) >= 2 and re.fullmatch(r"(ssh|ecdsa|sk)-[\w@.-]+", parts[0]) else ""


def _payload(actor: str, ticket_id: str, when: str, text: str) -> bytes:
    return f"{actor.lower()}\n{ticket_id}\n{when}\n{text}".encode("utf-8")


def may_issue(store, env=None, user: str | None = None) -> tuple[bool, str, "Actor"]:
    """(allowed, reason, who asked): only the store owner, confirmed by the OS user and not inside any agent session, issues or rotates
    keys. The environment check stops a session that says what it is; one that strips its environment still leaves a line in the log."""
    env = os.environ if env is None else env
    a = resolve_cli(store, None, env=env, user=user)
    held = [k for k in _SESSION_ENV if env.get(k)]
    if held:
        return False, f"Refused: keys are issued only by the owner at their own terminal, not from a session ({', '.join(held)} is set).", a
    if not (a.how == "os-user" and a.confirmed and a.name.lower() == (store.owner or "").lower()):
        return False, (f"Refused: only the store owner ({store.owner}), confirmed by the OS user in people.yaml, issues keys; "
                       f"this is {a.name} ({a.short()})."), a
    return True, "", a


def issue_key(actor: str, env=None, rotate: bool = False, out: Path | None = None, pubkey: Path | None = None) -> tuple[str, Path | None]:
    """Store `actor`'s public key; return (its fingerprint, the private key path when one was made). With `pubkey` the actor's own public
    key is registered; otherwise a new Ed25519 pair is made with ssh-keygen and the private key written to `out`, never into the key dir."""
    if not _principal(actor):
        raise ValueError("Name the actor (a key from people.yaml).")
    if not _keygen():
        raise ValueError("ssh-keygen was not found on PATH; install OpenSSH (it ships with Windows 10/11, macOS and Linux).")
    f = _pub_file(actor, env)
    if f.exists() and not rotate:
        raise ValueError(f"{actor} already has a key; pass --rotate to replace it (the old one stops working).")
    made = None
    if pubkey is not None:
        line = _pub_of(Path(pubkey).read_text(encoding="utf-8", errors="replace"))
        if not line:
            raise ValueError(f"{pubkey} is not an OpenSSH public key.")
    else:
        if out is None:
            raise ValueError("Pass --out PATH (where the actor's private key is written) or --pubkey FILE (the actor's own public key).")
        out = Path(out).resolve()
        pub_out = out.with_name(out.name + ".pub")
        if out.exists() or pub_out.exists():
            raise ValueError(f"{out} already exists; choose a new path.")
        if keys_dir(env).resolve() in out.parents:
            raise ValueError("The private key may not be written into the key directory; Desk keeps public keys only.")
        out.parent.mkdir(parents=True, exist_ok=True)
        r = _run_keygen(["-q", "-t", "ed25519", "-N", "", "-C", f"desk:{_principal(actor)}", "-f", str(out)])
        if r is None or r.returncode != 0:
            raise ValueError("ssh-keygen could not make a key: " + (r.stderr.decode(errors="replace").strip() if r else "not run"))
        line = _pub_of(pub_out.read_text(encoding="utf-8"))
        pub_out.unlink(missing_ok=True)  # the public half lives in the key dir only
        made = out
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(line + "\n", encoding="utf-8")
    r = _run_keygen(["-l", "-f", str(f)])
    words = r.stdout.decode(errors="replace").split() if r is not None and r.returncode == 0 else []
    return (words[1] if len(words) > 1 else ""), made


def log_key_event(home: Path, event: str, actor: str, fingerprint: str, by: "Actor") -> None:
    """One line in `.desk/writes.jsonl` for every key issued or rotated: when, what, for whom, the key fingerprint and who asked."""
    try:
        p = Path(home) / ".desk" / LOG
        p.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "ticket": "", "event": event, "for": _principal(actor),
               "fingerprint": fingerprint, **by.as_dict()}
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def key_matches(actor: str, key_path: str, env=None) -> bool:
    """True when the private key at `key_path` is the one whose public half is stored for `actor`."""
    if not key_path or not actor:
        return False
    try:
        want = _pub_of(_pub_file(actor, env).read_text(encoding="utf-8"))
    except OSError:
        return False
    r = _run_keygen(["-y", "-f", str(key_path)])
    return bool(want) and r is not None and r.returncode == 0 and _pub_of(r.stdout.decode(errors="replace")) == want


def sign(actor: str, ticket_id: str, when: str, text: str, key_path: str) -> str:
    """The SSH signature of (actor, ticket, time, words) with the actor's private key, as one base64 word; '' when it cannot be made."""
    if not key_path:
        return ""
    r = _run_keygen(["-Y", "sign", "-f", str(key_path), "-n", NAMESPACE], _payload(actor, ticket_id, when, text))
    if r is None or r.returncode != 0:
        return ""
    return "".join(ln.strip() for ln in r.stdout.decode("ascii", errors="replace").splitlines() if ln.strip() and not ln.startswith("-----"))


def check_signature(actor: str, ticket_id: str, when: str, text: str, sig: str, env=None) -> bool:
    """True when `sig` verifies against the public key stored for `actor`; False otherwise, or when the key or ssh-keygen is missing."""
    if not sig or not re.fullmatch(r"[A-Za-z0-9+/=]+", sig):
        return False
    try:
        pub = _pub_of(_pub_file(actor, env).read_text(encoding="utf-8"))
    except OSError:
        return False
    if not pub:
        return False
    with tempfile.TemporaryDirectory(prefix="desk-verify-") as d:
        allowed, sf = Path(d) / "allowed_signers", Path(d) / "sig"
        allowed.write_text(f'{_principal(actor)} namespaces="{NAMESPACE}" {pub}\n', encoding="utf-8")
        sf.write_text("\n".join([_ARMOR[0], *[sig[i:i + 70] for i in range(0, len(sig), 70)], _ARMOR[1]]) + "\n", encoding="ascii")
        r = _run_keygen(["-Y", "verify", "-f", str(allowed), "-I", _principal(actor), "-n", NAMESPACE, "-s", str(sf)],
                        _payload(actor, ticket_id, when, text))
    return r is not None and r.returncode == 0


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
        if key_matches(named, env.get("DESK_ACTOR_KEY", ""), env):  # DESK-0259/0262: the actor's own private key proves it, never the name
            return Actor(named, kind, inst or (instance_here() if kind == "person" else ""), "cli", "verified", True)
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
