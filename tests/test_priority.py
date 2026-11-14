"""DESK-0135, 0136, 0137, 0214: the priority score. One number 0 to 100 per open ticket from the record, James's rules of 2026-11-09
(health, no clients, people, repeatable process; DECISIONS 90), weights at every level with the nearest winning, rollups that rank by
the max, and every list in score order. Protects: a hand-set p0..p3 silently becoming the order again; a private people file being
read by a test; a store of 1,500 tickets taking seconds to score."""

import datetime as dt
import re
import time

import pytest
from fastapi.testclient import TestClient

from desk import bench, ops, priority
from desk.cli import main
from desk.ui.app import create_app

TODAY = dt.date(2026, 11, 10)


def _profile(store, key, title, **fields):
    fm = "".join(f"{k}: {v}\n" for k, v in fields.items())
    (store.projects_dir / f"{key}.md").write_text(f"---\nkey: {key}\nname: {title}\n{fm}---\n# {title}\n", encoding="utf-8")


def _mk(store, title, project="desk", **meta):
    t = ops.new(store, title, project)
    t.meta.update({"status": "ready", "assignee": "main", "created": "2026-11-08", "updated": "2026-11-10"}, **meta)
    t.write()
    return store.get(t.id)


def _scores(store, today=TODAY):
    return priority.Scores(store, store.tickets(), today=today)


def _toml(store, text):
    (store.home / "desk.toml").write_text(text, encoding="utf-8")
    return type(store)(store.home)  # re-read the config


@pytest.fixture
def shop(store):
    """Three projects with the profile lines the rules read: a paying client project, a free one, and an enabler."""
    _profile(store, "jre", "jRE", status="active", monetization="active", clients=2, enabler="false")
    _profile(store, "lab", "Lighting Lab", status="parked", monetization="none", clients=0, enabler="false")
    _profile(store, "desk", "Desk", status="active", monetization="planned", clients=0, enabler="true")
    return store


# -- the worked examples of docs/PRIORITY.md ------------------------------------------------------

def test_the_five_worked_examples_reproduce_the_documented_order_and_scores(shop):
    """The five tickets of PRIORITY.md section 5, with their facts as of 2026-11-10, land in the documented order with the documented scores."""
    s = shop
    _profile(s, "jeby-us", "jeby.us", status="active", monetization="active", clients=1, enabler="false")
    _profile(s, "life", "Life", status="active", monetization="none", clients=0, enabler="false")
    live = _mk(s, "Go live: merge jeby-us PR #1", "jeby-us", type="release", status="review", priority="p0", size="s", review_by="2026-11-14",
               tags=["emergency", "james-asked-3x"], track="Go-live", created="2026-11-07", updated="2026-11-08", owner="james", assignee="james")
    live.set_section("Report", "Built.\n")
    s.save(live, touch=False)
    choose = _mk(s, "Choose: threads use tickets directly", "desk", type="decision", priority="p0", size="xs", review_by="2026-11-13", track="First real use", owner="james", assignee="james")
    for i in range(9):
        _mk(s, f"Waits on the choice {i}", "desk", depends_on=[choose.id], priority="p2")
    health = _mk(s, "Appointment: book it", "life", type="task", priority="p1", size="xs", tags=["health"], deadline="2026-11-12", deadline_kind="hard", owner="james", assignee="james")
    feat = _mk(s, "Foreclosure screen weeks 1-4", "jre", type="feature", priority="p1", size="m", track="Backlog", assignee="jre")
    s.projects_dir.joinpath("jre.md").write_text(s.projects_dir.joinpath("jre.md").read_text(encoding="utf-8").replace("clients: 2", "clients: 0").replace("monetization: active", "monetization: planned"), encoding="utf-8")
    parked = _mk(s, "Rainbow Road through Chroma SDK", "lab", type="feature", priority="p1", size="m")
    sc = _scores(s)
    got = {t.id: sc.of(t).score for t in (live, choose, health, feat, parked)}
    assert got == {live.id: 97, choose.id: 78, health.id: 89, feat.id: 15, parked.id: 2}  # the table in docs/PRIORITY.md section 5
    assert {t.id: sc.of(t).raw for t in (live, choose, health, feat, parked)} == {live.id: 73.7, choose.id: 78.26, health.id: 91.5, feat.id: 15.4, parked.id: 2.4}
    order = [t.id for t in sc.sort([parked, feat, health, choose, live])]
    assert order == [live.id, health.id, choose.id, feat.id, parked.id]
    assert sc.of(live).reasons() == ["an emergency", "top priority, set by hand", "review by Nov 14"]
    assert sc.of(choose).reasons() == ["top priority, set by hand", "unblocks 9 tickets", "review by Nov 13"]
    assert sc.of(health).reasons() == ["health comes first", "due in 2 days", "high priority, set by hand"]
    assert sc.of(feat).reasons()[0] == "no clients yet: feature work waits"


# -- James's rules (DECISIONS 90) --------------------------------------------------------------------

def test_health_scores_high_and_a_hard_deadline_decides_how_urgent(shop):
    plain = _mk(shop, "A plain task", type="task", priority="p2", size="s")
    health = _mk(shop, "Appointment with no date", type="task", priority="p2", size="s", tags=["health"])
    soon = _mk(shop, "Appointment in two days", type="task", priority="p2", size="s", tags=["health"], deadline="2026-11-12", deadline_kind="hard")
    missed = _mk(shop, "Appointment missed", type="task", priority="p2", size="s", tags=["health"], deadline="2026-11-05", deadline_kind="hard")
    sc = _scores(shop)
    assert sc.of(health).score - sc.of(plain).score == 30  # the default health term
    assert sc.of(plain).score < sc.of(health).score < sc.of(soon).score < sc.of(missed).score  # the closer or more missed, the higher
    assert sc.of(soon).reasons()[0] == "health comes first" and "due in 2 days" in sc.of(soon).reasons()
    assert "missed by 5 days" in sc.of(missed).reasons()
    assert "health" not in sc.of(plain).why()


def test_feature_work_without_clients_waits_unless_the_project_is_an_enabler(shop):
    lab_feature = _mk(shop, "Lab feature", "lab", type="feature", priority="p1", size="s")
    lab_upgrade = _mk(shop, "Lab upgrade", "lab", type="upgrade", priority="p1", size="s")
    lab_bug = _mk(shop, "Lab bug", "lab", type="bug", priority="p1", size="s")
    desk_feature = _mk(shop, "Desk feature", "desk", type="feature", priority="p1", size="s")
    jre_feature = _mk(shop, "jRE feature", "jre", type="feature", priority="p1", size="s")
    sc = _scores(shop)
    term = lambda t, key: next((x for x in sc.of(t).terms if x.key == key), None)  # noqa: E731
    assert term(lab_feature, "no_clients").points == -20 and term(lab_upgrade, "no_clients").points == -20
    assert term(lab_bug, "no_clients") is None  # bugs, tasks, decisions and releases are never reduced
    assert term(desk_feature, "no_clients").points == 0 and term(desk_feature, "no_clients").words == "an enabler: exempt from the no-clients rule"
    assert term(jre_feature, "no_clients") is None  # a paying project
    assert sc.of(lab_feature).reasons()[0] == "no clients yet: feature work waits"
    assert sc.of(lab_bug).score > sc.of(lab_feature).score


def test_people_terms_come_only_from_the_private_file_with_an_optional_project_scope(shop, tmp_path):
    inv = _mk(shop, "Investor deck", "jre", type="task", tags=["with:family-investor"])
    inv_desk = _mk(shop, "Investor view in Desk", "desk", type="task", tags=["with:family-investor"])
    assoc = _mk(shop, "A call", "desk", type="task", associated=["counsel"])
    owned = _mk(shop, "Owned by a boosted person", "desk", type="task", owner="chaz", assignee="chaz")
    plain = _mk(shop, "No people", "desk", type="task")
    assert all(x.key != "people" for t in (inv, assoc, owned) for x in _scores(shop).of(t).terms)  # no file named: no people terms
    priv = tmp_path / "secret" / "priority.toml"
    priv.parent.mkdir()
    priv.write_text('[people.family-investor]\nboost = 25\nprojects = ["jre"]\nwords = "family priority"\n\n[people.counsel]\nboost = 12\n\n[people.chaz]\nboost = 5\n', encoding="utf-8")
    s = _toml(shop, f'[desk]\nowner = "james"\n\n[priority]\nprivate_weights = "{priv.as_posix()}"\n')
    sc = _scores(s)
    people = lambda t: next((x for x in sc.of(t).terms if x.key == "people"), None)  # noqa: E731
    assert people(inv).points == 25 and people(inv).words == "family priority" and people(inv).level == "private file"
    assert people(inv_desk) is None  # scoped to jre
    assert people(assoc).points == 12 and people(assoc).words == "a person who comes first"  # no words chosen: a neutral phrase
    assert people(owned).points == 5 and people(plain) is None
    assert sc.of(inv).reasons()[0] == "family priority"
    # the file is never printed: explain says present or absent, nothing more
    text = priority.explain(s, inv, sc)
    assert "private people file: present (never printed)" in text and "25" in text and str(priv) not in text and "secret" not in text


def test_a_missing_private_file_means_no_people_terms_and_no_error(shop, tmp_path):
    s = _toml(shop, f'[desk]\nowner = "james"\n\n[priority]\nprivate_weights = "{(tmp_path / "nowhere.toml").as_posix()}"\n')
    t = _mk(s, "Investor deck", "jre", type="task", tags=["with:family-investor"])
    assert priority.private_people(s) == {}
    assert all(x.key != "people" for x in _scores(s).of(t).terms)
    assert "private people file: absent" in priority.explain(s, t, _scores(s))


def test_tests_never_point_at_the_real_private_folder(shop):
    assert "jeby-private" not in str((shop.config.get("priority") or {}).get("private_weights") or "")


# -- weights at every level (DESK-0137) ----------------------------------------------------------

def test_weights_nearest_level_wins_and_explain_names_the_level(shop):
    t = _mk(shop, "Health in desk", "desk", type="task", tags=["health"], track="Go-live")
    lab = _mk(shop, "Health in the lab", "lab", type="task", tags=["health"], track="Go-live")
    sc = _scores(shop)
    tag = lambda sc, t: next(x for x in sc.of(t).terms if x.key == "tag:health")  # noqa: E731
    trk = lambda sc, t: next(x for x in sc.of(t).terms if x.key == "track")  # noqa: E731
    assert (tag(sc, t).points, tag(sc, t).level) == (30, "default") and (trk(sc, t).points, trk(sc, t).level) == (6, "default")
    s = _toml(shop, '[desk]\nowner = "james"\n\n[priority]\nbase = 12\n\n[priority.tags]\nhealth = 40\n\n[priority.tracks]\n"Go-live" = 9\n')
    (s.home / "tracks").mkdir()
    (s.home / "tracks" / "rules.yaml").write_text('rules: []\nweights:\n  "Go-live": 2\n', encoding="utf-8")
    _profile(s, "lab", "Lighting Lab", status="parked", monetization="none", clients=0, enabler="false", priority='{tags: {health: 50}}')
    sc = _scores(s)
    assert (tag(sc, t).points, tag(sc, t).level) == (40, "store")  # the store overrides the default
    assert (tag(sc, lab).points, tag(sc, lab).level) == (50, "project")  # the project overrides the store
    assert (trk(sc, t).points, trk(sc, t).level) == (2, "track rules")  # track rules sit between store and project
    base = next(x for x in sc.of(t).terms if x.key == "base")
    assert (base.points, base.level) == (12, "store")
    text = priority.explain(s, lab, sc)
    assert re.search(r"\+50\.0\s+tag:health\s+health comes first\s+project", text) and "store desk.toml [priority]: present" in text
    assert "track rules weights: present" in text and "project profile priority block: present" in text


def test_hand_set_priority_is_one_input_not_the_order(shop):
    p0_blocked = _mk(shop, "A p0 that cannot start", type="task", priority="p0", status="blocked", blocked_on="a decision")
    p2_ready = _mk(shop, "A p2 bug ready now", type="bug", priority="p2", size="xs")
    choose = _mk(shop, "Choose: a p1 that nine wait on", type="decision", priority="p1", size="xs", owner="james", assignee="james")
    for i in range(9):
        _mk(shop, f"Waits {i}", depends_on=[choose.id])
    sc = _scores(shop)
    assert sc.of(choose).score > sc.of(p2_ready).score > sc.of(p0_blocked).score
    assert "unblocks 9 tickets" in sc.of(choose).reasons() and "blocked" in sc.of(p0_blocked).reasons()
    value = next(x for x in sc.of(p0_blocked).terms if x.key == "value")
    assert value.points == 24 and value.words == "top priority, set by hand"
    explicit = _mk(shop, "Value set by hand", type="task", priority="p3", value=5)
    sc = _scores(shop)
    assert next(x for x in sc.of(explicit).terms if x.key == "value").words == "worth 5 of 5"


def test_emergencies_sit_in_their_own_band_and_pull_their_open_dependencies(shop):
    dep = _mk(shop, "A dependency", type="task", priority="p3", size="l")
    em = _mk(shop, "Asked three times", type="task", priority="p3", tags=["james-asked-3x"], depends_on=[dep.id])
    best = _mk(shop, "The best ordinary ticket", type="bug", priority="p0", size="xs", deadline="2026-11-10", deadline_kind="hard")
    sc = _scores(shop)
    assert 90 <= sc.of(em).score <= 99 and 90 <= sc.of(dep).score <= 99 and sc.of(best).score <= 89
    assert sc.of(em).reasons()[0] == "an emergency" and sc.of(dep).reasons()[0] == "an emergency waits on it"
    assert sc.of(best).reasons() == ["top priority, set by hand", "due today", "a bug"]  # the biggest terms first


def test_closed_tickets_have_no_score(shop):
    done = _mk(shop, "Done", type="task", status="done")
    sc = _scores(shop)
    assert sc.of(done).score is None and sc.of(done).reasons() == []
    assert "Closed tickets are not ranked" in priority.explain(shop, done, sc)


# -- rollups (DESK-0136) -------------------------------------------------------------------------------

def test_rollups_rank_by_max_and_show_sum_and_mean(shop):
    a = _mk(shop, "Desk a", "desk", type="task", priority="p0", size="xs", track="A")
    b = _mk(shop, "Desk b", "desk", type="task", priority="p3", size="l", track="A")
    for i in range(6):
        _mk(shop, f"jRE many {i}", "jre", type="task", priority="p2", track="B")
    sc = _scores(shop)
    ts = shop.tickets()
    desk, jre = priority.rollup(sc, [t for t in ts if t.project == "desk"]), priority.rollup(sc, [t for t in ts if t.project == "jre"])
    assert desk.max == sc.of(a).score and desk.sum == sc.of(a).score + sc.of(b).score and desk.mean == round(desk.sum / 2, 1) and desk.top.id == a.id
    assert jre.sum > desk.sum and desk.max > jre.max  # the pile is bigger, but the best thing to do is in desk
    assert desk.key < jre.key  # so desk ranks first
    rows = priority.by_group(sc, [t for t in ts if t.is_open], lambda t: t.project)
    assert [k for k, _ in rows] == ["desk", "jre"]
    assert priority.rollup(sc, []).text == "no open tickets"


def test_projects_board_and_scores_page_show_the_rollups_and_sort_by_them(shop):
    a = _mk(shop, "Desk a", "desk", type="task", priority="p0", size="xs", track="A")
    for i in range(4):
        _mk(shop, f"jRE many {i}", "jre", type="task", priority="p2", track="B")
    c = TestClient(create_app(shop.home))
    page = c.get("/projects").text
    assert page.index('data-val="desk"') < page.index('data-val="jre"')  # desk first: it holds the best ticket
    assert re.search(r'href="/scores\?project=desk"[^>]*>score \d+</a>', page) and re.search(r'href="/scores\?project=desk&amp;track=a"[^>]*>score \d+</a>', page)
    board = c.get("/board").text
    assert re.search(r'title="score \d+ · load \d+ · mean [\d.]+: the highest score', board)
    scores = c.get("/scores?project=desk").text
    assert "Tickets by score: Desk" in scores and a.id in scores and "highest score <b>" in scores and "load" in scores
    rows = re.findall(r'<a class="trow" href="/ticket/([A-Z]+-\d+)"', scores)
    assert rows[0] == a.id
    person = c.get("/scores?assignee=main").text
    assert "Tickets by score: Patch" in person and str(len(rows)) in person


def test_drew_stats_rolls_up_per_assignee_and_the_store(shop, capsys):
    _mk(shop, "Mine", "desk", type="task", priority="p0", assignee="james")
    _mk(shop, "Theirs", "desk", type="task", priority="p3", assignee="main")
    main(["--home", str(shop.home), "stats"])
    out = capsys.readouterr().out
    assert "By score" in out and re.search(r"james\s+1\s+\d+\s+\d+\s+[\d.]+\s+DESK-\d+", out) and re.search(r"Whole store: 2 open · score \d+ · load \d+ · mean", out)
    main(["--home", str(shop.home), "stats", "--json"])
    import json

    data = json.loads(capsys.readouterr().out)
    assert data["score"]["store"]["open"] == 2 and set(data["score"]["by_assignee"]) == {"james", "main"}


# -- every list sorts by score (DESK-0135) ---------------------------------------------------------

def test_lists_sort_by_score_cards_show_it_and_explain_prints_every_term(shop, capsys):
    low = _mk(shop, "Low p3 feature", "lab", type="feature", priority="p3", size="l", created="2026-11-10")
    high = _mk(shop, "High p0 bug", "desk", type="bug", priority="p0", size="xs",
              deadline=(dt.date.today() + dt.timedelta(days=1)).isoformat(), deadline_kind="hard")  # explain and the page judge by the real clock, so the deadline is tomorrow on any day the suite runs
    mid = _mk(shop, "Mid p2 task", "desk", type="task", priority="p2", status="in_progress")
    main(["--home", str(shop.home), "list", "--sort", "score"])
    out = capsys.readouterr().out
    assert out.index(high.id) < out.index(mid.id) < out.index(low.id) and "score" in out.splitlines()[0]
    main(["--home", str(shop.home), "list"])
    out = capsys.readouterr().out
    assert re.search(rf"{high.id}\s+desk\s+bug\s+ready\s+\d+\s+p0", out)  # the score column, in the default project order too
    assert shop.next_ready().id == high.id and shop.next_ready("lab").id == low.id
    main(["--home", str(shop.home), "explain", high.id])
    text = capsys.readouterr().out
    assert text.startswith(f"{high.id}  High p0 bug") and "Score " in text and "Why: " in text
    assert re.search(r"\+24\.0\s+value\s+top priority, set by hand\s+default", text) and re.search(r"\+8\.0\s+type\s+a bug", text) and "due in 1 day" in text
    assert "Levels: default → store → track rules → project; the nearest wins." in text
    c = TestClient(create_app(shop.home))
    today = c.get("/").text
    rows = re.findall(r'<a class="trow[^"]*" href="/ticket/([A-Z]+-\d+)"', today)
    assert rows.index(mid.id) >= 0 and re.search(rf'<b class="score" title="Score \d+: [^"]+">\d+</b>', today)
    page = c.get(f"/ticket/{high.id}").text
    assert re.search(r'<p class="muted" id="score" style="margin-top:0"><b class="score">\d+</b> of 100 · why: top priority, set by hand · due in 1 day · a bug · <a href="/doc/docs/PRIORITY.md">how the score works</a> · p0 · size', page)
    board = c.get("/board?open=desk&g=desk:untracked").text
    card = board[board.index('class="ccard'):]
    assert re.search(r'<div class="cc-why muted"><b class="score">\d+</b> [^<]+</div>', card)


def test_scoring_a_1500_ticket_store_takes_well_under_a_second(tmp_path):
    s = bench.build_store(tmp_path / "big", 1500)
    tickets = s.tickets()
    t0 = time.perf_counter()
    sc = priority.Scores(s, tickets)
    took = time.perf_counter() - t0
    assert len(sc.scores) == 1500 and took < 1.0, f"scoring took {took:.2f}s"
    t0 = time.perf_counter()
    assert priority.scores(s, tickets) is priority.scores(s)  # the cache: the same object until the store changes
    assert time.perf_counter() - t0 < 0.5


def test_the_reasons_are_plain_words_never_a_code(shop):
    """Protects: James's rule (2026-11-10 review): a card says "top priority, set by hand", never "hand-set p2" or a field name."""
    sc = priority.scores(shop)
    for t in shop.tickets():
        if t.is_open:
            why = sc.of(t).why(99)
            assert not re.search(r"p[0-3]|hand-set|value", why), (t.id, why)


def test_a_page_scores_the_store_once_never_per_card(shop, monkeypatch):
    """Protects: the 2026-11-10 review measured Today at twice its time because every card re-stamped the store. A page stamps the
    store a handful of times (one per view that asks), never once per row."""
    for i in range(8):
        _mk(shop, f"Card {i}", "desk" if i % 2 else "jre", type="task", status="in_progress", priority=f"p{i % 4}", track="A" if i % 3 else "")
    calls = []
    real = priority.stamp
    monkeypatch.setattr(priority, "stamp", lambda st: calls.append(1) or real(st))
    c = TestClient(create_app(shop.home))
    for path in ("/", "/board?open=*&g=*", "/projects"):
        calls.clear()
        page = c.get(path).text
        assert page.count('class="score') > 3, path  # the page did draw scores
        assert len(calls) <= 6, (path, len(calls))


def test_value_outside_1_to_5_is_a_validate_error(shop):
    """Protects: `value:` is documented as 1..5; a typo never silently changes the order (2026-11-10 review)."""
    from desk.rules import validate_store
    t = ops.new(shop, "Worth a lot", "desk", "task", owner="james")
    for bad in (9, 0, "high", 2.5, True):
        t.meta["value"] = bad
        with pytest.raises(ValueError, match="value must be a whole number from 1 to 5"):
            shop.save(t)  # the store runs the rules before it writes
    t.meta["value"] = 4
    shop.save(t)
    errors, _w = validate_store(shop)
    assert not any(t.id in e and "value" in e for e in errors), errors
