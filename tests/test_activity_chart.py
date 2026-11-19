"""DESK-0329: the activity chart on every ticket page (the owner, fifth ask, 2026-11-19: "a visual timeline diagram/chart about activity
on the ticket"). Every event of the ticket is a mark at its time on the status strip's own time axis, in a row per kind (Decisions,
Status, Comments, Changes, Git) named in words, coloured and shaped by who (owner, Patch and code sessions, coworkers, agents, rules,
other people) with a legend in words; busy spots print their count, and the counts add up to the events. Hover, focus or a tap names
the events of a spot; a click or Enter shows the newest of them in the list. Open by default, at 1280, 390 and 320 px, light and dark.

Each behaviour is checked by three methods: unit tests of the layout arithmetic (activity.js run in node), route tests through the
real app (TestClient), and real headless Edge or Chrome sessions driven over the DevTools protocol (device widths, dark, reduced
motion, real mouse and touch input), plus the edge cases: no events, one event, 200+ events, all on one day, created years ago."""

import datetime as dt
import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
from browser_harness import _CDP, browser, live_app, stop_browser
from fastapi.testclient import TestClient

from desk import activity, ops, timeline
from desk.ui.app import STATIC, create_app

NODE = shutil.which("node")
NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")
NEEDS_BROWSER = pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
JS = STATIC / "activity.js"
LANES = ["decisions", "status", "comments", "changes", "git"]


# ============================================================ helpers

def node(expr: str):
    """Evaluate `expr` in node with A = activity.js's exported functions; returns its JSON value."""
    code = f"const A = require({json.dumps(str(JS))}); process.stdout.write(JSON.stringify({expr}));"
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def E(t, ts, actor, kind, change, field="", old="", new="", what=""):
    return activity.Event(t.id, ts if isinstance(ts, str) else ts.isoformat(), actor, kind, "cli", change, field, old, new, what or f"{change} {field}")


def make(store, title, events_of, created: dt.datetime, status="ready"):
    """A ticket whose whole log is `events_of(t)`, created on `created`."""
    t = ops.new(store, title, "desk")
    activity.path(store.home, t.id).unlink()
    evs = events_of(t)
    if evs:
        activity.append(store.home, evs)
    x = store.get(t.id)
    x.meta["status"], x.meta["created"] = status, created.date().isoformat()
    x.write()
    return store.get(t.id)


def mixed(store, n=30, days=4):
    """n events over the last `days` days by every kind of actor, in every row: a created event, status moves, a decision, comments,
    field edits and runs of git saves (some folded)."""
    base = dt.datetime.now().replace(microsecond=0, second=0) - dt.timedelta(days=days)
    step = dt.timedelta(seconds=days * 86400 / (n + 1))

    def evs(t):
        out = [E(t, base, "owner-a", "owner", "created", "created", what="Created: mixed")]
        who = [("owner-a", "owner"), ("main-a", "main"), ("claude-code", "code"), ("chaz", "coworker"), ("checker", "subagent"), ("automation", "rule"),
               ("someone", "person")]
        flow = ["inbox", "ready", "in_progress", "review"]
        for i in range(1, n):
            a, k = who[i % len(who)]
            ts = base + step * i
            m = i % 6
            if m == 0 and i // 6 < len(flow) - 1:
                out.append(E(t, ts, a, k, "field", "status", flow[i // 6], flow[i // 6 + 1], f"status: {flow[i // 6]} → {flow[i // 6 + 1]}"))
            elif m == 1:
                out.append(E(t, ts, a, k, "section", "Decision", "", "Approved", f"Decision {i}: approved"))
            elif m == 2:
                out.append(E(t, ts, a, k, "comment", "comment", "", "x", f"Comment number {i} from {a}"))
            elif m == 3:
                out.append(E(t, ts, a, k, "field", "priority", "p2", "p1", f"priority {i}"))
            else:
                out.append(E(t, ts, "claude-code", "code", "git", "git", "git-user", f"abc{i}", f"Saved in git: change {i}"))
        return out

    return make(store, "A ticket with mixed activity", evs, base)


def busy(store, n=230, days=12):
    """A long ticket: n events, most of them git saves bunched on a few days, the way a real store looks (88% git saves)."""
    base = dt.datetime.now().replace(microsecond=0, second=0) - dt.timedelta(days=days)

    def evs(t):
        out = [E(t, base, "owner-a", "owner", "created", "created", what="Created: busy")]
        for i in range(1, n):
            day = base + dt.timedelta(days=(i * 7) % days, minutes=i)
            if i % 9 == 0:
                out.append(E(t, day, "chaz", "coworker", "comment", "comment", "", "x", f"Busy comment {i}"))
            elif i % 23 == 0:
                out.append(E(t, day, "owner-a", "owner", "section", "Decision", "", "ok", f"Decision {i}"))
            else:
                out.append(E(t, day, "claude-code", "code", "git", "git", "u", f"c{i}", f"Saved in git: busy {i}"))
        return out

    return make(store, "A ticket with a long busy life", evs, base)


def page(store, t):
    r = TestClient(create_app(store.home)).get(f"/ticket/{t.id}")
    assert r.status_code == 200
    return r.text


def session(base: str, path: str, width: int, steps, dark=False, reduced=False, settle=0.6):
    """Open `path` in a headless browser at exactly `width` px (DevTools device metrics), optionally dark and with reduced motion, and
    run `steps`: each is a JS expression (its value is collected) or a callable(cdp) for real input. Returns the collected values.
    The browser is stopped by the PID this call started, and its throw-away profile removed, before this returns."""
    import socket
    import urllib.request

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    tmp = Path(tempfile.mkdtemp(prefix="desk-ac-"))
    proc = None
    try:
        proc = subprocess.Popen([browser(), "--headless=new", "--disable-gpu", "--no-first-run", "--disable-extensions", f"--user-data-dir={tmp / 'profile'}",
                                 f"--remote-debugging-port={port}", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ws = None
        for _ in range(80):
            try:
                tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2).read())
                ws = next((x["webSocketDebuggerUrl"] for x in tabs if x.get("type") == "page"), None)
                if ws:
                    break
            except OSError:
                pass
            time.sleep(0.25)
        assert ws, "the browser did not open its DevTools port"
        cdp = _CDP(ws)
        out = []
        try:
            cdp.call("Page.enable")
            feats = [{"name": "prefers-color-scheme", "value": "dark" if dark else "light"},
                     {"name": "prefers-reduced-motion", "value": "reduce" if reduced else "no-preference"}]
            cdp.call("Emulation.setEmulatedMedia", features=feats)
            cdp.call("Emulation.setFocusEmulationEnabled", enabled=True)  # a headless page has no window focus; focus events need it
            cdp.call("Emulation.setDeviceMetricsOverride", width=width, height=900, deviceScaleFactor=1, mobile=False)
            cdp.call("Page.navigate", url=base + path)
            time.sleep(settle)
            for _ in range(40):
                if cdp.call("Runtime.evaluate", expression="document.readyState", returnByValue=True)["result"].get("value") == "complete":
                    break
                time.sleep(0.25)
            time.sleep(settle)
            for st in steps:
                if callable(st):
                    out.append(st(cdp))
                else:
                    res = cdp.call("Runtime.evaluate", expression=st, returnByValue=True, awaitPromise=True)
                    if "exceptionDetails" in res:
                        raise AssertionError(res["exceptionDetails"])
                    out.append(res["result"].get("value"))
                time.sleep(0.15)
        finally:
            cdp.close()
        return out
    finally:
        if proc is not None:
            stop_browser(proc, tmp)
        for _ in range(20):
            shutil.rmtree(tmp, ignore_errors=True)
            if not tmp.exists():
                break
            time.sleep(0.25)


def run(store, t, width, steps, **kw):
    with live_app(create_app(store.home)) as base:
        return session(base, f"/ticket/{t.id}", width, steps, **kw)


# what the page says about its chart, measured in the browser
STATE = """(function(){var box=document.getElementById('activity'),w=document.documentElement.clientWidth,over=[],split=[];
var spots=Array.prototype.slice.call(document.querySelectorAll('#activity .ac-spot'));
document.querySelectorAll('#activity *').forEach(function(e){var r=e.getBoundingClientRect();if(r.width&&r.right>w+1&&!e.closest('[hidden]'))over.push(e.className+' '+Math.round(r.right));});
document.querySelectorAll('#activity .ac-lab,#activity .ac-legend li,#activity .ac-head,#activity .ac-count').forEach(function(el){if(!el.offsetWidth)return;
 var tw=document.createTreeWalker(el,NodeFilter.SHOW_TEXT),t;while((t=tw.nextNode())){var re=/\\S+/g,m;while((m=re.exec(t.textContent))){
 var g=document.createRange();g.setStart(t,m.index);g.setEnd(t,m.index+m[0].length);var rs=Array.prototype.filter.call(g.getClientRects(),function(x){return x.width>0;});if(rs.length>1)split.push(m[0]);}}});
var strip=document.querySelector('.tl-strip').getBoundingClientRect(),track=document.querySelector('#activity .ac-track');var tr=track?track.getBoundingClientRect():{left:0,right:0};
var list=document.getElementById('tl-list');
return {w:w,scrollW:document.documentElement.scrollWidth,over:over,split:split,total:+box.getAttribute('data-total'),
 events:document.querySelectorAll('.tl-ev').length,marks:spots.reduce(function(s,b){return s+(+b.getAttribute('data-n'));},0),spots:spots.length,
 glyphs:document.querySelectorAll('#activity .ac-spot .ac-g').length,many:document.querySelectorAll('#activity .ac-many').length,
 manySum:Array.prototype.reduce.call(document.querySelectorAll('#activity .ac-many'),function(s,b){return s+(+b.querySelector('.ac-count').textContent);},0),
 minW:Math.min.apply(null,spots.map(function(b){return b.getBoundingClientRect().width;}).concat([999])),
 minH:Math.min.apply(null,spots.map(function(b){return b.getBoundingClientRect().height;}).concat([999])),
 chartTop:box.getBoundingClientRect().top,stripBottom:strip.bottom,listTop:list?list.getBoundingClientRect().top:null,
 trackL:tr.left,trackR:tr.right,stripL:strip.left+6,stripR:strip.right-6,visible:box.offsetHeight>0&&getComputedStyle(box).display!=='none',
 lanes:Array.prototype.map.call(document.querySelectorAll('#activity .ac-lane'),function(l){return [l.getAttribute('data-group'),l.querySelector('.ac-name').textContent,l.querySelector('.ac-n').textContent];}),
 legend:Array.prototype.filter.call(document.querySelectorAll('#activity .ac-legend li'),function(l){return !l.hidden;}).map(function(l){return l.textContent;}),
 bg:getComputedStyle(document.querySelector('.tl-strip-wrap')).backgroundColor,ink:getComputedStyle(document.querySelector('#activity .ac-lab')).color};})()"""


def oklab(h: str):
    """OKLab of a #rrggbb colour, for the distance checks below (the dataviz validator's formula)."""
    c = [int(h.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    r, g, b = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    l_, m_, s_ = [(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3), (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3),
                  (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)]
    return (0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_, 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
            0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_)


def dist(a: str, b: str) -> float:
    return 100 * sum((x - y) ** 2 for x, y in zip(oklab(a), oklab(b))) ** 0.5


def lum(h: str) -> float:
    c = [int(h.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    r, g, b = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# ============================================================ 1. open by default, under the strip, above the list

def test_route_the_chart_is_on_the_page_under_the_strip_and_above_the_list_without_a_click(store):
    """Protects acceptance 1 by the server's HTML: the chart sits inside the strip's box, between the strip and its date marks, before
    the event list, inside no fold, with its own stylesheet and script."""
    t = mixed(store)
    html = page(store, t)
    i_strip, i_chart, i_list = html.index('class="tl-strip"'), html.index('id="activity"'), html.index('id="tl-list"')
    assert i_strip < i_chart < i_list
    assert html.index('id="tl-strip"') < i_chart < html.index('class="tl-ends"')
    chart = html[i_chart:html.index("</ol>", i_chart)]
    assert "<details" not in chart and " hidden" not in chart.split('class="ac-tip"')[0]
    assert "/static/activity.css" in html and "/static/activity.js" in html


def test_static_the_include_line_and_the_frame_partial_exist():
    """Protects acceptance 1 by a read of the templates: ticket.html includes the chart right after the strip, and the partial carries
    the time axis ends and the event count the script needs."""
    tpl = (STATIC.parent / "templates" / "ticket.html").read_text(encoding="utf-8")
    part = (STATIC.parent / "templates" / "_activity.html").read_text(encoding="utf-8")
    strip_end = tpl.index("</ol>", tpl.index('class="tl-strip"'))
    assert tpl.index('{% include "_activity.html" %}') > strip_end and tpl.index('{% include "_activity.html" %}') < tpl.index('id="tl-axis"')
    for need in ('id="activity"', "data-start", "data-end", "data-total", "activity.js", "activity.css"):
        assert need in part, need


@NEEDS_BROWSER
@pytest.mark.parametrize("width", [1280, 390, 320])
def test_browser_the_chart_is_drawn_open_under_the_strip_and_above_the_list(store, width):
    """Protects acceptance 1 in a real page: the chart is visible on load with marks drawn, below the strip and above the list, and its
    rows span the strip's own width (the same time axis)."""
    t = mixed(store)
    d = run(store, t, width, [STATE])[0]
    assert d["visible"] and d["spots"] > 0, d
    assert d["stripBottom"] <= d["chartTop"] < d["listTop"], d
    assert abs(d["trackL"] - d["stripL"]) <= 1 and abs(d["trackR"] - d["stripR"]) <= 1, d


# ============================================================ 2. one mark per event; busy spots say how many

def test_unit_every_event_lands_in_exactly_one_spot_and_the_counts_add_up():
    """Protects acceptance 2 by the arithmetic: 0, 1 and 250 events (random times and rows, one unknown row) give spots whose counts
    add up to the events, each event once, every spot inside the track."""
    got = node("""(function(){var out=[];[0,1,250].forEach(function(n){var ev=[],seed=n+7;function rnd(){seed=(seed*16807)%2147483647;return seed/2147483647;}
      var groups=A.LANES.concat(['mystery']);for(var i=0;i<n;i++)ev.push({i:i,t:1000+Math.floor(rnd()*1e9),group:groups[Math.floor(rnd()*groups.length)],who:'patch'});
      var lay=A.layout(ev,1000,1000+1e9,1140),ids=[];lay.lanes.forEach(function(l){l.spots.forEach(function(s){s.events.forEach(function(e){ids.push(e.i);});});});
      out.push({n:n,sum:lay.lanes.reduce(function(s,l){return s+l.total;},0),ids:ids.sort(function(a,b){return a-b;}).join(','),spots:lay.n,
        inside:lay.lanes.every(function(l){return l.spots.every(function(s){return s.left>=0&&s.left+s.width<=100.0001;});})});});return out;})()""")
    for g in got:
        assert g["sum"] == g["n"] and g["ids"] == ",".join(str(i) for i in range(g["n"])) and g["inside"], g
    assert got[0]["spots"] == 43


def test_route_the_frame_counts_every_event_of_the_log_and_the_rows_carry_what_the_chart_reads(store):
    """Protects acceptance 2 by the server's HTML: data-total is the number of events in the log (plus nothing), each row the chart
    reads has a unique data-i, a row, a kind and a time, each kind with events has a row whose count is the rail's chip count, and
    each kind with none is named in the head line instead."""
    t = mixed(store, n=40)
    n_log = len(activity.read(store.home, t.id))
    html = page(store, t)
    total = int(re.search(r'id="activity"[^>]*data-total="(\d+)"', html).group(1))
    rows = re.findall(r'<li class="tl-ev [^"]*"([^>]*)>(<time class="tl-time" datetime="([^"]+)")', html)
    assert total == n_log == len(rows) == 40
    idx = sorted(int(re.search(r'data-i="(\d+)"', a).group(1)) for a, _, _ in rows)
    assert idx == list(range(40))
    assert all('data-group="' in a and 'data-kind="' in a for a, _, _ in rows)
    lanes = re.findall(r'class="ac-lane[^"]*" data-group="(\w+)"><span class="ac-lab"><span class="ac-name">([^<]+)</span> <span class="ac-n">([^<]+)</span>', html)
    chips = dict(re.findall(r'data-filter="(\w+)"[^>]*>[^<]*<span class="tl-n">(\d+)</span>', html))
    assert [k for k, _, _ in lanes] == [k for k in LANES if chips[k] != "0"]
    assert sum(int(n) for _, _, n in lanes) == total and all(n == chips[k] for k, _, n in lanes)
    idle = re.search(r'id="ac-none">None yet: ([^<]+)<', html)
    names = dict(re.findall(r'data-filter="(\w+)"[^>]*>([^<]+?) <span class="tl-n">', html))
    assert (idle.group(1).split(", ") if idle else []) == [names[k] for k in LANES if chips[k] == "0"]


@NEEDS_BROWSER
@pytest.mark.parametrize("width", [1280, 390, 320])
def test_browser_the_marks_add_up_to_the_events_in_the_list(store, width):
    """Protects acceptance 2 in a real page, on a 230 event ticket: the spots' counts add up to the events in the list, a spot with
    more than two events prints its count (and the legend explains the number), and every smaller spot draws one mark per event."""
    t = busy(store)
    d = run(store, t, width, [STATE])[0]
    assert d["total"] == d["events"] == d["marks"] == 230, d
    assert d["many"] > 0 and d["manySum"] + d["glyphs"] == 230, d
    assert any("a number: that many events in one spot" in x for x in d["legend"]), d["legend"]


# ============================================================ 3. rows in words; who by colour, shape and a legend in words

def test_static_who_has_a_shape_as_well_as_a_colour_and_no_colour_is_a_status_colour():
    """Protects acceptance 3 by a read of the stylesheet and the colour table: each kind of actor has its own shape rule, the main
    session and code sessions share Patch's colour, every actor colour is at least 10 OKLab units from every status colour (DESK-0306)
    and has 3:1 contrast on the card in both themes."""
    css = (STATIC / "activity.css").read_text(encoding="utf-8")
    shapes = {w: re.search(rf"\.ac-w-{w}\{{([^}}]*)\}}", css).group(1) for w in ("owner", "patch", "coworker", "agent", "rule", "other")}
    assert "50%" in shapes["owner"] and "2px" in shapes["patch"] and "rotate(45deg)" in shapes["coworker"]
    assert "clip-path" in shapes["agent"] and "transparent" in shapes["rule"] and "transparent" in shapes["other"] and "50%" in shapes["other"]
    assert len(set(shapes.values())) == 6
    k = timeline.KIND_COLOURS
    assert k["main"] == k["code"]
    desk = (STATIC / "desk.css").read_text(encoding="utf-8")
    status = re.findall(r"--st-\w+:(#[0-9A-Fa-f]{6})", desk) + [re.search(r"--ball:(#[0-9A-Fa-f]{6})", desk).group(1), re.search(r"--red:(#[0-9A-Fa-f]{6})", desk).group(1)]
    for kind, c in k.items():
        assert min(dist(c, s) for s in status) >= 10, (kind, c)
        assert contrast(c, "#FFFFFF") >= 3 and contrast(c, "#1D1D1B") >= 3, (kind, c)
    hues = [k["owner"], k["main"], k["coworker"]]
    assert min(dist(a, b) for i, a in enumerate(hues) for b in hues[i + 1:]) >= 15


def test_route_the_rows_are_named_in_words_with_their_counts_and_an_empty_kind_says_none_yet(store):
    """Protects acceptance 3 by the server's HTML: each kind with events is a row named in the chips' words (Decisions, Status,
    Comments, Changes, Git) with its count, in that order; the kinds with none are named in the head line ("None yet: ..."), so every
    kind is named and a short life takes one row, not five (DESK-0305's 700 px timeline budget)."""
    base = dt.datetime.now().replace(microsecond=0) - dt.timedelta(days=2)
    t = make(store, "Only comments", lambda t: [E(t, base, "owner-a", "owner", "created", "created"),
                                                 E(t, base + dt.timedelta(hours=3), "chaz", "coworker", "comment", "comment", what="Looks right")], base)
    html = page(store, t)
    lanes = re.findall(r'data-group="(\w+)"><span class="ac-lab"><span class="ac-name">([^<]+)</span> <span class="ac-n">([^<]+)</span>', html)
    assert lanes == [("comments", "Comments", "1"), ("changes", "Changes", "1")]
    assert '<span class="ac-none" id="ac-none">None yet: Decisions, Status, Git</span>' in html


@NEEDS_BROWSER
@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_browser_the_legend_names_who_in_words_and_each_mark_has_its_shape_and_name(store, dark):
    """Protects acceptance 3 in a real page: the legend lists each kind of actor present in words with its count and its shape, the
    marks differ by shape (radius, rotation, clip, hollow), and every spot's accessible name says who in words."""
    t = mixed(store, n=42)
    d = run(store, t, 1280, [STATE, """(function(){var g={};document.querySelectorAll('#activity .ac-spot .ac-g').forEach(function(x){var s=getComputedStyle(x);
      var w=(x.className.match(/ac-w-(\\w+)/)||[])[1];g[w]=[s.borderRadius,s.transform,s.clipPath,s.backgroundColor,s.borderTopColor].join('|');});
      var names=Array.prototype.map.call(document.querySelectorAll('#activity .ac-spot'),function(b){return b.getAttribute('aria-label');});
      return {shapes:g,names:names};})()"""], dark=dark)
    s, m = d
    legend = " / ".join(s["legend"])
    for words in ("(owner)", "code sessions", "Coworkers", "Agents", "Rules", "Other people"):
        assert words in legend, (words, legend)
    assert ("a number: that many events in one spot" in legend) is (s["many"] > 0), (legend, s["many"])  # explained only when drawn
    assert len(m["shapes"]) == 6 and len(set(m["shapes"].values())) == 6, m["shapes"]
    assert all(re.search(r" by .+, \w{3} \d+ \w{3}", n) or re.search(r"\d+ events, ", n) for n in m["names"]), m["names"][:5]


# ============================================================ 4. hover, focus and a tap name the event

def test_unit_a_spot_lists_its_events_newest_first_and_says_its_span_in_words():
    """Protects acceptance 4 by the arithmetic behind the words: a spot's events are newest first, its span is one time, a range on one
    day, or a range across days, and the year is named only when it is not the axis's year."""
    t = "Date.UTC(2026,10,18,21,9)"
    got = node(f"""(function(){{var a={{i:0,t:Date.UTC(2026,10,18,10,47)}},b={{i:1,t:{t}}},c={{i:2,t:Date.UTC(2026,10,17,22,0)}};
      var lay=A.layout([{{i:0,t:a.t,group:'comments',who:'owner'}},{{i:1,t:b.t,group:'comments',who:'patch'}}],Date.UTC(2026,10,18),Date.UTC(2026,10,19),26);
      return {{order:lay.lanes[2].spots[0].events.map(function(e){{return e.i;}}),one:A.span([b],2026),day:A.span([b,a],2026),days:A.span([b,c],2026),
        midnight:A.when(Date.UTC(2026,10,8),2026),old:A.when(Date.UTC(2023,1,3,9,5),2026),bad:A.when(NaN,2026)}};}})()""")
    assert got["order"] == [1, 0]
    assert got["one"] == "Wed 18 Nov 21:09" and got["day"] == "Wed 18 Nov 10:47 to 21:09" and got["days"] == "Tue 17 Nov 22:00 to Wed 18 Nov 21:09"
    assert got["midnight"] == "Sun 8 Nov" and got["old"] == "Fri 3 Feb 2023 09:05" and got["bad"] == "Date not recorded"


TIP = """(function(){var t=document.querySelector('#activity .ac-tip');return {shown:!t.hidden&&t.offsetHeight>0,text:t.textContent,
  inside:t.getBoundingClientRect().right<=document.documentElement.clientWidth+1&&t.getBoundingClientRect().left>=-1};})()"""


def _spot_with(text_js: str) -> str:
    """JS: the first spot whose accessible name contains `text_js`, scrolled into view and returned."""
    return f"""(function(){{var b=Array.prototype.find.call(document.querySelectorAll('#activity .ac-spot'),function(x){{return x.getAttribute('aria-label').indexOf({text_js})>=0;}});
      b.scrollIntoView({{block:'center'}});window.__b=b;var r=b.getBoundingClientRect();return [r.left+r.width/2,r.top+r.height/2];}})()"""


@NEEDS_BROWSER
@pytest.mark.parametrize("width", [1280, 390])
def test_browser_real_mouse_hover_names_the_event_with_time_who_and_what(store, width):
    """Protects acceptance 4 with real mouse input over the DevTools protocol: moving the pointer onto a one-event spot shows its time,
    who and what, inside the window; moving it away hides it."""
    base = dt.datetime.now().replace(microsecond=0, second=0) - dt.timedelta(days=3)
    t = make(store, "Hover me", lambda t: [E(t, base, "owner-a", "owner", "created", "created"),
                                            E(t, base + dt.timedelta(days=1, hours=2), "chaz", "coworker", "comment", "comment", what="The boards are posted")], base)
    pos = {}

    def hover(cdp):
        x, y = pos["xy"]
        cdp.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
        return None

    def away(cdp):
        cdp.call("Input.dispatchMouseEvent", type="mouseMoved", x=2, y=2)
        return None

    def where(cdp):
        pos["xy"] = cdp.call("Runtime.evaluate", expression=_spot_with("'The boards are posted'"), returnByValue=True)["result"]["value"]
        return pos["xy"]

    _, _, on, _, off = run(store, t, width, [where, hover, TIP, away, TIP])
    when = (base + dt.timedelta(days=1, hours=2)).strftime("%H:%M")
    assert on["shown"] and on["inside"] and "The boards are posted" in on["text"] and "chaz" in on["text"].lower() and when in on["text"], on
    assert not off["shown"], off


@NEEDS_BROWSER
def test_browser_keyboard_focus_names_the_event_and_arrows_move_between_spots(store):
    """Protects acceptance 4 for the keyboard: the chart is one Tab stop; focusing it names the newest spot; arrow keys move to another
    spot and name it; Escape hides the name."""
    t = mixed(store)
    got = run(store, t, 1280, [
        "(function(){var s=document.querySelectorAll('#activity .ac-spot[tabindex=\"0\"]');s[0].focus();return s.length;})()",
        TIP,
        "(function(){var a=document.activeElement;a.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowLeft',bubbles:true}));return [a===document.activeElement,document.activeElement.className];})()",
        TIP,
        "(function(){document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowUp',bubbles:true}));return document.activeElement.closest('.ac-lane')&&document.activeElement.closest('.ac-lane').getAttribute('data-group');})()",
        "(function(){document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));return 1;})()",
        TIP])
    stops, first, moved, second, lane, _, hidden = got
    assert stops == 1 and first["shown"] and first["text"], got
    assert moved[0] is False and "ac-spot" in moved[1] and second["shown"] and second["text"] != first["text"], got
    assert lane in LANES and not hidden["shown"], got


@NEEDS_BROWSER
def test_browser_a_tap_names_the_event_and_a_second_tap_shows_it_in_the_list(store):
    """Protects acceptance 4 for touch, with real touch input over the DevTools protocol: the first tap on a spot names it and says
    "Tap again"; the second tap shows the event in the list."""
    base = dt.datetime.now().replace(microsecond=0, second=0) - dt.timedelta(days=3)
    t = make(store, "Tap me", lambda t: [E(t, base, "owner-a", "owner", "created", "created"),
                                          E(t, base + dt.timedelta(days=2), "chaz", "coworker", "comment", "comment", what="Tap target comment")], base)
    pos = {}

    def where(cdp):
        cdp.call("Emulation.setTouchEmulationEnabled", enabled=True, maxTouchPoints=1)
        pos["xy"] = cdp.call("Runtime.evaluate", expression=_spot_with("'Tap target comment'"), returnByValue=True)["result"]["value"]
        return pos["xy"]

    def tap(cdp):
        x, y = pos["xy"]
        cdp.call("Input.dispatchTouchEvent", type="touchStart", touchPoints=[{"x": x, "y": y}])
        cdp.call("Input.dispatchTouchEvent", type="touchEnd", touchPoints=[])
        time.sleep(0.4)
        return None

    hit = "(function(){var h=document.querySelectorAll('.tl-ev.ac-hit');return h.length;})()"
    _, _, first, n1, _, n2 = run(store, t, 390, [where, tap, TIP, hit, tap, hit])
    assert first["shown"] and "Tap target comment" in first["text"] and "Tap again" in first["text"], first
    assert n1 == 0 and n2 == 1, (n1, n2)


# ============================================================ 5. click or Enter shows the event in the list

def test_route_every_row_the_chart_can_point_at_is_on_the_page_even_past_the_first_12(store):
    """Protects acceptance 5 by the server's HTML: every event row is on the page (hidden past the newest 12 or inside a git fold, not
    missing), so a click on any mark has a row to show."""
    t = busy(store, n=60)
    html = page(store, t)
    rows = re.findall(r'<li class="tl-ev [^"]*"[^>]*data-i="(\d+)"', html)
    assert len(rows) == 60 and ' hidden' in html.split('id="tl-list"')[1]


@NEEDS_BROWSER
@pytest.mark.parametrize("how", ["click", "enter"])
def test_browser_click_or_enter_opens_the_list_and_outlines_an_event_past_the_first_12(store, how):
    """Protects acceptance 5 in a real page: with the Decisions chip on, activating the oldest spot (whose events are past the newest
    12, some inside a git fold) puts All back, opens Show all and the fold, outlines every event of the spot and scrolls the newest of
    them into view with focus on it."""
    t = busy(store, n=80)
    act = ("b.click()" if how == "click" else "b.focus();b.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))")
    got = run(store, t, 1280, [
        "document.querySelector('.tl-chip[data-filter=decisions]').click()",
        f"""(function(){{var spots=Array.prototype.slice.call(document.querySelectorAll('#activity .ac-lane[data-group=git] .ac-spot'));var b=spots[0];
          {act};return b.getAttribute('data-i').split(' ').length;}})()""",
        """new Promise(function(res){setTimeout(function(){var h=Array.prototype.slice.call(document.querySelectorAll('.tl-ev.ac-hit'));var a=document.activeElement;var r=a.getBoundingClientRect();
          res({hit:h.length,shown:h.every(function(li){return li.offsetHeight>0;}),focus:a.classList.contains('ac-hit'),inView:r.top>=0&&r.bottom<=innerHeight,
           idx:h.map(function(li){return +li.getAttribute('data-i');}),all:document.querySelector('.tl-chip[data-filter=all]').getAttribute('aria-pressed'),
           more:document.getElementById('tl-showall').getAttribute('aria-expanded')});},900);})"""])
    _, n, d = got
    assert d["hit"] == n and d["shown"] and d["focus"] and d["inView"], d
    assert min(d["idx"]) >= 12 and d["all"] == "true" and d["more"] == "true", d


@NEEDS_BROWSER
def test_browser_real_mouse_click_jumps_to_the_event_in_the_list(store):
    """Protects acceptance 5 with a real mouse click over the DevTools protocol on a one-event spot: that row is outlined, focused and
    on screen."""
    t = mixed(store, n=36)
    pos = {}

    def where(cdp):
        pos["xy"] = cdp.call("Runtime.evaluate", expression=_spot_with("'Comment number 2 '"), returnByValue=True)["result"]["value"]
        return pos["xy"]

    def click(cdp):
        x, y = pos["xy"]
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            cdp.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", clickCount=1)
        time.sleep(0.8)
        return None

    d = run(store, t, 1280, [where, click, """(function(){var h=document.querySelectorAll('.tl-ev.ac-hit');var a=document.activeElement,r=a.getBoundingClientRect();
      return {n:h.length,text:h[0]&&h[0].textContent,focus:a===h[0],inView:r.top>=0&&r.bottom<=innerHeight};})()"""])[2]
    assert d["n"] == 1 and "Comment number 2 " in d["text"] and d["focus"] and d["inView"], d


# ============================================================ 6. widths, themes, reduced motion, targets

@NEEDS_BROWSER
@pytest.mark.parametrize("width", [1280, 390, 320])
@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_browser_no_sideways_scroll_no_split_word_and_44_px_targets_light_and_dark(store, width, dark):
    """Protects acceptance 5's layout lines at 1280, 390 and 320 px, light and dark: nothing in the chart past the right edge, no
    sideways scroll, no word on two lines in a row name, the legend or a count, every spot at least 24 px wide and 44 px tall, and
    the chart's text in the theme's ink on the theme's card."""
    t = busy(store, n=120)
    d = run(store, t, width, [STATE], dark=dark)[0]
    assert d["scrollW"] <= d["w"] and d["over"] == [] and d["split"] == [], d
    assert d["minW"] >= 24 and d["minH"] >= 44, d
    assert d["ink"] == ("rgb(245, 243, 236)" if dark else "rgb(20, 20, 20)"), d
    assert d["bg"] == ("rgb(29, 29, 27)" if dark else "rgb(255, 255, 255)"), d


@NEEDS_BROWSER
@pytest.mark.parametrize("width", [320, 390])
def test_browser_on_a_phone_the_chart_is_short_and_the_first_status_change_stays_near_the_top(store, width):
    """Protects the phone budget DESK-0305 set (the first status change or decision within 1.5 phone screens of the timeline's top) with
    the chart in place and every row busy, on the real page with all its styles: the chart's rows take at most 300 px, the chart's
    first status or decision mark is within 1.5 x 844 px of the timeline's top and above the list's, and a tap on it opens that event
    in the list."""
    from test_visual_timeline import _burst  # DESK-0305's own phone fixture: a busy ticket, the owner's comment, a burst of git saves

    t, _ = _burst(store)
    d = run(store, t, width, ["""(function(){var top0=document.getElementById('timeline').getBoundingClientRect().top;
      var rows=Array.prototype.filter.call(document.querySelectorAll('.tl-ev,.tl-foldh'),function(li){return li.offsetHeight>0;});
      var first=rows.filter(function(li){return /ek-(status|decision)/.test(li.className);})[0];
      var m=document.querySelector('#activity .ac-lane[data-group=decisions] .ac-spot,#activity .ac-lane[data-group=status] .ac-spot');
      var out={plot:document.querySelector('#activity .ac-plot').offsetHeight,firstTop:first?first.getBoundingClientRect().top-top0:-1,firstRow:rows.indexOf(first),
        markTop:m?m.getBoundingClientRect().top-top0:-1};
      m.click();var h=document.querySelector('.tl-ev.ac-hit');out.opened=!!h&&h.offsetHeight>0&&/ek-(status|decision)/.test(h.className);return out;})()"""])[0]
    assert 0 < d["plot"] <= 300 and 0 <= d["firstRow"] < 6 and 0 < d["markTop"] < 1.5 * 844 and d["markTop"] < d["firstTop"], d
    assert d["opened"], d


def test_static_motion_only_when_the_reader_allows_it_and_the_jump_respects_it():
    """Protects reduced motion by a read of the code: the chart's only transition sits under prefers-reduced-motion: no-preference, and
    the jump scrolls instantly when reduced motion is asked for."""
    css = (STATIC / "activity.css").read_text(encoding="utf-8")
    js = JS.read_text(encoding="utf-8")
    outside = re.sub(r"@media \(prefers-reduced-motion:no-preference\)\{[^@]*?\}\}", "", css)
    assert "transition" not in outside and "animation" not in outside
    assert "prefers-reduced-motion: reduce" in js and 'behavior: reduce ? "auto" : "smooth"' in js


@NEEDS_BROWSER
@pytest.mark.parametrize("reduced", [False, True], ids=["motion", "reduced"])
def test_browser_reduced_motion_turns_off_the_transition(store, reduced):
    """Protects reduced motion in a real page: with prefers-reduced-motion: reduce emulated the spots have no transition; without it
    they fade their hover in."""
    t = mixed(store)
    d = run(store, t, 1280, ["getComputedStyle(document.querySelector('#activity .ac-spot')).transitionDuration"], reduced=reduced)[0]
    assert (d == "0s") is reduced, d


# ============================================================ 7. edge cases

def test_route_no_events_says_so_and_draws_no_rows(store):
    """Protects the empty edge: a ticket whose log is empty and whose created date cannot be read says "No events recorded yet." and
    draws no rows (nothing to point at)."""
    t = make(store, "Nothing happened", lambda t: [], dt.datetime.now())
    x = store.get(t.id)
    x.meta["created"] = ""
    x.write()
    html = page(store, store.get(t.id))
    chart = html[html.index('id="activity"'):html.index("activity.js")]
    assert "No events recorded yet." in chart and 'data-total="0"' in chart and "ac-lane" not in chart


@NEEDS_BROWSER
def test_browser_no_events_and_one_event_render_sensibly(store):
    """Protects the empty and one-event edges in a real page at 320 px: no events draws the sentence and no spot, without a script
    error; one event draws one spot with one mark, at the right end (now) when it happened just now."""
    empty = make(store, "Nothing happened", lambda t: [], dt.datetime.now())
    x = store.get(empty.id)
    x.meta["created"] = ""
    x.write()
    made = dt.datetime.now().replace(microsecond=0, second=0) - dt.timedelta(hours=2)
    one = make(store, "One thing happened", lambda t: [E(t, made, "owner-a", "owner", "created", "created", what="Created: one")], made)
    with live_app(create_app(store.home)) as base:
        e = session(base, f"/ticket/{empty.id}", 320, ["(function(){return {spots:document.querySelectorAll('.ac-spot').length,text:document.getElementById('activity').textContent,w:document.documentElement.scrollWidth};})()"])[0]
        o = session(base, f"/ticket/{one.id}", 320, [STATE, "(function(){var b=document.querySelector('.ac-spot'),t=document.querySelector('#activity .ac-track').getBoundingClientRect(),r=b.getBoundingClientRect();return [b.getAttribute('data-bin'),r.left-t.left];})()"])
    assert e["spots"] == 0 and "No events recorded yet." in e["text"] and e["w"] <= 320, e
    assert o[0]["spots"] == 1 and o[0]["marks"] == 1 and o[0]["glyphs"] == 1 and o[0]["scrollW"] <= 320, o
    assert o[1][0] == "0" and o[1][1] < 1, o  # its creation: the first spot, under "Created" at the left end of the axis


def test_unit_all_on_one_day_spreads_across_the_track_and_years_ago_lands_by_date():
    """Protects the one-day and years-ago edges by the arithmetic: events through one day spread across the spots of a one-day axis;
    on a three-year axis the old ones sit at the left, this week's at the right; a range of no length puts everything at now."""
    got = node("""(function(){var d0=Date.UTC(2026,10,19,0,0),ev=[];for(var h=0;h<24;h++)ev.push({i:h,t:d0+h*3600e3,group:'git',who:'patch'});
      var one=A.layout(ev,d0,d0+86400e3,1140).lanes[4].spots.map(function(s){return s.bin;});
      var y0=Date.UTC(2023,10,19),y1=Date.UTC(2026,10,19);var old=A.layout([{i:0,t:y0+86400e3,group:'status',who:'owner'},{i:1,t:y1-3600e3,group:'status',who:'owner'}],y0,y1,1140).lanes[1].spots;
      return {one:one,n:A.spots(1140),old:old.map(function(s){return s.bin;}),zero:A.spotOf(5,5,5,10),before:A.spotOf(1,5,9,10),after:A.spotOf(99,5,9,10),
        parse:[A.parseTs('2026-11-18T21:09:05'),A.parseTs('2026-11-08'),A.parseTs('junk')]};})()""")
    assert len(got["one"]) == 24 and got["one"][0] == 0 and got["one"][-1] == 23 * got["n"] // 24  # 24 hours, 24 separate spots, by time
    assert got["old"] == [0, got["n"] - 1]
    assert got["zero"] == 9 and got["before"] == 0 and got["after"] == 9
    assert got["parse"][0] == 1795036145000 and got["parse"][1] == 1794096000000 and got["parse"][2] is None  # Date.UTC of the same fields


@NEEDS_BROWSER
def test_browser_a_ticket_created_years_ago_names_the_year_and_all_events_on_one_day_spread_out(store):
    """Protects the years-ago and one-day edges in a real page: a ticket created three years ago draws its old event at the left and its
    spot's name says the year; a ticket whose 30 events all fell on one day (five days ago) bunches them into busy spots that print how
    many, the counts adding up to the events."""
    now = dt.datetime.now().replace(microsecond=0, second=0)
    old = now - dt.timedelta(days=3 * 365)
    years = make(store, "Created long ago", lambda t: [E(t, old, "owner-a", "owner", "created", "created", what="Created long ago"),
                                                        E(t, now - dt.timedelta(hours=2), "chaz", "coworker", "comment", "comment", what="Still here")], old)
    day = (now - dt.timedelta(days=5)).replace(hour=9, minute=0)
    today = make(store, "A busy day", lambda t: [E(t, day + dt.timedelta(minutes=17 * i), "claude-code", "code", "git", "git", "u", f"c{i}", f"Saved {i}")
                                               for i in range(30)], day)
    with live_app(create_app(store.home)) as base:
        y = session(base, f"/ticket/{years.id}", 1280, ["(function(){var b=document.querySelector('.ac-lane[data-group=changes] .ac-spot');return [b.getAttribute('data-bin'),b.getAttribute('aria-label')];})()"])[0]
        d = session(base, f"/ticket/{today.id}", 1280, [STATE])[0]
    assert y[0] == "0" and str(old.year) in y[1], y
    assert d["marks"] == d["events"] == d["total"] == 31 and d["many"] >= 1 and d["manySum"] + d["glyphs"] == 31, d  # 30 saves, the file's Created
