"""DESK-0257: a browser must run the Desk scripts and styles of the build it talks to, not an older copy from its cache."""

import re
from urllib.parse import quote

from fastapi.testclient import TestClient

from desk import ops, version as ver
from desk.ui.app import create_app

PAGES = ["/", "/board", "/new", "/about"]
ASSET = re.compile(r'<(?:script|link)\b[^>]*?\b(?:src|href)="(/static/[^"]+)"')


def test_static_files_say_no_cache(store):
    """Protects the upgrade path: a /static response is revalidated by ETag before a browser reuses it, so a fixed progress.js cannot stay hidden behind a guessed freshness time."""
    c = TestClient(create_app(store.home))
    for path in ("/static/progress.js", "/static/desk.css"):
        r = c.get(path)
        assert r.status_code == 200
        assert r.headers.get("cache-control") == "no-cache", path


def test_static_addresses_carry_the_build_id(store):
    """Protects the upgrade path: every script and stylesheet address in a rendered page ends with ?v=<build id>, so a new build is a new address."""
    t = ops.new(store, "A ticket with every script", "desk")
    c = TestClient(create_app(store.home))
    build = ver.build_commit()
    seen = 0
    for path in PAGES + [f"/ticket/{t.id}"]:
        r = c.get(path)
        assert r.status_code == 200, path
        for url in ASSET.findall(r.text):
            seen += 1
            assert url.endswith(f"?v={quote(build, safe='')}"), f"{path}: {url}"
    assert seen >= 8  # the base page alone loads a stylesheet and five scripts; the ticket page loads more
