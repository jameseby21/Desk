"""DESK-0290: palette actions, keyboard chords, saved views, full-text search, theme and type size, drafts, the 390 px phone check.

Protects: James finds what he said before instead of saying it again (words inside a ticket's body, its comments, docs and intake), acts
on the open ticket from the keyboard through the same routes as the buttons, moves between pages without the mouse, keeps a pinned
filter, reads in the theme and size he chose on that device, never loses an unsent comment, and the pages still fit a phone."""

import datetime as dt
import json
import re
from pathlib import Path

import pytest
from browser_harness import browser, drive
from fastapi.testclient import TestClient

from desk import activity, lanes, ops, saved_views, search
from desk.ui.app import create_app

JSON = {"Accept": "application/json"}
STATIC = Path(__file__).resolve().parents[1] / "src" / "desk" / "ui" / "static"


def _t(store, title, project="desk", body="", comments="", **meta):
    t = ops.new(store, title, project)
    if body:
        t.set_section("Context", body)
    if comments:
        t.set_section("Comments", comments)
    t.meta.update(meta)
    if meta.get("status") == "review":
        t.set_section("Report", "Built and checked; the tests pass.")
    store.save(t)
    return t


def _ids(res):
    return [h.id for _, _, hs in res.groups for h in hs]


def _tok(c):
    return c.app.state.tokens.issue()


# -- full-text search ------------------------------------------------------------------------------------------------------

def test_a_word_only_in_the_body_or_a_comment_is_found_with_its_snippet(store):
    """A word said only inside a ticket (any section, or a comment) finds that ticket, and the hit shows the words around it."""
    a = _t(store, "Plain title one", body="Background.\n\nThe owner mentioned the quokkaline speaker before the round.")
    b = _t(store, "Plain title two", comments="- 2026-11-15 · james · Remember the zanzibarite cable.")
    c = _t(store, "Plain title three")
    c.set_section("Out of scope", "Nothing about the marmalith here.")  # a section the old index skipped
    store.save(c)
    c_ = TestClient(create_app(store.home))
    d = c_.get("/search.json", params={"q": "quokkaline"}).json()
    hits = [h for g in d["groups"] for h in g["hits"]]
    assert [h["id"] for h in hits] == [a.id]
    assert "[quokkaline] speaker" in search.plain_snippet(hits[0]["snippet"]) and "mentioned the" in hits[0]["snippet"]
    assert [h["id"] for g in c_.get("/search.json", params={"q": "zanzibarite"}).json()["groups"] for h in g["hits"]] == [b.id]
    assert [h["id"] for g in c_.get("/search.json", params={"q": "marmalith"}).json()["groups"] for h in g["hits"]] == [c.id]
    page = c_.get("/search", params={"q": "quokkaline"}).text
    assert a.id in page and "<mark>quokkaline</mark>" in page


BRACKETS = "- [ ] a step with [a link](http://x) then the zorblat word and - [x] done"


def test_markdown_brackets_in_a_snippet_are_not_marked_as_matches(store):
    """Only the searched word is highlighted: '- [ ]' boxes and [links](..) in a body stay plain text on /search, in the palette and the CLI."""
    t = _t(store, "Bracket body", body=BRACKETS)
    c = TestClient(create_app(store.home))
    page = c.get("/search", params={"q": "zorblat"}).text
    assert page.count("<mark>") == 1 and "<mark>zorblat</mark>" in page, page.split("<main>")[1][:600]
    assert "- [ ] a step with [a link](http://x) then the " in page and "- [x] done" in page
    snip = [h for g in c.get("/search.json", params={"q": "zorblat"}).json()["groups"] for h in g["hits"]][0]["snippet"]
    assert search.plain_snippet(snip).startswith("- [ ] a step with [a link](http://x) then the [zorblat] word and - [x] done")
    fts = search.Index(None, stale_days=store.stale_days, fts=False)
    fts.refresh(store, force=True)
    assert search.snippet_html(fts.search("zorblat").groups[0][2][0].snippet).count("<mark>") == 1  # the LIKE path too
    js = (STATIC / "search.js").read_text(encoding="utf-8")
    body = re.search(r"function mark\(s\) \{ (return [^;]*;) \}", js).group(1)
    import shutil
    import subprocess

    if shutil.which("node"):  # the palette's own mark(), run as it is in the page
        src = "function esc(s){return String(s);}\nfunction mark(s) {" + body + "}\nprocess.stdout.write(mark(" + json.dumps(snip) + "));"
        out = subprocess.run(["node", "-e", src], capture_output=True, text=True, encoding="utf-8", timeout=30).stdout
        assert out.count("<mark>") == 1 and "<mark>zorblat</mark>" in out and "- [ ] a step" in out, out
    assert t.id in page


def test_the_body_index_refreshes_on_save(store):
    """A save is searchable at once: the open index re-reads the saved ticket, even inside the one-second refresh pause."""
    t = _t(store, "Refresh me", body="Nothing special yet.")
    c = TestClient(create_app(store.home))
    assert not c.get("/search.json", params={"q": "pomegranite"}).json()["total"]  # opens the index, starts the pause
    t = store.get(t.id)
    t.set_section("Context", "Now it names the pomegranite plan.")
    store.save(t)
    d = c.get("/search.json", params={"q": "pomegranite"}).json()
    assert [h["id"] for g in d["groups"] for h in g["hits"]] == [t.id]


def test_search_without_fts5_falls_back_to_like_with_snippets(store):
    """On a SQLite without FTS5 the same searches work through LIKE, with the same [match] snippet and typo fallback."""
    a = _t(store, "Scheduler reads profiles", body="The quokkaline note lives in the body.")
    _t(store, "Something else")
    ix = search.Index(None, stale_days=store.stale_days, fts=False)
    ix.refresh(store, force=True)
    assert not ix.fts and search.has_fts5()  # this machine has FTS5; the fallback is forced
    res = ix.search("quokkaline")
    assert _ids(res) == [a.id] and "[quokkaline]" in search.plain_snippet(res.groups[0][2][0].snippet)
    assert _ids(ix.search("schedul")) == [a.id]
    fz = ix.search("shceduler")
    assert _ids(fz) == [a.id] and fz.fuzzy


def test_docs_and_intake_are_searched_and_private_files_are_not(store):
    """Words in docs/ and intake/ Markdown are found with a link the /doc page opens; a private file is never indexed."""
    (store.home / "docs").mkdir(exist_ok=True)
    (store.home / "docs" / "guide.md").write_text("# The guide\n\nHow the velvetquill review works.\n", encoding="utf-8")
    (store.home / "intake" / "2026-11-15").mkdir(parents=True)
    (store.home / "intake" / "2026-11-15" / "notes.md").write_text("# Notes\n\nA velvetquill idea from the call.\n", encoding="utf-8")
    (store.home / "intake" / "private_context.md").write_text("velvetquill private words\n", encoding="utf-8")
    c = TestClient(create_app(store.home))
    d = c.get("/search.json", params={"q": "velvetquill"}).json()
    paths = sorted(f["path"] for f in d["files"])
    assert paths == ["docs/guide.md", "intake/2026-11-15/notes.md"]
    assert all("[velvetquill]" in search.plain_snippet(f["snippet"]) for f in d["files"])
    assert {f["title"] for f in d["files"]} == {"The guide", "Notes"}
    page = c.get("/search", params={"q": "velvetquill"}).text
    assert 'href="/doc/docs/guide.md"' in page and "In docs and intake" in page
    assert c.get("/doc/docs/guide.md").status_code == 200
    assert not c.get("/search.json", params={"q": "velvetquill status:ready"}).json()["files"]  # a filter keeps it to tickets


# -- palette actions -------------------------------------------------------------------------------------------------------

def test_palette_actions_are_the_page_buttons_routes(store):
    """Every palette action posts to a route the ticket page's own buttons post to (one code path), with a fresh one-time token."""
    t = _t(store, "Ship the thing", status="review")
    c = TestClient(create_app(store.home))
    page = c.get(f"/ticket/{t.id}").text
    acts = {a["key"]: a for a in c.get(f"/palette/actions/{t.id}").json()["actions"]}
    assert {"approve", "changes", "snooze", "lane-later", "lane-now", "park"} <= set(acts)
    assert any(k.startswith("assign-") for k in acts)
    forms = set(re.findall(r'<form[^>]*action="([^"]+)"', page))
    for key in ("approve", "changes") + tuple(k for k in acts if k.startswith("assign-")):
        assert acts[key]["url"] in forms, key  # Approve, Send back and Assign: the very routes of the page's forms
    assert acts["approve"]["fields"]["action"] == "approve" and acts["changes"]["fields"]["action"] == "changes"
    toks = [a["fields"]["token"] for a in acts.values()]
    assert len(set(toks)) == len(toks) and all(c.app.state.tokens.valid(x) for x in toks)


def test_palette_approve_and_assign_post_through_the_same_routes(store):
    """Posting an action's fields (as the palette does) approves with James's words and assigns, exactly like the buttons."""
    t = _t(store, "Ship the thing", status="review")
    c = TestClient(create_app(store.home))
    acts = {a["key"]: a for a in c.get(f"/palette/actions/{t.id}").json()["actions"]}
    who = next(k for k in acts if k.startswith("assign-"))
    r = c.post(acts[who]["url"], data=acts[who]["fields"], headers=JSON)
    assert r.status_code == 200 and r.json()["ok"]
    assert str(store.get(t.id).meta.get("assignee")) == who[len("assign-"):]
    a = acts["approve"]
    r = c.post(a["url"], data={**a["fields"], a["words_field"]: "Ship it as written"}, headers=JSON)
    assert r.status_code == 200, r.text
    t2 = store.get(t.id)
    assert t2.status == "approved" and "Ship it as written" in t2.section("Decision")
    assert c.post(a["url"], data={**a["fields"], "words": "again"}, headers=JSON).status_code == 409  # the token was spent


def test_snooze_and_lane_write_the_fields_with_one_batch_each(store):
    """Snooze 1 week and Move to Later write snooze_until and lane through Store.save, each change carrying its batch id for undo."""
    t = _t(store, "Later maybe", status="ready")  # ready: the Planned lane, so Later is a move
    c = TestClient(create_app(store.home))
    acts = {a["key"]: a for a in c.get(f"/palette/actions/{t.id}").json()["actions"]}
    r = c.post(acts["snooze"]["url"], data=acts["snooze"]["fields"], headers=JSON)
    assert r.status_code == 200 and r.json()["batch"].startswith("b-"), r.text
    t2 = store.get(t.id)
    assert lanes.snooze_until(t2) == dt.date.today() + dt.timedelta(days=7) and lanes.is_snoozed(t2)
    b1 = r.json()["batch"]
    acts = {a["key"]: a for a in c.get(f"/palette/actions/{t.id}").json()["actions"]}
    assert "unsnooze" in acts and "snooze" not in acts
    r = c.post(acts["lane-later"]["url"], data=acts["lane-later"]["fields"], headers=JSON)
    assert r.status_code == 200 and lanes.lane_of(store.get(t.id)) == "later"
    evs = activity.read(store.home, t.id)
    assert any(e.field == "snooze_until" and e.batch == b1 for e in evs)
    assert any(e.field == "lane" and e.batch == r.json()["batch"] for e in evs)
    park = {a["key"]: a for a in c.get(f"/palette/actions/{t.id}").json()["actions"]}["park"]
    assert c.post(park["url"], data=park["fields"], headers=JSON).status_code == 422  # parking needs a reason
    plain = c.post(f"/ticket/{t.id}/lane", data={"lane": "now", "token": _tok(c)}, follow_redirects=False)
    assert plain.status_code == 303 and "undo=b-" in plain.headers["location"] and "msg=" in plain.headers["location"]


LANE_FORM = {"token", "lane", "reason", "snooze", "next"}  # the fields DESK-0283's POST /ticket/<ID>/lane reads (its lane menu posts them)


def test_palette_lane_park_and_snooze_post_the_lane_menus_fields_to_its_one_route(store):
    """Lane, Park and Snooze post DESK-0283's lane-menu fields (lane, reason, snooze) to /ticket/<ID>/lane; no second snooze route exists."""
    t = _t(store, "Later maybe", status="ready")
    c = TestClient(create_app(store.home))
    acts = {a["key"]: a for a in c.get(f"/palette/actions/{t.id}").json()["actions"]}
    for key in ("snooze", "lane-now", "lane-later", "park"):
        assert acts[key]["url"] == f"/ticket/{t.id}/lane", key
        assert set(acts[key]["fields"]) <= LANE_FORM, (key, acts[key]["fields"])
    assert acts["lane-later"]["fields"]["lane"] == "later" and acts["snooze"]["fields"]["snooze"] == "1w"
    assert acts["park"]["fields"]["lane"] == "parked" and acts["park"]["words_field"] == "reason"
    assert not [r for r in c.app.routes if getattr(r, "path", "") == "/ticket/{ticket_id}/snooze"]
    r = c.post(acts["snooze"]["url"], data=acts["snooze"]["fields"], headers=JSON)
    assert r.status_code == 200 and r.json()["batch"].startswith("b-"), r.text
    assert lanes.snooze_until(store.get(t.id)) == dt.date.today() + dt.timedelta(days=7)
    un = {a["key"]: a for a in c.get(f"/palette/actions/{t.id}").json()["actions"]}["unsnooze"]
    assert un["url"] == f"/ticket/{t.id}/lane" and un["fields"]["snooze"] == "clear"
    assert c.post(un["url"], data=un["fields"], headers=JSON).status_code == 200
    assert "snooze_until" not in store.get(t.id).meta
    park = {a["key"]: a for a in c.get(f"/palette/actions/{t.id}").json()["actions"]}["park"]
    r = c.post(park["url"], data={**park["fields"], park["words_field"]: "not this quarter"}, headers=JSON)
    assert r.status_code == 200 and lanes.lane_of(store.get(t.id)) == "parked" and store.get(t.id).meta["park_reason"] == "not this quarter"
    menu = c.post(f"/ticket/{t.id}/lane", data={"lane": "now", "next": f"/ticket/{t.id}", "token": _tok(c)}, follow_redirects=False)
    assert menu.status_code == 303 and menu.headers["location"].startswith(f"/ticket/{t.id}?undo=b-")  # the lane menu's plain form
    assert lanes.lane_of(store.get(t.id)) == "now" and "park_reason" not in store.get(t.id).meta


def test_the_stand_in_lane_route_steps_aside_when_the_lanes_package_is_present(store, monkeypatch):
    """Once DESK-0283's routes_lanes is in the app, the palette adds no lane route of its own, whatever order the two register in."""
    from desk.ui import routes_keys

    monkeypatch.setattr(routes_keys, "lanes_package_present", lambda: True)
    c = TestClient(create_app(store.home))
    assert not [r for r in c.app.routes if getattr(r, "path", "") == "/ticket/{ticket_id}/lane" and "POST" in (getattr(r, "methods", None) or ())]


# -- display settings ------------------------------------------------------------------------------------------------------

def test_theme_and_type_size_persist_per_device_and_render_before_paint(store):
    """The theme and size chosen on one device come back on every page of that device (on <html>, server-rendered), not on another."""
    c = TestClient(create_app(store.home))
    assert re.search(r'<html lang="en" data-size="normal">', c.get("/").text)  # the default: no theme attribute, the system decides
    r = c.post("/settings/display", data={"theme": "dark", "size": "large"}, headers=JSON)
    assert r.status_code == 200 and r.json()["ok"]
    for path in ("/", "/board", "/settings"):
        assert '<html lang="en" data-theme="dark" data-size="large">' in c.get(path).text, path
    s = c.get("/settings").text
    assert re.search(r'value="dark" checked', s) and re.search(r'value="large" checked', s)
    other = TestClient(create_app(store.home))
    assert 'data-theme="dark"' not in other.get("/").text
    assert c.post("/settings/display", data={"theme": "neon", "size": "large"}, headers=JSON).status_code == 400
    css = (STATIC / "keys.css").read_text(encoding="utf-8")
    assert ':root[data-theme="dark"]' in css and ':root[data-theme="light"]' in css


def _scheme_rules(css: str) -> list[tuple[str, str]]:
    """(selector, declarations) of every rule inside a prefers-color-scheme:dark block of `css`."""
    out = []
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for m in re.finditer(r"@media\s*\(prefers-color-scheme:\s*dark\)\s*\{", css):
        depth, i = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(css[i], 0)
            i += 1
        out += [(sel.strip(), decl) for sel, decl in re.findall(r"([^{}]+)\{([^{}]*)\}", css[m.end():i - 1])]
    return out


def _light_rule(css: str, sel: str, prop: str) -> str:
    """The value desk.css gives `prop` on `sel` outside any media block (the light look)."""
    flat = re.sub(r"@media[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", re.sub(r"/\*.*?\*/", "", css, flags=re.S))
    for s, decl in re.findall(r"([^{}]+)\{([^{}]*)\}", flat):
        if sel in [x.strip() for x in s.split(",")]:
            m = re.search(rf"(?:^|;)\s*{re.escape(prop)}\s*:\s*([^;]+)", decl)
            if m:
                return m.group(1).strip()
    return ""


def test_a_chosen_theme_overrides_every_dark_mode_rule_in_desk_css():
    """Dark or Light chosen against the computer's setting also sets the colours desk.css only changes in its dark-mode blocks (.ferr, .mask)."""
    desk = (STATIC / "desk.css").read_text(encoding="utf-8")
    keys = re.sub(r"\s+", "", (STATIC / "keys.css").read_text(encoding="utf-8"))
    rules = _scheme_rules(desk)
    assert {s for s, _ in rules} >= {":root", ".ferr", ".mask"}, rules
    for sel, decl in rules:
        for prop, val in re.findall(r"([\w-]+)\s*:\s*([^;]+)", decl):
            if sel == ":root":
                continue  # the colour tokens: keys.css sets them under each data-theme (the test above)
            light = _light_rule(desk, sel, prop)
            assert light, (sel, prop)  # desk.css has a light value to put back
            for theme, want in (("dark", f"{prop}:{val.strip()}"), ("light", f"{prop}:{light}")):
                pat = rf':root\[data-theme="{theme}"\]{re.escape(sel.replace(" ", ""))}(?::not\([^)]*\))?(?:,[^{{}}]*)?\{{[^}}]*{re.escape(want.replace(" ", ""))}'
                assert re.search(pat, keys), (theme, sel, want)


# -- saved views -----------------------------------------------------------------------------------------------------------

def test_saved_views_are_pinned_in_the_header_and_removable(store):
    """A named board filter is kept in the store's views.yaml (shared across devices), shown in every page's header, and removable."""
    c = TestClient(create_app(store.home))
    assert "Save view" in c.get("/board", params={"project": "desk"}).text and "Save view" not in c.get("/").text
    r = c.post("/views", data={"name": "Desk review", "url": "/board?project=desk", "token": _tok(c)}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/board?project=desk&msg=")
    assert saved_views.load(store.home) == [{"name": "Desk review", "url": "/board?project=desk"}]
    assert "Desk review" in (store.home / "views.yaml").read_text(encoding="utf-8")
    other = TestClient(create_app(store.home))  # another device reading the same store
    assert 'href="/board?project=desk">Desk review</a>' in other.get("/").text
    bad = c.post("/views", data={"name": "Away", "url": "https://example.com/x", "token": _tok(c)}, headers=JSON)
    assert bad.status_code == 422 and len(saved_views.load(store.home)) == 1
    r = c.post("/views/remove", data={"name": "Desk review", "token": _tok(c)}, headers=JSON)
    assert r.status_code == 200 and saved_views.load(store.home) == []
    assert "Desk review" not in c.get("/").text


def test_saved_view_writes_say_what_happened_and_can_be_undone(store):
    """Save, change and remove each answer one plain sentence and an Undo that puts the views back exactly as they were; a failure says why."""
    c = TestClient(create_app(store.home))
    r = c.post("/views", data={"name": "Desk review", "url": "/board?project=desk", "token": _tok(c)}, headers=JSON).json()
    assert r["ok"] and r["message"].startswith("Saved the view Desk review.") and c.app.state.tokens.valid(r["token"])
    u = r["undo"]
    assert c.post(u["url"], data=u["fields"], headers=JSON).json()["ok"] and saved_views.load(store.home) == []
    saved_views.add(store.home, "Desk review", "/board?project=desk")
    saved_views.add(store.home, "Open asks", "/search?q=ask:approve")
    before = saved_views.load(store.home)
    r = c.post("/views", data={"name": "desk review", "url": "/board?project=desk&status=review", "token": _tok(c)}, headers=JSON).json()
    assert r["ok"] and "Changed the view" in r["message"] and saved_views.load(store.home)[0]["url"] == "/board?project=desk&status=review"
    assert c.post(r["undo"]["url"], data=r["undo"]["fields"], headers=JSON).json()["ok"] and saved_views.load(store.home) == before
    r = c.post("/views/remove", data={"name": "Desk review", "token": _tok(c)}, headers=JSON).json()
    assert r["ok"] and r["undo"]["label"] == "Put it back" and [v["name"] for v in saved_views.load(store.home)] == ["Open asks"]
    assert c.post(r["undo"]["url"], data=r["undo"]["fields"], headers=JSON).json()["ok"]
    assert saved_views.load(store.home) == before  # back in its old place, first
    bad = c.post("/views", data={"name": "", "url": "/board?project=desk", "token": _tok(c)}, headers=JSON)
    assert bad.status_code == 422 and "name" in bad.json()["error"] and c.app.state.tokens.valid(bad.json()["token"])


def test_a_saved_view_never_keeps_one_off_messages_in_its_address(store):
    """A board address still carrying ?msg=, ?undo= or ?error= is saved as its filter only, so opening the view never repeats an old message."""
    saved_views.add(store.home, "Desk", "/board?project=desk&msg=Saved%20it&undo=b-0123456789ab&error=Nope")
    assert saved_views.load(store.home) == [{"name": "Desk", "url": "/board?project=desk"}]
    c = TestClient(create_app(store.home))
    page = c.get("/board", params={"project": "desk", "msg": "Saved it", "undo": "b-0123456789ab"}).text
    assert 'name="url" value="/board?project=desk"' in page
    assert "Save view" not in c.get("/board", params={"msg": "Saved it"}).text  # nothing left to save once the message is gone


# -- plain words -----------------------------------------------------------------------------------------------------------

def test_new_words_on_screen_pass_the_plain_words_check(store):
    """The palette hints, the shortcut sheet and the new pages read at grade 10 or below and use no unexplained jargon (DESK-0253)."""
    from desk import people as peoplemod, plainwords
    from desk.ui import routes_keys

    t = _t(store, "Ship the thing", status="review")
    acts = routes_keys.palette_actions(t, peoplemod.load(store.home), lambda: "x")
    hints = ". ".join(a["hint"].rstrip(".") for a in acts if not a["key"].startswith("assign-")) + "."
    sheet = re.findall(r'(?:row\(dl, \[[^\]]*\], |mk\("p", "muted", )"([^"]+)"', (STATIC / "keys.js").read_text(encoding="utf-8"))
    c = TestClient(create_app(store.home))
    pages = " ".join(re.sub(r"<[^>]+>", " ", c.get(p).text.split("<main>")[1].split("</main>")[0]) for p in ("/views",))
    for text in (hints, " ".join(sheet), pages):
        g = plainwords.grade(text)
        assert g is None or g <= plainwords.MAX_GRADE, (g, text[:200])
        assert not plainwords.jargon(text), (plainwords.jargon(text), text[:200])


# -- phone width -----------------------------------------------------------------------------------------------------------

_WIDE = re.compile(r"(?<![-\w])(?:min-width|width|flex-basis)\s*:\s*(\d+)px|flex\s*:\s*\d+\s+\d+\s+(\d+)px|minmax\(\s*(\d+)px")


def _too_wide(css: str) -> list[str]:
    css = re.sub(r"@media[^{]*\{", "{", css)  # a media condition (min-width:700px) is a breakpoint, not a width
    return [m.group(0) for m in _WIDE.finditer(css) if int(next(g for g in m.groups() if g)) > 390]


def test_key_pages_fit_a_390_px_phone(store):
    """Nothing on the key pages forces a width over 390 px: the viewport meta is there and no width in the CSS or inline styles is wider."""
    t = _t(store, "A ticket with a fairly long title that has to wrap on a phone screen", status="review")
    (store.home / "docs").mkdir(exist_ok=True)
    (store.home / "docs" / "a-very-long-file-name-that-would-push-a-phone-layout-sideways.md").write_text("# Long\n\nphonewidth word\n", encoding="utf-8")
    saved_views.add(store.home, "A long saved view name here", "/board?project=desk")
    c = TestClient(create_app(store.home))
    c.post("/settings/display", data={"theme": "dark", "size": "large"}, headers=JSON)
    for path in ("/", "/board", f"/ticket/{t.id}", "/search?q=phonewidth", "/settings", "/views"):
        html = c.get(path).text
        assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in html, path
        inline = " ".join(re.findall(r'style="([^"]*)"', html) + re.findall(r"<style>(.*?)</style>", html, re.S))
        assert not _too_wide(inline), (path, _too_wide(inline))
    for name in ("keys.css", "desk.css"):
        assert not _too_wide((STATIC / name).read_text(encoding="utf-8")), name


# -- in a real browser -----------------------------------------------------------------------------------------------------

_HEAD = """<script>addEventListener('load',function(){
if(window.top!==window)return;
var st=sessionStorage,out=JSON.parse(st.getItem('o')||'{}'),sleep=function(ms){return new Promise(function(r){setTimeout(r,ms);});},
 key=function(el,k,o){var e=new KeyboardEvent('keydown',Object.assign({key:k,bubbles:true,cancelable:true},o||{}));el.dispatchEvent(e);return e;},
 save=function(){st.setItem('o',JSON.stringify(out));},done=function(){fetch('/m',{method:'POST',body:JSON.stringify(out)});};
async function until(f,n){for(var i=0;i<(n||100)&&!f();i++){await sleep(100);}return f();}
(async function(){await sleep(300);var step=+(st.getItem('step')||0);
"""
_TAIL = "})();});</script>"

KEYS = _HEAD + """
if(step===0){var sh=function(){return document.getElementById('keys-sheet');};
 key(document.body,'?');out.sheet=!!sh()&&!sh().hidden;key(document.body,'Escape');out.sheetClosed=sh().hidden;
 var y=key(document.body,'y');out.yFree=!y.defaultPrevented;
 var ta=document.createElement('textarea');document.querySelector('main').appendChild(ta);ta.focus();key(ta,'?');key(ta,'g');key(ta,'b');out.notInField=sh().hidden&&location.pathname==='/board';ta.remove();
 st.setItem('step','1');save();key(document.body,'g');key(document.body,'n');await sleep(4000);out.landed='stayed';done();}
else{out.landed=location.pathname+location.search;done();}
""" + _TAIL


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_question_mark_sheet_and_g_chords_in_a_browser(store):
    """? opens the sheet and Esc closes it; g then n goes to Needs me; keys typed in a field do nothing; a lone y is left alone."""
    got = drive(store, KEYS, "/board")
    assert got["sheet"] and got["sheetClosed"], got
    assert got["yFree"] and got["notInField"], got
    assert got["landed"] == "/board?needs=1", got


PALETTE = _HEAD + """
if(step===0){st.setItem('step','1');save();location.href='/ticket/'+out.b;return;}
if(step===1){st.setItem('step','2');save();location.href='/ticket/'+out.a;return;}
document.dispatchEvent(new KeyboardEvent('keydown',{key:'k',ctrlKey:true,bubbles:true}));
out.actions=await until(function(){return document.querySelectorAll('#pal-list a.pal-act').length>0;});
var groups=Array.prototype.map.call(document.querySelectorAll('#pal-list .pal-group'),function(g){return g.textContent;});
out.groups=groups;out.recent=Array.prototype.map.call(document.querySelectorAll('#pal-list a.pal-item:not(.pal-act) .chip'),function(c){return c.textContent;});
var inp=document.getElementById('pal-input');inp.value='approve';inp.dispatchEvent(new Event('input',{bubbles:true}));
await until(function(){var s=document.querySelector('#pal-list a.pal-item.sel');return s&&/Approve/.test(s.textContent);});
key(inp,'Enter');out.wordsMode=await until(function(){return /Type your words/.test(document.getElementById('pal-status').textContent);});
inp.value='Ship it as written';key(inp,'Enter');
out.sent=await until(function(){return /Decision recorded/.test(document.getElementById('pal-status').textContent);},150);
out.status=document.getElementById('pal-status').textContent;st.clear();done();
""" + _TAIL


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_palette_lists_recent_tickets_and_approves_the_open_ticket(store):
    """Ctrl K on a ticket lists its actions and the tickets viewed before; approve, words, Enter: the decide route records the words."""
    a = _t(store, "Ship the thing", status="review")
    b = _t(store, "Looked at first")
    script = PALETTE.replace("var st=sessionStorage,out=JSON.parse(st.getItem('o')||'{}')",
                             "var st=sessionStorage,out=JSON.parse(st.getItem('o')||" + json.dumps(json.dumps({"a": a.id, "b": b.id})) + ")")
    got = drive(store, script, "/board", timeout=60)
    assert got["actions"] and any("Recently viewed" in g for g in got["groups"]) and b.id in got["recent"], got
    assert got["wordsMode"] and got["sent"], got
    t = store.get(a.id)
    assert t.status == "approved" and "Ship it as written" in t.section("Decision")


THEME = _HEAD + """
if(step===0){document.cookie='desk_theme=dark;path=/';st.setItem('step','1');save();location.reload();return;}
var mk=function(cls){var e=document.createElement('span');e.className=cls;e.textContent='word';document.querySelector('main').appendChild(e);return getComputedStyle(e);};
out.attr=document.documentElement.getAttribute('data-theme');out.os=matchMedia('(prefers-color-scheme: dark)').matches;
out.ferr=mk('ferr').color;out.mask=mk('mask').backgroundColor;out.shown=mk('mask shown').backgroundColor;
out.shownReady=(function(){var e=document.createElement('span');e.className='mask shown';e.setAttribute('data-ready','');document.querySelector('main').appendChild(e);return getComputedStyle(e).backgroundColor;})();
st.clear();done();
""" + _TAIL


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_dark_chosen_on_a_light_computer_paints_errors_and_masks_dark_in_a_browser(store):
    """With Dark chosen and the computer set to light, a form error is the pale red and a mask the dark red; a revealed mask stays clear."""
    got = drive(store, THEME, "/settings", timeout=60)
    assert got["attr"] == "dark" and not got["os"], got
    assert got["ferr"] == "rgb(255, 140, 146)" and got["mask"] == "rgb(90, 42, 45)", got
    assert got["shownReady"] == "rgba(0, 0, 0, 0)", got


VIEWS = _HEAD + """
var msg=function(){var m=document.querySelector('.dp-msg');return m?m.textContent:'';};
if(step===0){document.querySelector('.sv-save').open=true;var f=document.querySelector('form.sv-form');f.querySelector('[name=name]').value='Desk review';
 f.querySelector('button[type=submit]').click();out.saved=await until(function(){return /Saved the view Desk review/.test(msg());});
 out.saveUndo=!!document.querySelector('.sv-undo');out.stayed=location.pathname+location.search;
 st.setItem('step','1');save();location.href='/views';return;}
if(step===1){document.querySelector('form.sv-remove button[type=submit]').click();
 out.removed=await until(function(){return /Removed the view Desk review/.test(msg());});
 var u=document.querySelector('.sv-undo');out.undoLabel=u?u.textContent:'';out.struck=!!document.querySelector('li.sv-gone');
 st.setItem('step','2');save();u.click();out.undone=await until(function(){return /Undone/.test(msg());});save();return;}
out.chip=!!document.querySelector('header .sv a.sv-chip');st.clear();done();
""" + _TAIL


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_saving_and_removing_a_view_say_so_in_place_and_put_it_back_undoes_it(store):
    """In a browser: Save view and Remove show their sentence where you clicked without leaving the page; Put it back restores the view."""
    _t(store, "Something on the board")
    got = drive(store, VIEWS, "/board?project=desk", timeout=60)
    assert got["saved"] and got["saveUndo"] and got["stayed"] == "/board?project=desk", got
    assert got["removed"] and got["undoLabel"] == "Put it back" and got["struck"], got
    assert got["undone"] and got["chip"], got
    assert saved_views.load(store.home) == [{"name": "Desk review", "url": "/board?project=desk"}]


DRAFT = _HEAD + """
var ta=function(){return document.getElementById('comment-text');};
if(step===0){document.getElementById('add-comment').open=true;ta().value='A thought I have not sent';ta().dispatchEvent(new Event('input',{bubbles:true}));
 await sleep(600);st.setItem('step','1');save();location.reload();return;}
if(step===1){out.back=ta().value;out.note=!!document.querySelector('.draft-note');
 document.querySelector('#composer .post-btn').click();out.posted=await until(function(){return /Posted/.test(document.getElementById('post-status').textContent);},150);
 await sleep(300);st.setItem('step','2');save();location.reload();return;}
out.after=ta().value;out.kept=Object.keys(localStorage).filter(function(k){return k.indexOf('desk:draft:')===0;}).length;st.clear();done();
""" + _TAIL


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_an_unsent_comment_survives_a_reload_and_clears_on_post(store):
    """Text typed in the comment box comes back after a reload, and is gone once the comment is posted."""
    t = _t(store, "Talk about it")
    got = drive(store, DRAFT, f"/ticket/{t.id}", timeout=60)
    assert got["back"] == "A thought I have not sent" and got["note"], got
    assert got["posted"], got
    assert got["after"] == "" and got["kept"] == 0, got
    assert "A thought I have not sent" in store.get(t.id).section("Comments")
