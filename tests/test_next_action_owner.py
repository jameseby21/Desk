"""DESK-0334: the Next action card names the person whose step it is.

James's screenshot (2026-11-19, MGMT-0112): the card said "For you" although the ticket's assignee is Patch. Needs me lists a `do` for the
owner of a ready ticket, but the step belongs to the assignee, so the card says "For Patch" and says "For you" only when the viewer is the
assignee, or is asked something only the owner can answer (approve, choose, answer, provide). Three methods: reading.next_action as a
function for each viewer, the served ticket page, and (test_id_links_browser.py) the card in a real browser."""

import re

import pytest
from fastapi.testclient import TestClient

from desk import ops, reading
from desk.ui.app import create_app


@pytest.fixture
def world(store):
    t = ops.new(store, "Fix the card", "desk", "bug", owner="james", assignee="main")
    x = store.get(t.id)
    x.meta["status"] = "ready"
    x.meta["smallest_step"] = "Rewrite the first line"
    store.save(x)
    return store, store.get(t.id)


# ---- the Next action person --------------------------------------------------------------------------------------------------

def test_next_action_names_the_assignee_when_the_owner_has_only_a_do_ask_on_it(world):
    """Protects: owner James, assignee Patch, status ready: Needs me lists a `do` for the owner, but the step is Patch's, so the card
    says For Patch (unit: reading.next_action called for James and for Patch)."""
    store, t = world
    for_james = reading.next_action(store, t, "james")
    for_patch = reading.next_action(store, t, "main")
    assert for_james.for_whom == "Patch" and not for_james.mine
    assert for_patch.for_whom == "you" and for_patch.mine


def test_next_action_stays_for_you_when_the_owner_must_decide_and_is_not_the_assignee(store):
    """Protects: an approve or choose ask is the owner's even when someone else holds the ticket; the card still says For you."""
    t = ops.new(store, "Pick a colour", "desk", "decision", owner="james", assignee="main")
    x = store.get(t.id)
    x.meta["status"] = "review"
    x.sections["Report"] = "- Two looks drawn."
    store.save(x)
    assert reading.next_action(store, store.get(t.id), "james").for_whom == "you"


def test_next_action_for_you_when_nobody_else_holds_the_ticket(store):
    t = ops.new(store, "My own step", "desk", "task", owner="james", assignee="james")
    x = store.get(t.id)
    x.meta["status"] = "ready"
    store.save(x)
    assert reading.next_action(store, store.get(t.id), "james").for_whom == "you"


def test_the_served_ticket_page_says_for_patch_to_the_owner(world):
    """Protects the page itself: the owner (James, the browser) reads For Patch on a ticket Patch holds."""
    store, t = world
    c = TestClient(create_app(store.home), cookies={"desk_device": "testbrowser"})
    html = c.get(f"/ticket/{t.id}").text
    nxt = re.search(r'id="next-action".*?</section>', html, re.S).group(0)
    assert '<span class="rd-for">For Patch</span>' in nxt and "For you" not in nxt
