"""DESK-0118 (Chaz R2): the six moves between open, done and not accepted, with the mouse and with the keys D, N and Esc."""

import re

import pytest
from browser_harness import browser, run_in_browser
from fastapi.testclient import TestClient

from desk import ops
from desk.ui.app import STATIC, create_app

OPEN, DONE, NO = " ", "x", "!"
MARK = {OPEN: "- [ ] the line", DONE: "- [x] the line", NO: "- [!] the line"}


def _ticket(store, state=OPEN, extra=()):
    t = ops.new(store, "Moves", "jre")
    t.set_section("Acceptance", "\n".join([MARK[state], *extra]))
    store.save(t)
    return t


def _state(store, t) -> str:
    return re.match(r"- \[(.)\]", store.get(t.id).sections["Acceptance"]).group(1)


def test_press_is_the_rulebook_for_all_six_moves():
    """Protects: one table says what a click does. Done toggles; Not accepted toggles; setting one clears the other."""
    assert ops.press(OPEN, "done") == (DONE, False)  # 1 open -> done
    assert ops.press(DONE, "done") == (OPEN, False)  # 2 done -> open
    assert ops.press(OPEN, "no") == (NO, True)       # 3 open -> not accepted (needs a reason)
    assert ops.press(NO, "no") == (OPEN, False)      # 4 not accepted -> open
    assert ops.press(DONE, "no") == (NO, True)       # 5 done -> not accepted (done is cleared)
    assert ops.press(NO, "done") == (DONE, False)    # 6 not accepted -> done (the reason goes)
    with pytest.raises(ValueError):
        ops.press(OPEN, "maybe")


def _forms(page: str) -> list[str]:
    return re.findall(r'<form class="chk post-once".*?</form>', page, re.S)


def _click(c, t, index: int, which: str, reason: str = "A real reason"):
    """Do what the person does: find the form behind that box on the rendered page and send exactly what the browser would."""
    page = c.get(f"/ticket/{t.id}").text
    pick = {"done": lambda f: 'data-act="done"' in f,
            "no": lambda f: 'data-act="no"' in f or ('name="state" value="!"' in f and "textarea" in f)}[which]
    form = next(f for f in _forms(page) if f'name="index" value="{index}"' in f and pick(f))
    data = {k: v for k, v in re.findall(r'<input type="hidden" name="(\w+)" value="([^"]*)"', form)}
    if "textarea" in form:  # the reason panel: the Save button carries state "!"
        data.update(state="!", reason=reason)
    return c.post(f"/ticket/{t.id}/check", data=data, follow_redirects=False)


@pytest.mark.parametrize("start,which,end", [(OPEN, "done", DONE), (DONE, "done", OPEN), (OPEN, "no", NO),
                                             (NO, "no", OPEN), (DONE, "no", NO), (NO, "done", DONE)])
def test_each_of_the_six_moves_through_the_rendered_forms(store, start, which, end):
    t = _ticket(store, start, extra=["- [ ] untouched"])
    if start == NO:
        ops.toggle_check(store, t.id, "Acceptance", 0, "!", reason="Was wrong")
    c = TestClient(create_app(store.home))
    r = _click(c, t, 0, which)
    assert r.status_code == 303 and "error" not in r.headers["location"]
    assert _state(store, t) == end
    assert store.get(t.id).sections["Acceptance"].split("\n")[-2].startswith("- [ ] untouched")  # only that line moved
    if end != NO:
        assert "Was wrong" not in store.get(t.id).sections["Acceptance"] and "A real reason" not in store.get(t.id).sections["Acceptance"]


def test_lines_take_focus_and_the_keys_are_wired(store):
    """Protects: a line can be tabbed to; the script handles D, N and Esc (the real key events run in the browser test below)."""
    t = _ticket(store, OPEN, extra=["- [x] two"])
    page = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    assert page.count('<li class="task-list-item" tabindex="0"') == 2
    js = (STATIC / "checks.js").read_text(encoding="utf-8")
    assert all(k in js for k in ('"keydown"', '"Escape"', '"d"', '"n"'))


KEYS = """<script>addEventListener('load',function(){
var out={},lis=document.querySelectorAll('li.task-list-item'),submits=[];
document.addEventListener('submit',function(e){e.preventDefault();e.stopImmediatePropagation();submits.push([e.target.querySelector('[name=index]').value,e.target.querySelector('[name=state]').value]);},true);
function key(k,t){(t||document.activeElement).dispatchEvent(new KeyboardEvent('keydown',{key:k,bubbles:true,cancelable:true}));}
var li=lis[0],panel=li.querySelector('.nr-panel');
li.focus();out.focused=document.activeElement===li;
key('n');out.panelOpens=!panel.hidden;out.focusInReason=document.activeElement===panel.querySelector('textarea');
key('d');out.typingDDoesNothing=submits.length===0;
key('Escape');out.escCloses=panel.hidden;out.focusBackOnButton=document.activeElement.getAttribute('data-act')==='no';
li.focus();key('d');out.dOnOpenSubmitsX=JSON.stringify(submits)===JSON.stringify([['0','x']]);
submits.length=0;lis[1].focus();key('d');out.dOnDoneSubmitsOpen=JSON.stringify(submits)===JSON.stringify([['1',' ']]);
submits.length=0;lis[2].focus();key('n');out.nOnNotAcceptedSubmitsOpen=JSON.stringify(submits)===JSON.stringify([['2',' ']]);
submits.length=0;lis[2].focus();key('d');out.dOnNotAcceptedSubmitsX=JSON.stringify(submits)===JSON.stringify([['2','x']]);
submits.length=0;li.focus();li.dispatchEvent(new KeyboardEvent('keydown',{key:'d',ctrlKey:true,bubbles:true}));out.ctrlDIgnored=submits.length===0;
fetch('/m',{method:'POST',body:JSON.stringify(out)});});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_keyboard_d_n_esc_in_a_real_browser(store):
    """Protects: with focus on a line, D toggles Done, N opens (or clears) Not accepted, Esc closes the reason and returns focus."""
    t = ops.new(store, "Keys", "jre")
    t.set_section("Acceptance", "- [ ] open line\n- [x] done line\n- [!] failed line — not accepted: nope")
    store.save(t)
    html = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    css = (STATIC / "desk.css").read_text(encoding="utf-8")
    js = (STATIC / "checks.js").read_text(encoding="utf-8")
    html = re.sub(r'<link rel="stylesheet" href="/static/desk.css">', lambda m: f"<style>{css}</style>", html)
    html = re.sub(r'<link rel="stylesheet" href="https://fonts[^>]*>|<link rel="preconnect"[^>]*>', "", html)
    html = html.replace('<script src="/static/checks.js" defer></script>', f"<script>{js}</script>").replace("</body>", KEYS + "</body>")
    got = run_in_browser(html, 1280)
    assert got and all(got.values()), {k: v for k, v in got.items() if not v}
