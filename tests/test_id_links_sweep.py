"""DESK-0334: the sweep. Render every page type of Desk on a temporary store that mentions ticket ids in every field, and find no bare
existing id outside code, a link or a link-like span.

Why a sweep: the owner found a bare id on one card (2026-11-19) after Desk had linked ids in the body sections for weeks; a list of
places is how that happened. This test does not know the places. It renders each page, walks the text (tests/idsweep.py) and fails on
any id that exists in the store and is plain. A page type added later is caught the day it shows ticket text, by adding its path to
PATHS; and DESK_SWEEP_STORE=<a copy of a real store> runs the same walk over a real store (a copy, never the real folder).

Protects: no page type shows a ticket id as plain text; a failing page names the id and the words around it."""

import os
import shutil

import pytest
from fastapi.testclient import TestClient
from idsweep import bare_ids

from desk import ops
from desk.ui.app import create_app

PATHS = [
    "/", "/board", "/board?project=desk&company=*&open=*&g=*", "/board?project=desk&by=status", "/board?by=company", "/sprint", "/projects", "/scores", "/search?q=ticket", "/search?q=ZZZ", "/about", "/settings",
    "/new", "/whoami", "/brief", "/brief/week", "/flow", "/repeats", "/company", "/changes", "/decide", "/decide/batch", "/triage", "/views",
    "/automations", "/capture", "/undo", "/ticket/{a}", "/ticket/{b}", "/ticket/{c}", "/ticket/{e}", "/peek/{a}", "/card/ticket/{a}", "/card/ticket/{e}",
    "/doc/glossary.md", "/sprint/sprint-1",
]


def _store_with_ids_everywhere(store):
    ids = []
    for n in range(6):
        t = ops.new(store, f"Ticket number {n}", "desk", ["bug", "task", "feature", "decision", "task", "bug"][n], owner="james", assignee=["main", "james", "chaz", "main", "james", "bigd"][n])
        ids.append(t.id)
    refs = " and ".join(ids)
    for n, tid in enumerate(ids):
        t = store.get(tid)
        t.meta["title"] = f"Ticket number {n} about {ids[(n + 1) % 6]} (and {ids[(n + 2) % 6]})"
        t.meta["status"] = ["ready", "triage", "blocked", "in_progress", "claimed", "ready"][n]
        t.meta["smallest_step"] = f"Rewrite {ids[(n + 3) % 6]}'s first line; see ZZZ-9999"
        t.meta["blocked_on"] = f"{ids[(n + 4) % 6]}" if t.meta["status"] == "blocked" else ""
        t.meta["sprint"] = "sprint-1"
        for sec in ("Context", "Outcome", "Acceptance", "Prompt", "Report"):
            t.sections[sec] = f"- [ ] The {sec} says {refs}.\n" if sec == "Acceptance" else f"**Goal.** The {sec} says {refs}.\n"
        t.sections["Comments"] = f"- 2026-11-19 00:10 Patch: waiting on {ids[(n + 1) % 6]}.\n- 2026-11-19 00:11 James: ok {ids[(n + 2) % 6]}\n"
        t.sections["Decision"] = f'- 2026-11-19 00:12 James: "do {ids[(n + 5) % 6]} first"\n'
        t.sections["Evidence"] = f"Seen on {ids[(n + 1) % 6]}.\n"
        store.save(t)
    return ids


def _sweep(store, paths, existing):
    client = TestClient(create_app(store.home), cookies={"desk_device": "testbrowser"})
    seen, failures = 0, []
    for p in paths:
        r = client.get(p)
        if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
            continue  # a page type this store has none of (a doc that is not there); the real-page tests above carry those
        seen += 1
        bad = bare_ids(r.text, existing)
        if bad:
            failures.append((p, bad[:4]))
    return seen, failures


def test_no_page_type_shows_a_bare_existing_ticket_id(store):
    ids = _store_with_ids_everywhere(store)
    fmt = dict(zip("abcdef", ids))
    existing = {t.id for t in store.tickets()}
    seen, failures = _sweep(store, [p.format(**fmt) for p in PATHS], existing)
    assert seen >= 25, f"only {seen} pages answered; the sweep is not covering the page types"
    assert failures == [], failures


@pytest.mark.skipif(not os.environ.get("DESK_SWEEP_STORE"), reason="set DESK_SWEEP_STORE to a COPY of a real store to sweep it too")
def test_sweep_of_a_copy_of_a_real_store(tmp_path):
    from desk.store import Store

    src = os.environ["DESK_SWEEP_STORE"]
    dst = tmp_path / "copy"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".git", "assets", "__pycache__"))
    store = Store(dst)
    existing = {t.id for t in store.tickets()}
    some = sorted(existing)[:3] + sorted(existing)[-3:]
    seen, failures = _sweep(store, [p.format(**dict(zip("abcdef", some))) for p in PATHS], existing)
    assert seen >= 20 and failures == [], failures
