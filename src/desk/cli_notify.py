"""`drew notify` (DESK-0289): pair the phone, send a test, send the brief, drain the queue, show status, print the schedule.

Nothing here writes a ticket. Secrets are read from and written to the notify.env file outside every repository and are never
printed, except the ntfy topic once, by `drew notify pair`, because the phone needs it to subscribe. Docs: docs/NOTIFICATIONS.md,
docs/COMMANDS.md."""

from __future__ import annotations

import json
import sys

from . import notify_channels as nc, notify_send as ns
from .config import HomeNotFound, find_home
from .store import Store


def register(sub) -> None:
    sp = sub.add_parser("notify", help="notifications to your phone and email (docs/NOTIFICATIONS.md): pair, test, brief, drain, status, schedule")
    ns_sub = sp.add_subparsers(dest="notify_cmd", required=True)
    p = ns_sub.add_parser("pair", help="make a random ntfy topic, keep it in notify.env (outside every repository) and print the phone steps")
    p.add_argument("--new", action="store_true", help="replace the topic already paired (the old phone stops getting messages)")
    p.set_defaults(fn=cmd_pair)
    p = ns_sub.add_parser("test", help="send one test line to every channel that is on (or --channel NAME)")
    p.add_argument("--channel", help="ntfy, smtp, postmark, twilio or pushover")
    p.set_defaults(fn=cmd_test)
    p = ns_sub.add_parser("brief", help="send the morning brief (ids, titles that pass the private-term scan, counts) with held items folded in")
    p.add_argument("--dry-run", action="store_true", help="print exactly the text that would be sent and send nothing")
    p.set_defaults(fn=cmd_brief)
    p = ns_sub.add_parser("drain", help="send what the notification queue holds, through the privacy filter, the daily budget and quiet hours")
    p.add_argument("--dry-run", action="store_true", help="show what would be sent or held and change nothing")
    p.set_defaults(fn=cmd_drain)
    p = ns_sub.add_parser("status", help="which channels are on, which keys are set (masked), quiet hours, budget used today, queued and held counts")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_status)
    p = ns_sub.add_parser("schedule", help="print the two Windows Task Scheduler commands (morning brief, drain every 15 minutes); never installs them")
    p.add_argument("--print", dest="print_", action="store_true", help="print the commands (required: Desk never installs a task itself)")
    p.set_defaults(fn=cmd_schedule)


def _store(args) -> Store:
    try:
        return Store(find_home(args.home))
    except HomeNotFound as e:
        sys.exit(str(e))


def _results(results, names) -> int:
    if not names:
        print("No channel is on yet. Run `drew notify pair` for the phone, or see docs/NOTIFICATIONS.md for email.")
        return 1
    for r in results:
        print(f"  {r.channel}: {r.detail}")
    ok = sum(r.ok for r in results)
    print(f"Sent through {ok} of {len(results)} channel(s)." if ok == len(results) else f"Failed on {len(results) - ok} of {len(results)} channel(s); the lines above say why.")
    return 0 if ok == len(results) else 1


def cmd_pair(args):
    s = _store(args)
    st = ns.settings(s.home)
    sec = nc.read_secrets()
    topic = sec.get("NTFY_TOPIC", "")
    if topic and not args.new:
        print("Already paired. The topic below is the one in notify.env; --new makes a fresh one.")
    else:
        topic = nc.new_topic()
        try:
            path = nc.write_secret("NTFY_TOPIC", topic)
        except ValueError as e:
            sys.exit(str(e))
        print(f"Paired: a new random topic is saved in {path} (outside every repository).")
    server = nc.host("ntfy_server", nc.read_secrets()).rstrip("/")  # notify.env decides where the topic goes (H2), never desk.toml
    print(f"""
Topic (treat it like a password; anyone who has it can read these messages):
  {topic}

On your phone:
  1. Install "ntfy" from the App Store or Google Play (F-Droid works too).
  2. Open it, tap +, paste the topic above, leave the server as {server}, tap Subscribe.
     Or open this link on the phone: {server}/{topic}
  3. Back here, run: drew notify test
""".rstrip())
    if "ntfy" not in {str(c).lower() for c in st.get("channels") or []}:
        print('\nntfy is not in desk.toml [notify] channels; add it: channels = ["ntfy", "smtp"]')


def cmd_test(args):
    s = _store(args)
    print("Sending one test line...")
    _, results, names = ns.send_test(s.home, args.channel)
    if args.channel and not names:  # say why that one channel is off, not "nothing is paired"
        print(ns.channel_problem(ns.settings(s.home), nc.read_secrets(), args.channel.strip().lower()))
        sys.exit(1)
    sys.exit(_results(results, names))


def cmd_brief(args):
    s = _store(args)
    note, results, names = ns.send_brief(s, dry_run=args.dry_run)
    if args.dry_run:
        print(note.body)
        print(f"\n(dry run: nothing sent; it would go to {', '.join(names) or 'no channel, none is on yet'})", file=sys.stderr)
        return
    print("Sending the brief...")
    sys.exit(_results(results, names))


def cmd_drain(args):
    s = _store(args)

    def progress(i, n, line):
        print(f"[{i}/{n}] {line}", flush=True)

    rep = ns.drain(s, dry_run=args.dry_run, progress=progress)
    if rep.reason:
        print(rep.reason)
        return
    verb = "would be sent" if args.dry_run else "sent"
    skipped = f", {rep.skipped} unreadable line(s) skipped" if rep.skipped else ""
    print(f"Done: {rep.sent} {verb}, {rep.held} held for the next brief, {rep.failed} failed{skipped}." + (" Nothing changed (dry run)." if args.dry_run else ""))
    if rep.failed:
        sys.exit(1)


def cmd_status(args):
    s = _store(args)
    st = ns.status(s)
    if args.json:
        print(json.dumps(st, indent=2))
        return
    on = [c for c in st["channels"] if c["listed"] and c["ready"]]
    print(f"{len(on)} channel(s) on: {', '.join(c['name'] for c in on) or 'none yet (run drew notify pair)'}")
    for c in st["channels"]:
        state = "on" if c["listed"] and c["ready"] else ("off: needs " + ", ".join(c["missing"]) if c["listed"] else "off (not in [notify] channels)")
        print(f"  {c['name']:<9} {c['category']:<6} {'free' if c['free'] else 'paid':<5} {state}")
        for k, v in c["keys"].items():
            if v != "(not set)":
                print(f"            {k} = {v}")
    print(f"Secrets file: {st['secrets_file']}" + ("" if st["secrets_file_exists"] else " (not made yet)"))
    print(f"Quiet hours: {st['quiet']}{' (urgent alerts still go)' if st['urgent_bypasses_quiet'] else ''}")
    print(f"Budget: {st['used_today']} of {st['budget']} used today; {st['queued']} queued, {st['held']} held for the next brief"
          + (f"; {st['unreadable']} unreadable queue line(s) will be skipped by the next drain" if st["unreadable"] else ""))
    print(f"Brief time: {st['brief_time']}" + (f"; last sent {st['last_brief']}" if st["last_brief"] else ""))
    print("Private-term scan: ready" if st["scan_ready"] else f"Private-term scan: not ready ({st['scan_why']}); only ids are sent")
    print(f"Sends to: ntfy {st['hosts']['ntfy']} · smtp {st['hosts']['smtp']} (set in notify.env, never in desk.toml)")
    for p in st["problems"]:
        print(f"Problem: {p}")


def cmd_schedule(args):
    s = _store(args)
    if not args.print_:
        sys.exit("Desk never installs a scheduled task. Run `drew notify schedule --print` and paste the two lines into a terminal yourself.")
    # the quoting below is cmd.exe's; Windows PowerShell 5.1 splits the quoted /TR value apart, so the steps name the Command Prompt
    print("# Paste these into a Command Prompt (cmd.exe; from PowerShell, type cmd and press Enter first) to schedule the brief and the drain (Desk does not run them):")
    for line in ns.schedule_commands(s.home, ns.settings(s.home)):
        print(line)
    print('# To remove them later: schtasks /Delete /TN "Desk\\Notify brief" /F  and  schtasks /Delete /TN "Desk\\Notify drain" /F')
