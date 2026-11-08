"""DESK-0089: paste or drop an image into a comment: upload, limit, traversal, thumbnail."""

import re

from fastapi.testclient import TestClient

from desk import ops
from desk.ui.app import create_app

PNG = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 32
JSON = {"Accept": "application/json"}


def _client(store):
    return TestClient(create_app(store.home))


def _up(c, t, name, data, ctype="image/png"):
    return c.post(f"/ticket/{t.id}/attach", data={"token": c.app.state.tokens.issue()}, files={"file": (name, data, ctype)}, headers=JSON)


def test_page_carries_paste_handler(store):
    t = ops.new(store, "Shots", "jre")
    page = _client(store).get(f"/ticket/{t.id}").text
    assert f'data-attach="/ticket/{t.id}/attach"' in page and 'addEventListener("paste"' in page and 'addEventListener("drop"' in page


def test_upload_stores_file_returns_markdown_and_comment_shows_thumbnail(store):
    t = ops.new(store, "Shots", "jre")
    c = _client(store)
    r = _up(c, t, "image.png", PNG)
    assert r.status_code == 200
    j = r.json()
    assert re.fullmatch(rf"!\[[^\]]*\]\(assets/{t.id}/screenshot-[0-9-]+\.png\)", j["markdown"]) and j["token"]
    assert (store.ticket_assets_dir(t.id) / j["name"]).read_bytes() == PNG
    ops.comment(store, store.get(t.id), f"see this {j['markdown']}", actor="james")
    page = c.get(f"/ticket/{t.id}").text
    assert f'<a href="/assets/{t.id}/{j["name"]}" class="thumb"' in page and f'src="/assets/{t.id}/{j["name"]}"' in page
    assert c.get(j["url"]).status_code == 200


def test_limit_names_itself_and_non_images_refused(store):
    t = ops.new(store, "Shots", "jre")
    cfg = store.home / "desk.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace("max_mb = 8", "max_mb = 1"), encoding="utf-8")
    c = _client(store)
    r = _up(c, t, "big.png", PNG + b"\x00" * (1024 * 1024 + 1))
    assert r.status_code == 413 and "1 MB limit" in r.json()["error"] and r.json()["token"]
    r = _up(c, t, "evil.png", b"<script>alert(1)</script>")
    assert r.status_code == 415 and "Images only" in r.json()["error"]
    r = _up(c, t, "a.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml")
    assert r.status_code == 415
    assert not store.ticket_assets_dir(t.id).exists() or not list(store.ticket_assets_dir(t.id).iterdir())


def test_default_limit_is_8_mb(store):
    assert store.max_attach_mb == 8


def test_traversal_name_stays_inside_the_ticket_assets_folder(store):
    t = ops.new(store, "Shots", "jre")
    c = _client(store)
    r = _up(c, t, "../../../../evil.png", PNG)
    assert r.status_code == 200
    d = store.ticket_assets_dir(t.id)
    assert [p.name for p in d.iterdir()] == [r.json()["name"]] and not (store.home / "evil.png").exists()
    assert c.get(f"/assets/{t.id}/..%2F..%2Fdesk.toml").status_code == 404
    assert _up(c, ops.new(store, "Other", "jre"), "x.png", PNG).status_code == 200


def test_token_is_single_use_and_form_post_redirects_with_confirmation(store):
    t = ops.new(store, "Shots", "jre")
    c = _client(store)
    tok = c.app.state.tokens.issue()
    r = c.post(f"/ticket/{t.id}/attach", data={"token": tok}, files={"file": ("a.png", PNG, "image/png")}, follow_redirects=True)
    assert "Image attached" in r.text and "post-once attach-form" in r.text
    r = c.post(f"/ticket/{t.id}/attach", data={"token": tok}, files={"file": ("a.png", PNG, "image/png")})
    assert r.status_code == 409
