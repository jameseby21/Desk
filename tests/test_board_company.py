"""DESK-0183: the board and Today open at companies (a company is the `area:` of a project profile until DESK-0181 adds company
profiles); each click opens one level more and the address holds it. Protects: James's third ask (2026-11-08) for a board that
opens at the broadest level and shows nothing else."""

import re
import time

from fastapi.testclient import TestClient

from desk import bench, boardview as bv, companies, ops
from desk.ui.app import create_app

AREAS = {"jre": "Personal", "desk": "jebyLabs (free and open tools)", "jeby-us": "Client work (JA Insights)", "lessons": "Teaching and writing", "lab": "Research"}
JEBYLABS = "jebylabs-free-and-open-tools"
SEP = "›"  # the single right-pointing angle quotation mark between company and project


def _profile(store, key, title, area=None):
    fm = f"---\nkey: {key}\nname: {title}\n" + (f"area: {area}\n" if area else "") + "---\n"
    (store.projects_dir / f"{key}.md").write_text(fm + f"# {title}\n", encoding="utf-8")


def _mk(store, title, project, **meta):
    t = ops.new(store, title, project)
    t.meta.update(meta)
    t.write()
    return t


def _cards(page):
    return re.findall(r'<div class="cc-title" title="([^"]*)"', page)


def _fx(store, unsorted=False):
    titles = {"jre": "jRE", "desk": "Desk", "jeby-us": "jeby.us", "lessons": "Lessons", "lab": "Lab"}
    for key, area in AREAS.items():
        _profile(store, key, titles[key], area)
    if unsorted:
        _profile(store, "loose", "Loose")
        _mk(store, "Loose end", "loose", status="ready")
    _mk(store, "Alpha ui", "desk", track="UI", priority="p0", status="ready")
    _mk(store, "Beta ui", "desk", track="UI", priority="p2", status="in_progress")
    _mk(store, "Gamma core", "desk", track="Core", priority="p2", status="review")
    _mk(store, "Delta core", "desk", track="Core", status="done")
    _mk(store, "Errand", "jre", priority="p1", status="claimed")
    _mk(store, "Chore", "jre", priority="p1", status="ready")
    _mk(store, "Client thing", "jeby-us", status="ready", assignee="chaz")
    _mk(store, "Lesson done", "lessons", status="done")
    _mk(store, "Lab stuck", "lab", priority="p3", status="blocked")
    return TestClient(create_app(store.home))


def test_default_board_is_five_company_rows_and_nothing_else(store):
    page = _fx(store).get("/board").text
    assert len(re.findall(r'<a class="bcomp"', page)) == 5
    assert 'class="bproj"' not in page and _cards(page) == []  # no project rows, no cards until a company is opened
    assert ">jebyLabs</b>" in page and ">free and open tools</span>" in page  # the parenthetical is the muted subtitle
    assert ">Client work</b>" in page and ">JA Insights</span>" in page
    row = page[page.index(f'data-slug="{JEBYLABS}"'):]
    row = row[:row.index("</a>")]
    assert "1 project · 3 open · 1 in hand · 1 review · 25% done" in row and "top p0 &times;1" in row
    assert 'href="/board?company=' in page


def test_company_rows_sort_by_in_hand_then_open(store):
    _fx(store)
    names = [c.title for c in bv.companies(store, bv.build(store, store.tickets()))]
    assert names[:3] == ["jebyLabs", "Personal", "Client work"]  # 1 in hand + 3 open, 1 + 2, then 0 + 1 open


def test_company_row_counts_equal_the_sum_of_its_projects(store):
    _fx(store)
    _profile(store, "jre", "jRE", "jebyLabs (free and open tools)")  # two projects in one company
    rows = bv.build(store, store.tickets())
    for c in bv.companies(store, rows):
        mine = [r for r in rows if companies.of(store, r.key) == c.name]
        assert c.projects == len(mine)
        assert c.open == sum(r.open for r in mine) and c.total == sum(r.total for r in mine)
        assert c.in_hand == sum(r.counts["claimed"] + r.counts["in_progress"] for r in mine)
        assert c.review == sum(r.counts["review"] for r in mine) and c.done == sum(r.counts["done"] for r in mine)
    lab = next(c for c in bv.companies(store, rows) if c.title == "jebyLabs")
    assert lab.projects == 2 and lab.open == 5 and lab.in_hand == 2


def test_drill_url_round_trip_company_project_track(store):
    cl = _fx(store)
    co = cl.get(f"/board?company={JEBYLABS}").text
    assert re.findall(r'<details class="bproj"[^>]*data-val="([^"]+)"', co) == ["desk"] and _cards(co) == []
    assert '<nav class="crumbs"' in co and '<a href="/board">jeby</a>' in co and ">jebyLabs</a>" in co
    pr = cl.get(f"/board?company={JEBYLABS}&open=desk").text
    assert re.search(r'<details class="bproj"[^>]*data-val="desk" open>', pr) and _cards(pr) == []
    assert re.findall(r'class="bgroup"[^>]*data-val="([^"]+)"', pr) == ["desk:ui", "desk:core"]  # DESK-0136: the group holding the best ticket (Alpha ui, p0 ready) first
    tr = cl.get(f"/board?company={JEBYLABS}&open=desk&g=desk:ui").text
    assert sorted(_cards(tr)) == ["Alpha ui", "Beta ui"]
    assert sorted(_cards(cl.get(f"/board?company={JEBYLABS}&open=desk&g=desk:ui").text)) == ["Alpha ui", "Beta ui"]  # a reload draws the same view
    assert sorted(_cards(cl.get("/board?open=desk&g=desk:ui").text)) == ["Alpha ui", "Beta ui"]  # an old link finds its company
    assert f'href="/board?company={JEBYLABS}"' in tr  # the breadcrumb collapses back to the company


def test_filters_narrow_the_company_rows(store):
    cl = _fx(store)
    page = cl.get("/board?assignee=chaz").text
    assert len(re.findall(r'<a class="bcomp"', page)) == 1 and ">Client work</b>" in page and 'class="bproj"' not in page
    assert 'href="/board?assignee=chaz&amp;company=client-work-ja-insights"' in page
    inside = cl.get("/board?company=client-work-ja-insights&assignee=chaz").text
    assert _cards(inside) == ["Client thing"]  # inside a company a filter still opens what is left (DESK-0034)
    assert 'name="company" value="client-work-ja-insights"' in inside  # the dropdown forms keep the drill


def test_a_profile_without_area_is_unsorted_and_says_so(store):
    page = _fx(store, unsorted=True).get("/board").text
    assert len(re.findall(r'<a class="bcomp"', page)) == 6
    assert ">Unsorted</b>" in page and "projects with no area in their profile" in page
    assert companies.of(store, "loose") == "Unsorted" and companies.split("Research") == ("Research", "")


def test_today_cards_show_company_and_project(store):
    page = _fx(store).get("/").text
    assert f"jebyLabs {SEP} Desk</span> · DESK-" in page  # Beta ui, in hand
    assert f"Personal {SEP} jRE</span> · JRE-" in page  # Errand, in hand


def test_company_dropdown_narrows_today(store):
    cl = _fx(store)
    page = cl.get("/").text
    assert '<select name="company" aria-label="Company"' in page and ">Personal (2)</option>" in page
    narrowed = cl.get("/?company=personal").text
    assert "Errand" in narrowed and "Beta ui" not in narrowed and "Gamma core" not in narrowed
    assert 'name="company" value="personal"' in narrowed  # the Track and Sprint dropdowns keep the company


def test_company_level_renders_under_300_ms_at_1400_tickets(tmp_path):
    s = bench.build_store(tmp_path / "h", 1400)
    for i, p in enumerate(sorted(s.projects_dir.glob("*.md"))):
        p.write_text(p.read_text(encoding="utf-8").replace("\nname:", f"\narea: Area {i % 5}\nname:", 1), encoding="utf-8")
    cl = TestClient(create_app(s.home))
    assert cl.get("/board").status_code == 200  # warm: the first request parses every file once
    took = []
    for _ in range(5):
        t0 = time.perf_counter()
        page = cl.get("/board").text
        took.append((time.perf_counter() - t0) * 1000)
    assert len(re.findall(r'<a class="bcomp"', page)) == 5
    print(f"company level at 1,400 tickets: median {sorted(took)[2]:.0f} ms, all {[round(x) for x in took]}")
    assert sorted(took)[2] < 300, took


# -- a real browser: the calm-board rules of DESK-0035 hold at the company and project levels -----------------------------------

import pytest  # noqa: E402
from pathlib import Path  # noqa: E402
from browser_harness import browser, run_in_browser  # noqa: E402

STATIC = Path(__file__).resolve().parents[1] / "src" / "desk" / "ui" / "static"
MEASURE = """<script>addEventListener('load',function(){var w=document.documentElement.clientWidth,over=[];
document.querySelectorAll('.bcomp,.bproj>summary,.crumbs,.filters').forEach(function(e){var r=e.getBoundingClientRect();if(r.right>w+1)over.push(e.className+' '+Math.round(r.right));});
fetch('/m',{method:'POST',body:JSON.stringify({scroll:document.documentElement.scrollWidth,inner:w,over:over,rows:document.querySelectorAll('.bcomp,.bproj').length})});});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("level", ["/board", f"/board?company={JEBYLABS}&open=desk"])
def test_no_horizontal_scroll_at_the_company_and_project_levels(store, width, level):
    """Protects: at 1440 px and 390 px no row is wider than the page and the page never scrolls sideways (DESK-0035, DESK-0183)."""
    _fx(store)
    _profile(store, "lab", "Lab", "Research with a very long company name that goes on and on (and a long parenthetical too, for good measure)")
    html = TestClient(create_app(store.home)).get(level).text
    css = (STATIC / "desk.css").read_text(encoding="utf-8")
    html = re.sub(r'<link rel="stylesheet" href="/static/desk.css">', lambda m: f"<style>{css}</style>", html)
    html = re.sub(r'<link rel="stylesheet" href="https://fonts[^>]*>|<link rel="preconnect"[^>]*>', "", html)
    html = re.sub(r'<script src="/static/[^"]*"[^>]*></script>', "", html).replace("</body>", MEASURE + "</body>")
    data = run_in_browser(html, width)
    assert data["rows"] >= 1 and data["over"] == [] and data["scroll"] <= data["inner"], data
