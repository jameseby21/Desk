"""DESK-0325 (p0 bug): the Move box warns before the click instead of refusing after it.

Protects: three times in three days James chose a move, typed a reason, clicked Move and got "Stuck needs a name: set blocked_on ..."; the box
knew the rule before the click and said nothing. Standards: "Anything that will cause an error should have caused a warning beforehand."
Now Blocked asks for "Waiting on", Rejected and Parked ask for words, Move stays grey with the reason beside it until they are there, a
refusal from the server gives every typed value back, and `drew move ID STATUS --dry-run` prints the refusal (or "would move") and writes
nothing. Each behaviour is checked by several methods: the function (ops), the web route (HTTP, form and JSON), the command line, the page's
own markup and script in a real headless browser, and a sweep over every form in the templates."""

import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from browser_harness import browser, drive
from fastapi.testclient import TestClient

from desk import ops
from desk.cli import main
from desk.rules import RuleError
from desk.ui import app as app_mod
from desk.ui.app import create_app

JSON = {"Accept": "application/json"}
WORDS = "Waiting for the partner to send the sample file, not before Friday."


def _client(store):
    return TestClient(create_app(store.home), base_url="http://127.0.0.1:8799")


def _mk(store, title="Move me", status="ready", **meta):
    t = ops.new(store, title, "desk")
    t.meta.update({"status": status, "owner": "james", "assignee": "main", **meta})
    if status in ("done", "approved", "rejected"):
        t.append_line("Decision", f'- 2026-11-18 · james · {"approved" if status != "rejected" else status} · "closed by the test"')
    if status == "review":
        t.set_section("Report", "Built it.")
    store.save(t)
    return store.get(t.id)


def _post_move(c, t, to, reason="", waiting_on="", js=True):
    data = {"token": c.app.state.tokens.issue(), "to": to, "reason": reason, "waiting_on": waiting_on}
    return c.post(f"/ticket/{t.id}/move", data=data, headers=JSON if js else {}, follow_redirects=False)


def _bytes(store, tid):
    """Everything on disk that belongs to the ticket: its file and its event log."""
    parts = []
    for p in sorted(store.home.rglob(f"{tid}*")):
        if p.is_file():
            parts.append(p.name.encode() + b"\0" + p.read_bytes())
    return hashlib.sha256(b"\1".join(parts)).hexdigest()


def _drew(store, *argv):
    return main(["--home", str(store.home), *argv])


# -- behaviour 1: Blocked takes the name typed in the box ---------------------------------------------------------------------

def test_ops_blocked_move_takes_the_waiting_on_name(store):
    """Method 1, the function: the name typed in Waiting on becomes blocked_on, in the same save as the status."""
    t = _mk(store)
    ops.move(store, t, "blocked", actor="james", words=WORDS, waiting_on="DESK-0100")
    again = store.get(t.id)
    assert again.status == "blocked" and again.meta["blocked_on"] == "DESK-0100"


def test_web_blocked_move_with_a_waiting_on_goes_through_and_keeps_the_words(store):
    """Method 2, the web route: Blocked with a Waiting on name and a sentence moves, and the sentence is under Comments."""
    t = _mk(store)
    r = _post_move(_client(store), t, "blocked", WORDS, "the partner")
    assert r.status_code == 200 and r.json()["ok"]
    again = store.get(t.id)
    assert again.status == "blocked" and again.meta["blocked_on"] == "the partner" and WORDS in again.section("Comments")


def test_cli_blocked_move_takes_the_status_word_and_waiting_on(store):
    """Method 3, the command line: `drew move ID blocked --waiting-on NAME` (the status as a word, not only --to)."""
    t = _mk(store)
    _drew(store, "move", t.id, "blocked", "--waiting-on", "DESK-0100", "--reason", WORDS, "--as", "james")
    assert store.get(t.id).status == "blocked" and store.get(t.id).meta["blocked_on"] == "DESK-0100"


def test_blocked_without_a_name_is_refused_and_writes_nothing(store):
    """Edge: no name, a name of only spaces, and a secret-looking name are all refused; the file and event log do not change."""
    t = _mk(store)
    before = _bytes(store, t.id)
    c = _client(store)
    for typed in ("", "   ", "sk-ant-api03-" + "a" * 40):
        r = _post_move(c, t, "blocked", WORDS, typed)
        assert r.status_code == 422, typed
    assert _bytes(store, t.id) == before and store.get(t.id).status == "ready"


def test_a_ticket_that_already_names_what_it_waits_on_needs_no_new_name(store):
    """Edge: blocked_on already set (the box pre-fills it), so Move to Blocked with the field left as it was goes through."""
    t = _mk(store, blocked_on="DESK-0001")
    assert ops.move_refusal(store, t, "blocked", reason="") is None
    assert _post_move(_client(store), t, "blocked", "", "").status_code == 200


# -- behaviour 2: a refusal keeps every typed value -------------------------------------------------------------------------------

def test_refused_form_post_sends_back_both_typed_values_in_the_address(store):
    """Method 1, the redirect the no-script page uses: error, reason and waiting_on are all in it."""
    t = _mk(store)
    r = _post_move(_client(store), t, "rejected", "no", "DESK-0100", js=False)
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert r.status_code == 303 and "reason" in q["error"][0].lower() and q["reason"] == ["no"] and q["waiting_on"] == ["DESK-0100"]


def test_refused_form_post_fills_the_boxes_again_on_the_page(store):
    """Method 2, the page that redirect lands on: both inputs carry what was typed."""
    t = _mk(store)
    c = _client(store)
    r = _post_move(c, t, "rejected", "no", "DESK-0100", js=False)
    page = c.get(r.headers["location"]).text
    assert re.search(r'name="reason" value="no"', page) and re.search(r'name="waiting_on"[^>]*value="DESK-0100"', page)


def test_refused_json_post_changes_nothing_and_hands_a_fresh_key(store):
    """Method 3, the in-place path: the page stays as it is (typed values stay on screen), the answer is the sentence plus a new key."""
    t = _mk(store)
    before = _bytes(store, t.id)
    r = _post_move(_client(store), t, "blocked", WORDS, "")
    assert r.status_code == 422 and "Blocked needs a name" in r.json()["error"] and r.json()["token"]
    assert _bytes(store, t.id) == before


def test_page_does_not_echo_values_after_a_successful_move(store):
    """Edge: the typed values come back only with an error, never after a move that worked."""
    t = _mk(store)
    c = _client(store)
    ok = _post_move(c, t, "blocked", WORDS, "DESK-0100", js=False)
    assert "waiting_on=" not in ok.headers["location"] and "error" not in ok.headers["location"]


# -- behaviour 3: the box shows what the move needs ---------------------------------------------------------------------------------

def _options(page):
    return {m.group(1): m.group(0) for m in re.finditer(r'<option value="([^"]*)"[^>]*>', page)}


def test_move_box_markup_names_the_field_each_move_needs(store):
    """Method 1, the page's markup: Blocked needs waiting_on; Rejected and Parked need reason; Move starts with no destination chosen."""
    t = _mk(store)
    page = _client(store).get(f"/ticket/{t.id}").text
    opts = _options(page)
    assert 'data-need="waiting_on"' in opts["blocked"] and 'data-need="reason"' in opts["rejected"] and 'data-need="reason"' in opts["parked"]
    assert "data-need" not in opts["triage"] and "Choose where to move it" in opts[""] + page
    assert 'name="waiting_on"' in page and 'class="btn mv-go"' in page and "mv-why" in page


def test_parked_is_not_offered_to_a_ticket_that_is_parked_already(store):
    """Edge: no 'Parked' choice on a parked ticket (it would be refused as no change)."""
    t = _mk(store, lane="parked", park_reason="not this quarter")
    assert "parked" not in _options(_client(store).get(f"/ticket/{t.id}").text)


def test_the_waiting_on_box_is_filled_with_what_the_ticket_already_names(store):
    """Edge: a blocked ticket's page shows its blocked_on in the box, so unblock and re-block never lose it."""
    t = _mk(store, status="blocked", blocked_on="DESK-0001", blocked_from="ready")
    page = _client(store).get(f"/ticket/{t.id}").text
    assert re.search(r'name="waiting_on"[^>]*value="DESK-0001"', page)


@pytest.mark.skipif(browser() is None, reason="no headless browser on this machine")
def test_in_a_real_browser_move_stays_grey_until_the_field_is_filled(store):
    """Method 2, the page's own script in a real browser: choose Blocked, the Waiting on field appears and Move is grey with the reason
    beside it; fill it and Move turns on; Rejected and Parked wait for four characters of words; with no destination Move is grey."""
    t = _mk(store)
    script = """<script>window.addEventListener("load", function () {
      var f = document.querySelector("form.mv-form"), sel = f.querySelector(".mv-to"), go = f.querySelector(".mv-go");
      var wait = f.querySelector(".mv-wait input"), words = f.querySelector(".mv-words input"), why = f.querySelector(".mv-why");
      var out = {}; function snap(k) { out[k] = { off: go.disabled, waitHidden: f.querySelector(".mv-wait").hidden, why: why.textContent }; }
      function pick(v) { sel.value = v; sel.dispatchEvent(new Event("change", { bubbles: true })); }
      function type(el, v) { el.value = v; el.dispatchEvent(new Event("input", { bubbles: true })); }
      snap("start");
      pick("blocked"); snap("blocked_empty");
      type(wait, "DESK-0100"); snap("blocked_filled");
      pick("rejected"); snap("rejected_empty");
      type(words, "no"); snap("rejected_short");
      type(words, "Not needed any more."); snap("rejected_filled");
      pick("parked"); type(words, ""); snap("parked_empty");
      pick("triage"); snap("triage");
      fetch("/m", { method: "POST", body: JSON.stringify(out) });
    });</script>"""
    got = drive(store, script, f"/ticket/{t.id}")
    assert got["start"]["off"] and "Choose where" in got["start"]["why"]
    assert got["blocked_empty"]["off"] and not got["blocked_empty"]["waitHidden"] and "Blocked needs a name" in got["blocked_empty"]["why"]
    assert not got["blocked_filled"]["off"]
    assert got["rejected_empty"]["off"] and got["rejected_empty"]["waitHidden"] and got["rejected_short"]["off"]
    assert not got["rejected_filled"]["off"]
    assert got["parked_empty"]["off"] and "Parking needs" in got["parked_empty"]["why"]
    assert not got["triage"]["off"] and got["triage"]["waitHidden"]


# -- behaviour 4: drew move ID STATUS --dry-run -----------------------------------------------------------------------------------

def test_dry_run_prints_the_refusal_and_writes_nothing(store, capsys):
    """Method 1, the command line: a refused move prints 'Refused: <the rule's own sentence>', exits 1, and the files are untouched."""
    t = _mk(store)
    before = _bytes(store, t.id)
    with pytest.raises(SystemExit) as e:
        _drew(store, "move", t.id, "blocked", "--dry-run")
    assert e.value.code == 1
    out = capsys.readouterr().out
    assert out.startswith("Refused: ") and "Blocked needs a name" in out
    assert _bytes(store, t.id) == before


def test_dry_run_prints_would_move_and_writes_nothing(store, capsys):
    """Method 2, the command line: a move that would pass prints 'would move', and still writes nothing (not the status, not a comment)."""
    t = _mk(store)
    before = _bytes(store, t.id)
    _drew(store, "move", t.id, "blocked", "--waiting-on", "DESK-0100", "--reason", WORDS, "--dry-run")
    assert "would move ready -> blocked" in capsys.readouterr().out
    assert _bytes(store, t.id) == before and store.get(t.id).status == "ready"


REFUSALS = [
    ("blocked with no name", dict(), "blocked", {}),
    ("rejected with a short reason", dict(), "rejected", {"reason": "no"}),
    ("a status the ticket already has", dict(), "ready", {}),
    ("a closed ticket", dict(status="done"), "triage", {}),
    ("an unknown status", dict(), "somewhere", {}),
    ("a move the rules never allow", dict(), "done", {}),
    ("parked with no reason", dict(), "parked", {}),
    ("unblock on a ticket that is not blocked", dict(), "unblock", {}),
    ("a secret-looking sentence", dict(), "triage", {"reason": "key sk-ant-api03-" + "a" * 40}),
    ("approved with no words", dict(status="review"), "approved", {}),
]
PASSES = [
    ("blocked with a name", dict(), "blocked", {"waiting_on": "DESK-0100"}),
    ("rejected with a reason", dict(), "rejected", {"reason": "Not needed any more."}),
    ("triage", dict(), "triage", {}),
    ("parked with a reason", dict(), "parked", {"reason": "not this quarter"}),
    ("unblock of a blocked ticket", dict(status="blocked", blocked_on="DESK-0001", blocked_from="ready"), "unblock", {}),
]


@pytest.mark.parametrize("name,meta,to,typed", REFUSALS + PASSES, ids=[c[0] for c in REFUSALS + PASSES])
def test_the_dry_run_agrees_with_the_real_move(store, name, meta, to, typed):
    """Method 3, the two doors compared: for every case the dry run says exactly what the real move then does (the same sentence on a
    refusal; no refusal when it goes through), and the dry run never changes the file."""
    t = _mk(store, **meta)
    before = _bytes(store, t.id)
    said = ops.move_refusal(store, t, to, actor="james", reason=typed.get("reason"), waiting_on=typed.get("waiting_on"))
    assert _bytes(store, t.id) == before, "the dry run wrote something"
    try:
        ops.move_any(store, store.get(t.id), to, actor="james", reason=typed.get("reason"), waiting_on=typed.get("waiting_on"))
        real = None
    except (RuleError, ValueError) as e:
        real = str(e)
    assert said == real, (said, real)


def test_dry_run_accepts_the_to_flag_and_refuses_two_different_places(store, capsys):
    """Edge: `--to X` still works as before, the same as the status word; naming two different places is an error, not a guess."""
    t = _mk(store)
    _drew(store, "move", t.id, "--to", "triage", "--dry-run")
    assert "would move ready -> triage" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        _drew(store, "move", t.id, "triage", "--to", "claimed", "--dry-run")


def test_dry_run_on_a_missing_ticket_says_so(store, capsys):
    """Edge: a typo in the id is a plain message, not a trace."""
    with pytest.raises(SystemExit):
        _drew(store, "move", "DESK-9999", "triage", "--dry-run")
    assert "DESK-9999" in capsys.readouterr().err


# -- behaviour 5: the Decide page and the ticket's own answer box warn first, too ---------------------------------------------------

def _decide_page(store, title="Approve: the rule"):
    t = ops.new(store, title, "jre", "decision")
    t.set_section("Context", "Why this exists.\n\nPlain context.")
    t.set_section("Report", "Built it.")
    t.meta.update({"owner": "james", "status": "review"})
    store.save(t)
    return _client(store).get("/decide").text


def test_decide_page_buttons_that_need_words_say_so_in_their_markup(store):
    """Method 1, markup: with nothing recommended, Approve and Send back need words and carry data-needs, so the page can grey them."""
    page = _decide_page(store)
    assert re.search(r'value="approve" data-dm-key="y" data-needs="words"', page)
    assert re.search(r'value="changes" data-dm-key="n" data-needs="words"', page)
    assert 'value="snooze"' in page and not re.search(r'value="snooze"[^>]*data-needs', page)


@pytest.mark.skipif(browser() is None, reason="no headless browser on this machine")
def test_decide_page_buttons_wait_for_words_in_a_real_browser(store):
    """Method 2, a real browser: Approve and Send back are grey with the reason beside them until four characters are typed; Snooze is never grey."""
    _decide_page(store)
    script = """<script>window.addEventListener("load", function () {
      var f = document.getElementById("dm-form"), w = f.querySelector('input[name="words"]');
      var a = f.querySelector('button[value="approve"]'), n = f.querySelector('button[value="changes"]'), s = f.querySelector('button[value="snooze"]');
      var out = { before: [a.disabled, n.disabled, s.disabled], why: f.querySelector("[data-why]").textContent };
      w.value = "Yes, as written."; w.dispatchEvent(new Event("input", { bubbles: true }));
      out.after = [a.disabled, n.disabled, s.disabled]; out.whyAfter = f.querySelector("[data-why]").textContent;
      fetch("/m", { method: "POST", body: JSON.stringify(out) });
    });</script>"""
    got = drive(store, script, "/decide")
    assert got["before"] == [True, True, False] and "Type your words" in got["why"]
    assert got["after"] == [False, False, False] and got["whyAfter"] == ""


# -- the sweep: every form in Desk -----------------------------------------------------------------------------------------------

TEMPLATES = Path(app_mod.__file__).parent / "templates"
_FORM = re.compile(r"<form\b(.*?)</form>", re.S | re.I)


def _forms():
    """Every POST form in every template: (file, action with the template parts turned into *, the form's source)."""
    found = []
    for path in sorted(TEMPLATES.glob("*.html")):
        for m in _FORM.finditer(path.read_text(encoding="utf-8")):
            src = m.group(0)
            head = re.match(r"<form\b[^>]*>", src, re.S).group(0)
            if not re.search(r'method="post"', head, re.I):
                continue
            action = re.sub(r"\{\{.*?\}\}", "*", re.search(r'action="([^"]*)"', head).group(1))
            found.append((path.name, action, src))
    return found


def _tag(src, name):
    """The opening tags (input, textarea or select) of the field called `name` in a form's source."""
    return re.findall(rf'<(?:input|textarea|select)\b[^>]*name="{name}"[^>]*>', src)


def _required(src, name):
    return any(re.search(r"\brequired\b", t) for t in _tag(src, name))


# Forms whose route refuses a missing value, and how the form warns first. Every other POST form must be listed in NEVER_REFUSES.
#   "required": the field carries the browser's own `required`; "mv-form": the Move box script; "needs": a button marked data-needs.
CONTRACTS = {
    "/ticket/*/move": ("mv-form", None),
    "/ticket/*/refine": ("required", "line"),
    "/ticket/*/split": ("required", "title"),
    "/decide/delegate": ("required", "pattern"),
    "/decide/answer": ("needs", None),
}
# POST forms that cannot be refused for a missing value, with the reason. A new form must be put in one table or the other (the sweep fails
# otherwise), which is the point: nobody adds a form without deciding whether it can refuse.
NEVER_REFUSES = {
    "/automations/undo": "a button only",
    "/automations/run": "a button only",
    "/capture": "own script (capture.js) greys the button until there is text or a file",
    "/capture/paste": "own script; an empty paste lists nothing and says so",
    "/capture/paste/create": "rows are pre-filled; a row with no title is skipped and named",
    "/decide/batch": "ids are hidden fields filled by the server",
    "/new": "novalidate; newform.js shows what is missing next to each field before Save (DESK-0252)",
    "/settings": "radios with a default",
    "/settings/display": "radios with a default",
    "/settings/motivation": "radios with a default",
    "/settings/delegations/restore": "a button only",
    "/settings/delegations/remove": "a button only",
    "/nav": "a pick list, no free text",
    "/ticket/*/decide": "the Your call box: its words field is `required` (checked below); the reply form's option buttons need no words",
    "/ticket/*/comment": "composer.js keeps Post grey while the text is empty (checked in test_composer)",
    "/ticket/*/attach": "a file picker; an empty pick is ignored",
    "/ticket/*/assign": "a pick list, no free text",
    "/ticket/*/lane": "the Park form's reason is `required` (checked below); the other lane forms are buttons",
    "/triage/place": "the park reason has a default ('Parked from inbox triage.')",
    "/lanes/view": "buttons and radios",
    "/needs-me/seen": "a button only",
    "/views": "a name box that falls back to the page's own name",
    "/views/remove": "a button only",
    "/whoami": "a pick list or a typed name; an empty name is refused in the page's own words and nothing is written",
    "*": "the undo page's retry button (its action is filled in by the server)",
    "/undo/batch/*": "a button only",
    "/undo/event/*/*": "a button only",
}


def _contract_for(action):
    for pat, c in CONTRACTS.items():
        if re.fullmatch(pat.replace("*", "[^/]*"), action) or action == pat:
            return c
    return None


def test_the_sweep_finds_the_forms_and_every_one_is_classified():
    """Method 1, the sweep: every POST form in every template is either in CONTRACTS (its route refuses something the form must ask for)
    or in NEVER_REFUSES with the reason. An unclassified form fails, so a new form cannot be added without deciding."""
    forms = _forms()
    assert len(forms) >= 25, f"the sweep found only {len(forms)} forms; the scan is broken"
    unknown = sorted({a for _, a, _ in forms if _contract_for(a) is None and not any(
        re.fullmatch(p.replace("*", "[^/]*"), a) or a == p for p in NEVER_REFUSES)})
    assert not unknown, f"POST forms nobody decided about (add each to CONTRACTS or NEVER_REFUSES): {unknown}"


def test_the_sweep_every_contract_form_warns_before_the_click():
    """Method 2, the sweep: each form whose route refuses a missing value warns first: the field is `required`, or the Move box script,
    or the button is marked data-needs. Also: the Your call box and the Park form, which the table above names by hand."""
    problems = []
    seen = set()
    for name, action, src in _forms():
        kind = _contract_for(action)
        if kind is not None:
            seen.add(action)
            how, field = kind
            if how == "required" and not _required(src, field):
                problems.append(f"{name} {action}: `{field}` is not required")
            if how == "mv-form" and not ('class="post-once mv-form"' in src and "data-need=" in src and "mv-go" in src):
                problems.append(f"{name} {action}: the Move box has no warn-first markup")
            if how == "needs" and "data-needs=" not in src:
                problems.append(f"{name} {action}: no button is marked data-needs")
        if action == "/ticket/*/decide" and 'name="reply"' not in src and not _required(src, "words"):
            problems.append(f"{name} {action}: the Your call words box is not required")
        if action == "/ticket/*/lane" and 'value="parked"' in src and 'name="lane"' in src and not _required(src, "reason"):
            problems.append(f"{name} {action}: the Park reason is not required")
    assert not problems, problems
    assert seen == {"/ticket/*/move", "/ticket/*/refine", "/ticket/*/split", "/decide/delegate", "/decide/answer"}, f"contract forms not found: {seen}"


def test_the_sweep_the_server_really_refuses_what_each_form_warns_about(store):
    """Method 3, the sweep against the routes: for every contract the form warns about, posting the missing value really is refused (so the
    table is not a guess), and posting it filled is accepted. If a route stops refusing, the warning is dead weight and this says so."""
    c = _client(store)
    t = _mk(store, title="Sweep target", status="triage")

    def post(path, **data):
        return c.post(path, data={"token": c.app.state.tokens.issue(), **data}, headers=JSON, follow_redirects=False)

    refused = {
        "move blocked": post(f"/ticket/{t.id}/move", to="blocked"),
        "move rejected": post(f"/ticket/{t.id}/move", to="rejected", reason="no"),
        "move parked": post(f"/ticket/{t.id}/move", to="parked", reason=""),
        "refine": post(f"/ticket/{t.id}/refine", line=""),
        "split": post(f"/ticket/{t.id}/split", title="", details=""),
        "decide words": post(f"/ticket/{t.id}/decide", action="approve", words=""),
        "lane parked": post(f"/ticket/{t.id}/lane", lane="parked", reason=""),
    }
    for what, r in refused.items():
        assert r.status_code >= 400, (what, r.status_code, r.text[:200])
    assert store.get(t.id).status == "triage" and not store.get(t.id).meta.get("lane")
    assert post(f"/ticket/{t.id}/move", to="parked", reason="not this quarter").status_code == 200


# -- the fix round (checker's defects): grey at first paint, not only after the first keystroke -----------------------------------------
# Protects: base.html's pageshow handler used to switch every post-once submit button back on after load, undoing warnfirst.js, so Move
# and the Decide buttons were clickable at first paint and the click gave the red 422 box this ticket was filed against.

def _base_html():
    return (TEMPLATES / "base.html").read_text(encoding="utf-8")


def test_the_pageshow_handler_only_restores_a_form_that_was_mid_send():
    """Method 1, the source: the handler in base.html restores buttons only on a form carrying data-sending, so it can never switch on a
    button that warnfirst.js greyed."""
    m = re.search(r'addEventListener\("pageshow", function \(\) \{(.*?)\n\}\);', _base_html(), re.S)
    assert m, "the pageshow handler is gone from base.html"
    body = m.group(1)
    assert 'getAttribute("data-sending")' in body and body.index('getAttribute("data-sending")') < body.index("disabled = false")
    js = (Path(app_mod.__file__).parent / "static" / "warnfirst.js").read_text(encoding="utf-8")
    assert 'addEventListener("pageshow"' in js, "warnfirst.js does not run the check again when a page is shown from the back button"


@pytest.mark.skipif(browser() is None, reason="no headless browser on this machine")
def test_in_a_real_browser_move_is_grey_at_first_paint_and_stays_grey_after_the_page_settles(store):
    """Method 2, a real browser, read 600 ms AFTER load (the earlier test read inside the load event, before pageshow ran): with no
    destination chosen Move is grey, the form is marked as waiting, and the reason is beside it."""
    t = _mk(store)
    script = """<script>window.addEventListener("load", function () { setTimeout(function () {
      var f = document.querySelector("form.mv-form"), go = f.querySelector(".mv-go");
      fetch("/m", { method: "POST", body: JSON.stringify({ off: go.disabled, waiting: f.classList.contains("mv-waiting"), why: f.querySelector(".mv-why").textContent }) });
    }, 600); });</script>"""
    got = drive(store, script, f"/ticket/{t.id}", width=390)
    assert got["off"] and got["waiting"] and "Choose where" in got["why"], got


@pytest.mark.skipif(browser() is None, reason="no headless browser on this machine")
def test_in_a_real_browser_decide_buttons_are_grey_at_first_paint_and_stay_grey_after_the_page_settles(store):
    """Method 3, a real browser on the Decide page, read 600 ms after load: Approve and Send back are grey, Snooze is on, the reason shows."""
    _decide_page(store)
    script = """<script>window.addEventListener("load", function () { setTimeout(function () {
      var f = document.getElementById("dm-form");
      var d = function (v) { return f.querySelector('button[value="' + v + '"]').disabled; };
      fetch("/m", { method: "POST", body: JSON.stringify({ a: d("approve"), n: d("changes"), s: d("snooze"), why: f.querySelector("[data-why]").textContent }) });
    }, 600); });</script>"""
    got = drive(store, script, "/decide", width=390)
    assert got["a"] and got["n"] and not got["s"] and "Type your words" in got["why"], got


@pytest.mark.skipif(browser() is None, reason="no headless browser on this machine")
def test_in_a_real_browser_a_page_shown_again_from_the_back_button_keeps_the_grey_and_frees_a_stuck_send(store):
    """Edge, the back button (a page restored from the browser's cache fires pageshow with persisted true): a grey Move stays grey, and a
    form that was mid-send gets its button and label back."""
    t = _mk(store)
    script = """<script>window.addEventListener("load", function () { setTimeout(function () {
      var f = document.querySelector("form.mv-form"), go = f.querySelector(".mv-go");
      window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));
      var grey = go.disabled;
      var c = document.querySelector("form.chk, form.post-once:not(.mv-form)");
      var stuck = null;
      if (c) { var b = c.querySelector("button[type=submit]"); if (b) { c.setAttribute("data-sending", "1"); b.setAttribute("data-label", "Post"); b.textContent = "Sending..."; b.disabled = true;
        window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));
        stuck = { off: b.disabled, label: b.textContent, sending: c.hasAttribute("data-sending") }; } }
      fetch("/m", { method: "POST", body: JSON.stringify({ grey: grey, stuck: stuck }) });
    }, 400); });</script>"""
    got = drive(store, script, f"/ticket/{t.id}", width=390)
    assert got["grey"], got
    if got["stuck"] is not None:
        assert got["stuck"] == {"off": False, "label": "Post", "sending": False}, got


# -- the fix round: a refused move never puts a secret in the address bar (rule 80 item 2) --------------------------------------------
# Protects: the no-script refusal used to put the typed words and the Waiting on text into the redirect address, so a secret pasted into
# the box landed in the address bar, the browser history and any access log, although the move itself wrote nothing.

SECRET = "ghp_abcdefghijklmnopqrstuvwxyz0123456789AB"


def test_a_refused_form_post_with_a_secret_in_the_reason_keeps_it_out_of_the_address(store):
    """Method 1, the redirect: the reason is a secret, the refusal is the secret refusal, and the address carries neither the value nor a reason key."""
    t = _mk(store)
    r = _post_move(_client(store), t, "blocked", SECRET, "", js=False)
    loc = r.headers["location"]
    assert r.status_code == 303 and "error=" in loc and SECRET not in loc and "reason=" not in loc


def test_a_refused_form_post_with_a_secret_in_waiting_on_keeps_it_out_of_the_address(store):
    """Method 2, the other box: a secret typed in Waiting on is not echoed either."""
    t = _mk(store)
    r = _post_move(_client(store), t, "blocked", WORDS, "sk-ant-api03-" + "a" * 40, js=False)
    loc = r.headers["location"]
    assert r.status_code == 303 and "sk-ant" not in loc and "waiting_on=" not in loc


def test_the_page_that_a_secret_refusal_lands_on_never_shows_the_secret(store):
    """Method 3, the page the redirect lands on: the secret is in no input, anywhere in the page, and the secret's kind is named; the
    ticket on disk is unchanged."""
    t = _mk(store)
    before = _bytes(store, t.id)
    c = _client(store)
    r = _post_move(c, t, "blocked", "key is " + SECRET, "DESK-0100", js=False)
    assert _bytes(store, t.id) == before  # the refused post wrote nothing (read before the page is opened)
    page = c.get(r.headers["location"]).text
    assert SECRET not in page and "looks like" in page


def test_a_plain_refusal_still_gives_the_typed_words_back(store):
    """Edge: only a secret is held back; ordinary words (and a commit hash, which is not a secret) still come back so nothing typed is lost."""
    t = _mk(store)
    r = _post_move(_client(store), t, "blocked", "see commit 3f2a9c1b7d4e5a60", "", js=False)
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["reason"] == ["see commit 3f2a9c1b7d4e5a60"]


# -- the fix round: the refusal names the way out where the person is, and an unblock writes one line -----------------------------------

def test_the_rule_sentence_for_blocked_is_plain_and_does_not_name_a_file_field(store):
    """Method 1, the rule: the sentence says which ticket or person, not 'set blocked_on' (a field name nobody types in a box)."""
    t = _mk(store)
    with pytest.raises(RuleError) as e:
        ops.move(store, t, "blocked", actor="james")
    assert "blocked_on" not in str(e.value) and "which ticket or person" in str(e.value)


def test_cli_blocked_refusal_names_the_waiting_on_option(store, capsys):
    """Method 2, the command line, both the dry run and the real move: the refusal ends with the option that fixes it."""
    t = _mk(store)
    with pytest.raises(SystemExit):
        _drew(store, "move", t.id, "blocked", "--dry-run")
    assert "--waiting-on" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        _drew(store, "move", t.id, "blocked", "--as", "james")
    assert "--waiting-on" in capsys.readouterr().err
    assert store.get(t.id).status == "ready"


def test_web_blocked_refusal_does_not_mention_the_command_line(store):
    """Edge: the page's refusal points at its own Waiting on box and never prints a command-line option."""
    t = _mk(store)
    r = _post_move(_client(store), t, "blocked", WORDS, "")
    assert r.status_code == 422 and "--waiting-on" not in r.json()["error"]


def test_unblock_with_words_writes_one_comment_line_not_two(store):
    """Method 1, the file: `drew move ID unblock --reason WORDS` leaves one line under Comments (the words, with where it went)."""
    t = _mk(store, status="blocked", blocked_on="DESK-0001", blocked_from="ready")
    _drew(store, "move", t.id, "unblock", "--reason", "The partner sent the file.", "--as", "james")
    lines = [ln for ln in store.get(t.id).section("Comments").splitlines() if "unblocked" in ln]
    assert len(lines) == 1 and "The partner sent the file." in lines[0], lines


def test_unblock_with_no_words_still_leaves_one_line_saying_where_it_went(store):
    """Edge: with an empty box nothing the person typed exists, so Desk's own plain line is the one record."""
    t = _mk(store, status="blocked", blocked_on="DESK-0001", blocked_from="ready")
    _drew(store, "move", t.id, "unblock", "--as", "james")
    lines = [ln for ln in store.get(t.id).section("Comments").splitlines() if "unblocked" in ln]
    assert len(lines) == 1, lines
    assert store.get(t.id).status == "ready" and not store.get(t.id).meta.get("blocked_on")


def test_unblock_through_the_web_box_writes_one_line_too(store):
    """Method 3, the page's route: the same single line."""
    t = _mk(store, status="blocked", blocked_on="DESK-0001", blocked_from="ready")
    r = _post_move(_client(store), t, "unblock", "Got the file.")
    assert r.status_code == 200
    lines = [ln for ln in store.get(t.id).section("Comments").splitlines() if "unblocked" in ln]
    assert len(lines) == 1 and "Got the file." in lines[0], lines
