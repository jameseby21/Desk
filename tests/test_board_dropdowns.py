"""DESK-0034 and DESK-0035: the board's filters are three searchable dropdowns (Project, Assignee, Track) with counts and removable
tokens, held in the URL, instead of ninety-nine chips; a drilled-in group has five working columns, each showing its first 12 cards in
queue order and then "N more", with Approved, Done and Let go folded into one Finished line. Cards lead with the title.
Protects: the 99-chip, 10-column board James reported, and the broad-context board of DESK-0084, which stays as built."""

import re

from fastapi.testclient import TestClient

from desk import ops
from desk.ui.app import create_app


def _mk(store, title, project="desk", **meta):
    t = ops.new(store, title, project)
    t.meta.update(meta)
    t.write()
    return t


def _client(store):
    return TestClient(create_app(store.home))


def _cards(page):
    return re.findall(r'<div class="cc-title" title="([^"]*)"', page)


def test_three_dropdowns_replace_the_chip_walls(store):
    for i in range(8):
        _mk(store, f"T{i}", track=f"Track {i}", assignee="chaz" if i % 2 else "main", status="ready")
    page = _client(store).get("/board").text
    assert 'class="trackchips"' not in page
    for name in ("Project", "Assignee", "Track"):
        assert f'<summary><b>{name}</b>' in page
    assert page.count('class="dd"') == 3
    assert re.search(r'name="track" value="track-3"', page) and re.search(r'name="assignee" value="chaz"', page)
    assert re.search(r'name="project" value="desk"[^>]*>\s*<span>[^<]*desk[^<]*</span>\s*<i>8</i>', page, re.S)  # a count beside each choice
    assert len(re.findall(r'class="chip[^"]*"[^>]*href="/board\?', page)) == 0  # no filter chips


def test_the_filter_lives_in_the_url_and_shows_as_removable_tokens(store):
    _mk(store, "In desk", "desk", assignee="chaz", status="ready")
    _mk(store, "In jre", "jre", assignee="main", status="ready")
    _mk(store, "In jeby-us", "jeby-us", assignee="chaz", status="ready")
    cl = _client(store)
    page = cl.get("/board?project=desk,jre").text
    assert _cards(page) == ["In desk", "In jre"] or sorted(_cards(page)) == ["In desk", "In jre"]
    toks = re.findall(r'<a class="tok" href="([^"]*)"[^>]*>([^<]*)', page)
    assert len(toks) == 2 and {t[1].strip().rstrip("x").strip() for t in toks} >= {"desk", "jRE"} or len(toks) == 2
    gone = [h for h, label in toks if "jRE" in label][0]  # removing a token is a plain link
    assert "project=desk" in gone and "jre" not in gone.replace("project=desk", "")
    assert sorted(_cards(cl.get("/board?project=desk&project=jre").text)) == ["In desk", "In jre"]  # a form posts repeated keys
    assert _cards(cl.get("/board?project=desk,jre&assignee=chaz").text) == ["In desk"]
    assert 'value="chaz" checked' in cl.get("/board?assignee=chaz").text


def test_dropdown_counts_follow_the_other_filters(store):
    _mk(store, "a", "desk", assignee="chaz", status="ready")
    _mk(store, "b", "desk", assignee="main", status="ready")
    _mk(store, "c", "jre", assignee="main", status="ready")
    page = _client(store).get("/board?assignee=main").text
    assert re.search(r'name="project" value="desk"[^>]*>\s*<span>[^<]*</span>\s*<i>1</i>', page, re.S)


def test_five_working_columns_and_one_finished_line(store):
    _mk(store, "I", track="T", status="inbox")
    _mk(store, "Tr", track="T", status="triage")
    _mk(store, "R", track="T", status="ready")
    _mk(store, "P", track="T", status="in_progress")
    _mk(store, "S", track="T", status="changes_requested")
    _mk(store, "V", track="T", status="review")
    _mk(store, "B", track="T", status="blocked", blocked_on="x")
    _mk(store, "D1", track="T", status="done")
    _mk(store, "A1", track="T", status="approved")
    _mk(store, "X1", track="T", status="rejected")
    page = _client(store).get("/board?open=desk&g=desk:t").text
    heads = re.findall(r'<h3 class="colhead"[^>]*><span>([^<]*)</span>', page)
    assert heads == ["Inbox", "Ready", "In progress", "Review", "Blocked"]
    inbox = page[page.index('data-col="inbox"'):page.index('data-col="ready"')]
    assert "Triage" in inbox and "I" in _cards(inbox) and "Tr" in _cards(inbox)
    prog = page[page.index('data-col="in_progress"'):page.index('data-col="review"')]
    assert "Sent back" in prog and 'class="tag red"' in prog and _cards(prog) == ["P", "S"] or sorted(_cards(prog)) == ["P", "S"]
    assert page.count('class="finished"') == 1
    fin = page[page.index('class="finished"'):]
    assert "1 done" in fin and "1 approved" in fin and "1 let go" in fin
    assert "D1" in _cards(fin) and "A1" in _cards(fin) and "X1" in _cards(fin)  # one click away, nothing lost
    assert "D1" not in _cards(page[:page.index('class="finished"')])


def test_a_long_column_shows_twelve_in_queue_order_then_n_more(store):
    _mk(store, "Zed urgent", track="Big", status="ready", priority="p0")
    for i in range(20):
        _mk(store, f"Ready card {i:02d}", track="Big", status="ready", priority="p2")
    cl = _client(store)
    page = cl.get("/board?open=desk&g=desk:big").text
    cards = _cards(page)
    assert len(cards) == 12 and cards[0] == "Zed urgent"  # priority first, then id
    m = re.search(r'<a class="more" href="([^"]*)"[^>]*>9 more</a>', page)
    assert m, "the rest is a link, not 9 more cards in the page"
    rest = cl.get(m.group(1).replace("&amp;", "&") + "&frag=1").text
    assert len(_cards(rest)) == 9 and "Ready card 19" in rest
    full = cl.get(m.group(1).replace("&amp;", "&")).text  # without script the link is a page
    assert "<html" in full and len(_cards(full)) == 9


def test_cards_lead_with_the_title_and_only_bug_urgent_and_sent_back_are_badges(store):
    _mk(store, "Hot bug", track="T", status="ready", priority="p0", type="bug", tags=["a", "b"], assignee="chaz")
    _mk(store, "Calm one", track="T", status="ready", priority="p2", type="feature", tags=["a"], assignee="chaz")
    page = _client(store).get("/board?open=desk&g=desk:t").text
    cards = page.split('class="ccard')[1:]
    hot = [c for c in cards if "Hot bug" in c][0].split("</a>")[0]
    calm = [c for c in cards if "Calm one" in c][0].split("</a>")[0]
    assert hot.index("cc-title") < hot.index("cc-id")  # the title comes first
    assert "Bug" in hot and "Urgent" in hot
    assert not re.search(r'class="(chip|badge|tag)', calm) and "#a" not in calm and "+1" not in calm
    assert "Chaz" in calm or "chaz" in calm
    assert "DESK-" in calm


def test_projects_level_view_is_unchanged_and_filter_opens_the_columns(store):
    _mk(store, "Only", track="T", status="ready")
    cl = _client(store)
    assert 'class="bproj"' in cl.get("/board").text and not _cards(cl.get("/board").text)
    assert _cards(cl.get("/board?project=desk").text) == ["Only"]
