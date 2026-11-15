"""Where a notification goes (DESK-0289): one Channel per service, the secrets file outside every repository, and the transports.

Free channels are on once they have their secrets: ntfy (push to the ntfy phone app, paired with `drew notify pair`) and smtp (email
through the owner's own mail account and an app password). The best paid service in each category has an adapter that is switched off
until the owner names it in desk.toml [notify] channels and puts its keys in the secrets file: postmark (email), twilio (text) and
pushover (phone push). Each adapter builds the request its service documents (docs/NOTIFICATIONS.md cites each page); the transport is
passed in, so tests run every adapter against a fake and no test touches the network.

Secrets (passwords, API keys, the ntfy topic, addresses) live in %USERPROFILE%\\.desk\\notify.env (or the file named by
DESK_NOTIFY_ENV), one KEY=value per line; they are never printed, only masked. desk.toml holds the settings that are not secret.
Docs: docs/NOTIFICATIONS.md."""

from __future__ import annotations

import base64
import json
import os
import secrets as _secrets
import smtplib
import ssl
import string
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path

TIMEOUT_SECONDS = 15
TOPIC_LENGTH = 32
DEFAULT_SERVER = "https://ntfy.sh"


# -- the secrets file -------------------------------------------------------------------------

def env_path() -> Path:
    env = os.environ.get("DESK_NOTIFY_ENV", "").strip()
    return Path(env).expanduser() if env else Path.home() / ".desk" / "notify.env"


def inside_repo(path: Path) -> Path | None:
    """The repository that would hold `path`, or None. Secrets never live inside one (rule 30)."""
    p = Path(path).expanduser().resolve()
    for parent in [p.parent, *p.parent.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def read_secrets(path: Path | None = None) -> dict[str, str]:
    p = path or env_path()
    if not p.exists():
        return {}
    out = {}
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def write_secret(key: str, value: str, path: Path | None = None) -> Path:
    """Set one KEY=value in the secrets file, keeping the other lines; refuses a file inside a repository."""
    p = path or env_path()
    repo = inside_repo(p)
    if repo is not None:
        raise ValueError(f"The secrets file {p} is inside the repository {repo}; secrets live outside every repository. Set DESK_NOTIFY_ENV to a file outside it.")
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else ["# Desk notification secrets (DESK-0289). Never commit or share this file."]
    done = False
    for i, raw in enumerate(lines):
        if raw.split("=", 1)[0].strip() == key and "=" in raw:
            lines[i], done = f"{key}={value}", True
    if not done:
        lines.append(f"{key}={value}")
    with p.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    try:
        os.chmod(p, 0o600)  # owner only where the file system honours it
    except OSError:
        pass
    return p


def mask(value: str) -> str:
    """A secret shown as its first two characters and a count, never the value."""
    v = str(value or "")
    if not v:
        return "(not set)"
    return v[:2] + "*" * 6 + f" ({len(v)} characters)"


def new_topic() -> str:
    """A random 32-character ntfy topic: on the public server the topic is the password (docs.ntfy.sh/publish)."""
    alphabet = string.ascii_lowercase + string.digits
    return "".join(_secrets.choice(alphabet) for _ in range(TOPIC_LENGTH))


# -- transports -------------------------------------------------------------------------------

@dataclass
class Response:
    status: int
    body: str = ""


class Offline(RuntimeError):
    """Raised when DESK_NOTIFY_OFFLINE is set: the build and the tests never reach a real service."""


def http_post(url: str, data: bytes, headers: dict[str, str]) -> Response:
    """POST over HTTPS and return the status and body; an HTTP error is a Response, a network failure raises."""
    if os.environ.get("DESK_NOTIFY_OFFLINE"):
        raise Offline("notifications are offline (DESK_NOTIFY_OFFLINE is set)")
    if not url.lower().startswith("https://"):
        raise ValueError("notifications go over HTTPS only")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as r:  # noqa: S310 - https checked above
            return Response(r.status, r.read(4096).decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return Response(e.code, e.read(4096).decode("utf-8", "replace"))


def tls_context() -> ssl.SSLContext:
    """TLS that checks the server's certificate and hostname; smtplib's own default does neither, so the app password could be read."""
    return ssl.create_default_context()


def smtp_connect(host: str, port: int):
    if os.environ.get("DESK_NOTIFY_OFFLINE"):
        raise Offline("notifications are offline (DESK_NOTIFY_OFFLINE is set)")
    if int(port) == 465:
        return smtplib.SMTP_SSL(host, port, timeout=TIMEOUT_SECONDS, context=tls_context())
    return smtplib.SMTP(host, port, timeout=TIMEOUT_SECONDS)


# -- channels ---------------------------------------------------------------------------------

@dataclass
class Note:
    """One outgoing notification, already through the privacy filter."""
    title: str
    body: str
    urgent: bool = False


@dataclass
class Result:
    channel: str
    ok: bool
    detail: str


@dataclass
class Channel:
    name: str
    category: str          # push, email or text
    service: str           # the service's own name, for docs and status
    free: bool
    needs: tuple[str, ...]  # the keys it reads from the secrets file
    owner_steps: str
    settings: dict = field(default_factory=dict)

    def missing(self, sec: dict) -> list[str]:
        return [k for k in self.needs if not sec.get(k)]

    def ready(self, sec: dict) -> bool:
        return not self.missing(sec)

    def send(self, note: Note, sec: dict, post=http_post, smtp=smtp_connect) -> Result:  # pragma: no cover - every adapter overrides it
        raise NotImplementedError


def _ascii(text: str) -> str:
    """HTTP header values are Latin-1; a title keeps its words and loses only what cannot travel there."""
    return str(text).encode("ascii", "replace").decode("ascii")


def _check(name: str, r: Response, ok_when=lambda r: 200 <= r.status < 300) -> Result:
    if ok_when(r):
        return Result(name, True, f"sent (HTTP {r.status})")
    return Result(name, False, f"refused (HTTP {r.status})")


class Ntfy(Channel):
    def send(self, note, sec, post=http_post, smtp=smtp_connect):
        server = str(self.settings.get("ntfy_server") or DEFAULT_SERVER).rstrip("/")
        headers = {"Title": _ascii(note.title), "Priority": "high" if note.urgent else "default", "Tags": "desk",
                   "Content-Type": "text/plain; charset=utf-8"}
        if sec.get("NTFY_TOKEN"):
            headers["Authorization"] = "Bearer " + sec["NTFY_TOKEN"]
        return _check(self.name, post(f"{server}/{sec['NTFY_TOPIC']}", note.body.encode("utf-8"), headers))


class Smtp(Channel):
    def send(self, note, sec, post=http_post, smtp=smtp_connect):
        host = str(self.settings.get("smtp_host") or "smtp.gmail.com")
        port = int(self.settings.get("smtp_port") or 587)
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = sec["SMTP_USER"], sec.get("SMTP_TO") or sec["SMTP_USER"], note.title
        msg.set_content(note.body)
        conn = smtp(host, port)
        try:
            if port != 465:
                conn.starttls(context=tls_context())
            conn.login(sec["SMTP_USER"], sec["SMTP_PASSWORD"])
            conn.send_message(msg)
        finally:
            try:
                conn.quit()
            except Exception:  # noqa: BLE001 - closing a connection that already failed
                pass
        return Result(self.name, True, f"sent through {host}:{port}")


class Postmark(Channel):
    """Postmark email API: POST https://api.postmarkapp.com/email with X-Postmark-Server-Token; From needs a confirmed sender signature."""

    def send(self, note, sec, post=http_post, smtp=smtp_connect):
        payload = {"From": sec["POSTMARK_FROM"], "To": sec["POSTMARK_TO"], "Subject": note.title, "TextBody": note.body, "MessageStream": "outbound"}
        headers = {"Accept": "application/json", "Content-Type": "application/json", "X-Postmark-Server-Token": sec["POSTMARK_TOKEN"]}
        return _check(self.name, post("https://api.postmarkapp.com/email", json.dumps(payload).encode("utf-8"), headers))


class Twilio(Channel):
    """Twilio Messages API: POST .../Accounts/{AccountSid}/Messages.json, Basic auth, form fields To, From, Body (1,600 characters at most)."""

    def send(self, note, sec, post=http_post, smtp=smtp_connect):
        sid = sec["TWILIO_ACCOUNT_SID"]
        body = (note.title + ": " + note.body)[:1600]
        form = urllib.parse.urlencode({"To": sec["TWILIO_TO"], "From": sec["TWILIO_FROM"], "Body": body}).encode("ascii")
        auth = base64.b64encode(f"{sid}:{sec['TWILIO_AUTH_TOKEN']}".encode("utf-8")).decode("ascii")
        headers = {"Authorization": "Basic " + auth, "Content-Type": "application/x-www-form-urlencoded"}
        return _check(self.name, post(f"https://api.twilio.com/2010-04-01/Accounts/{urllib.parse.quote(sid)}/Messages.json", form, headers))


class Pushover(Channel):
    """Pushover: POST https://api.pushover.net/1/messages.json with token, user, message; success is JSON status 1."""

    def send(self, note, sec, post=http_post, smtp=smtp_connect):
        form = {"token": sec["PUSHOVER_TOKEN"], "user": sec["PUSHOVER_USER"], "title": note.title[:250], "message": note.body[:1024],
                "priority": "1" if note.urgent else "0"}
        r = post("https://api.pushover.net/1/messages.json", urllib.parse.urlencode(form).encode("utf-8"), {"Content-Type": "application/x-www-form-urlencoded"})

        def ok(resp):
            try:
                return resp.status == 200 and json.loads(resp.body or "{}").get("status") == 1
            except ValueError:
                return False
        return _check(self.name, r, ok)


def all_channels(settings: dict | None = None) -> list[Channel]:
    """Every adapter, free ones first. Which are on is decided by desk.toml [notify] channels plus the secrets they need."""
    st = settings or {}
    return [
        Ntfy("ntfy", "push", "ntfy (public ntfy.sh server)", True, ("NTFY_TOPIC",),
             "Install the ntfy app, run `drew notify pair`, subscribe to the topic it prints.", st),
        Smtp("smtp", "email", "your own mail account over SMTP", True, ("SMTP_USER", "SMTP_PASSWORD"),
             "Turn on 2-Step Verification, create an app password, put SMTP_USER, SMTP_PASSWORD and SMTP_TO in notify.env.", st),
        Postmark("postmark", "email", "Postmark", False, ("POSTMARK_TOKEN", "POSTMARK_FROM", "POSTMARK_TO"),
                 "Create a Postmark account, confirm a sender signature, add the server token; add postmark to [notify] channels.", st),
        Twilio("twilio", "text", "Twilio Programmable Messaging", False, ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM", "TWILIO_TO"),
               "Create a Twilio account, buy a number, finish 10DLC or toll-free verification; add twilio to [notify] channels.", st),
        Pushover("pushover", "push", "Pushover", False, ("PUSHOVER_TOKEN", "PUSHOVER_USER"),
                 "Install Pushover (30-day trial, then a one-time purchase per platform), register an application; add pushover to [notify] channels.", st),
    ]
