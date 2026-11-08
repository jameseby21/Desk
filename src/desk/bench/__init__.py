"""Performance harness: build a throwaway store of N synthetic tickets, time the data paths, compare with budgets and baseline.

`drew bench --tickets 1000` prints p50 and p95 per path. The pages are driven through the real FastAPI app (no network), so a
number includes the store reads, the profile reads and the template render a browser would wait for."""

from __future__ import annotations

import asyncio
import json
import math
import shutil
import statistics
import tempfile
import time
import tomllib
from pathlib import Path

from ..store import Store
from ..ticket import new_ticket

HERE = Path(__file__).parent
BUDGETS = HERE / "budgets.toml"
BASELINE = HERE / "baseline.json"
PROJECTS = 30
TARGET_BYTES = 3400
PATHS = ["today", "board", "ticket_page", "next_id", "new", "move", "reindex"]
_STATUS_CYCLE = ["inbox", "triage", "ready", "ready", "claimed", "in_progress", "review", "done", "done", "done"]
_LOREM = ("The scheduler reads each profile, resolves the brand block, and renders one card per ticket so the owner sees the day at a glance. "
          "Nothing here is real; the text only gives every ticket a believable weight on disk. ")


def load_budgets() -> dict:
    with BUDGETS.open("rb") as fh:
        return tomllib.load(fh)


def load_baseline() -> dict:
    try:
        return json.loads(BASELINE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def percentile(values: list[float], pct: float) -> float:
    s = sorted(values)
    return s[max(0, math.ceil(pct / 100 * len(s)) - 1)]


# -- the synthetic store -----------------------------------------------------------

def build_store(home: Path, n: int, projects: int = PROJECTS) -> Store:
    """30 projects with brand blocks and marks, n tickets of about 3.4 KB spread over every status."""
    s = Store.init(home, owner="james")
    keys = [f"p{i:02d}" for i in range(projects)]
    for k in keys:
        (s.projects_dir / f"{k}.md").write_text(
            f"---\nkey: {k}\nprefix: {k.upper()}\nname: Project {k}\nbrand: {{mark: \"assets/brand/{k}/mark.svg\", accent: \"#6E6C7A\", family: \"Bench\"}}\n---\n"
            f"# Project {k}\n\nSynthetic project for the bench.\n", encoding="utf-8")
        (s.brand_dir / k).mkdir(parents=True, exist_ok=True)
        (s.brand_dir / k / "mark.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8"/>', encoding="utf-8")
    s.tickets_dir.mkdir(exist_ok=True)
    counters: dict[str, int] = {}
    for i in range(n):
        k = keys[i % projects]
        counters[k] = counters.get(k, 0) + 1
        status = _STATUS_CYCLE[i % len(_STATUS_CYCLE)]
        t = new_ticket(f"{k.upper()}-{counters[k]:04d}", f"Synthetic ticket {i} for {k}", k, "feature", size="s", owner="main")
        t.meta["status"] = status
        t.meta["tags"] = ["bench"]
        t.sections["Context"] = (_LOREM * 4).strip() + "\n"
        t.sections["Outcome"] = "One visible result.\n"
        t.sections["Acceptance"] = "- [ ] First thing works\n- [ ] Second thing works\n- [x] Third thing works\n"
        t.sections["How to validate"] = "1. Run it and look.\n"
        t.sections["Prompt"] = (_LOREM * 2).strip() + "\n"
        t.sections["Comments"] = "".join(f"- 2026-11-0{1 + j % 8} · main · {_LOREM}\n" for j in range(2))
        if status == "review":
            t.sections["Report"] = "Done and checked.\n"
        if status == "done":
            t.sections["Decision"] = "2026-11-01 · main · approved · fine\n"
        pad = TARGET_BYTES - len(t.render().encode("utf-8"))
        if pad > 0:
            t.sections["Evidence"] = ("Evidence line. " * (pad // 15 + 1))[:pad].strip() + "\n"
        t.path = s.path_for(t)
        t.path.write_text(t.render(), encoding="utf-8", newline="\n")
    return s


# -- timing --------------------------------------------------------------------------

class _Client:
    """Drive the ASGI app in-process: one GET, the status and headers back."""

    def __init__(self, app):
        self.app = app
        self.loop = asyncio.new_event_loop()

    def close(self):
        self.loop.close()

    def get(self, path: str) -> tuple[int, dict]:
        out: dict = {}

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(msg):
            if msg["type"] == "http.response.start":
                out["status"] = msg["status"]
                out["headers"] = {k.decode().lower(): v.decode() for k, v in msg["headers"]}

        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http", "path": path,
                 "raw_path": path.encode(), "query_string": b"", "root_path": "", "headers": [(b"host", b"bench")],
                 "client": ("127.0.0.1", 1), "server": ("bench", 80)}
        self.loop.run_until_complete(self.app(scope, receive, send))
        return out["status"], out["headers"]


def _db_ms(header: str) -> float:
    for part in header.split(","):
        part = part.strip()
        if part.startswith("db;dur="):
            return float(part[7:])
    return 0.0


def _stats(samples: list[float], extra: dict | None = None) -> dict:
    d = {"p50_ms": round(statistics.median(samples), 2), "p95_ms": round(percentile(samples, 95), 2), "n": len(samples)}
    return {**d, **(extra or {})}


def run(n: int, runs: int = 7, progress=None) -> dict:
    """Build a store of n tickets in a temp dir (removed afterwards), time every path, return the results."""
    from .. import ops
    from ..ui.app import create_app

    say = progress or (lambda _m: None)
    tmp = Path(tempfile.mkdtemp(prefix="desk-bench-"))
    try:
        home = tmp / "home"
        t0 = time.perf_counter()
        s = build_store(home, n)
        say(f"built {n} tickets in {time.perf_counter() - t0:.1f} s")
        client = _Client(create_app(home))
        try:
            names = sorted(p.name for p in s.tickets_dir.glob("*.md"))
            tid = "-".join(names[len(names) // 2].split("-")[:2])
            results: dict = {}
            for name, url in (("today", "/"), ("board", "/board"), ("ticket_page", f"/ticket/{tid}")):
                samples, dbs = [], []
                client.get(url)  # warm: imports, template compile
                for _ in range(runs):
                    t = time.perf_counter()
                    status, hdr = client.get(url)
                    samples.append((time.perf_counter() - t) * 1000)
                    dbs.append(_db_ms(hdr.get("server-timing", "")))
                    if status != 200:
                        raise RuntimeError(f"{url} answered {status}")
                results[name] = _stats(samples, {"db_p50_ms": round(statistics.median(dbs), 2)})
                say(f"{name}: p50 {results[name]['p50_ms']} ms")

            def timeit(fn, count):
                out = []
                for i in range(count):
                    t = time.perf_counter()
                    fn(i)
                    out.append((time.perf_counter() - t) * 1000)
                return out

            results["next_id"] = _stats(timeit(lambda i: Store(home).next_id("p00"), runs))
            results["new"] = _stats(timeit(lambda i: ops.new(Store(home), f"Bench new {i}", "p01", "task"), runs))
            mover = next(t for t in s.tickets() if t.status == "inbox")

            def do_move(i):
                store = Store(home)
                ops.move(store, store.get(mover.id), "triage" if i % 2 == 0 else "inbox", actor="main")

            results["move"] = _stats(timeit(do_move, runs))
            results["reindex"] = _stats(timeit(lambda i: Store(home).index(), max(3, runs // 2)))
            say("done")
        finally:
            client.close()
        return {"tickets": n, "runs": runs, "paths": results}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# -- judging -------------------------------------------------------------------------

def budget_failures(result: dict, budgets: dict | None = None) -> list[str]:
    b = (budgets or load_budgets())["budgets_ms"]
    out = []
    for path, st in result["paths"].items():
        lim = b.get(path)
        if lim is not None and st["p95_ms"] > lim:
            out.append(f"{path}: p95 {st['p95_ms']} ms is over the {lim} ms budget")
    return out


def regressions(result: dict, baseline: dict | None = None, budgets: dict | None = None) -> list[str]:
    base = (baseline if baseline is not None else load_baseline()).get(str(result["tickets"]))
    if not base:
        return []
    reg = (budgets or load_budgets())["regression"]
    out = []
    for path, st in result["paths"].items():
        was = base["paths"].get(path)
        if not was:
            continue
        limit = was["p50_ms"] * (1 + reg["max_share"]) + reg["floor_ms"]
        if st["p50_ms"] > limit:
            out.append(f"{path}: p50 {st['p50_ms']} ms is more than {int(reg['max_share'] * 100)}% over the baseline {was['p50_ms']} ms")
    return out


def save_baseline(result: dict) -> Path:
    data = load_baseline()
    data[str(result["tickets"])] = {k: result[k] for k in ("tickets", "runs", "paths")}
    BASELINE.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return BASELINE


def table(result: dict, budgets: dict | None = None) -> str:
    b = (budgets or load_budgets())["budgets_ms"]
    rows = [["path", "p50 ms", "p95 ms", "db p50 ms", "budget p95 ms", "n"]]
    for name in PATHS:
        st = result["paths"].get(name)
        if st:
            rows.append([name, f"{st['p50_ms']:.1f}", f"{st['p95_ms']:.1f}", f"{st['db_p50_ms']:.1f}" if "db_p50_ms" in st else "", str(b.get(name, "")), str(st["n"])])
    w = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = ["  ".join(c.rjust(w[i]) if i else c.ljust(w[i]) for i, c in enumerate(r)) for r in rows]
    lines.insert(1, "  ".join("-" * x for x in w))
    return f"{result['tickets']} tickets, {result['runs']} runs per page path\n" + "\n".join(lines)
