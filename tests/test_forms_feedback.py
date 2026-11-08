"""One-time form tokens, comment duplicate detection, and the confirmation line."""

import re

from fastapi.testclient import TestClient

from desk import ops
from desk.ui.app import create_app


def _client(store):
    return TestClient(create_app(store.home))


def _token(page: str, action: str) -> str:
    form = re.search(rf'<form[^>]*action="{re.escape(action)}".*?</form>', page, re.S).group(0)
    return re.search(r'name="token" value="([^"]+)"', form).group(1)


def test_double_post_yields_one_line(store):
    t = ops.new(store, "Talk", "jre")
    c = _client(store)
    page = c.get(f"/ticket/{t.id}").text
    tok = _token(page, f"/ticket/{t.id}/comment")
    r1 = c.post(f"/ticket/{t.id}/comment", data={"token": tok, "text": "Hello there"}, follow_redirects=False)
    assert "done=comment" in r1.headers["location"]
    r2 = c.post(f"/ticket/{t.id}/comment", data={"token": tok, "text": "Hello there"})
    assert r2.status_code == 409 and "already sent" in r2.text
    assert store.get(t.id).section("Comments").count("Hello there") == 1


def test_confirmation_line_shown(store):
    t = ops.new(store, "Talk", "jre")
    c = _client(store)
    r = c.post(f"/ticket/{t.id}/comment", data={"token": c.app.state.tokens.issue(), "text": "Hi"}, follow_redirects=True)
    assert re.search(r"Comment added \d\d:\d\d", r.text)


def test_identical_comment_with_fresh_token_is_duplicate(store):
    t = ops.new(store, "Talk", "jre")
    c = _client(store)
    for _ in range(2):
        r = c.post(f"/ticket/{t.id}/comment", data={"token": c.app.state.tokens.issue(), "text": "Same  words", "who": "em"}, follow_redirects=True)
    assert "That comment is already on the ticket" in r.text
    assert store.get(t.id).section("Comments").count("Same") == 1


def test_different_text_and_other_author_accepted(store):
    t = ops.new(store, "Talk", "jre")
    assert ops.comment(store, t, "One", actor="em").added
    assert ops.comment(store, store.get(t.id), "Two", actor="em").added
    assert ops.comment(store, store.get(t.id), "one", actor="chaz").added
    assert not ops.comment(store, store.get(t.id), " ONE ", actor="em").added
    assert store.get(t.id).section("Comments").count("\n") == 2


def test_old_duplicate_outside_window_is_added(store):
    t = ops.new(store, "Talk", "jre")
    t.append_line("Comments", "- 2026-01-01 09:00 · em · Same")
    store.save(t)
    assert ops.comment(store, store.get(t.id), "Same", actor="em").added


def test_tokens_on_every_state_form_and_missing_token_refused(store):
    t = ops.new(store, "Talk", "jre")
    c = _client(store)
    page = c.get(f"/ticket/{t.id}").text
    assert _token(page, f"/ticket/{t.id}/move")
    assert c.post(f"/ticket/{t.id}/move", data={"to": "triage"}).status_code == 409
    assert store.get(t.id).status == "inbox"
    assert "disabled = true" in page


def test_progress_handler_and_confirmation_survive_redirects(store):
    """DESK-0090: every state-changing form is post-once, base.html swaps in Sending..., and redirects carry ?done=."""
    t = ops.new(store, "Talk", "jre")
    c = _client(store)
    page = c.get(f"/ticket/{t.id}").text
    assert page.count('class="post-once"') >= 2 and "Sending..." in page and "data-sending" in page
    assert "post-once" in c.get("/settings").text
    r = c.post(f"/ticket/{t.id}/comment", data={"token": c.app.state.tokens.issue(), "text": "Hi"}, follow_redirects=True)
    assert 'role="status"' in r.text and "Comment added" in r.text
    # review-week style redirect via next=
    r = c.post(f"/ticket/{t.id}/move", data={"token": c.app.state.tokens.issue(), "to": "triage"}, follow_redirects=False)
    assert "done=move" in r.headers["location"]
