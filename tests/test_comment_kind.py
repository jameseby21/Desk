"""DESK-0125 (Chaz R9): every comment stores a kind (Question, Suggestion, Concern or FYI; Question by default), and Ctrl Enter posts
through the same single-shot path as the button.

File form: `- 2026-11-08 03:41 · james · the words <!-- kind: Concern -->`. The kind rides at the end of the line as a comment, so older
readers of the file see the same words as before and comments written before this have no kind."""

import re

import pytest
from browser_harness import browser, drive
from fastapi.testclient import TestClient

from desk import identity, ops
from desk.cli import main
from desk.ui.app import create_app

JSON = {"Accept": "application/json"}


def _ticket(store):
    return ops.new(store, "Kinds", "jre")


def test_every_comment_stores_a_kind_and_question_is_the_default(store):
    t = _ticket(store)
    ops.comment(store, store.get(t.id), "Plain words", actor="james")
    for kind in ops.COMMENT_KINDS:
        ops.comment(store, store.get(t.id), f"About {kind}", actor="james", kind=kind.lower())  # any case is accepted
    lines = ops.comment_lines(store.get(t.id))
    assert re.fullmatch(r"- \d{4}-\d\d-\d\d \d\d:\d\d · james · Plain words <!-- kind: Question -->", lines[0])
    assert [ln.rsplit("kind: ", 1)[1].rstrip(" ->") for ln in lines[1:]] == ["Question", "Suggestion", "Concern", "FYI"]
    with pytest.raises(ValueError, match="Question, Suggestion, Concern or FYI"):
        ops.comment(store, store.get(t.id), "x", actor="james", kind="Rant")


def test_older_comments_have_no_kind_and_duplicates_ignore_it(store):
    t = _ticket(store)
    t.append_line("Comments", "- 2026-01-01 09:00 · em · Old words")
    store.save(t)
    assert ops.split_comment_kind("Old words") == (None, "Old words")
    assert ops.split_comment_kind("New words <!-- kind: FYI -->") == ("FYI", "New words")
    assert ops.comment(store, store.get(t.id), "Same words", actor="em", kind="Question").added
    assert not ops.comment(store, store.get(t.id), "same  words", actor="em", kind="Concern").added  # same words, other kind: still a duplicate
    assert ops.locate_comment(store.get(t.id), "em", "Same words") == 1


def test_cli_comment_takes_a_kind_and_show_prints_it(store, capsys, monkeypatch):
    t = _ticket(store)
    monkeypatch.setenv("DESK_ACTOR", "em")  # DESK-0261: `--as` must be the caller
    main(["--home", str(store.home), "comment", t.id, "Please look", "--kind", "Concern", "--as", "em"])
    main(["--home", str(store.home), "comment", t.id, "No kind given", "--as", "em"])
    capsys.readouterr()
    main(["--home", str(store.home), "show", t.id])
    shown = capsys.readouterr().out.splitlines()
    comments = [identity.parse_tag(ln)[1] for ln in shown if ln.startswith("- 20") and "· em ·" in ln]
    assert comments[0].endswith("Please look <!-- kind: Concern -->") and comments[1].endswith("No kind given <!-- kind: Question -->")
    with pytest.raises(SystemExit):
        main(["--home", str(store.home), "comment", t.id, "x", "--kind", "Rant"])


def test_api_default_given_and_unknown_kinds(store):
    t = _ticket(store)
    c = TestClient(create_app(store.home))

    def post(**extra):
        return c.post(f"/ticket/{t.id}/comment", data={"token": c.app.state.tokens.issue(), "text": f"words {len(extra)} {sorted(extra.values())}", **extra}, headers=JSON)

    assert post().json()["line"].endswith("<!-- kind: Question -->")
    assert post(kind="Suggestion").json()["line"].endswith("<!-- kind: Suggestion -->")
    r = post(kind="Rant")
    assert r.status_code == 422 and "Question, Suggestion, Concern or FYI" in r.json()["error"]
    assert len(ops.comment_lines(store.get(t.id))) == 2


def test_page_offers_the_four_kinds_with_question_chosen_and_shows_kinds_on_comments(store):
    t = _ticket(store)
    ops.comment(store, store.get(t.id), "A worry", actor="james", kind="Concern")
    t2 = store.get(t.id)
    t2.append_line("Comments", "- 2026-01-01 09:00 · em · Old words")
    store.save(t2)
    page = TestClient(create_app(store.home)).get(f"/ticket/{t.id}").text
    form = re.search(r'<form[^>]*id="composer".*?</form>', page, re.S).group(0)
    assert re.findall(r'name="kind" value="(\w+)"( checked)?', form) == [("Question", " checked"), ("Suggestion", ""), ("Concern", ""), ("FYI", "")]
    assert 'role="radiogroup"' in form
    comments = re.search(r"<div class=\"md\" data-comments>.*?</div>", page, re.S).group(0)
    assert '<span class="kind kind-concern">Concern</span>' in comments and "A worry" in comments and "<!--" not in comments and "@@" not in comments
    assert comments.count('class="kind ') == 1  # the older comment has none


LIVE = """<script>addEventListener('load',function(){if(!document.getElementById('composer'))return;
var out={},ta=document.getElementById('comment-text'),form=document.getElementById('composer'),st=document.getElementById('post-status'),
sleep=function(ms){return new Promise(function(r){setTimeout(r,ms);});},
key=function(el){el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',ctrlKey:true,bubbles:true,cancelable:true}));};
(async function(){
 form.querySelector('input[name=kind][value=Concern]').click();
 ta.value='Ctrl Enter once';ta.focus();
 key(ta);out.label=form.querySelector('.post-btn').textContent.trim();key(ta);await sleep(60);key(ta);out.locked=ta.readOnly;
 for(var i=0;i<80&&!/Posted/.test(st.textContent);i++){await sleep(100);}
 out.status=st.textContent.trim();
 var li=document.querySelectorAll('[data-comments] li');out.count=li.length;out.pill=li.length?((li[li.length-1].querySelector('.kind')||{}).textContent||''):'';
 ta.value='Plain Enter does not post';ta.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));await sleep(300);
 out.plainEnterQuiet=document.querySelectorAll('[data-comments] li').length===1&&ta.value==='Plain Enter does not post';
 fetch('/m',{method:'POST',body:JSON.stringify(out)});})();});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_ctrl_enter_posts_once_with_its_kind_in_a_real_browser(store):
    """Protects: Ctrl Enter pressed three times quickly goes through the same single-shot path as the button, stores one comment with the
    chosen kind, and plain Enter still only makes a new line."""
    t = _ticket(store)
    got = drive(store, LIVE, f"/ticket/{t.id}", intercept=lambda scope: 0.8 if scope["path"].endswith("/comment") else None, timeout=40)
    assert got["label"] == "Posting…" and got["locked"] and got["status"] == "Posted" and got["count"] == 1 and got["pill"] == "Concern" and got["plainEnterQuiet"]
    lines = ops.comment_lines(store.get(t.id))
    assert len(lines) == 1 and identity.parse_tag(lines[0])[1].endswith("Ctrl Enter once <!-- kind: Concern -->")
