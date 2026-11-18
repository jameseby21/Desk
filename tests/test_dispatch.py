"""DESK-0166: validate warns on undispatched work. Protects: a ready, unblocked ticket of Patch's with no worker for over an hour is
named by `drew validate` and listed by `drew dispatch` grouped by repository, so it is dispatched before James notices."""

import json
import os
import time

from desk import dispatch, ops
from desk.cli import main
from desk.rules import validate_store


def _ready(store, title, project="jre", age_h=2.0, **meta):
    t = ops.new(store, title, project)
    t.meta.update({"status": "ready", "assignee": "main", **meta})
    t.write()
    old = time.time() - age_h * 3600
    os.utime(t.path, (old, old))  # the file has been still for age_h hours
    return store.get(t.id)


def _warns(store):
    return [w for w in validate_store(store)[1] if "undispatched" in w]


def test_warns_when_ready_unblocked_patchs_and_still_for_over_an_hour(store):
    t = _ready(store, "Build the thing", effort_hours=[2, 3])
    w = _warns(store)
    assert len(w) == 1 and w[0].startswith(f"{t.id}: undispatched for 2 h (jre)") and "drew dispatch" in w[0]


def test_not_a_warning_when_recent_or_blocked_or_waiting_or_someone_elses(store):
    _ready(store, "Fresh", age_h=0.2)
    _ready(store, "A worker has it", blocked_on="dispatched 2026-11-08 to the Desk worker")
    _ready(store, "Waiting for a reason", blocked_on="waits for James's answer")
    _ready(store, "Chaz has it", assignee="chaz")
    _ready(store, "Claimed", status="claimed")
    _ready(store, "Decision for James", assignee="james", type="decision")
    assert _warns(store) == []


def test_dependencies_must_be_in_review_or_done(store):
    dep = _ready(store, "Dep", age_h=0.1)
    waits = _ready(store, "Waits on dep", depends_on=[dep.id])
    assert _warns(store) == []  # the dependency is only ready, so this one cannot start
    dep.meta["status"] = "review"
    dep.write()
    assert [w.split(":")[0] for w in _warns(store)] == [waits.id]
    dep.meta["status"] = "done"
    dep.write()
    assert [w.split(":")[0] for w in _warns(store)] == [waits.id]


def test_setting_blocked_on_a_worker_name_makes_the_warning_go_away(store):
    t = _ready(store, "Name the worker")
    assert len(_warns(store)) == 1
    t.meta["blocked_on"] = "dispatched to the jre worker"
    t.write()
    old = time.time() - 7200
    os.utime(t.path, (old, old))
    assert _warns(store) == []


def test_dispatch_groups_by_repo_oldest_first_with_the_brief_fields(store, capsys):
    (store.projects_dir / "jre.md").write_text("---\nrepo: jeby Real Estate\n---\n# jRE\n", encoding="utf-8")
    a = _ready(store, "Older in jre", "jre", age_h=5, size="m", effort_hours=[3, 4])
    b = _ready(store, "Newer in jre", "jre", age_h=2)
    c = _ready(store, "In desk", "desk", age_h=3)
    for t, h in ((b, 2), (c, 3)):
        os.utime(t.path, (time.time() - h * 3600,) * 2)
    main(["--home", str(store.home), "dispatch"])
    out = capsys.readouterr().out
    assert out.index("jeby Real Estate: 2 ticket(s)") < out.index("desk: 1 ticket(s)")  # the repo with the longest wait first
    assert out.index(a.id) < out.index(b.id) and "3-4 h" in out and "deps: none" in out
    main(["--home", str(store.home), "dispatch", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert [g["repo"] for g in data] == ["jeby Real Estate", "desk"]
    first = data[0]["tickets"][0]
    assert first["id"] == a.id and first["size"] == "m" and first["effort"] == "3-4 h" and first["deps"] == [] and first["idle_hours"] >= 4.9


def test_nothing_waiting_says_so(store, capsys):
    main(["--home", str(store.home), "dispatch"])
    assert "Nothing is waiting for a worker" in capsys.readouterr().out
    assert dispatch.count(store) == 0
