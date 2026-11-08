"""DESK-0092: display-time profanity filter; files stay raw."""

from fastapi.testclient import TestClient

from desk import ops, profanity, style as styles
from desk.cli import main
from desk.ui.app import create_app
from desk.ui.markdown import render


def test_censor_keeps_first_and_last_letter():
    f = profanity.make_filter()
    assert f("this is fucking broken") == "this is f*****g broken"
    assert f("Shit happens, SHIT") == "S**t happens, S**T"
    assert f("class assessment and Scunthorpe") == "class assessment and Scunthorpe"  # whole words only


def test_store_additions_file(store):
    (store.home / "profanity-extra.txt").write_text("# mine\nfrabjous\n", encoding="utf-8")
    assert profanity.make_filter(store.home)("a frabjous day") == "a f******s day"


def test_markdown_skips_code_and_targets():
    f = profanity.make_filter()
    out = render("fucking `fucking` [shit](/doc/shit.md)\n\n```\nfucking\n```\n", censor=f)
    assert "f*****g <code>fucking</code>" in out and "<code>fucking</code>" in out.split("</p>")[1] + "</pre>" or "fucking\n</code>" in out
    assert "f*****g" in out and 'href="/doc/shit.md"' in out and ">s**t</a>" in out
    assert render("fucking") == "<p>fucking</p>\n"  # no filter given: untouched


def test_ui_default_on_raw_file_unchanged_and_switch_off(store):
    t = ops.new(store, "That fucking bug", "jre")
    ops.comment(store, store.get(t.id), "this is fucking broken", actor="james")
    c = TestClient(create_app(store.home))
    page = c.get(f"/ticket/{t.id}").text
    assert "f*****g broken" in page and "That f*****g bug" in page and "fucking" not in page
    assert "fucking broken" in store.get(t.id).path.read_text(encoding="utf-8")
    assert "fucking" not in c.get("/").text
    r = c.post("/settings", data={"name": "plain", "dog": "drew", "profanity": "off"}, follow_redirects=False)
    assert r.status_code == 303 and "profanity_filter = false" in styles.settings_path(store.home).read_text(encoding="utf-8")
    assert "fucking broken" in c.get(f"/ticket/{t.id}").text
    assert "checked" in c.get("/settings").text


def test_show_filters_unless_raw(store, capsys):
    t = ops.new(store, "Plain", "jre")
    ops.comment(store, store.get(t.id), "so fucking `fucking` slow", actor="james")
    main(["--home", str(store.home), "show", t.id])
    out = capsys.readouterr().out
    assert "so f*****g `fucking` slow" in out
    main(["--home", str(store.home), "show", t.id, "--raw"])
    assert "so fucking `fucking` slow" in capsys.readouterr().out
