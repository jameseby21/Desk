"""The `desk` command line. Agents use this; the owner mostly uses the UI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, ops
from .config import HomeNotFound, find_home
from .rules import RuleError, validate_store
from .store import Store
from .ticket import PRIORITIES, SIZES, STATUSES, TYPES
from .words import screen


def _store(args) -> Store:
    try:
        return Store(find_home(args.home))
    except HomeNotFound as e:
        _die(str(e))


def _die(msg: str, code: int = 1):
    print(msg, file=sys.stderr)
    sys.exit(code)


def _table(rows: list[list[str]], headers: list[str]) -> str:
    widths = [len(h) for h in headers]
    for r in rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(c))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    out = [line, "  ".join("-" * w for w in widths)]
    for r in rows:
        out.append("  ".join(c.ljust(widths[i]) for i, c in enumerate(r)))
    return "\n".join(out)


def _print_ticket(t) -> None:
    print(t.render(), end="")


def _id_label(s: Store, t) -> str:
    """The id, plus `(formerly OLD)` when the project's prefix has changed since the ticket was made."""
    old = s.formerly(t)
    return f"{t.id} (formerly {old})" if old else t.id


def _by_project(tickets):
    return sorted(tickets, key=lambda t: (t.project, t.id))


# ---- commands --------------------------------------------------------------

def cmd_init(args):
    home = Path(args.path or ".").resolve()
    Store.init(home, owner=args.owner)
    print(f"Desk store ready at {home}")


def cmd_new(args):
    s = _store(args)
    s.sync_before()
    dod = [x.strip() for x in (args.dod or "core").split(",") if x.strip()]
    try:
        t = ops.new(s, args.title, args.project, args.type, priority=args.priority, size=args.size, owner=args.owner, dod=dod, source=args.source or "", assignee=args.assignee)
    except (RuleError, ValueError) as e:
        _die(str(e))
    s.sync_after(f"desk: new {t.id}")
    print(t.path)


def cmd_list(args):
    s = _store(args)
    rows = []
    for t in _by_project(s.tickets()):
        stale = s.is_stale(t)
        if args.project and t.project != args.project:
            continue
        if args.status and t.status != args.status:
            continue
        if args.owner and str(t.meta.get("owner")) != args.owner:
            continue
        if args.stale and not stale:
            continue
        if not args.all and not t.is_open and not args.status:
            continue
        rows.append([_id_label(s, t), t.project, t.meta.get("type", ""), t.status + (" (stale)" if stale else ""), t.meta.get("priority", ""),
                     t.meta.get("size", ""), str(t.meta.get("owner", "")), str(t.meta.get("title", ""))[:60]])
    if not rows:
        print("No tickets match.")
        return
    print(_table(rows, ["id", "project", "type", "status", "pri", "size", "owner", "title"]))


def cmd_next(args):
    s = _store(args)
    t = s.next_ready(args.project)
    if args.json:
        ctx = t.render() if t else f"No ready ticket for project {args.project or '(any)'}."
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx[:10000]}}))
        return
    if not t:
        print(f"No ready ticket for project {args.project or '(any)'}.")
        return
    _print_ticket(t)


def cmd_show(args):
    s = _store(args)
    try:
        t = s.get(args.id)
    except KeyError as e:
        _die(str(e))
    if s.formerly(t):  # a renamed project: say so before the file, which keeps its original id
        print(f"# {_id_label(s, t)} · project {t.project} · {s.project_title(t.project)}\n")
    _print_ticket(t)


def _write_cmd(args, fn, label):
    s = _store(args)
    s.sync_before()
    try:
        t = s.get(args.id)
        t = fn(s, t)
    except (KeyError, RuleError, ValueError) as e:
        _die(str(e))
    s.sync_after(f"desk: {label} {t.id}")
    print(f"{t.id} -> {t.status} ({screen(t.status)})")


def cmd_claim(args):
    _write_cmd(args, lambda s, t: ops.claim(s, t, args.owner), "claim")


def cmd_start(args):
    _write_cmd(args, lambda s, t: ops.start(s, t), "start")


def cmd_report(args):
    if not args.file and not args.text:
        _die("Give --file report.md or --text \"...\"")
    text = ops.load_report_file(Path(args.file)) if args.file else args.text
    _write_cmd(args, lambda s, t: ops.report(s, t, text), "report")


def cmd_decide(args):
    if bool(args.approve) == bool(args.changes):
        _die("Pick one: --approve \"reason\" or --changes \"reason\"")
    approve = bool(args.approve)
    reason = args.approve or args.changes
    _write_cmd(args, lambda s, t: ops.decide(s, t, approve, reason, actor=args.as_), "decide")


def cmd_done(args):
    _write_cmd(args, lambda s, t: ops.done(s, t, args.commit), "done")


def cmd_block(args):
    _write_cmd(args, lambda s, t: ops.block(s, t, args.on, args.reason, actor=args.as_), "block")


def cmd_unblock(args):
    _write_cmd(args, lambda s, t: ops.unblock(s, t, actor=args.as_, to=args.to), "unblock")


def cmd_reject(args):
    _write_cmd(args, lambda s, t: ops.reject(s, t, args.reason, actor=args.as_), "reject")


def cmd_move(args):
    _write_cmd(args, lambda s, t: ops.move(s, t, args.to, actor=args.as_, reason=args.reason), "move")


def cmd_comment(args):
    _write_cmd(args, lambda s, t: ops.comment(s, t, args.text, actor=args.as_), "comment")


def cmd_validate(args):
    s = _store(args)
    errors, warnings = validate_store(s)
    for w in warnings:
        print(f"warning: {w}")
    for e in errors:
        print(f"error: {e}")
    n = len(s.tickets())
    print(f"{n} ticket(s), {len(errors)} error(s), {len(warnings)} warning(s)")
    sys.exit(1 if errors else 0)


def cmd_index(args):
    s = _store(args)
    print(s.index())


def cmd_board(args):
    s = _store(args)
    cols = [st for st in STATUSES if st not in ("done", "rejected")] + (["done"] if args.all else [])
    tickets = _by_project([t for t in s.tickets() if not args.project or t.project == args.project])
    if args.project:
        print(f"\n{s.project_title(args.project)} ({args.project}, prefix {s.key_for(args.project)})")
    for st in cols:
        items = [t for t in tickets if t.status == st]
        if not items and st not in ("ready", "in_progress", "review"):
            continue
        print(f"\n{screen(st)} ({st}) · {len(items)}")
        for t in items:
            flag = " · chewed back" if s.is_stale(t) else ""
            proj = "" if args.project else f"  [{t.project}]"
            print(f"  {_id_label(s, t)}{proj}  {t.meta.get('priority')}  {t.meta.get('size')}  {t.meta.get('owner')}  {t.meta.get('title')}{flag}")
    print()


def cmd_rename_project(args):
    s = _store(args)
    s.sync_before()
    try:
        path, changed = ops.rename_project(s, args.old, args.new, prefix=args.prefix, name=args.name)
    except (RuleError, ValueError) as e:
        _die(str(e))
    s.sync_after(f"desk: rename project {args.old} -> {args.new}")
    print(f"{path}\n{len(changed)} ticket(s) now say project: {args.new}; ids unchanged, prefix {s.key_for(args.new)}"
          + (f", formerly {', '.join(sorted(s.prefixes_for(args.new) - {s.key_for(args.new)}))}" if changed else ""))


def cmd_attach(args):
    s = _store(args)
    s.sync_before()
    try:
        t = s.get(args.id)
        dest = ops.attach(s, t, Path(args.file), caption=args.caption)
    except (KeyError, RuleError, ValueError) as e:
        _die(str(e))
    s.sync_after(f"desk: attach {t.id}")
    print(dest)


def cmd_snap(args):
    s = _store(args)
    s.sync_before()
    try:
        t = s.get(args.id)
        dest = ops.snap(s, t, caption=args.caption)
    except (KeyError, RuleError, ValueError) as e:
        _die(str(e))
    s.sync_after(f"desk: snap {t.id}")
    print(dest)


def cmd_intake(args):
    s = _store(args)
    s.sync_before()
    try:
        t, dest = ops.intake(s, Path(args.path), args.topic, args.project, actor=args.as_ or "main")
    except (RuleError, ValueError) as e:
        _die(str(e))
    s.sync_after(f"desk: intake {t.id}")
    print(f"{dest}\n{t.path}")


def cmd_clone(args):
    s = _store(args)
    s.sync_before()
    try:
        src = s.get(args.id)
        t = ops.clone(s, src, args.title, project=args.project, actor=args.as_ or "main")
    except (KeyError, RuleError, ValueError) as e:
        _die(str(e))
    s.sync_after(f"desk: clone {src.id} -> {t.id}")
    print(t.path)


def cmd_stats(args):
    s = _store(args)
    st = ops.stats(s, since=args.since, project=args.project)
    if args.json:
        print(json.dumps(st, indent=2))
        return
    scope = (f" since {st['since']}" if st["since"] else "") + (f" in {st['project']}" if st["project"] else "")
    print(f"Tickets{scope}: {st['total']} total · {st['done']} done · {st['in_review']} in review (built, not yet proven)"
          f" · {st['open']} open · {st['rejected']} let go")
    for label, table in (("status", st["by_status"]), ("project", st["by_project"]), ("type", st["by_type"]),
                         ("created, week of", st["created_per_week"]), ("done, week of", st["done_per_week"])):
        print(f"\nBy {label}")
        rows = [[str(k), str(v)] for k, v in table.items()] or [["(none)", "0"]]
        print(_table(rows, [label, "count"]))


def cmd_home(args):
    print(_store(args).home)


# ---- parser ----------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="desk", description="Tickets as markdown files. Done means proof.")
    p.add_argument("--home", help="the ticket store (else DESK_HOME, else the nearest desk.toml upward)")
    p.add_argument("--version", action="version", version=f"desk {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def actor(sp):
        sp.add_argument("--as", dest="as_", help="who is acting (default: main)")

    sp = sub.add_parser("init", help="create a store here (desk.toml, tickets/, projects/, intake/)")
    sp.add_argument("path", nargs="?")
    sp.add_argument("--owner", default="james")
    sp.set_defaults(fn=cmd_init)

    sp = sub.add_parser("home", help="print the resolved store path")
    sp.set_defaults(fn=cmd_home)

    sp = sub.add_parser("new", help="create a ticket from the template (next id for the project)")
    sp.add_argument("title")
    sp.add_argument("--project", required=True)
    sp.add_argument("--type", default="slice", choices=TYPES)
    sp.add_argument("--priority", default="p2", choices=PRIORITIES)
    sp.add_argument("--size", default="s", choices=SIZES)
    sp.add_argument("--owner", default="main")
    sp.add_argument("--dod", help="comma-separated Definition of Done modules (default core)")
    sp.add_argument("--source", help="path#section into intake/")
    sp.add_argument("--assignee", help="who will execute it: main, james, codex, a project key, or any name")
    sp.set_defaults(fn=cmd_new)

    sp = sub.add_parser("list", help="table of tickets (open ones unless --all or --status)")
    sp.add_argument("--project")
    sp.add_argument("--status", choices=STATUSES)
    sp.add_argument("--owner")
    sp.add_argument("--stale", action="store_true", help="only tickets untouched past the stale window")
    sp.add_argument("--all", action="store_true")
    sp.set_defaults(fn=cmd_list)

    sp = sub.add_parser("next", help="the highest-priority ready ticket, printed in full")
    sp.add_argument("--project")
    sp.add_argument("--json", action="store_true", help="SessionStart hook format")
    sp.set_defaults(fn=cmd_next)

    sp = sub.add_parser("show", help="print a ticket")
    sp.add_argument("id")
    sp.set_defaults(fn=cmd_show)

    sp = sub.add_parser("claim", help="ready → claimed; sets owner and branch")
    sp.add_argument("id")
    sp.add_argument("--owner", help="project key or a name (default: the ticket's project)")
    sp.set_defaults(fn=cmd_claim)

    sp = sub.add_parser("start", help="claimed → in_progress")
    sp.add_argument("id")
    sp.set_defaults(fn=cmd_start)

    sp = sub.add_parser("report", help="write the Report section → review")
    sp.add_argument("id")
    sp.add_argument("--file")
    sp.add_argument("--text")
    sp.set_defaults(fn=cmd_report)

    sp = sub.add_parser("decide", help="approve or send back, in words")
    sp.add_argument("id")
    sp.add_argument("--approve", metavar="REASON")
    sp.add_argument("--changes", metavar="REASON")
    actor(sp)
    sp.set_defaults(fn=cmd_decide)

    sp = sub.add_parser("done", help="approved → done (after the merge)")
    sp.add_argument("id")
    sp.add_argument("--commit")
    sp.set_defaults(fn=cmd_done)

    sp = sub.add_parser("block", help="mark stuck, on a named ticket or person")
    sp.add_argument("id")
    sp.add_argument("--on", required=True)
    sp.add_argument("reason")
    actor(sp)
    sp.set_defaults(fn=cmd_block)

    sp = sub.add_parser("unblock", help="restore the status from before the block")
    sp.add_argument("id")
    sp.add_argument("--to", choices=STATUSES)
    actor(sp)
    sp.set_defaults(fn=cmd_unblock)

    sp = sub.add_parser("reject", help="let it go, with a reason")
    sp.add_argument("id")
    sp.add_argument("reason")
    actor(sp)
    sp.set_defaults(fn=cmd_reject)

    sp = sub.add_parser("move", help="a guarded status move (inbox → triage → ready, and the rest)")
    sp.add_argument("id")
    sp.add_argument("--to", required=True, choices=STATUSES)
    sp.add_argument("--reason")
    actor(sp)
    sp.set_defaults(fn=cmd_move)

    sp = sub.add_parser("comment", help="append a dated comment")
    sp.add_argument("id")
    sp.add_argument("text")
    actor(sp)
    sp.set_defaults(fn=cmd_comment)

    sp = sub.add_parser("validate", help="schema, lifecycle rules, dangling depends_on (exit 1 on errors)")
    sp.set_defaults(fn=cmd_validate)

    sp = sub.add_parser("index", help="rebuild .desk/index.sqlite from the files")
    sp.set_defaults(fn=cmd_index)

    sp = sub.add_parser("board", help="the board in the terminal")
    sp.add_argument("--project")
    sp.add_argument("--all", action="store_true")
    sp.set_defaults(fn=cmd_board)

    sp = sub.add_parser("intake", help="copy a hand-off (folder or zip) into intake/ and open an intake ticket")
    sp.add_argument("path")
    sp.add_argument("--topic", required=True)
    sp.add_argument("--project", required=True)
    actor(sp)
    sp.set_defaults(fn=cmd_intake)

    sp = sub.add_parser("rename-project", help="rename projects/<old>.md to <new>.md; tickets keep their ids, the old key and prefix become aliases")
    sp.add_argument("old")
    sp.add_argument("new")
    sp.add_argument("--prefix", help="a new ticket-id prefix for new tickets (old ids stay valid)")
    sp.add_argument("--name", help="a new display name (the profile's title line)")
    sp.set_defaults(fn=cmd_rename_project)

    sp = sub.add_parser("attach", help="copy a file into tickets/assets/<ID>/ and list it under Attachments")
    sp.add_argument("id")
    sp.add_argument("file")
    sp.add_argument("--caption")
    sp.set_defaults(fn=cmd_attach)

    sp = sub.add_parser("snap", help="capture the screen to a PNG and attach it")
    sp.add_argument("id")
    sp.add_argument("--caption")
    sp.set_defaults(fn=cmd_snap)

    sp = sub.add_parser("clone", help="a new ticket with the source's Context, Outcome, Acceptance and Prompt; sets cloned_from")
    sp.add_argument("id")
    sp.add_argument("title")
    sp.add_argument("--project", help="put the clone in another project (default: the source's)")
    actor(sp)
    sp.set_defaults(fn=cmd_clone)

    sp = sub.add_parser("stats", help="counts by status, project and type; created and done per week")
    sp.add_argument("--since", metavar="YYYY-MM-DD", help="only tickets created on or after this date")
    sp.add_argument("--project")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_stats)
    return p


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252; tickets are UTF-8
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
