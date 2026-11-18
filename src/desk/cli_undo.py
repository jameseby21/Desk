"""`drew undo` (DESK-0284): take back the last change to a ticket, one change by its number, or a whole batch.

The undo writes new events (the log only grows) and is recorded as whoever runs the command; there is no `--as` here (decision 110).
Docs: docs/COMMANDS.md (`drew undo`)."""

from __future__ import annotations

import sys

from . import activity, undo


def register(sub) -> None:
    sp = sub.add_parser("undo", help="take back a change: the last save on a ticket, one change (--seq N, its number in `drew history --json`), "
                                     "or every change of a batch (--batch B); writes new events, so both stay in the record")
    sp.add_argument("id", nargs="?", help="the ticket")
    sp.add_argument("--seq", type=int, help="the number of one change in the ticket's log")
    sp.add_argument("--batch", help="a batch id (b-...): every change of one action, across tickets, newest first")
    sp.add_argument("--force", action="store_true", help="undo even when the field was changed since (the refusal says by whom)")
    sp.add_argument("--dry-run", action="store_true", help="say what would be undone, with the count; write nothing")
    sp.set_defaults(fn=run)


def _store(args):
    from .cli import _store as store

    return store(args)


def run(args) -> None:
    s = _store(args)
    if bool(args.id) == bool(args.batch):
        _die("Give a ticket id (drew undo DESK-0001) or a batch (drew undo --batch b-0123456789ab), not both.")
    if args.batch:
        evs = undo.batch_events(s, args.batch)
        if not evs:
            _die(f"No changes with batch {args.batch}.")
    else:
        try:
            tid = s.get(args.id).id
        except KeyError as e:
            _die(str(e).strip("'"))
        evs = [e for e in activity.read(s.home, tid) if e.seq == args.seq] if args.seq else undo.last_change(s, tid)
        if not evs:
            _die(f"{tid} has no change #{args.seq}." if args.seq else f"{tid} has no recorded changes to undo.")
    if args.dry_run:
        ok, no = undo.plan(s, evs)
        for e in ok:
            print(f"  would undo  {e.ticket} #{e.seq}  {e.what}")
        for e, why in no:
            print(f"  refused     {e.ticket} #{e.seq}  {why}")
        print(f"Dry run: nothing written. {len(ok)} of {len(evs)} change{'s' if len(evs) != 1 else ''} would be undone.")
        return
    s.sync_before()
    try:
        res = undo.revert(s, evs, force=args.force)
    except undo.Refused as e:
        _die(str(e) + ("" if not e.conflict else " (add --force)"))
    except ValueError as e:
        _die(f"Could not undo: {e}. Nothing was changed.")
    if res.batch:
        s.sync_after(f"desk: undo {', '.join(sorted({e.ticket for e in res.reverted}))}")
    print(res.message)
    if res.batch:
        print(f"Batch {res.batch}. To redo: drew undo --batch {res.batch}")


def _die(msg: str):
    print(msg, file=sys.stderr)
    sys.exit(1)
