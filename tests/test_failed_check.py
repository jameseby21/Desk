"""DESK-0091: the third checkbox state `- [!]`, tried and failed."""

import pytest
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
    assert "chk-failed" in out
    page = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    assert "chk2" in page and "Failed checks (1)" in page


def _post(c, t, **data):
    return c.post(f"/ticket/{t.id}/check", data={"token": c.app.state.tokens.issue(), "section": "Acceptance", **data}, follow_redirects=False)


def test_two_boxes_right_aligned_done_and_not_accepted_with_reason(store):
    t = ops.new(store, "Two boxes", "jre")
    t.set_section("Acceptance", "- [ ] one\n- [ ] two")
    t.set_section("How to validate", "- [ ] open it")
    store.save(t)
    c = TestClient(create_app(store.home))
    page = c.get(f"/ticket/{t.id}").text
    assert page.count('class="chk2"') == 3 and page.count("> Done</label>") == 3 and page.count("Not accepted</summary>") == 3
    assert 'name="section" value="How to validate"' in page
    # Done writes [x]; only that line changes
    assert _post(c, t, index=1, state="x").status_code == 303
    assert store.get(t.id).sections["Acceptance"].strip().split("\n") == ["- [ ] one", "- [x] two"]
    # Not accepted without a reason is refused and the file is untouched
    r = _post(c, t, index=0, state="!")
    assert "error=" in r.headers["location"] and store.get(t.id).sections["Acceptance"].startswith("- [ ] one")
    r = _post(c, t, index=0, state="!", reason="   ")
    assert "error=" in r.headers["location"]
    # with a reason the same line carries the suffix, and the page shows it in red
    assert "error" not in _post(c, t, index=0, state="!", reason="Wrong colour").headers["location"]
    assert store.get(t.id).sections["Acceptance"].split("\n")[0] == "- [!] one — not accepted: Wrong colour"
    page = c.get(f"/ticket/{t.id}").text
    assert '<div class="nr-reason">Not accepted: Wrong colour</div>' in page and "— not accepted" not in page.split("Acceptance (this")[1].split("Done means")[0]
    # unchecking returns to [ ] and removes the suffix
    assert "error" not in _post(c, t, index=0, state=" ").headers["location"]
    assert store.get(t.id).sections["Acceptance"].split("\n")[0] == "- [ ] one"
    # How to validate lines work the same way
    ops.toggle_check(store, t.id, "How to validate", 0, "!", reason="not there")
    assert store.get(t.id).sections["How to validate"].strip() == "- [!] open it — not accepted: not there"
    with pytest.raises(ValueError):
        ops.toggle_check(store, t.id, "How to validate", 0, "!")
    ops.toggle_check(store, t.id, "Acceptance", 0)  # None flips open <-> done
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
