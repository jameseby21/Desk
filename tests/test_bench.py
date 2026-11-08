"""Performance harness, Server-Timing and the slow-request log. The `bench` marker runs the 1,000-ticket case against budgets and baseline."""

import logging

import pytest
from fastapi.testclient import TestClient

from desk import bench, timing
from desk.ui.app import create_app


def test_percentile_nearest_rank():
    assert bench.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 50) == 5
    assert bench.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10
    assert bench.percentile([7], 95) == 7


def test_built_store_has_brand_blocks_and_3k_tickets(tmp_path):
    s = bench.build_store(tmp_path / "h", 12, projects=3)
    assert len(s.tickets()) == 12
    assert all(s.brand_for(k) for k in s.projects())
    size = sorted(s.ticket_paths())[0].stat().st_size
    assert 3000 <= size <= 3900


def test_run_small_covers_every_path():
    res = bench.run(20, runs=2)
    assert set(res["paths"]) == set(bench.PATHS)
    assert res["paths"]["today"]["db_p50_ms"] >= 0
    assert "p95 ms" in bench.table(res)


def test_budget_and_regression_logic():
    res = {"tickets": 1000, "runs": 1, "paths": {"today": {"p50_ms": 130, "p95_ms": 200, "n": 1}, "new": {"p50_ms": 10, "p95_ms": 20, "n": 1}}}
    assert bench.budget_failures(res) == ["today: p95 200 ms is over the 150 ms budget"]
    base = {"1000": {"paths": {"today": {"p50_ms": 100}, "new": {"p50_ms": 10}}}}
    assert len(bench.regressions(res, base)) == 1  # 130 > 100 * 1.2 + 2
    assert bench.regressions({**res, "tickets": 5}, base) == []


def test_server_timing_header_on_every_response(store):
    c = TestClient(create_app(store.home))
    for url in ("/", "/board", "/about", "/nope"):
        h = c.get(url).headers["server-timing"]
        assert h.startswith("db;dur=") and "render;dur=" in h and "total;dur=" in h


def test_slow_requests_are_logged(store, monkeypatch, caplog):
    c = TestClient(create_app(store.home))
    monkeypatch.setattr(timing, "SLOW_MS", 0.0)
    with caplog.at_level(logging.WARNING, logger="desk.slow"):
        c.get("/board")
    assert any("slow request GET /board" in r.getMessage() for r in caplog.records)
    caplog.clear()
    monkeypatch.setattr(timing, "SLOW_MS", 60000.0)
    with caplog.at_level(logging.WARNING, logger="desk.slow"):
        c.get("/board")
    assert not caplog.records


@pytest.mark.bench
def test_bench_1k_keeps_budgets_and_baseline():
    res = bench.run(1000)
    print("\n" + bench.table(res))
    problems = bench.budget_failures(res) + bench.regressions(res)
    assert not problems, "\n".join(problems)
