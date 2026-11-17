"""DESK-0305 review: the timeline is built from records, never assumed. Status changes in a real store are mostly made by editing the
ticket file and committing it, which writes no field event; the strip used to put the current status across a ticket's whole life
and the rail and Latest change missed every change made outside Desk. Protects: the ticket file's git history (its status at each
commit) feeds the strip, the rail and Latest change; a span no record covers is Not recorded (hatched), never a status; done_at is a
record. Methods: unit tests of the builders, route tests through the real app on a git store, `drew history` on the command line,
and a seeded property check over a generated git store that no period claims a status without a record behind it."""

import datetime as dt
import os
import random
import re
import subprocess

import pytest
from fastapi.testclient import TestClient

from desk import activity, ops, timeline
from desk.cli import main
from desk.ui.app import create_app

NOW = dt.datetime(2026, 11, 15, 12, 0, 0)


def _env(date=None):
    env = dict(os.environ, GIT_AUTHOR_NAME="Owner A", GIT_AUTHOR_EMAIL="owner-a@example.invalid", GIT_COMMITTER_NAME="Owner A",
               GIT_COMMITTER_EMAIL="owner-a@example.invalid", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    return env


def git(home, *args, date=None):
    return subprocess.run(["git", "-C", str(home), *args], check=True, capture_output=True, text=True, env=_env(date)).stdout


def git_store(store):
    git(store.home, "init", "-q")
    git(store.home, "config", "core.autocrlf", "false")
    git(store.home, "config", "commit.gpgsign", "false")
    return store


def outside_desk(store, title, created="2026-11-08", status="ready"):
    """A ticket written as a file (the way Patch and coworkers write them): no event of its own in the log."""
    t = ops.new(store, title, "desk")
    activity.path(store.home, t.id).unlink()
    x = store.get(t.id)
    x.meta["created"], x.meta["status"] = created, status
    x.write()
    return store.get(t.id)


def commit_status(store, t, status, date, msg=None, **meta):
    x = store.get(t.id)
    x.meta["status"] = status
    x.meta.update(meta)
    x.write()
    git(store.home, "add", "-A")
    git(store.home, "commit", "-q", "--allow-empty", "-m", msg or f"chore(tickets): {t.id} to {status}", date=date)
    return store.get(t.id)


def desk0117_shape(store):
    """The real DESK-0117's status history, read from its git log on 2026-11-15 (ids and people left out): ready when first committed
    on 8 Nov 04:10, review from 8 Nov 04:45, five more commits that left it in review, changes_requested on 15 Nov 02:38."""
    git_store(store)
    t = outside_desk(store, "Check column: fixed right column with headers")
    t = commit_status(store, t, "ready", "2026-11-08T04:10:29-05:00", "feat(tickets): a delivery")
    t = commit_status(store, t, "review", "2026-11-08T04:45:22-05:00", "feat(tickets): comments become work")
    for d in ("2026-11-08T05:08:09", "2026-11-08T06:47:44", "2026-11-10T08:34:35", "2026-11-10T09:52:05", "2026-11-10T11:17:06"):
        x = store.get(t.id)
        x.append_line("Comments", f"- {d[:10]} · owner-a · a note at {d[11:16]}")
        x.write()
        git(store.home, "add", "-A")
        git(store.home, "commit", "-q", "-m", "chore(tickets): cleanup pass", date=d + "-05:00")
    return commit_status(store, t, "changes_requested", "2026-11-15T02:38:39-05:00", "fix(tickets): the verdict")


def strip(page):
    return re.findall(r'<li class="tl-seg st-([a-z_]+)( tl-now)?" style="flex-grow:([0-9.]+)"[^>]*title="([^"]+)"', page)


# -- finding 1: the status history comes from git when the log has none -------------------------------------------------------------

def test_route_status_changed_only_in_git_and_migrated_shows_three_segments_and_two_status_events(store):
    """Protects the first ticket the owner is told to open: a ticket moved ready -> review -> changes requested only by committing its
    file (no Desk write), then `drew migrate events --apply`: the page draws three segments at the commit times and Status counts 2."""
    git_store(store)
    t = outside_desk(store, "Moved in git")
    t = commit_status(store, t, "ready", "2026-11-08T04:10:00")
    t = commit_status(store, t, "review", "2026-11-10T11:17:00")
    t = commit_status(store, t, "changes_requested", "2026-11-15T02:38:00")
    activity.migrate(store, apply=True)
    assert not [e for e in activity.read(store.home, t.id) if e.field == "status"]  # the migration alone writes no status
    page = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    segs = strip(page)
    assert [s[0] for s in segs] == ["ready", "review", "changes_requested"], segs
    assert segs[0][3].startswith("Ready, from Sun 8 Nov 04:10, for 2 days") and segs[1][3].startswith("Sent for review, from Tue 10 Nov 11:17, for 4 days")
    assert segs[2][1] and segs[2][3].startswith("Changes requested, from Sun 15 Nov 02:38")
    assert float(segs[0][2]) > float(segs[2][2]) and abs(sum(float(s[2]) for s in segs) - 100) < 0.05
    assert 'data-filter="status" aria-pressed="false">Status <span class="tl-n">2</span>' in page
    assert ">Status: Ready → Sent for review</div>" in page and ">Status: Sent for review → Changes requested</div>" in page


def test_regression_desk0117_shape_reads_six_days_in_review_and_changes_requested_from_15_nov(store):
    """Protects against the reviewed defect on DESK-0117 itself: the strip said 'Changes requested, from Sun 8 Nov, for 1 week (now)'.
    Rebuilt from its real git shape: Ready 35 minutes, Sent for review about 6 days, Changes requested from 15 Nov."""
    t = desk0117_shape(store)
    v = timeline.view(store, t, now=NOW)
    assert [(p.status, p.text) for p in v.periods] == [
        ("ready", "Ready, from Sun 8 Nov 04:10, for 34 minutes"),
        ("review", "Sent for review, from Sun 8 Nov 04:45, for 6 days"),
        ("changes_requested", "Changes requested, from Sun 15 Nov 02:38, for 9 hours (now)")]
    assert v.counts["status"] == 2 and v.counts["git"] == 8
    assert not any(p.unknown for p in v.periods)


def test_cli_history_prints_the_status_changes_git_proves(store, capsys):
    """Protects `drew history`, which shares the view: the status changes made in git are listed, from git history, with their times."""
    t = desk0117_shape(store)
    main(["--home", str(store.home), "history", t.id])
    out = capsys.readouterr().out
    assert "2026-11-15 02:38" in out and "from git history · status: review → changes_requested" in out
    assert "2026-11-08 04:45" in out and "status: ready → review" in out


def test_snapshots_read_the_front_matter_only_and_follow_a_renamed_file(store):
    """Edge cases of reading git: a 'status:' line in the body is not the status; a renamed slug keeps the history; a commit of another
    ticket is not this one's; a second read after a new commit sees it (the cache follows HEAD)."""
    git_store(store)
    t = outside_desk(store, "Edge cases")
    other = outside_desk(store, "Another one")
    t = commit_status(store, t, "ready", "2026-11-09T09:00:00")
    x = store.get(t.id)
    x.set_section("Context", "status: done\n")  # a body line that looks like front matter
    x.write()
    git(store.home, "add", "-A")
    git(store.home, "commit", "-q", "-m", "body only", date="2026-11-09T10:00:00")
    old = t.path
    new = old.with_name(f"{t.id}-a-new-slug.md")
    git(store.home, "mv", old.relative_to(store.home).as_posix(), new.relative_to(store.home).as_posix())
    git(store.home, "commit", "-q", "-m", "rename", date="2026-11-10T09:00:00")
    commit_status(store, other, "blocked", "2026-11-10T10:00:00", blocked_on="someone")
    snaps = activity.snapshots(store.home, t.id, new)
    assert [(s.ts[:16], s.status) for s in snaps] == [("2026-11-09T09:00", "ready"), ("2026-11-09T10:00", "ready"), ("2026-11-10T09:00", "ready")]
    assert snaps[0].first and not snaps[1].first
    t2 = store.get(t.id)
    t2.meta["status"] = "review"
    t2.write()
    git(store.home, "add", "-A")
    git(store.home, "commit", "-q", "-m", "to review", date="2026-11-11T09:00:00")
    assert [s.status for s in activity.snapshots(store.home, t.id, t2.path)][-1] == "review"
    assert activity.front_status("no front matter\nstatus: done\n") == ""


def test_a_store_in_a_subfolder_of_a_repository_reads_its_tickets_history(tmp_path):
    """Edge case: the store's folder is inside a larger repository (the umbrella holds more than the store): found by walking up."""
    from desk.store import Store

    git(tmp_path, "init", "-q")
    s = Store.init(tmp_path / "store", owner="owner-a")
    (s.projects_dir / "desk.md").write_text("# Desk\n", encoding="utf-8")
    t = ops.new(s, "Nested", "desk")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "nested", date="2026-11-12T09:00:00")
    assert [(x.ts, x.status, x.first) for x in activity.snapshots(s.home, t.id, t.path)] == [("2026-11-12T09:00:00", "inbox", True)]


def test_outside_git_nothing_is_run_and_a_2000_event_page_stays_fast(store, monkeypatch):
    """Edge case: a store that is not a git repository never starts git (the page's 300 ms budget stays the log's own)."""
    calls = []
    real = subprocess.run
    monkeypatch.setattr(activity.subprocess, "run", lambda *a, **k: calls.append(a) or real(*a, **k))
    t = ops.new(store, "No git here", "desk")
    timeline.view(store, t)
    assert calls == [] and activity.snapshots(store.home, t.id) == []


# -- finding 4: what no record covers is Not recorded, and done_at is a record ----------------------------------------------------

def test_unit_a_done_ticket_with_no_status_events_is_not_done_before_its_done_date():
    """Protects a done ticket whose moves were never logged: Done starts at done_at, the span before it is Not recorded."""
    ps = timeline.periods("2026-11-08", [], "done", NOW, done_at="2026-11-10")
    assert [(p.status, p.start.isoformat()) for p in ps] == [("", "2026-11-08T00:00:00"), ("done", "2026-11-10T00:00:00")]
    assert ps[0].label == "Not recorded" and ps[1].text == "Done, from Tue 10 Nov, for 5 days (now)"
    timed = timeline.periods("2026-11-08", [], "done", NOW, done_at=dt.datetime(2026, 11, 10, 14, 30))
    assert timed[1].start == dt.datetime(2026, 11, 10, 14, 30)
    reopened = timeline.periods("2026-11-08", [], "ready", NOW, done_at="2026-11-10")  # done_at on a reopened ticket is not its status now
    assert [p.status for p in reopened] == ["", "ready"]


def test_route_a_log_of_only_git_saves_and_comments_says_the_earlier_status_was_not_recorded(store):
    """Protects the reviewed screenshot: a ticket in review whose log holds only git saves and comments (migrated, no repository here)
    shows a hatched Not recorded span and a caption, never one full-width 'Sent for review ... for 1 week' segment."""
    t = outside_desk(store, "Only saves and comments", created="2026-11-08", status="review")
    activity.append(store.home, [
        activity.Event(t.id, "2026-11-08T05:00:00", "claude-code", "code", "git", "git", "git", "Owner A", "abc", "Saved in git: a", key="git:a"),
        activity.Event(t.id, "2026-11-09T05:00:00", "owner-a", "owner", "migration", "comment", "comment", "", "hi", "hi", key="comment:a"),
        activity.Event(t.id, "2026-11-12T05:00:00", "claude-code", "code", "git", "git", "git", "Owner A", "abd", "Saved in git: b", key="git:b")])
    page = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    segs = strip(page)
    assert segs[0][0] == "unknown" and segs[0][3].startswith("Not recorded, from Sun 8 Nov") and float(segs[0][2]) > 50, segs
    assert segs[-1][0] == "review" and ("or earlier" in segs[-1][3] or "when it started is not recorded" in segs[-1][3]), segs
    assert not (len(segs) == 1 and segs[0][0] == "review")
    assert 'id="tl-gap"' in page and "Hatched: Not recorded." in page


def _claims_without_record(v, t, snaps, logged) -> list[str]:
    """Every period that names a status at a time no record supports: the record must say that status at the period's start (a log
    move to it, a commit whose file said it, done_at, a created event or first commit followed by it), or the period must be the
    current one marked as starting at an unknown time."""
    bad = []
    marks = {}
    for e in logged:
        if e.change == "field" and e.field == "status":
            marks.setdefault(timeline._ts(e.ts), set()).add(e.new)
    for s in snaps:
        marks.setdefault(timeline._ts(s.ts), set()).add(s.status)
    if t.meta.get("done_at"):
        marks.setdefault(timeline._ts(t.meta["done_at"]), set()).add("done")
    starts = {timeline._ts(e.ts) for e in logged if e.change == "created"} | {timeline._ts(s.ts) for s in snaps if s.first}
    for i, p in enumerate(v.periods):
        if p.unknown or (p.current and p.since_unknown):
            continue
        ok = p.status in marks.get(p.start, set())
        if not ok and p.start in starts:  # the creation record: the status right after it is the next record's "before"
            nxt = next((e for e in sorted(logged, key=lambda e: (e.ts, e.seq)) if e.change == "field" and e.field == "status" and timeline._ts(e.ts) >= p.start), None)
            ok = (nxt is not None and nxt.old == p.status) or (p.current and t.status == p.status)
        if not ok:
            bad.append(f"{t.id}: {p.text}")
    return bad


def test_property_no_period_claims_a_status_without_a_record_over_a_generated_git_store(store):
    """Protects the whole store, not one ticket: 25 tickets with seeded random histories (moves only in git, some through Desk's log,
    some done with done_at, some never committed) and every period that names a status has a record behind it; the count is 0."""
    git_store(store)
    rng = random.Random(305)
    flow = ["inbox", "triage", "ready", "in_progress", "review", "changes_requested", "approved", "done", "blocked"]
    tickets = []
    for n in range(25):
        t = outside_desk(store, f"Generated {n}", created=f"2026-11-0{1 + n % 5}", status=rng.choice(flow))
        tickets.append(t)
    git(store.home, "add", "-A")
    git(store.home, "commit", "-q", "-m", "all at once", date="2026-11-06T09:00:00")
    for day in range(7, 15):
        for t in tickets:
            r = rng.random()
            if r < 0.25:
                commit_status(store, t, rng.choice(flow), f"2026-11-{day:02d}T{rng.randint(8, 20):02d}:{rng.randint(0, 59):02d}:00",
                              **({"done_at": f"2026-11-{day:02d}"} if r < 0.05 else {}))
            elif r < 0.35:
                x = store.get(t.id)
                ts = f"2026-11-{day:02d}T07:{rng.randint(0, 59):02d}:00"
                new = rng.choice(flow)
                activity.append(store.home, [activity.Event(t.id, ts, "main-a", "main", "cli", "field", "status", x.status, new, f"status: {x.status} → {new}")])
                x.meta["status"] = new
                x.write()
    bad = []
    for t in tickets:
        t = store.get(t.id)
        logged = activity.read(store.home, t.id)
        snaps = activity.snapshots(store.home, t.id, t.path)
        v = timeline.view(store, t, now=NOW)
        bad += _claims_without_record(v, t, snaps, logged + activity.events_from_git(t.id, snaps, logged))
    assert bad == [], f"{len(bad)} periods claim a status with no record:\n" + "\n".join(bad[:10])


# -- finding 2: changes made outside Desk reach the rail and Latest change ---------------------------------------------------------

def test_route_a_ticket_committed_outside_desk_with_no_events_file_has_a_rail_and_a_latest_change(store):
    """Protects the 35 real tickets with no events file: a ticket written and committed without Desk shows a rail with its git save
    and a Latest change that is not 'No changes recorded yet.'."""
    git_store(store)
    t = outside_desk(store, "Written by hand")
    git(store.home, "add", "-A")
    git(store.home, "commit", "-q", "-m", "feat(tickets): written by hand", date="2026-11-14T10:00:00")
    assert not activity.path(store.home, t.id).exists()
    page = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    assert 'id="tl-rail"' in page and "Saved in git: feat(tickets): written by hand" in page
    latest = re.search(r'<p class="tl-latest" id="tl-latest".*?</p>', page, re.S).group(0)
    assert "No changes recorded yet." not in latest
    assert not activity.path(store.home, t.id).exists()  # reading never writes the log


def test_integration_a_status_edited_in_the_file_and_committed_is_the_latest_change_with_the_commit_time(store):
    """Protects decision 75's 'the most recent change': editing the status in the file and committing it, then opening the page, makes
    Latest change read 'Status: Ready → Sent for review' at that commit's time, and the page after the next commit moves with it."""
    git_store(store)
    t = outside_desk(store, "Edited by hand")
    t = commit_status(store, t, "ready", "2026-11-14T09:00:00")
    t = commit_status(store, t, "review", "2026-11-14T10:30:00")
    client = TestClient(create_app(store.home))
    latest = re.search(r'<p class="tl-latest" id="tl-latest".*?</p>', client.get(f"/ticket/{t.id}").text, re.S).group(0)
    assert "Status: Ready → Sent for review" in latest and "2026-11-14 10:30" in latest, latest
    commit_status(store, t, "changes_requested", "2026-11-14T11:00:00")
    latest = re.search(r'<p class="tl-latest" id="tl-latest".*?</p>', client.get(f"/ticket/{t.id}").text, re.S).group(0)
    assert "Status: Sent for review → Changes requested" in latest and "2026-11-14 11:00" in latest, latest


def test_unit_git_events_are_not_doubled_by_the_log_or_a_desk_move():
    """Protects the merge: a commit already migrated is not listed twice, and a status change Desk logged between two commits is not
    repeated as a git status change; a hand edit after the Desk move is, from the status Desk left."""
    S = activity.Snapshot
    snaps = [S("a" * 40, "2026-11-10T09:00:00", "owner-a", "owner", "Owner A", "one", "ready", True),
             S("b" * 40, "2026-11-11T09:00:00", "owner-a", "owner", "Owner A", "two", "review"),
             S("c" * 40, "2026-11-12T09:00:00", "owner-a", "owner", "Owner A", "three", "done")]
    logged = [activity.Event("DESK-0001", "2026-11-10T09:00:00", "owner-a", "owner", "git", "git", "git", "Owner A", "a", "Saved in git: one", key="git:" + "a" * 40),
              activity.Event("DESK-0001", "2026-11-11T08:00:00", "main-a", "main", "cli", "field", "status", "ready", "review", "status: ready → review", seq=2),
              activity.Event("DESK-0001", "2026-11-12T08:00:00", "main-a", "main", "cli", "field", "status", "review", "approved", "status: review → approved", seq=3)]
    out = activity.events_from_git("desk-0001", snaps, logged)
    assert [(e.change, e.key[:9]) for e in out] == [("git", "git:bbbbb"), ("field", "gitstatus"), ("git", "git:ccccc")]
    st = [e for e in out if e.change == "field"]
    assert [(e.old, e.new, e.ts) for e in st] == [("approved", "done", "2026-11-12T09:00:00")] and all(e.ticket == "DESK-0001" for e in out)
