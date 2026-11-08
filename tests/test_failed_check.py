"""DESK-0091: the third checkbox state `- [!]`, tried and failed."""

from fastapi.testclient import TestClient

from desk import ops, review as rv
from desk.cli import main
from desk.ticket import Ticket, check_lines
from desk.ui.app import create_app
from desk.ui.markdown import render


def test_failed_line_parsed_rendered_red_and_preserved(store):
    t = ops.new(store, "Boxes", "jre")
    t.set_section("Acceptance", "- [ ] one\n- [!] two\n- [x] three")
    store.save(t)
    assert [c for _, c in check_lines(t.sections["Acceptance"])] == [False, False, True]
    assert "- [!] two" in store.get(t.id).render()
    out = render(t.section("Acceptance"))
    assert "chk-failed" in out and "Not run" in out
    page = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    assert "st-failed" in page and "st-done" in page and "st-open" in page and "Failed checks (1)" in page


def test_ui_cycles_open_done_failed_and_only_that_line_changes(store):
    t = ops.new(store, "Cycle", "jre")
    t.set_section("Acceptance", "- [ ] one\n- [ ] two")
    store.save(t)
    c = TestClient(create_app(store.home))
    seen = []
    for _ in range(4):
        page = c.get(f"/ticket/{t.id}").text
        i = page.index('name="index" value="1"')
        nxt = page[i:i + 200].split('name="state" value="')[1][0]
        r = c.post(f"/ticket/{t.id}/check", data={"token": c.app.state.tokens.issue(), "section": "Acceptance", "index": 1, "state": nxt}, follow_redirects=False)
        assert r.status_code == 303 and "error" not in r.headers["location"]
        seen.append(store.get(t.id).sections["Acceptance"].split("\n"))
    assert [s[1] for s in seen] == ["- [x] two", "- [!] two", "- [ ] two", "- [x] two"] and all(s[0] == "- [ ] one" for s in seen)
    ops.toggle_check(store, t.id, "Acceptance", 0)  # None cycles
    assert store.get(t.id).sections["Acceptance"].startswith("- [x] one")


def test_review_line_shows_failed_count_and_report_warns(store, capsys):
    (store.projects_dir / "management.md").write_text("# Management\n", encoding="utf-8")
    t = ops.new(store, "Ship it", "jre", "feature")
    t.set_section("Acceptance", "- [!] it works")
    store.save(t)
    ops.move(store, t, "ready", actor="main")
    ops.claim(store, t, "jre")
    main(["--home", str(store.home), "report", t.id, "--text", "Ran it; failed."])
    assert "failed check (Acceptance): it works" in capsys.readouterr().err
    main(["--home", str(store.home), "review", "new"])
    rt = next(x for x in store.tickets() if rv.is_review_ticket(x))
    assert "1 failed" in rt.section("Acceptance")
