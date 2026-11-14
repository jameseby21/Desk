"""The `desk` command line. Agents use this; the owner mostly uses the UI."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from . import accept_migrate, check_migrate, commands_migrate, deadlines, nav, ops, profanity, retype, review as rv, style as styles, tests_migrate, tracks as trk, version as ver
from .config import HomeNotFound, find_home
from .rules import RuleError, validate_store
from .store import Store
from .ticket import PRIORITIES, SIZES, STATUSES, TYPES


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
        if args.deadline:
            deadlines.set_deadline(s, t, deadlines.parse_arg(args.deadline), args.deadline_kind, args.as_ or "main")
    except (RuleError, ValueError) as e:
        _die(str(e))
    if not args.assignee:
        _fill_from_rules(s, t)
    s.sync_after(f"desk: new {t.id}")
    print(t.path)


def cmd_sub(args):
    """DESK-0208: a subticket of a main ticket, through the same writer as `drew new`."""
    from . import subtickets

    s = _store(args)
    s.sync_before()
    dod = [x.strip() for x in (args.dod or "core").split(",") if x.strip()]
    try:
        t = ops.sub(s, args.parent, args.title, project=args.project, type_=args.type, priority=args.priority, size=args.size, owner=args.owner, dod=dod,
                    source=args.source or "", assignee=args.assignee)
    except (RuleError, ValueError) as e:
        _die(str(e))
    if not args.assignee:
        _fill_from_rules(s, t)
    s.sync_after(f"desk: sub {t.id} of {t.meta['parent']}")
    print(t.path)
    print(f"{t.id} is {subtickets.label_for(s.get(t.meta['parent']))}")


def _fill_from_rules(s: Store, t) -> None:
    """DESK-0168: a new ticket with no assignee gets one when assign.yaml (or the built-in table) names exactly one person."""
    from . import assign_rules

    try:
        out = assign_rules.apply_new(s, t)
    except assign_rules.RulesError as e:
        print(f"warning: {e}", file=sys.stderr)
        return
    if out.state == "filled":
        print(f"{t.id}: assignee {out.assignee} (rule {out.rule})", file=sys.stderr)
    elif out.state == "conflict":
        print(f"warning: {t.id}: {out.warning}", file=sys.stderr)


def cmd_list(args):
    from . import companies, people, priority, subtickets

    from . import sprints

    s = _store(args)
    who = people.load(s.home)
    every = s.tickets()
    fam = subtickets.Family(every)  # DESK-0208: a subticket is listed like any ticket, its title followed by the main ticket it is part of
    sc = priority.scores(s, every)  # DESK-0135: a score column; --sort score orders the table by it, highest first
    rows = []
    due = None
    if args.due is not None:
        due = deadlines.immediate(s.tickets(), days=args.due)  # the same set as Today's Immediate Action Required row, soonest first
        if not due:
            print(f"No hard deadline within {args.due} days, and none missed.")
            return
        for t in due:
            if args.project and t.project != args.project:
                continue
            rows.append([_id_label(s, t), t.project, deadlines.of(t)[0].isoformat(), deadlines.words(deadlines.days_left(t)), t.status, t.meta.get("priority", ""),
                         str(t.meta.get("owner", "")), str(t.meta.get("title", ""))[:60]])
        print(_table(rows, ["id", "project", "deadline", "when", "status", "pri", "owner", "title"]) if rows else "No tickets match.")
        return
    for t in (sc.sort(every) if getattr(args, "sort", "") == "score" else _by_project(every)):
        stale = s.is_stale(t)
        if args.project and t.project != args.project:
            continue
        if args.status and t.status != args.status:
            continue
        if args.owner and str(t.meta.get("owner")) != args.owner:
            continue
        if args.assignee and ops.assignee_of(t).lower() != who.resolve(args.assignee).lower():
            continue
        if args.sprint and sprints.norm(sprints.sprint_of(t)) != sprints.norm(args.sprint):
            continue
        if args.stale and not stale:
            continue
        if not args.all and not t.is_open and not args.status:
            continue
        part = fam.label(t)
        score = sc.of(t).score
        rows.append([_id_label(s, t), t.project, t.meta.get("type", ""), t.status + (" (stale)" if stale else ""), str(score) if score is not None else "", t.meta.get("priority", ""),
                     t.meta.get("size", ""), str(t.meta.get("owner", "")), who.display(ops.assignee_of(t)), companies.title(companies.of(s, t.project)),
                     str(t.meta.get("title", ""))[:60] + (f" ({part})" if part else "")])
    if not rows:
        print("No tickets match.")
        return
    print(_table(rows, ["id", "project", "type", "status", "score", "pri", "size", "owner", "assignee", "company", "title"]))


def cmd_explain(args):
    """`drew explain ID` (DESK-0137): the score, every term in plain words with its points, and which level set each weight."""
    from . import priority

    s = _store(args)
    try:
        t = s.get(args.id)
    except KeyError as e:
        _die(str(e))
    print(priority.explain(s, t))


def cmd_next_after(args, s):
    """`drew next --after ID`: the ticket after (or with --previous, before) ID in the walk this person chose in the UI."""
    saved = styles.load_nav(s.home)
    scope, order = args.scope or saved[0], args.order or saved[1]
    try:
        n = nav.neighbours(s.tickets(), args.after, scope, order)
    except KeyError:
        _die(f"No ticket {args.after}.")
    except ValueError as e:
        _die(str(e))
    target = n.prev if args.previous else n.next
    print(n.text)
    if target is None:
        print(n.prev_note if args.previous else n.next_note)
        return
    t = s.get(target)
    print(f"{t.id}  {t.status}  {t.meta.get('title')}")


def cmd_next(args):
    s = _store(args)
    if args.after:
        return cmd_next_after(args, s)
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
    from . import companies

    co = companies.of(s, t.project)
    known = co != companies.UNSORTED
    if s.formerly(t) or known:  # a renamed project (it keeps its original id), or the company it sits in (DESK-0181): say so before the file
        print(f"# {_id_label(s, t)}" + (f" · company {companies.title(co)}" if known else "") + f" · project {t.project} · {s.project_title(t.project)}\n")
    if args.raw:
        _print_ticket(t)
    else:  # display filter: code spans and fences stay as written; --raw or [style] profanity_filter = false shows everything
        fn = profanity.make_filter(s.home) if styles.load(s.home)["profanity"] else None
        print(profanity.censor_markdown(t.render(), fn) if fn else t.render(), end="")


def _write_cmd(args, fn, label):
    s = _store(args)
    s.sync_before()
    try:
        t = s.get(args.id)
        t = fn(s, t)
    except (KeyError, RuleError, ValueError) as e:
        _die(str(e))
    s.sync_after(f"desk: {label} {t.id}")
    print(f"{t.id} -> {t.status}")


def cmd_claim(args):
    _write_cmd(args, lambda s, t: ops.claim(s, t, args.owner), "claim")


def cmd_start(args):
    _write_cmd(args, lambda s, t: ops.start(s, t), "start")


def cmd_report(args):
    if not args.file and not args.text:
        _die("Give --file report.md or --text \"...\"")
    text = ops.load_report_file(Path(args.file)) if args.file else args.text
    failed = []

    def run(s, t):
        t = ops.report(s, t, text)
        failed.extend(t.failed_checks())
        return t

    _write_cmd(args, run, "report")
    for section, line in failed:  # handed over with a line marked `- [!]`: say so, once per line
        print(f"warning: handed over with a failed check ({section}): {line}", file=sys.stderr)


def cmd_decide(args):
    if bool(args.approve) == bool(args.changes):
        _die("Pick one: --approve \"reason\" or --changes \"reason\"")
    approve = bool(args.approve)
    reason = args.approve or args.changes
    _write_cmd(args, lambda s, t: ops.decide(s, t, approve, reason, actor=args.as_), "decide")


def cmd_comments(args):
    from . import comments as cm, identity

    s = _store(args)
    if args.mark_sweep:  # the flags come first in the sweep: repeated asks, then hand-offs nobody answered (DESK-0158)
        from . import repeats

        for f in repeats.flags(s):
            print(f"FLAG {f}")
    if args.mark_sweep and not (args.author or args.since or args.unanswered):
        print(f"sweep marked at {cm.mark_sweep(s.home).isoformat(sep=' ')}")
        return
    try:
        since = cm.parse_since(args.since, s.home)
    except ValueError as e:
        _die(str(e))
    rows = cm.query(s, author=args.author, since=since, unanswered=args.unanswered, answerers=tuple(args.answered_by or cm.ANSWERERS))
    if args.json:
        print(json.dumps([r.as_json() for r in rows], indent=2, ensure_ascii=False))
    else:
        for r in rows:
            mark = "" if r.answered else "  [open]"
            kind = f" {r.kind}" if r.kind else ""
            print(f"{r.ticket_id}  {r.when.strftime('%Y-%m-%d %H:%M')}  {r.author} [{identity.describe(r.ident or {})}]{kind}{mark}\n    {r.text}")
        print(f"{len(rows)} comment(s)")
    if args.mark_sweep:
        cm.mark_sweep(s.home)


def cmd_needs_me(args):
    from . import needs_me as nm

    s = _store(args)
    who = args.as_
    if args.seen or args.seen_all:
        every = nm.build(s, who)
        want = {i.upper() for i in args.seen or []}
        picked = [i.ticket for i in every if i.group == "change" and (args.seen_all or i.id in want)]
        skipped = sorted(want - {t.id for t in picked})
        n = nm.mark(s, picked)
        print(f"marked {n} change(s) seen")
        for i in skipped:
            print(f"{i}: no change to mark seen (asks stay until you answer them)")
        return
    from . import subtickets

    items = nm.build(s, who)
    fam = subtickets.Family(s.tickets())  # DESK-0208: a subticket on the list says which main ticket it is part of
    if args.json:
        print(json.dumps([{"ticket": i.id, "title": str(i.ticket.meta.get("title") or ""), "group": i.group, "kind": i.kind, "why": i.why,
                           "emergency": i.emergency, "part_of": fam.label(i.ticket)} for i in items], indent=2, ensure_ascii=False))
        return
    for i in items:
        part = fam.label(i.ticket)
        print(f"{i.chip:<13} {i.id}  {i.ticket.meta.get('title')}{f' ({part})' if part else ''}{'  [EMERGENCY]' if i.emergency else ''}\n    {i.why}")
    print(nm.summary(items) if items else nm.empty_words(styles.load(s.home)["name"])[0])


def cmd_done(args):
    _write_cmd(args, lambda s, t: ops.done(s, t, args.commit), "done")


def cmd_block(args):
    _write_cmd(args, lambda s, t: ops.block(s, t, args.on, args.reason, actor=args.as_), "block")


def cmd_unblock(args):
    _write_cmd(args, lambda s, t: ops.unblock(s, t, actor=args.as_, to=args.to), "unblock")


def cmd_reject(args):
    _write_cmd(args, lambda s, t: ops.reject(s, t, args.reason, actor=args.as_), "reject")


def cmd_move(args):
    from . import sprints

    if not args.to and args.sprint is None:
        _die("Say where: --to <status>, --sprint <name> (or --sprint '' to take it out of its sprint), or both.")

    def run(s, t):
        if args.sprint is not None:
            t = sprints.set_sprint(s, t, args.sprint, actor=args.as_)
        if args.to:
            t = ops.move(s, t, args.to, actor=args.as_, reason=args.reason)
        return t

    try:
        _write_cmd(args, run, "move")
    except sprints.SprintError as e:
        _die(str(e))


def cmd_assign(args):
    def run(s, t):
        t = ops.assign(s, t, args.to, note=args.note or "", handoff=args.handoff or "", actor=args.as_)
        print(f"{t.id} assigned to {t.meta['assignee']} at {t.meta['assigned_at']}")
        print(f"Done means: {ops.ASSIGN_DOD}" + ("" if t.meta.get("handoff") else "   (no --handoff given yet: link the hand-off record)"))
        return t

    _write_cmd(args, run, "assign")


def cmd_comment(args):
    s = _store(args)
    s.sync_before()
    try:
        res = ops.comment(s, s.get(args.id), args.text, actor=args.as_, kind=args.kind)
    except (KeyError, RuleError, ValueError) as e:
        _die(str(e))
    if not res.added:
        print(f"{res.id}: {res.message.lower()}; nothing written")
        return
    s.sync_after(f"desk: comment {res.id}")
    print(f"{res.id} -> {res.status}")


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


def cmd_brand_check(args):
    from . import brand

    s = _store(args)
    rows = brand.missing(s)
    for key, reason in rows:
        print(f"{key}: {reason}")
    print(f"{len(rows)} project(s) without a usable brand")
    if args.create:
        s.sync_before()
        try:
            created, skipped = brand.create(s, assignee=args.assignee)
        except (RuleError, ValueError) as e:
            _die(str(e))
        s.sync_after("desk: brand-check")
        for i in created:
            print(f"created {i}")
        for i in skipped:
            print(f"already open {i}")


def cmd_search(args):
    from . import search as srch

    s = _store(args)
    ix = srch.open_index(s)
    ix.refresh(s)
    res = ix.search(" ".join(args.query), limit=args.limit)
    if args.json:
        print(json.dumps(res.as_json(), indent=2, ensure_ascii=False))
        return
    if res.query.empty:
        _die("Give words to find, filters like status:review, or both. See docs/COMMANDS.md for the grammar.")
    note = " (closest spellings; nothing matched exactly)" if res.fuzzy and not any(not h.fuzzy for _, _, hs in res.groups for h in hs) else ""
    print(f"{res.total}{'+' if res.capped else ''} match(es) for: {res.query.raw}{note}")
    for project, n, hits in res.groups:
        print(f"{chr(10)}{project} ({n})")
        for h in hits:
            print(f"  {h.id}  {h.status}  {h.priority}  {h.title[:70]}" + (f"  [{h.assignee}]" if h.assignee else ""))
            if h.snippet:
                print(f"      {h.snippet[:110]}")
        if n > len(hits):
            print(f"  ... {n - len(hits)} more in {project}: add project:{project} and --limit {n}")


def cmd_people(args):
    from . import people

    s = _store(args)
    if args.action == "init":
        try:
            print(people.write_seed(s.home, force=args.force))
        except FileExistsError as e:
            _die(str(e))
        return
    pe = people.load(s.home)
    src = s.home / "people.yaml"
    print(f"people from {src if src.is_file() else 'the built-in team (run `drew people init` to write people.yaml)'}"
          + (f" plus {people.private_path()}" if people.private_path().is_file() else ""))
    for label, ps in pe.groups():
        print(f"{chr(10)}{label}")
        for p in ps:
            print(f"  {p.key:<16} {p.name:<18} {p.kind:<9} {p.colour or '-':<8} {p.role}")


def cmd_outbox(args):
    """DESK-0174: the entry that lists, for one coworker thread, the tickets to look at since the last entry."""
    from . import comments as cm, outbox as ob, people

    s = _store(args)
    if not (args.for_ or args.all):
        _die("Say whose: --for <actor> (chaz, em, lily, bri, joe, bigd) or --all.")
    pe = people.load(s.home)
    now = dt.datetime.now().replace(microsecond=0)
    names = ob.actors(s) if args.all else [pe.resolve(args.for_)]
    shared = cm.entries(s)
    out = []
    for actor in names:
        try:
            since, how = ob.parse_since(args.since, s.home, actor)
        except ValueError as e:
            _die(str(e))
        lines = ob.collect(s, actor, since, shared)
        out.append((actor, since, how, lines))
    if args.json:
        print(json.dumps([ob.as_json(s, a, ln, sn, hw) for a, sn, hw, ln in out], indent=2, ensure_ascii=False))
    else:
        shown = 0
        for actor, since, how, lines in out:
            if not lines:
                if not args.all:
                    print(f"Nothing new for {pe.display(actor)} since {since.strftime('%Y-%m-%d %H:%M')} ({how}).")
                continue
            if shown:
                print(chr(10) + "-" * 60 + chr(10))
            print(ob.render(s, actor, lines, since, how), end="")
            shown += 1
        if args.all and not shown:
            print("Nothing new for any thread.")
    if args.posted:
        for actor, _, _, lines in out:
            if lines:
                ob.set_cutoff(s.home, actor, now)
                print(f"cut-off for {actor} is now {now.isoformat()}", file=sys.stderr)


def cmd_dispatch(args):
    """DESK-0166: the ready tickets nobody has started, grouped by repository, so one worker per repo can be started."""
    from . import dispatch as dp

    s = _store(args)
    rows = dp.undispatched(s)
    groups = dp.by_repo(s, rows)
    if args.json:
        print(json.dumps([{"repo": repo, "tickets": [dp.brief(t, h) for t, h in rs]} for repo, rs in groups], indent=2, ensure_ascii=False))
        return
    if not rows:
        print("Nothing is waiting for a worker: no ticket is ready, unblocked and Patch's for more than an hour.")
        return
    for repo, rs in groups:
        print(f"{repo}: {len(rs)} ticket(s), oldest first")
        for t, h in rs:
            b = dp.brief(t, h)
            print(f"  {b['id']}  {b['size']:<2} {b['effort']:<8} waiting {b['idle_hours']:>5.1f} h  deps: {', '.join(b['deps']) or 'none'}  {b['title'][:60]}")


def cmd_sprint(args):
    """DESK-0147: sprints.yaml. `new` creates, `list` lists, `show` prints a sprint's tickets in score order with the burn-down numbers."""
    from . import sprints as sp

    s = _store(args)
    if args.action == "new":
        if not args.name:
            _die("Give the sprint a name: drew sprint new 'sprint 6 2026' --start 2026-11-09 --end 2026-11-22 --project desk")
        try:
            x = sp.create(s, args.name, start=args.start or "", end=args.end or "", owner=args.owner or "", project=args.project or "", note=args.note or "")
        except sp.SprintError as e:
            _die(str(e))
        s.sync_after(f"desk: sprint new {x.name}")
        print(f"sprint `{x.name}`" + (f" in {x.project}" if x.project else "") + f" created in {s.home / sp.PATH}")
        return
    if args.action == "list":
        rows = sp.all_sprints(s)
        defs = {x.slug: x for x in sp.load(s.home)}
        print(_table([[n, defs[slug].start if slug in defs else "", defs[slug].end if slug in defs else "", defs[slug].project if slug in defs else "", str(total), str(op)]
                      for slug, n, total, op in rows] or [["(none)", "", "", "", "0", "0"]], ["sprint", "start", "end", "project", "tickets", "open"]))
        return
    if not args.name:
        _die("Which sprint? drew sprint show 'sprint 6 2026'")
    import datetime as _dt

    gs = sp.groups(s, sp.slugify(args.name, 60), today=_dt.date.today())
    if not gs:
        _die(f"No sprint `{args.name}` in sprints.yaml and no ticket names it.")
    for g in gs:
        meta = f"{g.sprint.start} to {g.sprint.end}" if g.sprint and g.sprint.start else "no dates"
        print(f"{g.name}: {meta}" + (f" · owner {g.sprint.owner}" if g.sprint and g.sprint.owner else ""))
        print(f"  {g.total} ticket(s): {g.done} done, {g.remaining} remaining" + (f"; {g.days_left} of {g.days_total} days left; ideal remaining today {g.ideal_now}" if g.days_left is not None else ""))
        for t in g.tickets:
            print(f"  {t.id}  {t.meta.get('priority')}  {t.status:<12} {str(t.meta.get('title', ''))[:60]}")
        if g.series:
            print("  burn-down (day: remaining / ideal): " + ", ".join(f"{d[5:]}: {r}/{i}" for d, r, i in g.series[-7:]))


def cmd_repeats(args):
    """DESK-0158: repeated asks and stale hand-offs. --apply makes the tickets with a repeated ask p0 with the tag emergency."""
    from . import repeats

    s = _store(args)
    tickets = s.tickets()
    rs = repeats.repeats(s, tickets)
    stale = repeats.stale_handoffs(s, tickets)
    for r in rs:
        print(r.message(s.owner))
    for t, at in stale:
        print(repeats.stale_message(s, t, at))
    print(f"{len(rs)} repeated ask(s), {len(stale)} stale hand-off(s)")
    if args.apply and rs:
        s.sync_before()
        for r in rs:
            t = s.get(r.ticket.id)
            if repeats.escalate(s, t):
                t.append_line("Comments", ops._stamp("desk", "repeated ask: priority p0, tag emergency", at_time=True))
                s.save(t)
                print(f"{t.id} -> p0, emergency")
        s.sync_after("desk: repeats --apply")


def cmd_deadline(args):
    """DESK-0193: set or clear a ticket's deadline and its kind."""
    s = _store(args)
    s.sync_before()
    try:
        t = s.get(args.id)
        if args.clear:
            deadlines.set_deadline(s, t, None, "", args.as_ or "main")
            print(f"{t.id}: deadline cleared")
        else:
            if not args.date or not args.kind:
                _die("Give a date and one of --hard, --soft or --sprint, for example: drew deadline DESK-0193 2026-11-12 --hard")
            d = deadlines.parse_arg(args.date)
            deadlines.set_deadline(s, t, d, args.kind, args.as_ or "main")
            print(f"{t.id}: deadline {d.isoformat()} ({args.kind}), {deadlines.words(deadlines.days_left(t))}")
    except (KeyError, RuleError, ValueError) as e:
        _die(str(e).strip("'"))
    s.sync_after(f"desk: deadline {t.id}")


def cmd_refine(args):
    """DESK-0145: tracked refinement of the requirements: a dated Acceptance line and a Comments note; review_by may move with a reason."""
    s = _store(args)
    s.sync_before()
    try:
        t = ops.refine(s, s.get(args.id), args.line, actor=args.as_, review_by=args.review_by, reason=args.reason or "")
    except (KeyError, RuleError, ValueError) as e:
        _die(str(e))
    s.sync_after(f"desk: refine {t.id}")
    print(f"{t.id}: refinement added to Acceptance" + (f"; review_by is now {t.meta['review_by']}" if args.review_by else ""))


def cmd_split(args):
    """DESK-0145: a change of scope is a new ticket, linked both ways, with a completion decision asked on the old one."""
    s = _store(args)
    s.sync_before()
    try:
        old = s.get(args.id)
        new = ops.split(s, old, args.title, size=args.size, due=args.due, details=args.details or "", outcome=args.outcome or "", acceptance=args.acceptance, actor=args.as_)
    except (KeyError, RuleError, ValueError) as e:
        _die(str(e))
    s.sync_after(f"desk: split {old.id} -> {new.id}")
    print(f"{new.id} created from {old.id}: depends_on {old.id}; {old.id} lists it under blocks and asks for the completion decision (done as scoped, or closed in favour)")
    print(new.path)


def cmd_whoami(args):
    """DESK-0144: how this command line would be recorded: the actor, kind, instance, source and how it was established."""
    from . import identity

    s = _store(args)
    a = identity.resolve_cli(s, args.as_)
    if args.json:
        print(json.dumps(a.as_dict(), indent=2))
        return
    print(f"{a.display()}: {a.short()}" + (f" · instance {a.instance}" if a.instance else "") + (f" · parent {a.parent}" if a.parent else ""))
    if not a.confirmed:
        print("This is a claim, not a check: Desk records it as claimed. To be confirmed, run as your own operating-system user, listed under os_users in people.yaml.")


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
        print(f"\n{st} · {len(items)}")
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
    if args.check:
        from . import intake_check

        try:
            text, errors = intake_check.run(Path(args.path), s)
        except OSError as e:
            _die(str(e))
        print(text, end="")
        print(f"report: {Path(args.path).with_name(Path(args.path).name + '.check.md')}")
        sys.exit(1 if errors else 0)
    if not (args.topic and args.project):
        _die("intake needs --topic and --project (or --check to only validate a SLICES file)")
    s.sync_before()
    try:
        t, dest = ops.intake(s, Path(args.path), args.topic, args.project, actor=args.as_ or "main")
    except (RuleError, ValueError) as e:
        _die(str(e))
    _fill_from_rules(s, t)
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
    from . import priority

    s = _store(args)
    st = ops.stats(s, since=args.since, project=args.project)
    every = [t for t in s.tickets() if not args.project or t.project == args.project]
    sc = priority.scores(s)
    whole = priority.rollup(sc, every)
    by_person = priority.by_group(sc, [t for t in every if t.is_open], lambda t: ops.assignee_of(t) or "(unassigned)")
    st["score"] = {"store": {"open": whole.n, "max": whole.max, "sum": whole.sum, "mean": whole.mean, "top": whole.top.id if whole.top else ""},
                   "by_assignee": {k: {"open": r.n, "max": r.max, "sum": r.sum, "mean": r.mean, "top": r.top.id if r.top else ""} for k, r in by_person}}
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
    print("\nBy assignee")
    print(_table([[k, str(v["open"]), str(v["done_this_week"])] for k, v in st["by_assignee"].items()] or [["(none)", "0", "0"]], ["assignee", "open", "done this week"]))
    print("\nBy score (DESK-0136: the highest score ranks; load is the sum, mean is the health)")
    print(_table([[k, str(r.n), str(r.max), str(r.sum), f"{r.mean:g}", r.top.id if r.top else ""] for k, r in by_person] or [["(none)", "0", "0", "0", "0", ""]],
                 ["assignee", "open", "score", "load", "mean", "top ticket"]))
    print(f"Whole store: {whole.n} open · score {whole.max} · load {whole.sum} · mean {whole.mean:g}" + (f" · top {whole.top.id}" if whole.top else ""))
    print("\nDecisions per week")
    print(_table([[k, str(v)] for k, v in st["decisions_per_week"].items()], ["week of", "tickets decided"]))
    print(f"\nReview weeks: {st['review_weeks']['created']} created, {st['review_weeks']['closed']} closed")


def cmd_review_new(args):
    s = _store(args)
    s.sync_before()
    try:
        t, _ = rv.create(s, rv.parse_day(args.week), project=args.project, owner=args.owner)
    except (rv.ReviewError, RuleError, ValueError) as e:
        _die(str(e))
    s.sync_after(f"desk: review new {t.id}")
    print(t.path)


def cmd_review_sync(args):
    s = _store(args)
    s.sync_before()
    try:
        if args.id:
            t = s.get(args.id)
            if not rv.is_review_ticket(t):
                _die(f"{t.id} is not a review-week ticket.")
        else:
            t = rv.find(s, rv.monday_of(dt.date.today()))
            if not t:
                _die("No ticket review for this week yet. Run `drew review new`.")
        res = rv.apply(s, t)
    except (KeyError, rv.ReviewError, RuleError, ValueError) as e:
        _die(str(e))
    s.sync_after(f"desk: review sync {t.id}")
    k = res["kpi"]
    print(f"{t.id}: {k['waiting']} waiting, {res['added']} added, {res['checked']} checked off, {res['resolved']} resolved; "
          f"{k['decided']} decided this week")


class _Version(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        print(ver.describe())
        parser.exit()


def _home_or_none(args) -> Path | None:
    try:
        return find_home(args.home)
    except HomeNotFound:
        return None


def cmd_bench(args):
    from . import bench

    if args.search_rows:
        r = bench.search_scale(args.search_rows)
        print(f"search over {r['rows']} synthetic rows (index built in {r['built_s']} s): p50 {r['p50_ms']} ms, p95 {r['p95_ms']} ms over {r['n']} queries; budget {bench.load_budgets()['budgets_ms']['search']} ms")
        return
    result = bench.run(args.tickets, runs=args.runs, progress=lambda m: print(m, file=sys.stderr))
    print(bench.table(result))
    for f in bench.budget_failures(result):
        print("OVER BUDGET  " + f)
    for f in bench.regressions(result):
        print("REGRESSION   " + f)
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if args.save_baseline:
        print(f"baseline saved to {bench.save_baseline(result)}")


def cmd_sync(args):
    s = _store(args)
    made, last = s.sync_now()
    print(("committed pending changes; " if made else "nothing pending to commit; ") + last)


def cmd_doctor(args):
    lines, stale = ver.doctor_report(_home_or_none(args), args.repo)
    for line in lines:
        print(line)
    if stale:
        sys.exit(1)


def cmd_upgrade(args):
    import shutil
    import subprocess

    repo = ver.find_repo(args.repo, _home_or_none(args))
    if repo is None:
        _die("No Desk repo found. Pass --repo PATH, set DESK_REPO, or keep the repo at <store>/desk.")
    running = ver.listeners()
    if running:
        pid = running[0]
        _die(f"Refusing to upgrade: a process (PID {pid}) is listening on the UI port {ver.ui_port()}. "
             f"If it is the Desk UI, stop it first with `{ver.stop_hint(pid)}`, then run `drew upgrade` again.")
    uv = shutil.which("uv")
    if not uv:
        _die("uv is not on PATH; install uv first (https://docs.astral.sh/uv/).")
    if ver.on_windows() and ver.in_uv_tools_dir():
        return _upgrade_detached(uv, repo)
    r = subprocess.run([uv, "tool", "install", "--force", str(repo)])
    if r.returncode != 0:
        _die(f"uv tool install failed (exit {r.returncode}). If Desk no longer starts, restore it with `{ver.restore_command(repo)}`.", r.returncode)
    exe = shutil.which("drew")
    out = subprocess.run([exe, "--version"], capture_output=True, text=True).stdout.strip() if exe else ""
    print(f"Upgraded: {out or 'installed (run `drew --version` to confirm)'}")


def _upgrade_detached(uv: str, repo: Path):
    """Windows cannot delete drew.exe while it runs, and `uv tool install --force` deletes it. So hand the install to a .cmd that
    starts after this process has exited, outside the tools directory (DESK-0132)."""
    import subprocess
    import tempfile

    tmp = Path(tempfile.gettempdir())
    script, log = tmp / "desk-upgrade.cmd", tmp / "desk-upgrade.log"
    script.write_bytes(ver.upgrade_script(uv, repo, str(log)).encode("utf-8"))
    flags = getattr(subprocess, "DETACHED_PROCESS", 0x8) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
    subprocess.Popen(["cmd.exe", "/c", str(script)], creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("Upgrade handed off to a separate process; it starts in 2 seconds, after this command exits (Windows cannot replace drew.exe while it runs).")
    print(f"In about 30 seconds run `drew doctor` (the installed commit should equal the repo HEAD) or read {log}.")
    print(f"If Desk stops starting (No module named 'desk'), restore it with: {ver.restore_command(repo)}")


def cmd_stop(args):
    """DESK-0169: end whatever listens on the UI port, by PID. Exit 0 when nothing listens."""
    port = args.port or ver.ui_port()
    pids = ver.listeners(port)
    if not pids:
        print(f"nothing listens on port {port}; nothing to stop")
        return
    failed = 0
    for pid in pids:
        if ver.stop_pid(pid):
            print(f"PID {pid} stopped (was listening on port {port})")
        else:
            failed += 1
            print(f"PID {pid} did not stop; end it by hand with `{ver.stop_hint(pid)}`", file=sys.stderr)
    if failed:
        sys.exit(1)


def cmd_refresh(args):
    """DESK-0169: stop the UI, install from the repo (or a git ref of it), start the UI again, print the version."""
    import shutil
    import subprocess
    import tempfile
    import time

    repo = ver.find_repo(args.repo, _home_or_none(args))
    if repo is None:
        _die("No Desk repo found. Pass --repo PATH, set DESK_REPO, or keep the repo at <store>/desk.")
    uv = shutil.which("uv")
    if not uv:
        _die("uv is not on PATH; install uv first (https://docs.astral.sh/uv/).")
    if args.ref and not ver.ref_exists(repo, args.ref):
        _die(f"{args.ref} is not a branch, tag or commit of {repo}. Nothing was stopped or installed.")
    source = ver.install_source(repo, args.ref)
    port = ver.ui_port()
    for pid in ver.listeners(port):
        if ver.stop_pid(pid):
            print(f"PID {pid} stopped (was listening on port {port})")
        else:
            _die(f"PID {pid} did not stop; end it with `{ver.stop_hint(pid)}` and run `drew refresh` again. Nothing was installed.")
    if ver.on_windows() and ver.in_uv_tools_dir():  # drew.exe cannot replace itself while it runs: a detached script installs after we exit (DESK-0132)
        tmp = Path(tempfile.gettempdir())
        script, log = tmp / "desk-refresh.cmd", tmp / "desk-refresh.log"
        script.write_bytes(ver.refresh_script(uv, source, str(log), port, repo).encode("utf-8"))
        flags = getattr(subprocess, "DETACHED_PROCESS", 0x8) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
        subprocess.Popen(["cmd.exe", "/c", str(script)], creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"Refresh handed off to a separate process (installing {source}); it starts in 2 seconds, after this command exits, then opens drew-ui in its own window.")
        print(f"In about 30 seconds read {log}: it ends with `Desk <version> · <commit>` from `drew --version`. If Desk stops starting, restore it with: {ver.restore_command(repo)}")
        return
    r = subprocess.run([uv, "tool", "install", "--force", source])
    if r.returncode != 0:
        _die(f"uv tool install failed (exit {r.returncode}). If Desk no longer starts, restore it with `{ver.restore_command(repo)}`.", r.returncode)
    exe = shutil.which("drew-ui")
    if exe:
        log = Path(tempfile.gettempdir()) / "desk-ui.log"
        out = open(log, "ab")
        kw = {"creationflags": getattr(subprocess, "DETACHED_PROCESS", 0x8) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)} if ver.on_windows() else {"start_new_session": True}
        subprocess.Popen([exe], stdin=subprocess.DEVNULL, stdout=out, stderr=out, close_fds=True, **kw)
        for _ in range(60):
            if ver.listeners(port):
                break
            time.sleep(0.5)
        print(f"drew-ui started on port {port} (log: {log})")
    else:
        print("drew-ui is not on PATH after the install; start it yourself.")
    d = shutil.which("drew")
    print(subprocess.run([d, "--version"], capture_output=True, text=True).stdout.strip() if d else "run `drew --version` to see the installed commit")


def cmd_migrate(args):
    if args.what == "hierarchy":
        return cmd_migrate_hierarchy(args)
    if args.undo:
        _die("--undo is for `drew migrate hierarchy` only")
    if not (args.dry_run or args.apply):
        _die("Give --dry-run or --apply.")
    if args.what == "type":
        return cmd_migrate_type(args)
    if args.what == "acceptance":
        return cmd_migrate_acceptance(args)
    if args.what == "checks":
        return cmd_migrate_checks(args)
    if args.what == "tests":
        return cmd_migrate_tests(args)
    if args.what == "assignee":
        return cmd_migrate_assignee(args)
    if args.what == "commands":
        return cmd_migrate_commands(args)
    if args.what == "events":
        return cmd_migrate_events(args)
    s = _store(args)
    rules_path = Path(args.rules) if args.rules else s.home / trk.RULES_PATH
    if not rules_path.is_absolute() and not rules_path.exists():
        rules_path = s.home / rules_path
    try:
        rules = trk.load_rules(rules_path)
    except trk.RulesError as e:
        _die(str(e))
    tickets = s.tickets()
    pairs = trk.plan(tickets, rules)
    rows = [[t.id, str(t.meta.get("title", ""))[:60], name] for t, name in pairs]
    if rows:
        print(_table(rows, ["ticket", "title", "track"]))
        print()
    print(_table([[n, str(c)] for n, c in trk.counts(pairs)], ["track", "tickets"]))
    kept = len(tickets) - len(pairs)
    print()
    print(f"{len(pairs)} to set; {kept} left alone (already have a track, or no rule matched); rules: {rules_path}")
    if not args.apply:
        print("Dry run: nothing written. Add --apply to write track: lines.")
        return
    wrote = sum(1 for t, name in pairs if trk.write_track(t, name))
    print(f"Wrote track: to {wrote} tickets. updated dates were not changed.")


def _in_store(s, given, default):
    p = Path(given) if given else s.home / default
    return p if p.is_absolute() or p.exists() else s.home / p


def cmd_migrate_hierarchy(args):
    """DESK-0181: company: in each profile, the [hierarchy] block in desk.toml, and track: mapped to the vocabulary with the old text
    kept as legacy_track:. A dry run unless --apply; --undo takes all of it back out (with --dry-run it only counts)."""
    from . import hierarchy

    s = _store(args)
    if args.undo:
        profiles, tickets, toml = hierarchy.undo_plan(s)
        print(f"{len(profiles)} profile(s) with a company: line, {len(tickets)} ticket(s) with legacy_track:, desk.toml block: {'yes' if toml else 'no'}")
        if args.dry_run:
            print("Dry run: nothing written. Run `drew migrate hierarchy --undo` without --dry-run to restore.")
            return
        np_, nt, wt = hierarchy.undo(s)
        print(f"Undone: {np_} profile(s) lost their company: line, {nt} ticket(s) restored track: from legacy_track:"
              + (", and the [hierarchy] block left desk.toml" if wt else "") + ". Every other byte is as it was.")
        return
    try:
        cmap = hierarchy.load_companies(_in_store(s, args.companies, hierarchy.COMPANIES_PATH))
        vocab = hierarchy.load_vocabulary(_in_store(s, args.vocabulary, hierarchy.VOCABULARY_PATH))
    except hierarchy.HierarchyError as e:
        _die(str(e))
    p = hierarchy.plan(s, cmap, vocab)
    names = {**cmap.names, **hierarchy.company_names(s)}
    if p.profiles:
        print(_table([[k, ck, names.get(ck, ck)] for k, ck in p.profiles], ["project", "company", "shown as"]))
        print()
    if p.tickets:
        print(_table([[t.id, old, new] for t, old, new in p.tickets], ["ticket", "track (kept as legacy_track)", "new track"]))
        print()
    if p.unmapped:
        rows = [[v, str(n)] for v, n in sorted(p.unmapped.items(), key=lambda kv: (-kv[1], kv[0]))]
        print(_table(rows, ["track not in the vocabulary map (left alone)", "tickets"]))
        print()
    print(f"{len(p.profiles)} profile(s) get company:; {len(p.tickets)} ticket(s) get a new track: and legacy_track:; {p.kept} ticket(s) left alone; "
          f"desk.toml {'gets the [hierarchy] block' if p.toml else 'already has [hierarchy]'}.")
    if not args.apply:
        print("Dry run: nothing written. Add --apply to write; `drew migrate hierarchy --undo` takes it back out.")
        return
    np_, nt, wt = hierarchy.apply(s, p)
    print(f"{np_} profile(s) and {nt} ticket(s) written" + (", and desk.toml" if wt else "") + ". updated dates were not changed.")


def cmd_migrate_events(args):
    """DESK-0162 / DESK-0045: the Comments lines and the git history of every ticket file become events in its log, so old tickets
    have a timeline. A dry run lists the counts per ticket; --apply writes each event once (a second run writes nothing)."""
    from . import activity

    s = _store(args)

    def progress(i, n):
        print(f"Looked at {i:,} of {n:,} tickets", file=sys.stderr, flush=True)

    print("Reading the git history of tickets/ (one git log call) ...", file=sys.stderr, flush=True)
    rows, wrote, note = activity.migrate(s, apply=args.apply, progress=progress)
    if rows:
        print(_table([[tid, str(c), str(g), str(c + g)] for tid, c, g, _ in rows], ["ticket", "comments", "git commits", "events"]))
        print()
    if note:
        print(f"Note: {note}. Comments were still read.")
    total, nt = sum(c + g for _, c, g, _ in rows), len(rows)
    tk = "ticket" if nt == 1 else "tickets"
    if not rows:
        print("Nothing to add: every ticket's history is already in its log.")
    elif not args.apply:
        print(f"Dry run: nothing written. {total:,} events would be added to {nt:,} {tk} "
              f"({sum(c for _, c, _, _ in rows):,} from comments, {sum(g for _, _, g, _ in rows):,} from git). Add --apply to write them.")
    else:
        print(f"Wrote {wrote:,} events to {nt:,} {tk}. Running it again adds nothing.")


def cmd_history(args):
    """DESK-0171: a ticket's timeline in the terminal: the latest change, then every event newest first; --by groups and --filter narrows."""
    from . import timeline

    s = _store(args)
    try:
        t = s.get(args.id)
    except KeyError as e:
        _die(str(e).strip("'"))
    try:
        filters = timeline.parse_filters(args.filter or [])
    except ValueError as e:
        _die(str(e))
    view = timeline.view(s, t, filters=filters)
    if args.json:
        print(json.dumps([r.as_json() for r in view.shown], indent=2, ensure_ascii=False))
        return
    print(timeline.text(view, by=args.by, colour=sys.stdout.isatty()))


def cmd_migrate_assignee(args):
    """DESK-0168: fill blank assignees from the rules. Blank is James's "not yet scanned" marker, so --apply is run only when he says."""
    from . import assign_rules as ar, people

    s = _store(args)
    try:
        rules, source = ar.load(s.home)
    except ar.RulesError as e:
        _die(str(e))
    pairs = ar.plan(s.tickets(), rules, people.load(s.home), include_closed=args.include_closed)
    fill = [(t, o) for t, o in pairs if o.state == "filled"]
    rows = [[t.id, str(t.meta.get("title", ""))[:50], o.assignee, o.rule] for t, o in fill]
    if rows:
        print(_table(rows, ["ticket", "title", "assignee", "rule"]))
        print()
    by_rule: dict = {}
    for _, o in fill:
        by_rule[(o.assignee, o.rule)] = by_rule.get((o.assignee, o.rule), 0) + 1
    print(_table([[a, r, str(n)] for (a, r), n in sorted(by_rule.items(), key=lambda kv: (-kv[1], kv[0]))] or [["(none)", "", "0"]], ["assignee", "rule", "tickets"]))
    conflict = [t.id for t, o in pairs if o.state == "conflict"]
    print()
    print(f"{len(fill)} blank assignee(s) to fill; {len(conflict)} left blank because two rules disagree; {len(pairs) - len(fill) - len(conflict)} left blank because no rule matches; "
          f"tickets that already have an assignee are never changed; rules: {source}")
    if conflict:
        print("disagree: " + ", ".join(conflict[:12]) + (" ..." if len(conflict) > 12 else ""))
    if not args.apply:
        print("Dry run: nothing written. --apply writes `assignee:` and `assigned_by: rule:<name>` into the blank tickets (updated dates are not changed). Run it only when James says: blank is his not-yet-scanned marker.")
        return
    wrote = sum(ar.write_assignee(t, o.assignee, o.rule) for t, o in fill)
    print(f"Filled {wrote} ticket(s). updated dates were not changed.")


def cmd_assign_rules(args):
    from . import assign_rules as ar

    s = _store(args)
    if args.action == "init":
        try:
            print(ar.write_seed(s.home, force=args.force))
        except FileExistsError as e:
            _die(str(e))
        return
    try:
        rules, source = ar.load(s.home)
    except ar.RulesError as e:
        _die(str(e))
    print(f"assignment rules from {source}")
    for r in rules:
        print(f"  {r.name:<26} -> {r.assign:<14} {'(fallback) ' if r.fallback else ''}when {r.when}")


def cmd_migrate_type(args):
    s = _store(args)
    tickets = s.tickets()
    pairs = retype.plan(tickets)
    rows = [[t.id, str(t.meta.get("title", ""))[:56], f"{t.meta.get('type')} -> {new}"] for t, new in pairs]
    if rows:
        print(_table(rows, ["ticket", "title", "type"]))
        print()
    print(_table([[n, str(c)] for n, c in retype.counts(pairs)], ["new type", "tickets"]))
    print()
    print(f"{len(pairs)} legacy tickets to retype; {len(tickets) - len(pairs)} left alone.")
    if not args.apply:
        print("Dry run: nothing written. Add --apply to rewrite the type: lines.")
        return
    wrote = sum(1 for t, new in pairs if retype.write_type(t, new))
    print(f"Retyped {wrote} tickets. updated dates were not changed.")


def cmd_migrate_acceptance(args):
    s = _store(args)
    pairs = accept_migrate.plan(s.tickets())
    for t, lines in pairs:
        for state, text in lines:
            print(f"{t.id}  [{state}] {text}")
    total = sum(len(lines) for _, lines in pairs)
    by_text: dict = {}
    for _, lines in pairs:
        for _, text in lines:
            by_text[text] = by_text.get(text, 0) + 1
    if pairs:
        print()
        print(_table([[str(v), k] for k, v in sorted(by_text.items(), key=lambda kv: (-kv[1], kv[0]))], ["lines", "boilerplate text"]))
    print()
    print(f"{total} boilerplate lines in {len(pairs)} tickets would be removed; {len(s.tickets()) - len(pairs)} tickets left alone.")
    if not args.apply:
        print("Dry run: nothing written. Add --apply to remove them (only Acceptance changes; updated dates are not touched).")
        return
    wrote = sum(accept_migrate.strip_file(t) for t, _ in pairs)
    print(f"Removed {wrote} lines from {len(pairs)} tickets.")


def cmd_migrate_tests(args):
    s = _store(args)
    todo = tests_migrate.plan(s.tickets())
    for t in todo:
        print(f"{t.id}  {str(t.meta.get('title', ''))[:60]}")
    print(f"\n{len(todo)} ticket(s) without Tests text would get the seed; {len(s.tickets()) - len(todo)} left alone.")
    if not args.apply:
        print("Dry run: nothing written. Add --apply to add the section (only `## Tests` changes; updated dates are not touched).")
        return
    wrote = sum(tests_migrate.apply_file(t) for t in todo)
    print(f"Added the Tests section to {wrote} ticket(s).")


def cmd_migrate_commands(args):
    s = _store(args)
    todo = commands_migrate.plan(s.tickets())
    seeded = 0
    for t, found in todo:
        seeded += bool(found)
        print(f"{t.id}  {str(t.meta.get('title', ''))[:50]}  " + (", ".join(f"`{c}`" for c in found) if found else "(placeholder)"))
    print(f"\n{len(todo)} ticket(s) without a Commands section would get one ({seeded} seeded from How to validate, {len(todo) - seeded} with the placeholder); {len(s.tickets()) - len(todo)} left alone.")
    if not args.apply:
        print("Dry run: nothing written. Add --apply to add the section (only `## Commands` is added; updated dates are not touched).")
        return
    wrote = sum(commands_migrate.apply_file(t, found) for t, found in todo)
    print(f"Added the Commands section to {wrote} ticket(s).")


def cmd_migrate_checks(args):
    s = _store(args)
    tickets = s.tickets()
    pairs = check_migrate.plan(tickets)
    for t, lines in pairs:
        for section, old, new in lines:
            print(f"{t.id}  {section}")
            print(f"  - {old}")
            for ln in new:
                print(f"  + {ln}")
    skipped = [t.id for t in tickets if check_migrate.has_crlf(t)]
    total = sum(len(lines) for _, lines in pairs)
    print()
    print(f"{total} inline `not accepted` lines in {len(pairs)} tickets would be rewritten as a quoted reason; {len(tickets) - len(pairs)} tickets left alone.")
    if skipped:
        print(f"{len(skipped)} tickets with CRLF line endings are skipped: {', '.join(skipped[:8])}")
    if not args.apply:
        print("Dry run: nothing written. Add --apply to rewrite them (only those lines change; updated dates are not touched).")
        return
    wrote = sum(check_migrate.rewrite_file(t) for t, _ in pairs)
    print(f"Rewrote {wrote} lines in {len(pairs)} tickets.")


def cmd_home(args):
    print(_store(args).home)


# ---- parser ----------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="desk", description="Tickets as markdown files. Done means proof.")
    p.add_argument("--home", help="the ticket store (else DESK_HOME, else the nearest desk.toml upward)")
    p.add_argument("--version", action=_Version, nargs=0, help="print the version and build commit")
    sub = p.add_subparsers(dest="cmd", required=True)

    def actor(sp):
        sp.add_argument("--as", dest="as_", help="who is acting (default: main)")

    sp = sub.add_parser("init", help="create a store here (desk.toml, tickets/, projects/, intake/)")
    sp.add_argument("path", nargs="?")
    sp.add_argument("--owner", default="james")
    sp.set_defaults(fn=cmd_init)

    sp = sub.add_parser("doctor", help="installed version and commit vs the repo, tool and store paths, UI port; exit 1 when stale")
    sp.add_argument("--repo", help="the Desk repo (else DESK_REPO, else <store>/desk)")
    sp.set_defaults(fn=cmd_doctor)

    sp = sub.add_parser("bench", help="build a throwaway store of N synthetic tickets and print p50/p95 per data path against the budgets")
    sp.add_argument("--tickets", type=int, default=1000)
    sp.add_argument("--runs", type=int, default=7, help="timed requests per page path")
    sp.add_argument("--out", help="write the results as JSON here")
    sp.add_argument("--search-rows", type=int, default=0, metavar="N", help="instead: time search over N synthetic rows in memory (50000 for the budget case)")
    sp.add_argument("--save-baseline", action="store_true", help="record these numbers in desk/bench/baseline.json for this ticket count")
    sp.set_defaults(fn=cmd_bench)

    sp = sub.add_parser("sync", help="with [git] sync = true: pull, commit what is pending now (skipping the idle wait), push")
    sp.set_defaults(fn=cmd_sync)

    sp = sub.add_parser("upgrade", help="reinstall the tool from the repo (refuses while the UI is running)")
    sp.add_argument("--repo", help="the Desk repo (else DESK_REPO, else <store>/desk)")
    sp.set_defaults(fn=cmd_upgrade)

    sp = sub.add_parser("dispatch", help="the ready tickets assigned to Patch with nothing blocking them and no worker, grouped by repository, oldest first: id, size, effort, dependencies")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_dispatch)

    sp = sub.add_parser("sprint", help="sprints (sprints.yaml): `sprint new 'sprint 6 2026' --start --end --owner --project`, `sprint list`, `sprint show NAME` (tickets in score order and burn-down numbers); put a ticket in one with `drew move ID --sprint NAME`")
    sp.add_argument("action", choices=["new", "list", "show"])
    sp.add_argument("name", nargs="?")
    sp.add_argument("--start", metavar="YYYY-MM-DD")
    sp.add_argument("--end", metavar="YYYY-MM-DD")
    sp.add_argument("--owner")
    sp.add_argument("--project", help="the project that runs it (the same name may exist in two projects); leave out for a sprint that spans projects")
    sp.add_argument("--note", help="the scope note")
    sp.set_defaults(fn=cmd_sprint)

    sp = sub.add_parser("whoami", help="how this command line would be recorded on a write: actor, kind (person, thread, session, subagent), instance, source and how it was established; a name that is only claimed is shown as claimed. Environment: DESK_ACTOR, DESK_ACTOR_KIND, DESK_INSTANCE, DESK_SUBAGENT_ID, DESK_PARENT_SESSION")
    sp.add_argument("--json", action="store_true")
    actor(sp)
    sp.set_defaults(fn=cmd_whoami)

    sp = sub.add_parser("repeats", help="repeated asks (the owner asked for the same thing twice on a ticket within 7 days) and stale hand-offs (blocked_on names the owner, unchanged across two sweeps); --apply makes the repeated-ask tickets p0 with the tag emergency")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(fn=cmd_repeats)

    sp = sub.add_parser("refine", help="a tracked refinement: adds `- [ ] <date> refinement: LINE` to Acceptance and a note to Comments; --review-by DATE --reason TEXT moves review_by and writes the slip down. A change of scope is not a refinement: use `split`")
    sp.add_argument("id")
    sp.add_argument("line")
    sp.add_argument("--review-by", metavar="YYYY-MM-DD")
    sp.add_argument("--reason", help="why the refinement costs time (required with --review-by)")
    actor(sp)
    sp.set_defaults(fn=cmd_refine)

    sp = sub.add_parser("split", help="a change of scope becomes a new ticket with details and a timeline: it depends_on ID, ID lists it under blocks and asks for the completion decision (done as scoped, or closed in favour)")
    sp.add_argument("id")
    sp.add_argument("--title", required=True)
    sp.add_argument("--size", default="s", choices=["xs", "s", "m"])
    sp.add_argument("--due", metavar="YYYY-MM-DD", help="the new ticket's review_by")
    sp.add_argument("--details", help="what the new ticket is for")
    sp.add_argument("--outcome")
    sp.add_argument("--acceptance", action="append", help="an Acceptance line (repeat for more)")
    actor(sp)
    sp.set_defaults(fn=cmd_split)

    sp = sub.add_parser("stop", help="stop the Desk UI: end whatever listens on the UI port (DESK_UI_PORT, default 8765) by PID and say which; exit 0 when nothing listens")
    sp.add_argument("--port", type=int, help="the port (default: the UI port)")
    sp.set_defaults(fn=cmd_stop)

    sp = sub.add_parser("refresh", help="stop the UI, install Desk from the repo (or --ref, a branch, tag or commit of it), start drew-ui again and print the version")
    sp.add_argument("--repo", help="the Desk repo (else DESK_REPO, else <store>/desk)")
    sp.add_argument("--ref", help="install this git ref of the repo instead of its working folder")
    sp.set_defaults(fn=cmd_refresh)

    sp = sub.add_parser("migrate", help="one-off bulk edits; `migrate track` assigns tracks from tracks/rules.yaml, `migrate type` retires the legacy slice and chore types, `migrate acceptance` strips boilerplate Acceptance lines that restate the Definition of Done, `migrate checks` rewrites inline `not accepted` lines as the quoted-reason block, `migrate tests` adds the Tests section to every ticket without one, `migrate commands` adds the Commands section (seeded from the How to validate lines that look like commands), `migrate assignee` fills blank assignees from assign.yaml (never overrides one), `migrate events` turns every ticket's Comments lines and the git history of its file into events in its activity log (events/<ID>.jsonl), once, `migrate hierarchy` writes company: into each project profile, the [hierarchy] block into desk.toml and the track vocabulary into each ticket (old value kept as legacy_track:); --undo takes it back out byte for byte")
    sp.add_argument("what", choices=["track", "type", "acceptance", "checks", "tests", "assignee", "commands", "events", "hierarchy"])
    sp.add_argument("--include-closed", action="store_true", help="assignee: also fill closed tickets (default: open tickets only)")
    mode = sp.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print ticket -> track and counts; write nothing")
    mode.add_argument("--apply", action="store_true", help="write track: into each matched ticket's frontmatter")
    sp.add_argument("--undo", action="store_true", help="hierarchy only: remove the company: lines and the desk.toml block, and restore track: from legacy_track: (with --dry-run: only count)")
    sp.add_argument("--rules", help="rules file (default tracks/rules.yaml in the store)")
    sp.add_argument("--companies", help="hierarchy: the project -> company map (default tracks/companies.yaml in the store)")
    sp.add_argument("--vocabulary", help="hierarchy: the track vocabulary and its map from old values (default tracks/vocabulary.yaml in the store)")
    sp.set_defaults(fn=cmd_migrate)

    sp = sub.add_parser("history", help="a ticket's timeline from its activity log: the latest change, then every event newest first (who, kind, where from, what, when); --by groups by a dimension, --filter key=value narrows (repeat it; a,b means either)")
    sp.add_argument("id")
    sp.add_argument("--by", choices=["actor", "kind", "source", "project", "company", "track", "sprint", "field"], help="group the events by this dimension, with counts")
    sp.add_argument("--filter", action="append", metavar="KEY=VALUE", help="keep only events where KEY (actor, kind, source, project, company, track, sprint, field) is VALUE")
    sp.add_argument("--json", action="store_true", help="the events shown, as JSON")
    sp.set_defaults(fn=cmd_history)

    sp = sub.add_parser("home", help="print the resolved store path")
    sp.set_defaults(fn=cmd_home)

    sp = sub.add_parser("new", help="create a ticket from the template (next id for the project)")
    sp.add_argument("title")
    sp.add_argument("--project", required=True)
    sp.add_argument("--type", default="feature", choices=TYPES)
    sp.add_argument("--priority", default="p2", choices=PRIORITIES)
    sp.add_argument("--size", default="s", choices=SIZES)
    sp.add_argument("--owner", default="main")
    sp.add_argument("--dod", help="comma-separated Definition of Done modules (default core)")
    sp.add_argument("--source", help="path#section into intake/")
    sp.add_argument("--assignee", help="who will execute it: main, james, codex, a project key, or any name")
    sp.add_argument("--deadline", metavar="YYYY-MM-DD", help="a date the ticket must not slip past (see `drew deadline`)")
    sp.add_argument("--deadline-kind", default="hard", choices=deadlines.KINDS, help="with --deadline: hard (default), soft or sprint")
    actor(sp)
    sp.set_defaults(fn=cmd_new)

    sp = sub.add_parser("sub", help="a subticket: a piece of a main ticket (an icon from Chaz, an input from James), tracked and assigned like any ticket but kept off the boards; the main ticket's card shows its progress")
    sp.add_argument("parent", help="the main ticket's id, for example DESK-0183")
    sp.add_argument("title")
    sp.add_argument("--assignee", help="who does it: chaz, james, main, a project key, or any name (blank: assign.yaml may fill it)")
    sp.add_argument("--project", help="the project it belongs to (default: the main ticket's project; any project is allowed)")
    sp.add_argument("--type", default="task", choices=TYPES, help="default task")
    sp.add_argument("--priority", choices=PRIORITIES, help="default: the main ticket's priority")
    sp.add_argument("--size", default="s", choices=SIZES)
    sp.add_argument("--owner", default="main")
    sp.add_argument("--dod", help="comma-separated Definition of Done modules (default core)")
    sp.add_argument("--source", help="path#section into intake/")
    actor(sp)
    sp.set_defaults(fn=cmd_sub)

    sp = sub.add_parser("list", help="table of tickets (open ones unless --all or --status)")
    sp.add_argument("--project")
    sp.add_argument("--status", choices=STATUSES)
    sp.add_argument("--owner")
    sp.add_argument("--assignee", help="only tickets assigned to this name")
    sp.add_argument("--sprint", help="only tickets in this sprint")
    sp.add_argument("--stale", action="store_true", help="only tickets untouched past the stale window")
    sp.add_argument("--due", type=int, metavar="DAYS", help="open tickets with a hard deadline within DAYS days or already missed, soonest first (what Today's Immediate Action Required row shows)")
    sp.add_argument("--sort", choices=["project", "score"], default="project", help="score: the priority score, highest first (DESK-0135); project: by project, then id")
    sp.add_argument("--all", action="store_true")
    sp.set_defaults(fn=cmd_list)

    sp = sub.add_parser("explain", help="why a ticket scores what it does: the score, every signal in plain words with its points, and which level (default, store, track rules, project, private file) set each weight")
    sp.add_argument("id")
    sp.set_defaults(fn=cmd_explain)

    sp = sub.add_parser("deadline", help="set a ticket's deadline: `drew deadline ID 2026-11-12 --hard` (or --soft, --sprint); `drew deadline ID --clear` removes it. Only a hard deadline reaches Today's Immediate Action Required row")
    sp.add_argument("id")
    sp.add_argument("date", nargs="?", metavar="YYYY-MM-DD")
    kind = sp.add_mutually_exclusive_group()
    kind.add_argument("--hard", action="store_const", const="hard", dest="kind", help="a date that cannot move")
    kind.add_argument("--soft", action="store_const", const="soft", dest="kind", help="a date to aim for")
    kind.add_argument("--sprint", action="store_const", const="sprint", dest="kind", help="the sprint's end")
    sp.add_argument("--clear", action="store_true", help="remove the deadline")
    actor(sp)
    sp.set_defaults(fn=cmd_deadline)

    sp = sub.add_parser("next", help="the highest-scoring ready ticket whose dependencies are closed (the priority score, DESK-0135), printed in full")
    sp.add_argument("--project")
    sp.add_argument("--json", action="store_true", help="SessionStart hook format")
    sp.add_argument("--after", metavar="ID", help="instead: the ticket after ID in the walk chosen in the UI (Previous and Next)")
    sp.add_argument("--previous", action="store_true", help="with --after: the one before")
    sp.add_argument("--scope", choices=list(nav.SCOPES), help="with --after: project, track, company or sprint (default: the saved choice)")
    sp.add_argument("--order", choices=list(nav.ORDERS), help="with --after: date, priority or review (default: the saved choice)")
    sp.set_defaults(fn=cmd_next)

    sp = sub.add_parser("show", help="print a ticket")
    sp.add_argument("id")
    sp.add_argument("--raw", action="store_true", help="show the profanity unfiltered (it is filtered by default; see [style] profanity_filter)")
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
    sp.add_argument("--to", choices=STATUSES)
    sp.add_argument("--sprint", help="put the ticket in this sprint (it must exist in sprints.yaml); an empty name takes it out")
    sp.add_argument("--reason")
    actor(sp)
    sp.set_defaults(fn=cmd_move)

    sp = sub.add_parser("assign", help="hand a ticket to someone: sets assignee and assigned_at, logs it in Comments")
    sp.add_argument("id")
    sp.add_argument("--to", required=True, help="who: main, james, codex, a project key, or any name")
    sp.add_argument("--note", help="what you are handing over, in a line")
    sp.add_argument("--handoff", help="the hand-off record: a URL or a Drive doc id")
    actor(sp)
    sp.set_defaults(fn=cmd_assign)

    sp = sub.add_parser("comment", help="append a dated comment")
    sp.add_argument("id")
    sp.add_argument("text")
    sp.add_argument("--kind", default="Question", help="Question (default), Suggestion, Concern or FYI")
    actor(sp)
    sp.set_defaults(fn=cmd_comment)

    sp = sub.add_parser("comments", help="list comments across the store (James's notes are work): --author, --since, --unanswered, --json")
    sp.add_argument("--author", help="only comments by this person (case does not matter)")
    sp.add_argument("--since", help="YYYY-MM-DD, YYYY-MM-DD HH:MM, or last-sweep (from .desk/state; everything when none is marked)")
    sp.add_argument("--unanswered", action="store_true", help="no later line by patch in the ticket, and the ticket has not changed on a later day")
    sp.add_argument("--answered-by", action="append", help="who counts as answering (default patch; repeat for more)")
    sp.add_argument("--json", action="store_true", help="a JSON list: ticket, title, when, author, kind, text, answered")
    sp.add_argument("--mark-sweep", action="store_true", help="after listing, record now as the last sweep (alone: just record it)")
    sp.set_defaults(fn=cmd_comments)

    sp = sub.add_parser("needs-me", help="the tickets with an ask for you, and those someone else changed since you last acted (reading does not count)")
    sp.add_argument("--seen", nargs="+", metavar="ID", help="Mark seen: clear these changes without acting (asks stay)")
    sp.add_argument("--seen-all", action="store_true", help="Mark all changes seen")
    sp.add_argument("--json", action="store_true")
    actor(sp)
    sp.set_defaults(fn=cmd_needs_me)

    sp = sub.add_parser("validate", help="schema, lifecycle rules, dangling depends_on (exit 1 on errors)")
    sp.set_defaults(fn=cmd_validate)

    sp = sub.add_parser("brand-check", help="projects with no brand block or a missing mark file; --create opens a ticket for each")
    sp.add_argument("--create", action="store_true", help="open one chore per missing project in project `brand`")
    sp.add_argument("--assignee", default="chaz")
    sp.set_defaults(fn=cmd_brand_check)

    sp = sub.add_parser("search", help="find tickets by part of an id or title, typos allowed, with filters (project: status: type: tag: ask: owner: assignee: track: sprint: priority: is:stale|open|closed before: after:); grouped by project")
    sp.add_argument("query", nargs="+")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--limit", type=int, default=12, help="most tickets shown per project (default 12)")
    sp.set_defaults(fn=cmd_search)

    sp = sub.add_parser("outbox", help="the entry for a coworker thread's team folder: the tickets to look at since the last entry (assigned, changed by Patch or James, new and naming them), one line each, with the Tickets: header; the cut-off per actor is kept in .desk/state")
    sp.add_argument("--for", dest="for_", metavar="ACTOR", help="chaz, em, lily, bri, joe, bigd (or any key or name in people.yaml)")
    sp.add_argument("--all", action="store_true", help="one entry for every coworker thread that has changes")
    sp.add_argument("--since", help="last-entry (default; a week back when there is none), YYYY-MM-DD or YYYY-MM-DD HH:MM")
    sp.add_argument("--json", action="store_true", help="for the poster: actor, cut-off, the tickets with changes and ask, and the text")
    sp.add_argument("--posted", action="store_true", help="after printing, move the cut-off to now (use it when the entry is posted)")
    sp.set_defaults(fn=cmd_outbox)

    sp = sub.add_parser("assign-rules", help="show the auto-assignment rules (assign.yaml, or the built-in table); `assign-rules init` writes assign.yaml")
    sp.add_argument("action", nargs="?", choices=["list", "init"], default="list")
    sp.add_argument("--force", action="store_true", help="init: replace an existing assign.yaml")
    sp.set_defaults(fn=cmd_assign_rules)

    sp = sub.add_parser("people", help="list the people owners and assignees can be (people.yaml, the built-in team when there is none, plus the private file); `people init` writes people.yaml")
    sp.add_argument("action", nargs="?", choices=["list", "init"], default="list")
    sp.add_argument("--force", action="store_true", help="init: replace an existing people.yaml")
    sp.set_defaults(fn=cmd_people)

    sp = sub.add_parser("index", help="rebuild .desk/index.sqlite from the files")
    sp.set_defaults(fn=cmd_index)

    sp = sub.add_parser("board", help="the board in the terminal")
    sp.add_argument("--project")
    sp.add_argument("--all", action="store_true")
    sp.set_defaults(fn=cmd_board)

    sp = sub.add_parser("intake", help="copy a hand-off (folder or zip) into intake/ and open an intake ticket; --check validates a SLICES file instead")
    sp.add_argument("path")
    sp.add_argument("--topic")
    sp.add_argument("--project")
    sp.add_argument("--check", action="store_true", help="only validate the SLICES file at PATH: columns, project keys, type, size, ask, Depends on ids, Acceptance and How to validate; writes <file>.check.md beside it; exit 1 on errors")
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

    sp = sub.add_parser("review", help="the weekly ticket review: a ticket whose checkboxes are everything waiting on the owner")
    rsub = sp.add_subparsers(dest="review_cmd", required=True)
    rp = rsub.add_parser("new", help="create this week's ticket review (one per ISO week)")
    rp.add_argument("--week", metavar="YYYY-MM-DD", help="any day in the week (default: today)")
    rp.add_argument("--project", default="management")
    rp.add_argument("--owner", default="james")
    rp.set_defaults(fn=cmd_review_new)
    rp = rsub.add_parser("sync", help="regenerate the checkbox list and KPI block in place")
    rp.add_argument("id", nargs="?", help="default: this week's review ticket")
    rp.set_defaults(fn=cmd_review_sync)
    return p


READ_ONLY = {"list", "show", "search", "validate", "board", "stats", "doctor", "bench", "home", "index", "people", "assign-rules", "dispatch", "next",
             "outbox", "whoami", "stop", "refresh", "init", "upgrade", "sync", "brand-check", "history"}


def _identify(args):
    """DESK-0144: work out who is acting and keep it for the rest of the command, so every write (Comments lines, the write log) records
    the actor, its kind, instance, source and how sure we are. `--as` is kept for agents and is recorded as claimed."""
    from . import identity

    if args.cmd in READ_ONLY and args.cmd != "whoami":
        return None
    try:
        s = Store(find_home(args.home))
    except HomeNotFound:
        return None
    return identity.CURRENT.set(identity.resolve_cli(s, getattr(args, "as_", None)))


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252; tickets are UTF-8
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    token = _identify(args)
    try:
        args.fn(args)
    finally:
        if token is not None:
            from . import identity

            identity.CURRENT.reset(token)


if __name__ == "__main__":
    main()
