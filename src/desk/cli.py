"""The `desk` command line. Agents use this; the owner mostly uses the UI."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from . import accept_migrate, check_migrate, nav, ops, profanity, retype, review as rv, style as styles, tests_migrate, tracks as trk, version as ver
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
    from . import people

    s = _store(args)
    who = people.load(s.home)
    rows = []
    for t in _by_project(s.tickets()):
        stale = s.is_stale(t)
        if args.project and t.project != args.project:
            continue
        if args.status and t.status != args.status:
            continue
        if args.owner and str(t.meta.get("owner")) != args.owner:
            continue
        if args.assignee and ops.assignee_of(t).lower() != who.resolve(args.assignee).lower():
            continue
        if args.stale and not stale:
            continue
        if not args.all and not t.is_open and not args.status:
            continue
        rows.append([_id_label(s, t), t.project, t.meta.get("type", ""), t.status + (" (stale)" if stale else ""), t.meta.get("priority", ""),
                     t.meta.get("size", ""), str(t.meta.get("owner", "")), who.display(ops.assignee_of(t)), str(t.meta.get("title", ""))[:60]])
    if not rows:
        print("No tickets match.")
        return
    print(_table(rows, ["id", "project", "type", "status", "pri", "size", "owner", "assignee", "title"]))


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
    if s.formerly(t):  # a renamed project: say so before the file, which keeps its original id
        print(f"# {_id_label(s, t)} · project {t.project} · {s.project_title(t.project)}\n")
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
    print(f"{t.id} -> {t.status} ({screen(t.status)})")


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
    from . import comments as cm

    s = _store(args)
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
            print(f"{r.ticket_id}  {r.when.strftime('%Y-%m-%d %H:%M')}  {r.author}{kind}{mark}\n    {r.text}")
        print(f"{len(rows)} comment(s)")
    if args.mark_sweep:
        cm.mark_sweep(s.home)


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
        print(f"{res.id}: that comment is already on the ticket; nothing written")
        return
    s.sync_after(f"desk: comment {res.id}")
    print(f"{res.id} -> {res.status} ({screen(res.status)})")


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
    print("\nBy assignee")
    print(_table([[k, str(v["open"]), str(v["done_this_week"])] for k, v in st["by_assignee"].items()] or [["(none)", "0", "0"]], ["assignee", "open", "done this week"]))
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


def cmd_migrate(args):
    if args.what == "type":
        return cmd_migrate_type(args)
    if args.what == "acceptance":
        return cmd_migrate_acceptance(args)
    if args.what == "checks":
        return cmd_migrate_checks(args)
    if args.what == "tests":
        return cmd_migrate_tests(args)
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

    sp = sub.add_parser("migrate", help="one-off bulk edits; `migrate track` assigns tracks from tracks/rules.yaml, `migrate type` retires the legacy slice and chore types, `migrate acceptance` strips boilerplate Acceptance lines that restate the Definition of Done, `migrate checks` rewrites inline `not accepted` lines as the quoted-reason block, `migrate tests` adds the Tests section to every ticket without one")
    sp.add_argument("what", choices=["track", "type", "acceptance", "checks", "tests"])
    mode = sp.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="print ticket -> track and counts; write nothing")
    mode.add_argument("--apply", action="store_true", help="write track: into each matched ticket's frontmatter")
    sp.add_argument("--rules", help="rules file (default tracks/rules.yaml in the store)")
    sp.set_defaults(fn=cmd_migrate)

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
    sp.set_defaults(fn=cmd_new)

    sp = sub.add_parser("list", help="table of tickets (open ones unless --all or --status)")
    sp.add_argument("--project")
    sp.add_argument("--status", choices=STATUSES)
    sp.add_argument("--owner")
    sp.add_argument("--assignee", help="only tickets assigned to this name")
    sp.add_argument("--stale", action="store_true", help="only tickets untouched past the stale window")
    sp.add_argument("--all", action="store_true")
    sp.set_defaults(fn=cmd_list)

    sp = sub.add_parser("next", help="the highest-priority ready ticket, printed in full")
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
    sp.add_argument("--to", required=True, choices=STATUSES)
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
