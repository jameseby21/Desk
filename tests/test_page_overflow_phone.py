"""DESK-0303 (DESK-0282 checker F1 and F2): on a phone no page is wider than the window, whatever long text a ticket holds.

What the checker saw at a real 375 px client width on a copy of the store: Today 496 px wide (the Needs me tools row, a flex row that never
wrapped, pushed "All open (967) ->" to x 370 to 496 once decide.js added "Decide one by one (D)"); /ticket/DESK-0117 409 px (a Source value
in a `<code>`); /settings 598 px (the store folder in a `<code>` widened the whole definition grid); and a `<strong>` holding a Drive link on
DESK-0157 611 px wide in a 305 px column. Causes: the tools row did not wrap, and a long unbroken token (a link, a path, an id) had no place
to break while its grid track or flex item took its minimum width from that token. Protects: at 375 and 320 px, on five real-shaped pages
(Today with asks and changes, two tickets with long tokens, a ticket with a long Not accepted reason, /settings with a 130-character
store folder), the page scrolls only up and down and no element ends past the right edge outside a scrolling box. Three methods: a static
read of the stylesheet, the page's own scroll width in the real app inside a frame of the phone's width, and a sweep of every element's box.
"""

import datetime as dt
import json
import re
from pathlib import Path

import pytest
from browser_harness import browser, drive

from desk import identity, ops, people
from desk.store import Store

STATIC = Path(__file__).resolve().parents[1] / "src" / "desk" / "ui" / "static"
CSS = re.sub(r"/\*.*?\*/", "", (STATIC / "desk.css").read_text(encoding="utf-8"), flags=re.S)

URL200 = "https://drive.google.com/file/d/" + "1j0b8CS_2CuwkQjSwe63VzgK9C8l7VMFk" * 5 + "/view"  # one unbroken token, about 200 characters
SOURCE = "Chaz entry 2026-11-08 (DESK-0100): https://docs.google.com/document/d/" + "14RUwagu4-WVDMfm9S2alJ60yHK4NVrXvgLvjIweeENY" * 3 + "/edit"
LONG_REASON = ("This is not a human-readable message and so therefore is rejected on principle. More formally: does not follow project "
               "guidelines and rules. ") * 4
PATH_TOKEN = r"C:\Users\someone\Documents\jeby_Industries\desk\src\desk\ui\templates\a_very_long_template_name_that_never_breaks.html"


def _home(tmp_path: Path) -> Path:
    """A store folder whose full path is 130 characters with no space in it, as a deep real checkout can be."""
    base = str(tmp_path / "store-")
    return Path(base + "x" * max(1, 130 - len(base)))


@pytest.fixture
def phone_store(tmp_path, monkeypatch):
    home = _home(tmp_path)
    assert len(str(home)) >= 130, home
    s = Store.init(home, owner="james")
    for key, title in (("jre", "jRE"), ("desk", "Desk")):
        (s.projects_dir / f"{key}.md").write_text(f"# {title}\n", encoding="utf-8")
    monkeypatch.setattr(identity, "os_user", lambda: "owner-login")  # the owner's own browser, so Today shows his Needs me
    people.link_os_user(s.home, "james", "owner-login")
    return s


def _fixtures(s: Store) -> dict:
    """The five real shapes: Today with asks and someone else's change, DESK-0117 (a long Source), DESK-0157 (a Drive link in bold),
    DESK-0082 (long Not accepted reasons) and /settings (the long store folder)."""
    for i in range(3):
        t = ops.new(s, f"Choose: option {i} for a question with a longer title that wraps on a phone screen", "desk", "decision", owner="james")
        t.meta["status"] = "ready"
        t.set_section("Context", "Pick one.\n\nOptions: A one; B two.")
        s.save(t)
    now = dt.datetime.now()
    ch = ops.new(s, "Changed by someone else", "desk", "feature", owner="main")
    ch.meta["status"] = "in_progress"
    ch.append_line("Comments", f"- {(now - dt.timedelta(hours=3)).strftime('%Y-%m-%d %H:%M')} · james · please look <!-- kind: Question -->")
    ch.append_line("Comments", f"- {(now - dt.timedelta(hours=1)).strftime('%Y-%m-%d %H:%M')} · lily · done? {URL200} <!-- kind: Question -->")
    s.save(ch)
    src = ops.new(s, "Removed a repeated Report heading", "desk", "task")
    src.meta["source"] = SOURCE
    src.meta["handoff"] = "1" + "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789" * 3
    src.set_section("Report", f"Touched `{PATH_TOKEN}` and `{'x' * 120}`.")
    s.save(src)
    bold = ops.new(s, "Lily's answer in a Drive file", "desk", "task")
    bold.set_section("Context", f"**Lily's answer the same day (LILY-30, {URL200}):**\n\n- [{URL200}]({URL200})\n- plain {URL200}")
    bold.set_section("Acceptance", f"- [ ] The link {URL200} opens\n- [ ] **{URL200}**")
    s.save(bold)
    rej = ops.new(s, "A ticket with long Not accepted reasons", "desk")
    rej.set_section("Acceptance", f"- [!] Posting the same comment three times stores one line\n  > Not accepted (must fix): {LONG_REASON.strip()}\n"
                                  f"- [!] A reused form token is refused\n  > Not accepted (should): {URL200}\n- [ ] Still open")
    s.save(rej)
    return {"today": "/", "source": f"/ticket/{src.id}", "bold": f"/ticket/{bold.id}", "reasons": f"/ticket/{rej.id}", "settings": "/settings"}


# The real app (every stylesheet and script, decide.js included) in a frame exactly `width` px wide: a browser window will not shrink
# below about 500 px, so the frame is the phone. The measuring runs in the outer page against each frame's document.
FRAME = """<script>addEventListener('load',function(){if(window!==top)return;
var paths=%s,widths=%s,out={},jobs=[];paths.forEach(function(p){widths.forEach(function(w){jobs.push([p,w]);});});document.body.innerHTML='';
function sweep(d,win,w){var over=[];d.querySelectorAll('body *').forEach(function(e){var r=e.getBoundingClientRect();
 if(!r.width||r.right<=w+1||e.closest('[hidden]'))return;
 for(var p=e.parentElement;p&&p!==d.body;p=p.parentElement){var s=win.getComputedStyle(p).overflowX;if(s==='auto'||s==='scroll')return;}
 over.push(e.tagName+'.'+String(e.className).slice(0,30)+' '+Math.round(r.left)+'-'+Math.round(r.right)+' '+(e.textContent||'').trim().slice(0,30));});
 return over;}
function next(){if(!jobs.length){fetch('/m',{method:'POST',body:JSON.stringify(out)});return;}
 var j=jobs.shift(),f=document.createElement('iframe');f.style.cssText='width:'+j[1]+'px;height:900px;border:0';
 f.onload=function(){setTimeout(function(){var d=f.contentDocument,de=d.documentElement,win=f.contentWindow,cw=de.clientWidth;
  var tools=d.querySelector('.nm-tools'),tr=tools?tools.getBoundingClientRect():null,card=d.getElementById('needs-me');
  var strongs=Array.prototype.map.call(d.querySelectorAll('.md strong'),function(s){var col=s.closest('.cols>*')||d.body;
   return {w:Math.round(s.getBoundingClientRect().width),col:Math.round(col.getBoundingClientRect().width)};});
  out[j[0]+'@'+j[1]]={iw:win.innerWidth,cw:cw,sw:de.scrollWidth,bsw:d.body.scrollWidth,over:sweep(d,win,cw).slice(0,12),
   tools:tr?{right:Math.round(tr.right),links:tools.querySelectorAll('a,button').length,
    inCard:Array.prototype.every.call(tools.querySelectorAll('a,button'),function(b){return b.getBoundingClientRect().right<=card.getBoundingClientRect().right+0.5;})}:null,
   strongs:strongs};
  f.remove();next();},400);};
 f.src=j[0];document.body.appendChild(f);}
next();});</script>"""


def _run(store, widths):
    paths = _fixtures(store)
    got = drive(store, FRAME % (json.dumps(list(paths.values())), json.dumps(widths)), "/about", timeout=90)
    return paths, got


# -- method 1: a static read of the stylesheet --------------------------------------------------------------------------------

def _rules(selector: str) -> list[str]:
    return [b for sel, b in re.findall(r"([^{}]+)\{([^{}]*)\}", CSS) if selector in [x.strip() for x in sel.split(",")]]


def test_static_the_needs_me_tools_wrap_and_take_their_own_line_on_a_phone():
    """Protects the cause of F1: the tools row wraps, may shrink (min-width:0), and on a phone starts on its own full-width line instead
    of being pushed right by margin-left:auto."""
    base = " ".join(_rules(".nm-tools"))
    assert "flex-wrap:wrap" in base and "min-width:0" in base, base
    phone = re.search(r"@media \(max-width:700px\)\{[^@]*?\.nm-tools\{([^}]*)\}", CSS)
    assert phone and "flex-basis:100%" in phone.group(1) and "margin-left:0" in phone.group(1), phone and phone.group(1)


def test_static_long_tokens_break_only_where_a_line_cannot_hold_them():
    """Protects the cause of F2: code spans, definition values and every markdown body (bold, links, code) break an over-long token
    with overflow-wrap:break-word, which never splits a word that fits on a line (so no header or label is split, DESK-0302), and the
    definition grid's value column is minmax(0,1fr) so the folder cannot widen it."""
    for sel in ("code", ".md", ".docmeta dd", ".fields"):
        assert any("overflow-wrap:break-word" in b and "min-width:0" in b for b in _rules(sel)), (sel, _rules(sel))
    grid = " ".join(_rules(".docmeta"))
    assert "grid-template-columns:max-content minmax(0,1fr)" in grid, grid


# -- method 2 and 3: the real app in a phone-width frame ------------------------------------------------------------------------

@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_browser_no_page_is_wider_than_a_375_or_320_px_phone(phone_store):
    """Protects the owner's phone (method 2, the page's own scroll width): each of the five pages scrolls only up and down at 375 and
    320 px client width, the Needs me tools (three of them, decide.js's link included) end inside the card, and every bold line fits
    its column."""
    paths, got = _run(phone_store, [375, 320])
    assert len(got) == 10, sorted(got)
    for key, d in got.items():
        assert d["sw"] <= d["cw"] and d["bsw"] <= d["cw"], (key, d)
        for st in d["strongs"]:
            assert st["w"] <= st["col"], (key, st)
    for w in (375, 320):
        tools = got[f"/@{w}"]["tools"]
        assert tools and tools["links"] == 3 and tools["inCard"] and tools["right"] <= got[f"/@{w}"]["cw"], tools


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_browser_no_element_passes_the_right_edge_on_a_phone(phone_store):
    """Protects each element, not only the total (method 3, a sweep of every box): nothing outside a scrolling box ends past the
    window's right edge on any of the five pages at 375 and 320 px."""
    paths, got = _run(phone_store, [375, 320])
    bad = {k: d["over"] for k, d in got.items() if d["over"]}
    assert not bad, bad
