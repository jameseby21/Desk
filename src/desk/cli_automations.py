"""`drew auto` (DESK-0292): list, explain, dry-run, run, init and undo for the automation rules in automations.yaml.
Docs: docs/AUTOMATIONS.md, docs/COMMANDS.md (Automations)."""

from __future__ import annotations

import datetime as dt
import sys

from . import automations as au
from . import identity
from .config import HomeNotFound, find_home
from .store import Store

BAR = 20


def register(sub) -> None:
    sp = sub.add_parser("auto", help="automation rules (automations.yaml): list, explain, dry-run, run, init, undo")
    asub = sp.add_subparsers(dest="auto_cmd", required=True)
    p = asub.add_parser("list", help="every rule: on or off, what it does and where it comes from")
    p.set_defaults(fn=cmd_list)
    p = asub.add_parser("explain", help="one rule in full: its source, when, then, and the tickets it matches today")
    p.add_argument("rule")
    p.set_defaults(fn=cmd_explain)
    p = asub.add_parser("dry-run", help="what each rule would change today and why; changes nothing")
    p.add_argument("--rule", help="only this rule")
    p.add_argument("--today", metavar="YYYY-MM-DD", help="pretend it is this day")
    p.set_defaults(fn=cmd_dry_run)
    p = asub.add_parser("run", help="the sweep: run every rule now as automation, with progress and a summary line; prints the undo command")
    p.add_argument("--rule", help="only this rule")
    p.add_argument("--today", metavar="YYYY-MM-DD", help="pretend it is this day")
    p.set_defaults(fn=cmd_run)
    p = asub.add_parser("init", help="write the default automations.yaml (the rules deduced from the record, each with its source); never overwrites")
    p.set_defaults(fn=cmd_init)
    p = asub.add_parser("undo", help="restore every ticket one run changed (the batch id `drew auto run` printed)")
    p.add_argument("batch")
    p.set_defaults(fn=cmd_undo)


def _die(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(1)


def _store(args) -> Store:
    try:
        return Store(find_home(args.home))
    except HomeNotFound as e:
        _die(str(e))


def _rules(s: Store):
    try:
        return au.load(s.home)
    except au.AutomationError as e:
        _die(str(e))


def _today(args) -> dt.date:
    if not getattr(args, "today", None):
        return dt.date.today()
    try:
        return dt.date.fromisoformat(args.today)
    except ValueError:
        _die(f"`{args.today}` is not a date. Write it as YYYY-MM-DD.")


def cmd_list(args) -> None:
    s = _store(args)
    rules, _ = _rules(s)
    on = len([r for r in rules if r.enabled])
    print(f"{len(rules)} rules in {au.path(s.home)}, {on} on.")
    for r in rules:
        print(f"{'on ' if r.enabled else 'off'}  {r.name}")
        print(f"     {r.does}")
        print(f"     source: {r.source}")


def cmd_explain(args) -> None:
    s = _store(args)
    rules, repos = _rules(s)
    try:
        r = au.find(rules, args.rule)
    except au.AutomationError as e:
        _die(str(e))
    print(f"{r.name} ({'on' if r.enabled else 'off'})")
    print(f"What it does: {r.does}")
    print(f"Source: {r.source}")
    print("When: " + "; ".join(au.describe_when(r) or ["always"]))
    print("Then: " + ("; ".join(au.describe_then(r)) or "nothing"))
    print("Hand-set values: " + ("this rule overrides them" if r.override else "kept; this rule never changes a value a person set"))
    res = au.sweep(s, [au.Rule(**{**r.__dict__, "enabled": True})], repos, today=_today(args))
    for n in res.notes:
        print(n)
    print(f"Today it would change {len(res.changed)} ticket(s)" + (":" if res.changed else "."))
    for i in res.changed:
        print(f"  {i.ticket}  {i.title[:60]}")


def _print_items(res: au.Sweep) -> None:
    by_rule: dict = {}
    for i in res.items:
        by_rule.setdefault(i.rule, []).append(i)
    for rule, items in by_rule.items():
        print(f"\n{rule}: {len([i for i in items if not i.skipped])} ticket(s)")
        for i in items:
            print(f"  {i.ticket}  {i.title[:60]}")
            print(f"    why: {i.why}")
            for c in i.changes:
                print(f"    {'would: ' if not res.applied else ''}{c}")
            if i.skipped:
                print(f"    held back: {i.skipped}")


def _progress(i: int, n: int, rule: str, changed: int) -> None:
    fill = round(BAR * i / n) if n else BAR
    print(f"[{'#' * fill}{'-' * (BAR - fill)}] {i} of {n} rules · {rule}: {changed} ticket(s)", file=sys.stderr, flush=True)


def cmd_dry_run(args) -> None:
    s = _store(args)
    rules, repos = _rules(s)
    if args.rule:
        try:
            au.find(rules, args.rule)
        except au.AutomationError as e:
            _die(str(e))
    res = au.sweep(s, rules, repos, today=_today(args), apply=False, only=args.rule)
    _print_items(res)
    for n in res.notes:
        print(n)
    print("\n" + res.summary())


def cmd_run(args) -> None:
    s = _store(args)
    rules, repos = _rules(s)
    me = identity.CURRENT.get()
    res = au.sweep(s, rules, repos, today=_today(args), apply=True, only=args.rule, progress=_progress,
                   started_by=me.name if me is not None else "")
    _print_items(res)
    for n in res.notes:
        print(n)
    print("\n" + res.summary())


def cmd_init(args) -> None:
    s = _store(args)
    try:
        p = au.init(s.home)
    except au.AutomationError as e:
        _die(str(e))
    rules, _ = au.load(s.home)
    print(f"Wrote {p} with {len(rules)} rules ({len([r for r in rules if r.enabled])} on). `drew auto dry-run` shows what they would change today.")


def cmd_undo(args) -> None:
    s = _store(args)
    try:
        done, kept = au.undo(s, args.batch)
    except au.AutomationError as e:
        _die(str(e))
    for k in kept:
        print(k)
    print(f"Undone: {len(done)} ticket(s) restored from run {args.batch}" + (f"; {len(kept)} left as they are." if kept else "."))
