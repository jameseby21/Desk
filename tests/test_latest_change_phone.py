"""DESK-0305 checker F1: on a phone the Latest change box showed two lines and a third cut in half at its bottom edge, and the date and
'+N git saves since' were never visible (DESK-0082, DESK-0117 and MGMT-0040 at a real 375 px client width). Cause: the two-line clamp
sat on the padded box itself with inline children, so the third line showed through the bottom padding and the date and the count,
which come after what changed, were clamped away. Protects: what changed is a whole two-line clamp of its own (a box with no padding),
who, when and '+N git saves since' are whole and inside the box, at 320 and 390 px. Three methods: a static read of the stylesheet,
text-line geometry in a real browser, and element-box geometry in a real browser."""

import datetime as dt
import re
from pathlib import Path

import pytest
from browser_harness import browser, inline_assets, run_in_browser
from fastapi.testclient import TestClient

from desk import activity, ops
from desk.ui.app import create_app

STATIC = Path(__file__).resolve().parents[1] / "src" / "desk" / "ui" / "static"

# the three real shapes the checker opened, and two edge cases
DESK_0082 = ("Your three Not accepted reasons went to: tolerant duplicate rule including changed users DESK-0142; visible progress bar as an "
             "app-wide rule DESK-0152 (rule in 10-slice-contract.md); human-readable reused-key message on DESK-0122.", 2)
MGMT_0040 = ("2026-11-12 entries from Lily in Patch's folder, filed (rule 25): LILY-60 -> PART-0014, 61 -> PART-0015, 62 -> PART-0016, "
             "63 -> PART-0017, 64 -> PART-0018 and the rest as fyi.", 1)
DESK_0117 = ("Removed a repeated '## Report' heading so Desk shows the first Report section whole, then filed the follow-ups.", 3)
LONG_400 = (("A four hundred character latest change that keeps going so the clamp has to cut it on a phone, " * 5)[:400], 7)
ONE_TOKEN = ("x" * 300, 1)  # one unbroken token: it must break inside the box, never run past its right edge
SHORT = ("Status: Ready → In progress", 0)  # fits on one line, no git saves: nothing clamped, nothing extra
SHAPES = {"DESK-0082": DESK_0082, "MGMT-0040": MGMT_0040, "DESK-0117": DESK_0117, "400 chars": LONG_400, "one token": ONE_TOKEN, "short": SHORT}


def _ticket(store, what: str, saves: int):
    """A ticket whose latest change is the comment `what`, followed by `saves` git saves (as on the real tickets)."""
    t = ops.new(store, "Latest change on a phone", "desk")
    activity.path(store.home, t.id).unlink()  # the log is written here, in time order, so the comment is the newest change
    start = dt.datetime.now().replace(second=0, microsecond=0) - dt.timedelta(minutes=30)
    evs = [activity.Event(t.id, (start - dt.timedelta(hours=1)).isoformat(), "owner-a", "owner", "ui", "created", "created", "", "x", "Created: x"),
           activity.Event(t.id, start.isoformat(), "Patch", "main", "ui", "comment", "comment", "", "x", what)]
    evs += [activity.Event(t.id, (start + dt.timedelta(minutes=1 + i)).isoformat(), "claude-code", "code", "git", "git", "git", "git-user", f"s{i}",
                           f"Saved in git: pass {i}", key=f"git:phone{i}") for i in range(saves)]
    activity.append(store.home, evs)
    return store.get(t.id)


def _measure(store, what, saves, width, script):
    t = _ticket(store, what, saves)
    html = inline_assets(TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text, STATIC)
    return run_in_browser(html.replace("</body>", script + "</body>", 1), width)


# -- method 1: a static read of the stylesheet --------------------------------------------------------------------------------

def _phone_block(css: str) -> str:
    """Every rule inside the `@media (max-width:600px)` blocks, joined."""
    out = []
    for m in re.finditer(r"@media \(max-width:600px\)\{", css):
        depth, i = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(css[i], 0)
            i += 1
        out.append(css[m.end():i - 1])
    return "\n".join(out)


def test_static_on_a_phone_the_clamp_is_on_what_changed_not_on_the_padded_box():
    """Protects the cause: -webkit-line-clamp sits on `.tl-latest .tl-what` (no padding, so a clamped third line cannot show through)
    and never on `.tl-latest` itself, which has 8 px of padding; who, when and the git-save count never wrap inside themselves."""
    phone = _phone_block(re.sub(r"/\*.*?\*/", "", (STATIC / "desk.css").read_text(encoding="utf-8"), flags=re.S))
    rules = {sel.strip(): body for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", phone)}
    clamped = [sel for sel, body in rules.items() if "line-clamp" in body]
    assert clamped == [".tl-latest .tl-what"], clamped
    assert "padding" not in rules[".tl-latest .tl-what"] and "overflow:hidden" in rules[".tl-latest .tl-what"]
    whole = [sel for sel, body in rules.items() if "white-space:nowrap" in body]
    assert any(all(c in sel for c in (".tl-who", ".tl-when", ".tl-plus")) for sel in whole), whole


# -- method 2: text-line geometry in a real browser -----------------------------------------------------------------------------

LINES = """<script>addEventListener('load',function(){setTimeout(function(){
var lat=document.getElementById('tl-latest'),b=lat.getBoundingClientRect(),cs=getComputedStyle(lat),pt=parseFloat(cs.paddingTop),pb=parseFloat(cs.paddingBottom);
var bottom=b.bottom-pb,cut=[],shown=[];
function clipOf(n){var c={t:-1e9,b:1e9};for(var e=n.parentElement;e;e=e.parentElement){if(getComputedStyle(e).overflowY!=='visible'){var r=e.getBoundingClientRect();
 c.t=Math.max(c.t,r.top);c.b=Math.min(c.b,r.bottom);}if(e===lat)break;}return c;}
var tw=document.createTreeWalker(lat,NodeFilter.SHOW_TEXT),n;
// a glyph box (ascent plus descent) can stand 1 to 2 px outside its line box, and that sliver holds no ink (it is above the capitals
// and below the descenders): a line counts as cut when more than 3 px of it shows and more than 3 px is hidden, as in the defect (8 of 19 px)
while((n=tw.nextNode())){if(!n.textContent.trim())continue;var c=clipOf(n),g=document.createRange();g.selectNodeContents(n);
 Array.prototype.forEach.call(g.getClientRects(),function(r){if(!r.width)return;var vis=Math.min(r.bottom,c.b)-Math.max(r.top,c.t),slack=3;
  if(vis>slack&&vis<r.height-slack)cut.push(Math.round(r.top)+'..'+Math.round(r.bottom)+' clip '+c.t.toFixed(1)+'..'+c.b.toFixed(1)+' shows '+vis.toFixed(1)+' px: '+n.textContent.slice(0,30));
  if(vis>slack)shown.push(Math.min(r.bottom,c.b));});}
var what=lat.querySelector('.tl-what'),wb=what.getBoundingClientRect();
var lh=parseFloat(getComputedStyle(what).lineHeight);
fetch('/m',{method:'POST',body:JSON.stringify({w:document.documentElement.clientWidth,sh:lat.scrollHeight,ch:lat.clientHeight,cut:cut,
 lastBottom:Math.max.apply(null,shown),bottom:bottom,whatH:wb.height,lh:lh,whatRight:wb.right,boxRight:b.right-parseFloat(cs.paddingRight)+0.5,
 whatSW:what.scrollWidth,whatCW:what.clientWidth})});},300);});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
@pytest.mark.parametrize("width", [320, 390])
@pytest.mark.parametrize("shape", list(SHAPES))
def test_browser_no_line_of_the_latest_change_is_cut_by_the_box_edge(store, shape, width):
    """Protects the phone: the box holds all it shows (scrollHeight <= clientHeight), no text line straddles the bottom of the box's
    content area or of the what-changed clamp, the last visible glyph ends above the bottom padding, what changed is at most two whole
    lines tall, and a 300-character unbroken token breaks inside the box instead of running past it."""
    what, saves = SHAPES[shape]
    d = _measure(store, what, saves, width, LINES)
    assert width - 20 <= d["w"] <= width, d  # a real phone-width client area (the iframe's, less its own scrollbar)
    assert d["sh"] <= d["ch"], d
    assert d["cut"] == [], d
    assert d["lastBottom"] <= d["bottom"] + 0.5, d
    assert d["whatH"] <= 2 * d["lh"] + 1, d
    assert d["whatRight"] <= d["boxRight"] and d["whatSW"] <= d["whatCW"] + 1, d


# -- method 3: element-box geometry in a real browser ---------------------------------------------------------------------------

BOXES = """<script>addEventListener('load',function(){setTimeout(function(){
var lat=document.getElementById('tl-latest'),b=lat.getBoundingClientRect(),cs=getComputedStyle(lat);
var inner={l:b.left+parseFloat(cs.paddingLeft)-0.5,r:b.right-parseFloat(cs.paddingRight)+0.5,t:b.top+parseFloat(cs.paddingTop)-0.5,b:b.bottom-parseFloat(cs.paddingBottom)+0.5};
function box(c){var e=lat.querySelector('.'+c);if(!e)return null;var rs=Array.prototype.filter.call(e.getClientRects(),function(x){return x.width>0;});
 var tops={};rs.forEach(function(x){tops[Math.round(x.top)]=1;});
 return {shown:e.offsetWidth>0&&getComputedStyle(e).visibility!=='hidden',lines:Object.keys(tops).length,text:e.textContent,
  inside:rs.length>0&&rs.every(function(x){return x.left>=inner.l&&x.right<=inner.r&&x.top>=inner.t&&x.bottom<=inner.b;})};}
fetch('/m',{method:'POST',body:JSON.stringify({who:box('tl-who'),when:box('tl-when'),plus:box('tl-plus'),what:box('tl-what')})});},300);});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
@pytest.mark.parametrize("width", [320, 390])
@pytest.mark.parametrize("shape", list(SHAPES))
def test_browser_who_when_and_the_git_save_count_are_whole_inside_the_box(store, shape, width):
    """Protects what a person needs from the line on a phone: who, the date and '+N git saves since' are shown, each on one line, and
    each lies wholly inside the box's content area; a ticket with no git saves after its latest change shows no count."""
    what, saves = SHAPES[shape]
    d = _measure(store, what, saves, width, BOXES)
    for k in ("who", "when"):
        assert d[k] and d[k]["shown"] and d[k]["inside"] and d[k]["lines"] == 1, (k, d)
    if saves:
        assert d["plus"]["shown"] and d["plus"]["inside"] and d["plus"]["lines"] == 1, d
        assert d["plus"]["text"] == f"+{saves} git save{'s' if saves != 1 else ''} since", d
    else:
        assert d["plus"] is None, d
    assert d["what"]["shown"] and d["what"]["inside"] and 1 <= d["what"]["lines"] <= 2, d
