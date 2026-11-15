"""`drew lane`, `drew snooze` and `drew park` (DESK-0283). Each writes through Store.save, so the change is an event in the ticket's
activity log, recorded for whoever runs the command (no --as: nobody acts as anyone, decision 110)."""

from __future__ import annotations

import sys

from . import lanes
from .config import HomeNotFound, find_home
from .rules import RuleError
from .store import Store


def _die(msg: str):
    print(msg, file=sys.stderr)
    sys.exit(1)


def _open(args):
    try:
        s = Store(find_home(args.home))
    except HomeNotFound as e:
        _die(str(e))
    try:
        return s, s.get(args.id)
    except KeyError:
        _die(f"No ticket {args.id}.")


def _where(t) -> str:
    lane = lanes.lane_of(t)
    how = "" if lanes.explicit_lane(t) else " (derived: no lane is set)"
    bits = [f"{t.id}: lane {lane}{how}"]
    if lane == "parked" and t.meta.get("park_reason"):
        bits.append(f"parked because: {t.meta['park_reason']}")
    d = lanes.snooze_until(t)
    if d and lanes.is_snoozed(t):
        bits.append(f"snoozed until {d.isoformat()}")
    return "; ".join(bits)


def _warn(s, tid: str) -> None:
    """Say at once what `drew validate` would warn about this ticket (an emergency put aside, a snooze past a hard deadline)."""
    for w in lanes.warnings(s.get(tid)):
        print(f"warning: {w}", file=sys.stderr)


def cmd_lane(args):
    s, t = _open(args)
    if not args.lane and not args.clear:
        print(_where(t))
        return
    s.sync_before()
    try:
        lanes.set_lane(s, t, "" if args.clear else args.lane, args.reason or "")
    except (RuleError, ValueError) as e:
        _die(str(e))
    s.sync_after(f"desk: lane {t.id}")
    print(_where(s.get(t.id)))
    _warn(s, t.id)


def cmd_snooze(args):
    s, t = _open(args)
    if args.clear == bool(args.when):
        _die(f"Give a date or --clear, for example: drew snooze {t.id} 1w   (also 2026-11-22, 1d, 2w, next-monday)")
    try:
        until = None if args.clear else lanes.parse_when(args.when)
    except ValueError as e:
        _die(str(e))
    s.sync_before()
    try:
        lanes.set_snooze(s, t, until)
    except (RuleError, ValueError) as e:
        _die(str(e))
    s.sync_after(f"desk: snooze {t.id}")
    print(f"{t.id}: back on Today now" if until is None else f"{t.id}: snoozed until {until.isoformat()} (off Today and Needs me until then)")
    _warn(s, t.id)


def cmd_park(args):
    s, t = _open(args)
    s.sync_before()
    try:
        lanes.park(s, t, args.reason)
    except (RuleError, ValueError) as e:
        _die(str(e))
    s.sync_after(f"desk: park {t.id}")
    print(f"{t.id}: parked ({' '.join(args.reason.split())}). Nothing was deleted; `drew lane {t.id} planned` brings it back.")
    _warn(s, t.id)


def register(sub) -> None:
    """Add the three commands to `drew`'s subcommands."""
    sp = sub.add_parser("lane", help="show or set a ticket's lane: `drew lane ID now|planned|later|parked` (parked needs --reason); "
                                     "`drew lane ID` prints it; --clear goes back to the derived lane")
    sp.add_argument("id")
    sp.add_argument("lane", nargs="?", choices=lanes.LANES)
    sp.add_argument("--reason", help="why it is parked (one line)")
    sp.add_argument("--clear", action="store_true", help="remove the lane field; the derived lane shows")
    sp.set_defaults(fn=cmd_lane)

    sp = sub.add_parser("snooze", help="keep a ticket off Today and Needs me until a date: `drew snooze ID 2026-11-22` "
                                       "(or 1d, 1w, 2w, next-monday); `drew snooze ID --clear` brings it back now")
    sp.add_argument("id")
    sp.add_argument("when", nargs="?", metavar="DATE|1d|1w|2w|next-monday")
    sp.add_argument("--clear", action="store_true", help="end the snooze now")
    sp.set_defaults(fn=cmd_snooze)

    sp = sub.add_parser("park", help='park a ticket with a one-line reason: `drew park ID "not this quarter"`; nothing is deleted')
    sp.add_argument("id")
    sp.add_argument("reason")
    sp.set_defaults(fn=cmd_park)
