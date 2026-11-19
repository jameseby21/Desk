"""DESK-0334 in a real browser: the Next action card on a ticket Patch holds says "For Patch" and links the id in its smallest step; an
id inside a whole-row link is a keyboard-reachable span that follows its own ticket; text a script draws (a toast, a message) is linked
by static/idlinks.js with the same rule as the server's.

The browser is headless Edge or Chrome driven over the DevTools Protocol (tests/browser_harness.py) with a throw-away profile that is
removed on return; the server is Desk on a free port (never 8765) on a temporary store. Two widths: 1280 and 390 (a phone)."""

import json

import pytest
from browser_harness import browser, cdp_measure, live_app

from desk import ops
from desk.ui.app import create_app

pytestmark = pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")

CARD = """(() => { const n = document.getElementById('next-action'); if (!n) return null;
  const a = n.querySelector('.rd-step a.tid'); return { who: n.querySelector('.rd-for').textContent.trim(), step: n.querySelector('.rd-step').textContent.trim(),
  href: a && a.getAttribute('href'), linkText: a && a.textContent, mine: n.classList.contains('mine'),
  underline: a && getComputedStyle(a).textDecorationLine, sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }; })()"""

ROW = """(() => { const s = document.querySelector('a span.tid[data-href]'); if (!s) return null;
  const opened = []; const real = window.open; window.open = (u) => { opened.push(u); };
  s.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, ctrlKey: true }));
  window.open = real;
  return { href: s.getAttribute('data-href'), role: s.getAttribute('role'), tab: s.tabIndex, opened, text: s.textContent,
           nestedAnchors: document.querySelectorAll('a a').length }; })()"""

TOAST = """new Promise((resolve) => { const d = document.createElement('div'); d.id = 'probe'; d.textContent = 'Moved %(a)s to done, not %(missing)s, see `%(a)s` and http://x/%(a)s';
  document.body.appendChild(d);
  setTimeout(() => resolve({ links: [...d.querySelectorAll('a.tid')].map(a => a.getAttribute('href')), missing: [...d.querySelectorAll('.tid-missing')].map(a => a.title),
                             text: d.textContent }), 900); })"""


@pytest.fixture
def served(store):
    b = ops.new(store, "Rewrite the first line of the readability ticket", "desk", "task", owner="james", assignee="main")
    a = ops.new(store, f"Fix the card that hid {b.id}", "desk", "bug", owner="james", assignee="main")
    x = store.get(a.id)
    x.meta["status"] = "ready"
    x.meta["smallest_step"] = f"Rewrite {b.id}'s first line so a stranger can find the sentence"
    store.save(x)
    with live_app(create_app(store.home)) as base:
        yield base, a, b


@pytest.mark.parametrize("width", [1280, 390])
def test_browser_next_action_card_says_for_patch_and_links_the_id(served, width):
    """Protects the owner's screenshot (MGMT-0112): the card names Patch, the id in the smallest step is a link with an underline, and the page does not scroll sideways."""
    base, a, b = served
    got = cdp_measure(base, [f"/ticket/{a.id}"], [width], CARD)[f"/ticket/{a.id}@{width}"]
    assert got["who"] == "For Patch" and got["mine"] is False, got
    assert got["href"] == f"/ticket/{b.id}" and got["linkText"] == b.id and "underline" in (got["underline"] or ""), got
    assert got["step"].startswith("Smallest step: Rewrite " + b.id), got
    assert got["sw"] <= got["cw"], got


def test_browser_an_id_in_a_row_is_a_role_link_span_that_follows_its_ticket(served):
    """Protects: the board row is one link, so the id in its title is a span with role=link and tabindex 0; a Ctrl click opens that ticket (not the row's)."""
    base, a, b = served
    path = "/board?project=desk&company=*&open=*&g=*"
    got = cdp_measure(base, [path], [1280], ROW)[f"{path}@1280"]
    assert got and got["role"] == "link" and got["tab"] == 0 and got["text"] == b.id, got
    assert got["href"] == f"/ticket/{b.id}" and got["opened"] == [f"/ticket/{b.id}"], got
    assert got["nestedAnchors"] == 0, got


def test_browser_text_a_script_draws_is_linked_by_the_same_rule(served):
    """Protects: a node added after load is linked by idlinks.js: the id that exists, not the missing one, not the id in code or a URL."""
    base, a, b = served
    expr = TOAST % {"a": b.id, "missing": "ZZZ-9999"}
    got = cdp_measure(base, ["/about"], [1280], expr)["/about@1280"]
    assert got["links"] == [f"/ticket/{b.id}"], got
    assert got["missing"] == ["No such ticket"], got
    assert json.dumps(got["text"]).count(b.id) == 3, got  # the link, the id in code and the id in the URL all keep their words
