"""`drew brief` (DESK-0288): the daily brief as plain text, or as JSON with --json. Read-only; registered from cli.py with one line."""

from __future__ import annotations

import datetime as dt
import json


def register(sub) -> None:
    sp = sub.add_parser("brief", help="the daily brief as plain text: what needs your decision, hard deadlines this week, what shipped yesterday (ids, titles and counts only)")
    sp.add_argument("--day", metavar="YYYY-MM-DD", help="the day the brief is for (default: today)")
    sp.add_argument("--json", action="store_true", help="every section of the brief as JSON")
    sp.set_defaults(fn=dispatch)


def dispatch(args) -> None:
    from . import brief
    from .cli import _die, _store

    s = _store(args)
    try:
        day = dt.date.fromisoformat(args.day) if args.day else dt.date.today()
    except ValueError:
        _die(f"`{args.day}` is not a date. Write it as YYYY-MM-DD, for example 2026-11-15.")
    b = brief.build_brief(s, day)
    if args.json:
        print(json.dumps(b, default=str, ensure_ascii=False, indent=1))
    else:
        print(brief.brief_text(b))
