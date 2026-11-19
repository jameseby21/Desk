"""DESK-0305 and DESK-0306 polish: seven small defects found by the owner and by Chaz on the first build of the visual timeline.

(a) The header's "N of 5 decided today" stayed stale after Undo. (b) The Decide box's grey hint "Empty means: Agree with the
recommendation" was cut off on a phone. (c) The Undo toast sat on top of the buttons of the page at 375 px. (d) A date on the Undo
log broke at its hyphen. (e) The timeline's Ready and Changes requested colours were also the colours of the people who made the
changes, so one colour meant two things. (f) The Latest line and the rail printed the raw key "claude-code" beside "Code session".

Each behaviour is checked by at least three methods: a read of the source or stylesheet, a route or function call, and a real
headless browser (phone widths by Chrome DevTools Protocol device metrics, never a small --window-size), plus the edge cases."""

import datetime as dt
import re
from pathlib import Path

import pytest
from browser_harness import browser, cdp_measure, drive, live_app
from fastapi.testclient import TestClient

from desk import activity, identity, motivation as mot, ops, people, timeline
from desk.cli import main
from desk.ui.app import STATIC, create_app

HERE = "http://127.0.0.1:8799"
JSON = {"Accept": "application/json"}
CHOOSE = "Options: A lime mark, it leaves the rotation; B a second candidate is drawn and rotates."
NEEDS_BROWSER = pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")


def _src(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def _owner(store, monkeypatch):
    """A browser on the owner's machine: the OS user is linked to the owner, so web writes are the confirmed owner's."""
    monkeypatch.setattr(identity, "os_user", lambda: "owner-login")
    people.link_os_user(store.home, "james", "owner-login")
    return TestClient(create_app(store.home), base_url=HERE)


def _review(store, title="Approve: the plan"):
    t = ops.new(store, title, "desk", "feature")
    t.meta.update({"status": "review", "owner": "james", "assignee": "main"})
    t.set_section("Report", "Built and tested.")
    store.save(t)
    return store.get(t.id)


def _rec_ask(store):
    """A decision ticket whose worker recommended option A: its Decide page shows the "Empty means" hint."""
    t = ops.new(store, "Choose: is the lime mark final?", "desk", "decision")
    t.set_section("Context", "Why this exists.\n\n" + CHOOSE)
    t.set_section("Report", "Chaz recommends: A; it is the most-used mark.")
    t.meta.update({"owner": "james", "status": "ready"})
    store.save(t)
    return store.get(t.id)


# ======================================================= (a) the decided-today count after Undo

def test_a_static_undo_announces_every_successful_undo_and_the_header_listens():
    """Protects (a) by a read of the source: undo.js dispatches desk:undone on both success paths (the toast's Undo and the /undo
    page's buttons) and motivation.js listens for it, after the early return for a page with no header."""
    undo, motiv = _src("undo.js"), _src("motivation.js")
    assert 'new CustomEvent("desk:undone"' in undo
    assert undo.count("announce(") >= 4, "the toast's Undo, the /undo page's buttons and its Undo anyway must each announce a success"
    assert 'addEventListener("desk:undone"' in motiv
    assert motiv.index('if (!root) return;') < motiv.index('addEventListener("desk:undone"')


_COUNT_ON_EVENT = """<script>addEventListener('load',function(){var n=0,of=window.fetch;
window.fetch=function(u){if(String(u).indexOf('/motivation/state.json')>=0)n++;return of.apply(this,arguments);};
setTimeout(function(){var n0=n;document.dispatchEvent(new CustomEvent('desk:undone'));
 setTimeout(function(){fetch('/m',{method:'POST',body:JSON.stringify({n0:n0,n1:n})});},700);},1500);});</script>"""


@NEEDS_BROWSER
@pytest.mark.parametrize("count,celebrate,asked", [(True, True, 1), (True, False, 1), (False, True, 1), (False, False, 0)],
                         ids=["both-on", "count-only", "celebrate-only", "both-off"])
def test_a_browser_the_header_asks_for_the_count_again_when_an_undo_is_announced(store, count, celebrate, asked):
    """Protects (a) at run time, apart from undo.js: a desk:undone event on the page makes motivation.js read the count once more, and
    reads nothing when both the count and the celebration are switched off (no needless request)."""
    mot.save(store.home, focus=True, focus_minutes=25, goal=5, count=count, celebrate=celebrate)
    t = _review(store)
    got = drive(store, _COUNT_ON_EVENT, f"/ticket/{t.id}")
    assert got["n1"] - got["n0"] == asked, got


_UNDO_THEN_READ = """<script>addEventListener('load',function(){var sleep=function(ms){return new Promise(function(r){setTimeout(r,ms);});};
(async function(){var out={};try{await sleep(1500);var c=document.getElementById('mot-count');out.before=c?c.textContent:null;window.__same=1;
 document.dispatchEvent(new KeyboardEvent('keydown',{key:'z',bubbles:true}));
 for(var i=0;i<80;i++){await sleep(100);var t=document.querySelector('.undo-toast');if(t&&/Undone/.test(t.textContent))break;}
 await sleep(1200);out.after=c?c.textContent:null;var t2=document.querySelector('.undo-toast');out.toast=t2?t2.textContent:'';out.same=window.__same===1;}catch(e){out.error=String(e);}
 fetch('/m',{method:'POST',body:JSON.stringify(out)});})();});</script>"""


@NEEDS_BROWSER
def test_a_browser_approve_then_undo_takes_the_header_count_back_down(store, monkeypatch):
    """Protects (a) end to end: one approval counts 1 in the header; pressing Z on its Undo toast puts the ticket back, and the header
    then reads 0 of 5 without a reload (before the fix it stayed at 1 of 5 until the next page)."""
    c = _owner(store, monkeypatch)
    t = _review(store)
    r = c.post(f"/ticket/{t.id}/decide", data={"token": c.app.state.tokens.issue(), "action": "approve", "words": "Yes, go."}, headers=JSON)
    batch = r.headers["X-Desk-Undo"]
    assert store.get(t.id).status == "approved"
    got = drive(store, _UNDO_THEN_READ, f"/decide?undo={batch}&msg=Approved.")  # a page that does not reload by itself when a ticket changes
    assert got.get("error") is None and got["same"], got  # the page was never reloaded, so a fresh count can only come from the header itself
    assert got["before"] == "1 of 5 decided today", got
    assert got["toast"].startswith("Undone") or "Undone" in got["toast"] or "Redo" in got["toast"], got
    assert store.get(t.id).status == "review"
    assert got["after"] == "0 of 5 decided today", got


# ======================================================= (b) the Decide hint at phone width

def _hint(page: str) -> str:
    m = re.search(r'<input type="text" name="words"[^>]*placeholder="([^"]*)"', page)
    assert m, "the words box has no hint"
    return m.group(1)


def test_b_route_the_hint_with_a_recommendation_is_short_and_plain(store):
    """Protects (b) by the rendered page: the hint beside a recommendation is under 28 characters and no longer repeats the whole
    sentence 'Agree with the recommendation' (which is already the Y button's name)."""
    _rec_ask(store)
    hint = _hint(TestClient(create_app(store.home)).get("/decide").text)
    assert "Empty means" in hint or "empty" in hint.lower(), hint
    assert len(hint) <= 28 and "recommendation" not in hint.lower(), hint


def test_b_static_the_template_no_longer_builds_the_hint_from_the_long_sentence():
    """Protects (b) by a read of the template: the placeholder does not interpolate the agree sentence."""
    tpl = (STATIC.parent / "templates" / "decide.html").read_text(encoding="utf-8")
    box = tpl[tpl.index('name="words"'):].split(">", 1)[0]
    assert "{{ agree }}" not in box, box


_FIT = """(function(){var inp=document.querySelector('.dm-words input[name=words]');if(!inp)return null;var cs=getComputedStyle(inp);
var avail=inp.clientWidth-parseFloat(cs.paddingLeft)-parseFloat(cs.paddingRight);
var c=document.createElement('canvas').getContext('2d');c.font=cs.font;
var s=document.createElement('span');s.style.cssText='position:absolute;visibility:hidden;white-space:nowrap;font:'+cs.font+';letter-spacing:'+cs.letterSpacing;
s.textContent=inp.placeholder;document.body.appendChild(s);var w2=s.getBoundingClientRect().width;s.remove();
return {ph:inp.placeholder,avail:avail,canvas:c.measureText(inp.placeholder).width,span:w2,sw:document.documentElement.scrollWidth,cw:document.documentElement.clientWidth};})()"""


def _provide_ask(store):
    """A decision ticket that asks the owner to provide something: its box carries the other long hint."""
    t = ops.new(store, "Provide: the domain name", "desk", "decision")
    t.set_section("Context", "Why this exists.\n\nWe need the domain name chosen.")
    t.meta.update({"owner": "james", "status": "ready"})
    store.save(t)
    return store.get(t.id)


@NEEDS_BROWSER
@pytest.mark.parametrize("make", [_rec_ask, _provide_ask], ids=["agree-hint", "provide-hint"])
def test_b_browser_the_hint_fits_inside_the_box_at_320_375_and_390(store, make):
    """Protects (b) in a real layout, two ways to measure the text (a canvas and a hidden span): at 320, 375 and 390 px the whole hint
    (the agree hint, and the neighbouring Provide hint that was cut off the same way) is narrower than the room inside the box, so
    nothing is cut off; the page does not scroll sideways."""
    make(store)
    with live_app(create_app(store.home)) as base:
        got = cdp_measure(base, ["/decide"], [320, 375, 390], _FIT)
    for key, d in got.items():
        assert d and d["ph"], (key, d)
        assert d["canvas"] <= d["avail"] and d["span"] <= d["avail"] + 0.5, (key, d)
        assert d["sw"] <= d["cw"], (key, d)


# ======================================================= (c) the Undo toast and the buttons beneath it

def test_c_static_on_a_phone_the_toast_lets_taps_through_but_keeps_its_own_buttons():
    """Protects (c) by a read of the stylesheet: under 600 px the toast itself ignores pointers (a tap lands on the button beneath it),
    while its Undo, Log and close controls still take them."""
    css = re.sub(r"/\*.*?\*/", "", _src("undo.css"), flags=re.S)
    phone = "".join(css[m.end():].split(chr(10), 1)[0] for m in re.finditer(r"@media \(max-width:600px\)\{", css))  # each phone rule is on one line
    assert re.search(r"\.undo-toast\s*\{[^}]*pointer-events:none", phone), phone[:400]
    assert re.search(r"\.undo-toast[^{]*\.undo-actions[^{]*\{[^}]*pointer-events:auto", phone), phone[:600]


def test_c_static_the_next_action_closes_the_toast_on_a_phone_only():
    """Protects (c) by a read of the script: a press or a form send outside the toast closes it, only when the window is 600 px or
    narrower, never while an undo is running."""
    js = _src("undo.js")
    assert 'addEventListener("pointerdown"' in js and "max-width: 600px" in js
    assert re.search(r'addEventListener\("submit", nextAction', js)


_TOAST = """(async function(){var sleep=function(ms){return new Promise(function(r){setTimeout(r,ms);});};
var t=document.querySelector('.undo-toast');if(!t)return {toast:false};
var tx=t.querySelector('.undo-text').getBoundingClientRect(),ub=t.querySelector('button.ball').getBoundingClientRect();
var h1=document.elementFromPoint(tx.left+tx.width/2,tx.top+tx.height/2),h2=document.elementFromPoint(ub.left+ub.width/2,ub.top+ub.height/2);
var out={toast:true,textBlocks:!!(h1&&h1.closest('.undo-toast')),undoTakesTap:!!(h2&&h2.closest('.undo-toast')),
 vw:document.documentElement.clientWidth,right:Math.round(t.getBoundingClientRect().right)};
t.querySelector('button.ball').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}));await sleep(150);
out.keptOnOwnButton=!!document.querySelector('.undo-toast');
(document.querySelector('.dm-words input')||document.body).dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}));await sleep(150);
out.goneAfterNextAction=!document.querySelector('.undo-toast');return out;})()"""


@NEEDS_BROWSER
@pytest.mark.parametrize("width", [375, 390])
def test_c_browser_on_a_phone_a_tap_reaches_the_row_beneath_and_the_next_action_closes_the_toast(store, width):
    """Protects (c) in a real layout at 375 and 390 px: the toast's text does not catch a tap meant for the button under it, its Undo
    button still does, pressing its own button leaves it open, and pressing anything else closes it."""
    _rec_ask(store)
    with live_app(create_app(store.home)) as base:
        got = cdp_measure(base, ["/decide?undo=b-aaaaaaaaaaaa&msg=Saved."], [width], _TOAST)
    d = got[f"/decide?undo=b-aaaaaaaaaaaa&msg=Saved.@{width}"]
    assert d["toast"] and d["right"] <= d["vw"], d
    assert not d["textBlocks"], d
    assert d["undoTakesTap"] and d["keptOnOwnButton"] and d["goneAfterNextAction"], d


@NEEDS_BROWSER
def test_c_browser_on_a_wide_screen_nothing_changes(store):
    """Protects the edge of (c): at 1280 px the toast still takes pointers (hover holds it) and a press elsewhere does not close it,
    so the ten seconds to Undo are never cut short on a desktop."""
    _rec_ask(store)
    with live_app(create_app(store.home)) as base:
        got = cdp_measure(base, ["/decide?undo=b-aaaaaaaaaaaa&msg=Saved."], [1280], _TOAST)
    d = got["/decide?undo=b-aaaaaaaaaaaa&msg=Saved.@1280"]
    assert d["toast"] and d["textBlocks"] and d["keptOnOwnButton"] and not d["goneAfterNextAction"], d


# ======================================================= (d) the date on the Undo log

def _undo_batch(store):
    a, b = ops.new(store, "Listed one", "desk"), ops.new(store, "Listed two", "desk")
    tok = identity.CURRENT.set(identity.Actor("chaz", "person", "t1", "cli", "claimed", False))
    try:
        with activity.batch("b-dddddddddddd"):
            for t in (a, b):
                x = store.get(t.id)
                x.meta["priority"] = "p1"
                store.save(x)
    finally:
        identity.CURRENT.reset(tok)


def test_d_route_the_date_sits_alone_in_a_no_break_span():
    """Protects (d) by the page's markup: every group's date is its own span that cannot break (the time and the count stay outside)."""
    import tempfile

    from desk.store import Store

    with tempfile.TemporaryDirectory() as d:
        s = Store.init(Path(d) / "home", owner="james")
        (s.projects_dir / "desk.md").write_text("# Desk\n", encoding="utf-8")
        _undo_batch(s)
        page = TestClient(create_app(s.home)).get("/undo").text
    spans = re.findall(r'<span class="nowrap">(\d{4}-\d{2}-\d{2})</span>', page)
    assert len(spans) >= 1, page[page.index("ul-ghead"):][:600]


def test_d_static_the_no_break_class_is_defined_in_the_undo_stylesheet():
    """Protects (d) by a read of the stylesheet: the class the page uses really stops a break."""
    css = re.sub(r"/\*.*?\*/", "", _src("undo.css"), flags=re.S)
    assert re.search(r"\.nowrap\s*\{[^}]*white-space:nowrap", css)


_DATE = """(function(){var m=document.querySelector('.ul-ghead .muted');if(!m)return null;m.style.display='block';m.style.width='60px';  /* a squeeze far narrower than any phone */
var w=document.createTreeWalker(m,NodeFilter.SHOW_TEXT),n,tn=null;while((n=w.nextNode())){if(/^\\d{4}-\\d{2}-\\d{2}/.test(n.nodeValue)){tn=n;break;}}
if(!tn)return {found:false};var r=document.createRange();r.setStart(tn,0);r.setEnd(tn,10);var tops={};
[].forEach.call(r.getClientRects(),function(q){tops[Math.round(q.top)]=1;});
return {found:true,lines:Object.keys(tops).length,ws:getComputedStyle(tn.parentElement).whiteSpace,sw:document.documentElement.scrollWidth};})()"""


@NEEDS_BROWSER
def test_d_browser_the_date_stays_on_one_line_even_when_squeezed(store):
    """Protects (d) in a real layout: with the line squeezed to 60 px (narrower than the date) the ten characters of the date still
    sit on one line, and the element holding them says white-space: nowrap; before the fix the date broke after '2026-11-'."""
    _undo_batch(store)
    with live_app(create_app(store.home)) as base:
        got = cdp_measure(base, ["/undo"], [375], _DATE)
    d = got["/undo@375"]
    assert d and d["found"] and d["lines"] == 1 and d["ws"] == "nowrap", d


# ======================================================= (e) status colours are not actor colours

def _greyish(hexcolour: str, spread: int = 24) -> bool:
    h = hexcolour.lstrip("#")
    c = [int(h[i:i + 2], 16) for i in (0, 2, 4)]
    return max(c) - min(c) <= spread


def _token(css: str, name: str) -> str:
    return re.search(rf"--{name}:(#[0-9A-Fa-f]{{6}})", css).group(1)


def test_e_static_ready_and_changes_requested_use_neutral_greys_no_actor_has():
    """Protects (e) by a read of the stylesheet: the two status colours Chaz named are neutral greys, and neither equals a kind colour
    or a palette colour that an actor's dot can have."""
    css = _src("desk.css")
    actor_colours = {c.lower() for c in [*timeline.KIND_COLOURS.values(), *timeline.PALETTE]}
    for name in ("st-ready", "st-changes"):
        c = _token(css, name)
        assert _greyish(c) and c.lower() not in actor_colours, (name, c)
    assert _token(css, "st-ready").lower() != _token(css, "st-changes").lower()


def _status_ticket(store):
    """Inbox, then Ready (set by the code session), then Changes requested (set by a coworker): the two statuses and the two actors
    whose colours used to collide."""
    t = ops.new(store, "A ticket that went ready then back", "desk")
    activity.path(store.home, t.id).unlink()
    base = dt.datetime.now().replace(microsecond=0) - dt.timedelta(days=3)
    evs = [activity.Event(t.id, base.isoformat(), "owner-a", "owner", "ui", "created", "created", "", t.meta["title"], "Created: x"),
           activity.Event(t.id, (base + dt.timedelta(hours=10)).isoformat(), "claude-code", "code", "cli", "field", "status", "inbox", "ready", "status: inbox → ready"),
           activity.Event(t.id, (base + dt.timedelta(hours=40)).isoformat(), "chaz", "coworker", "ui", "field", "status", "ready", "changes_requested",
                          "status: ready → changes_requested")]
    activity.append(store.home, evs)
    x = store.get(t.id)
    x.meta["status"], x.meta["created"] = "changes_requested", base.date().isoformat()
    x.write()
    return store.get(t.id)


_COLOURS = """(function(){function hex(c){var m=c.match(/[0-9.]+/g).map(Number);return '#'+m.slice(0,3).map(function(x){return ('0'+x.toString(16)).slice(-2);}).join('');}
var seg=function(s){var e=document.querySelector('.tl-seg.st-'+s);return e?hex(getComputedStyle(e).backgroundColor):null;};
var sw=function(s){var e=document.querySelector('.tl-key .tl-sw.st-'+s);return e?hex(getComputedStyle(e).backgroundColor):null;};
var dots=[];document.querySelectorAll('.tl-dot').forEach(function(d){dots.push(hex(getComputedStyle(d).backgroundColor));});
return {ready:seg('ready'),changes:seg('changes_requested'),swReady:sw('ready'),swChanges:sw('changes_requested'),dots:dots};})()"""


@NEEDS_BROWSER
@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_e_browser_no_status_colour_on_the_page_is_also_the_dot_of_a_person(store, dark):
    """Protects (e) in a real page, light and dark at 1280 px: the Ready and Changes requested segments and their key swatches are
    grey, and no dot beside an event (the code session's, the coworker's, the owner's) is the same colour as either."""
    t = _status_ticket(store)
    with live_app(create_app(store.home)) as base:
        got = cdp_measure(base, [f"/ticket/{t.id}"], [1280], _COLOURS, dark=dark)
    d = got[f"/ticket/{t.id}@1280"]
    assert d["ready"] and d["changes"], d
    assert d["swReady"] == d["ready"] and d["swChanges"] == d["changes"], d
    assert _greyish(d["ready"]) and _greyish(d["changes"]), d
    assert len({x.lower() for x in d["dots"]}) >= 2 and not ({d["ready"], d["changes"]} & {x.lower() for x in d["dots"]}), d


# ======================================================= (f) one plain name for the code session

def _code_ticket(store, actor="claude-code", kind="code"):
    t = ops.new(store, "A ticket the code session changed", "desk")
    activity.path(store.home, t.id).unlink()
    now = dt.datetime.now().replace(microsecond=0)
    activity.append(store.home, [
        activity.Event(t.id, (now - dt.timedelta(hours=3)).isoformat(), "owner-a", "owner", "ui", "created", "created", "", t.meta["title"], "Created: x"),
        activity.Event(t.id, (now - dt.timedelta(hours=1)).isoformat(), actor, kind, "cli", "field", "priority", "p2", "p1", "priority: p2 → p1")])
    return store.get(t.id)


def _visible(html: str) -> str:
    return re.sub(r"<[^>]*>", " ", html)


def test_f_unit_the_row_and_the_latest_line_name_the_code_session_once(store):
    """Protects (f) by a call of the builders: the raw key becomes one plain name, and the line does not say it twice."""
    t = _code_ticket(store)
    v = timeline.view(store, t)
    rs = v.rows
    r = next(x for x in rs if x.event.field == "priority")
    assert r.actor_name == "Code session" and "claude-code" not in timeline.latest_line(r)
    assert timeline.latest_line(r).startswith("Code session · priority: p2 → p1")
    assert "Code session (Code session)" not in timeline.latest_line(r)
    assert "claude-code" not in timeline.text(v)


def test_f_route_the_page_shows_no_raw_key_in_the_latest_line_or_the_rail(store):
    """Protects (f) through the real page: neither the Latest line (its words and its hover) nor the rail's visible text carries
    'claude-code', and the name appears once per line, not as 'Code session  Code session'."""
    t = _code_ticket(store)
    page = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    latest = re.search(r'<p class="tl-latest".*?</p>', page, re.S).group(0)
    assert "claude-code" not in latest and "Code session (Code session)" not in latest, latest
    assert _visible(latest).split().count("Code") == 1, _visible(latest)
    rail = page[page.index('id="tl-rail"'):]
    rail = rail[:rail.index("</ol>")] if "</ol>" in rail else rail
    assert "claude-code" not in _visible(rail), _visible(rail)[:400]
    assert re.search(r'<span class="tl-actor"[^>]*>[^<]*<i[^>]*></i>Code session\s*</span>', rail) or "Code session <small>Code session" not in rail


def test_f_cli_drew_history_prints_the_plain_name(store, capsys):
    """Protects (f) on the command line: `drew history` prints the one plain name, not the raw key."""
    t = _code_ticket(store)
    main(["--home", str(store.home), "history", t.id])
    out = capsys.readouterr().out
    assert "claude-code" not in out and "Code session · priority" in out and "Code session (Code session)" not in out, out


def test_f_edge_a_named_person_and_other_agents_keep_their_own_wording(store, capsys):
    """Protects the edge of (f): only the raw session key is renamed; a person keeps 'Name (Kind)', and an unknown actor is shown as written."""
    t = ops.new(store, "Edge", "desk")
    activity.path(store.home, t.id).unlink()
    now = dt.datetime.now().replace(microsecond=0)
    activity.append(store.home, [
        activity.Event(t.id, (now - dt.timedelta(hours=2)).isoformat(), "chaz", "coworker", "ui", "field", "priority", "p2", "p1", "priority: p2 → p1"),
        activity.Event(t.id, (now - dt.timedelta(hours=1)).isoformat(), "somebody-new", "person", "cli", "field", "size", "s", "m", "size: s → m")])
    names = {r.event.field: r.actor_name for r in timeline.rows(store, store.get(t.id))}
    assert names["priority"] == "Chaz" and names["size"] == "somebody-new", names
    main(["--home", str(store.home), "history", t.id])
    assert "Chaz (Coworker)" in capsys.readouterr().out
