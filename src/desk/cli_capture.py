"""`drew capture hotkey` (DESK-0286): a Windows Start-menu shortcut that opens Desk's capture box with Ctrl+Alt+J.

The shortcut is a .lnk in the person's own Start menu (no admin rights) whose target is Explorer with the capture address, so the default
browser opens http://127.0.0.1:<port>/capture. Windows only honours a shortcut key on a .lnk in the Start menu or on the desktop.
--dry-run (the default) prints the path, the key, the address and the PowerShell it would run, and changes nothing; --install and
--uninstall run that PowerShell. Tests only ever use --dry-run and the generated text.
Docs: docs/COMMANDS.md (drew capture hotkey)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HOTKEY = "CTRL+ALT+J"
HOTKEY_TEXT = "Ctrl+Alt+J"
LINK_NAME = "Desk capture.lnk"
DEFAULT_PORT = 8765


def link_path(appdata: str | None = None) -> Path:
    """Where the shortcut lives: the person's own Start menu Programs folder."""
    base = appdata if appdata is not None else os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / LINK_NAME


def capture_url(port: int) -> str:
    return f"http://127.0.0.1:{int(port)}/capture"


def _ps(s: str) -> str:
    """A PowerShell single-quoted string: nothing inside is expanded."""
    return "'" + str(s).replace("'", "''") + "'"


def script(action: str, link: Path, url: str) -> str:
    """The PowerShell that installs or removes the shortcut. Plain text, so a person can read exactly what will run."""
    if action == "uninstall":
        return (f"$link = {_ps(link)}\n"
                "if (Test-Path -LiteralPath $link) { Remove-Item -LiteralPath $link -Force; 'Removed ' + $link } else { 'Nothing to remove at ' + $link }\n")
    return (f"$link = {_ps(link)}\n"
            "$shell = New-Object -ComObject WScript.Shell\n"
            "$s = $shell.CreateShortcut($link)\n"
            "$s.TargetPath = Join-Path $env:WINDIR 'explorer.exe'\n"
            f"$s.Arguments = {_ps(url)}\n"
            f"$s.Hotkey = {_ps(HOTKEY)}\n"
            "$s.Description = 'Desk: capture a ticket in one sentence'\n"
            "$s.Save()\n"
            "'Created ' + $link\n")


def plan(action: str, port: int, appdata: str | None = None) -> dict:
    link = link_path(appdata)
    url = capture_url(port)
    return {"action": action, "link": link, "url": url, "key": HOTKEY_TEXT, "script": script(action, link, url)}


def describe(p: dict, dry: bool) -> str:
    lead = "Dry run, nothing changed. " if dry else ""
    if p["action"] == "uninstall":
        head = f"{lead}Remove the shortcut {p['link']}"
    else:
        head = f"{lead}Create the shortcut {p['link']}\n  key:     {p['key']}\n  opens:   {p['url']}"
    out = f"{head}\nPowerShell it runs:\n" + "".join("  " + ln + "\n" for ln in p["script"].splitlines())
    return out + (f"To do it: drew capture hotkey --{p['action']}\n" if dry else "")


def run(p: dict) -> tuple[int, str]:
    """Run the plan's PowerShell (Windows only). Returns (exit code, what PowerShell printed)."""
    if os.name != "nt":
        return 2, "The capture shortcut is for Windows. On other systems, bookmark " + p["url"] + " and give it a key in your desktop's settings."
    r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", p["script"]],
                       capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def cmd_capture(args) -> None:
    if args.capture_cmd != "hotkey":
        print("Say what to set up: drew capture hotkey --dry-run", file=sys.stderr)
        sys.exit(2)
    action = "uninstall" if args.uninstall else "install"
    dry = args.dry_run or not (args.install or args.uninstall)
    port = args.port or int(os.environ.get("DESK_UI_PORT") or DEFAULT_PORT)
    p = plan(action, port)
    print(describe(p, dry), end="")
    if dry:
        return
    code, out = run(p)
    if out:
        print(out)
    if code:
        print(f"The shortcut was not {'removed' if action == 'uninstall' else 'created'} (PowerShell exit {code}).", file=sys.stderr)
        sys.exit(1)
    print(("Done. Press " + HOTKEY_TEXT + " to open the capture box (Desk must be running).") if action == "install" else "Done. The shortcut is gone.")


def register(sub) -> None:
    """Add `drew capture` to the CLI's subcommands (one line in cli.build_parser)."""
    sp = sub.add_parser("capture", help="capture helpers: `drew capture hotkey` puts Desk's capture box on Ctrl+Alt+J (Windows)")
    csub = sp.add_subparsers(dest="capture_cmd", required=True)
    hp = csub.add_parser("hotkey", help="create or remove a Start-menu shortcut with Ctrl+Alt+J that opens /capture; --dry-run shows what it will do")
    g = hp.add_mutually_exclusive_group()
    g.add_argument("--install", action="store_true", help="create the shortcut")
    g.add_argument("--uninstall", action="store_true", help="remove the shortcut")
    hp.add_argument("--dry-run", action="store_true", help="print the path, key and PowerShell, change nothing (the default)")
    hp.add_argument("--port", type=int, help=f"the Desk UI port the shortcut opens (default: DESK_UI_PORT, else {DEFAULT_PORT})")
    hp.set_defaults(fn=cmd_capture)
