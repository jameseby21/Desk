"""DESK-0119 (Chaz R3): Not accepted needs a reason of 3 or more non-space characters, with a severity; the saved reason keeps its
severity, who, when, evidence count and an Edit link; Cancel with no saved reason returns the line to open."""

import re

import pytest
from fastapi.testclient import TestClient

from browser_harness import browser, run_in_browser
from desk import ops
from desk.ticket import parse_checks, render_check
from desk.ui.app import STATIC, create_app

JSON = {"Accept": "application/json"}


def _ticket(store, acc="- [ ] one\n- [x] two\n- [ ] three"):
    t = ops.new(store, "Reasons", "jre")
    t.set_section("Acceptance", acc)
    store.save(t)
    return t


def _post(c, t, **data):
    return c.post(f"/ticket/{t.id}/check", data={"token": c.app.state.tokens.issue(), "section": "Acceptance", **data}, headers=JSON)


def test_parse_reads_the_new_block_the_legacy_line_and_plain_lines():
    text = ("- [x] done  <!-- by james 2026-11-08T03:40 -->\n"
            "- [!] README  <!-- by james 2026-11-08T03:41 -->\n"
            "  > Not accepted (should): The README lists 12 commands.\n"
            "  > Evidence: assets/DESK-0082/a.png\n"
            "  > Evidence: assets/DESK-0082/b.png\n"
            "- [!] old form — not accepted: Wrong colour\n"
            "- [!] bare\n"
            "- [ ] open")
    c = parse_checks(text)
    assert [x.state for x in c] == ["x", "!", "!", "!", " "]
    assert (c[0].text, c[0].who, c[0].when) == ("done", "james", "2026-11-08T03:40")
    assert (c[1].text, c[1].severity, c[1].reason, c[1].who, c[1].evidence) == (
        "README", "should", "The README lists 12 commands.", "james", ["assets/DESK-0082/a.png", "assets/DESK-0082/b.png"])
    assert (c[1].first, c[1].last) == (1, 4)
    assert (c[2].text, c[2].reason, c[2].severity, c[2].legacy) == ("old form", "Wrong colour", "", True)
    assert (c[3].reason, c[4].reason) == ("", "")
    assert render_check(c[1]) == text.split("\n")[1:5]  # a new-format block renders back to the same four lines


@pytest.mark.parametrize("reason", ["", "   ", "ab", " a b ", "\n\t"])
def test_a_reason_under_three_characters_is_refused_and_nothing_is_written(store, reason):
    t = _ticket(store)
    before = store.get(t.id).sections["Acceptance"]
    with pytest.raises(ValueError, match="at least 3"):
        ops.toggle_check(store, t.id, "Acceptance", 0, "!", actor="james", reason=reason)
    assert store.get(t.id).sections["Acceptance"] == before


def test_saved_block_has_quoted_reason_severity_who_and_when(store):
    t = _ticket(store)
    ops.toggle_check(store, t.id, "Acceptance", 0, "!", actor="james", reason="  The   README is\nmissing commands ", severity="should")
    lines = store.get(t.id).sections["Acceptance"].split("\n")
    assert re.fullmatch(r"- \[!\] one  <!-- by james \d{4}-\d\d-\d\dT\d\d:\d\d -->", lines[0])
    assert lines[1] == "  > Not accepted (should): The README is missing commands"
    assert lines[2] == "- [x] two"  # the next line is untouched
    # default severity is Must fix; a stranger's severity word is refused
    ops.toggle_check(store, t.id, "Acceptance", 2, "!", actor="james", reason="nope")
    assert "  > Not accepted (must fix): nope" in store.get(t.id).sections["Acceptance"]
    with pytest.raises(ValueError, match="severity"):
        ops.toggle_check(store, t.id, "Acceptance", 1, "!", actor="james", reason="nope", severity="whenever")


def test_editing_a_saved_reason_rewrites_one_block_and_keeps_its_evidence(store):
    t = _ticket(store, "- [!] one  <!-- by em 2026-11-07T09:00 -->\n  > Not accepted (nit): old\n  > Evidence: assets/X/a.png\n- [ ] two")
    ops.toggle_check(store, t.id, "Acceptance", 0, "!", actor="james", reason="new words here", severity="must fix")
    lines = store.get(t.id).sections["Acceptance"].strip().split("\n")
    assert len(lines) == 4 and lines[1] == "  > Not accepted (must fix): new words here" and lines[2] == "  > Evidence: assets/X/a.png"
    assert " by james " in lines[0] and lines[3] == "- [ ] two"
    ops.toggle_check(store, t.id, "Acceptance", 0, " ", actor="james")  # clearing drops reason, severity, who, when; evidence stays
    assert store.get(t.id).sections["Acceptance"].strip().split("\n")[:2] == ["- [ ] one", "  > Evidence: assets/X/a.png"]


def test_api_empty_or_short_reason_returns_422_and_a_good_one_200(store):
    t = _ticket(store)
    c = TestClient(create_app(store.home))
    for bad in ("", "  ", "ab"):
        r = _post(c, t, index=0, state="!", reason=bad)
        assert r.status_code == 422 and "reason" in r.json()["error"].lower()
    assert store.get(t.id).sections["Acceptance"].startswith("- [ ] one")
    r = _post(c, t, index=0, state="!", reason="Wrong colour", severity="nit")
    assert r.status_code == 200 and r.json()["state"] == "!"
    assert "  > Not accepted (nit): Wrong colour" in store.get(t.id).sections["Acceptance"]
    assert _post(c, t, index=0, state="!", reason="Wrong colour", severity="bogus").status_code == 422


def _acc(page: str) -> str:
    return page.split("Acceptance (this")[1].split('id="done-means"')[0]


def test_saved_reason_shows_severity_who_when_evidence_count_and_edit(store):
    t = _ticket(store, "- [!] one  <!-- by james 2026-11-08T03:41 -->\n  > Not accepted (should): Missing two commands\n"
                      "  > Evidence: assets/D/a.png\n  > Evidence: assets/D/b.png\n- [x] two\n  > Evidence: assets/D/c.png")
    acc = _acc(TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text)
    reason = re.search(r'<div class="nr-reason".*?</div>', acc, re.S).group(0)
    assert "Should" in reason and "Missing two commands" in reason and "james" in reason.lower() and "3:41 AM" in reason
    assert "2 screenshots" in reason and 'data-act="edit"' in reason and ">Edit<" in reason
    assert "> Not accepted" not in acc and "Evidence:" not in acc and "<!--" not in acc  # file syntax never leaks into the page
    assert "1 screenshot<" in acc  # a done line with one piece of evidence shows its count too


def test_panel_has_the_question_three_severities_and_the_right_cancel(store):
    t = _ticket(store)
    acc = _acc(TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text)
    panels = re.findall(r'<div class="nr-panel".*?</form></div>', acc, re.S)
    assert len(panels) == 3
    open_p, done_p = panels[0], panels[1]
    assert "Why isn&#39;t this accepted?" in open_p
    assert [v for v in re.findall(r'name="severity" value="([^"]+)"( checked)?', open_p)] == [("must fix", " checked"), ("should", ""), ("nit", "")]
    assert "Save reason" in open_p
    assert re.search(r'<button type="button"[^>]*data-act="cancel"', open_p)           # open line: Cancel just closes
    assert re.search(r'<button type="submit"[^>]*name="state" value=" "[^>]*>Cancel', done_p)  # done line: Cancel returns it to open


def test_cancel_on_a_done_line_posts_open_and_the_saved_edit_panel_is_prefilled(store):
    t = _ticket(store, "- [x] one\n- [!] two  <!-- by em 2026-11-08T01:00 -->\n  > Not accepted (nit): tiny")
    c = TestClient(create_app(store.home))
    assert _post(c, t, index=0, state=" ").status_code == 200  # what the Cancel button on a done line sends
    assert store.get(t.id).sections["Acceptance"].startswith("- [ ] one")
    panel = re.findall(r'<div class="nr-panel".*?</form></div>', _acc(c.get(f"/ticket/{t.id}").text), re.S)[1]
    assert ">tiny</textarea>" in panel and re.search(r'value="nit" checked', panel)


PANEL_JS = """<script>addEventListener('load',function(){
var out={},lis=document.querySelectorAll('li.task-list-item'),subs=[];
document.addEventListener('submit',function(e){e.preventDefault();e.stopImmediatePropagation();var f=e.target,s=e.submitter,sev=f.querySelector('[name=severity]:checked');
 subs.push([f.querySelector('[name=index]').value,s&&s.name==='state'?s.value:f.querySelector('[name=state]').value,(f.querySelector('textarea')||{}).value,sev?sev.value:null]);});
var l0=lis[0],p0=l0.querySelector('.nr-panel'),ta=p0.querySelector('textarea'),save=p0.querySelector('button[value="!"]');
l0.querySelector('[data-act=no]').click();out.opens=!p0.hidden&&document.activeElement===ta;
ta.value=' a b ';save.click();out.shortRefused=subs.length===0&&p0.querySelector('.nr-err').textContent.length>0&&!p0.hidden;
ta.value='abc';p0.querySelector('[value=nit]').click();save.click();out.goodSent=JSON.stringify(subs)===JSON.stringify([['0','!','abc','nit']]);
subs.length=0;p0.querySelector('[data-act=cancel]').click();out.cancelClosesAndSendsNothing=p0.hidden&&subs.length===0&&!l0.querySelector('.box.pending');
var l1=lis[1],p1=l1.querySelector('.nr-panel');l1.querySelector('[data-act=no]').click();
out.doneLooksClearedWhileAsking=!l1.querySelector('[data-act=done] .box').classList.contains('on-done')&&l1.querySelector('[data-act=no] .box').classList.contains('pending');
p1.querySelector('[data-act=cancel-clear]').click();out.cancelOnDoneSendsOpen=JSON.stringify(subs)===JSON.stringify([['1',' ','','must fix']]);
subs.length=0;var l2=lis[2],p2=l2.querySelector('.nr-panel');l2.querySelector('[data-act=edit]').click();
out.editOpensPrefilled=!p2.hidden&&p2.querySelector('textarea').value==='nope'&&p2.querySelector('[value=should]').checked;
fetch('/m',{method:'POST',body:JSON.stringify(out)});});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_reason_panel_in_a_real_browser(store):
    """Protects: the panel opens focused, refuses under 3 characters with a message, sends severity and reason, Cancel from an open
    line closes without sending, Cancel from a Done line sends open, Edit opens the saved reason prefilled."""
    t = _ticket(store, "- [ ] open line\n- [x] done line\n- [!] failed  <!-- by em 2026-11-08T01:00 -->\n  > Not accepted (should): nope")
    html = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    css = (STATIC / "desk.css").read_text(encoding="utf-8")
    js = (STATIC / "checks.js").read_text(encoding="utf-8")
    html = re.sub(r'<link rel="stylesheet" href="/static/desk.css">', lambda m: f"<style>{css}</style>", html)
    html = re.sub(r'<link rel="stylesheet" href="https://fonts[^>]*>|<link rel="preconnect"[^>]*>', "", html)
    html = html.replace('<script src="/static/checks.js" defer></script>', f"<script>{js}</script>").replace("</body>", PANEL_JS + "</body>")
    got = run_in_browser(html, 1280)
    assert got and all(got.values()), {k: v for k, v in got.items() if not v}
