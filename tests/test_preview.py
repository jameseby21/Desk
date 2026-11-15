"""DESK-0291: attachments and local files open in a side panel, read-only. Each file type renders; a local path is served only inside
the allowed folders after it is resolved, never a key or private folder, never through a link that leads out, never for another site
or a renamed host. Protects: the owner sees the evidence without leaving the ticket, and nothing on disk leaks through Desk."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.parse import quote

import pytest
from browser_harness import browser, drive
from fastapi.testclient import TestClient

from desk import filepreview, localfiles as lf, officetext, ops, plainwords
from desk.rules import validate_store
from desk.ui.app import create_app

PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")
BASE = "http://127.0.0.1:8765"
LOCAL = ("127.0.0.1", 50000)


def _zip(path: Path, parts: dict[str, bytes | str]) -> Path:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in parts.items():
            z.writestr(name, data)
    path.write_bytes(buf.getvalue())
    return path


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'


def _docx(path: Path, text="First paragraph of the draft", extra="") -> Path:
    return _zip(path, {"word/document.xml": f'<?xml version="1.0"?>{extra}<w:document {W}><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p>'
                                            f'<w:p><w:r><w:t>Second &lt;b&gt;line</w:t></w:r></w:p></w:body></w:document>'})


def _xlsx(path: Path) -> Path:
    return _zip(path, {
        "xl/workbook.xml": f'<workbook {S} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Budget" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/sharedStrings.xml": f'<sst {S}><si><t>Item</t></si><si><t>Cost</t></si><si><t>Paint</t></si></sst>',
        "xl/worksheets/sheet1.xml": f'<worksheet {S}><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
                                    f'<row r="2"><c r="A2" t="s"><v>2</v></c><c r="C2"><v>42</v></c></row></sheetData></worksheet>',
    })


def _pptx(path: Path) -> Path:
    return _zip(path, {f"ppt/slides/slide{n}.xml": f'<p:sld {A}><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Slide text {n}</a:t></a:r></a:p>'
                                                   f'</p:txBody></p:sp></p:spTree></p:cSld></p:sld>' for n in (2, 1, 10)})


@pytest.fixture
def roots(store, tmp_path):
    """An allowed folder with one file of every kind, and a folder outside it holding a secret."""
    root = tmp_path / "allowed"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("outside secret\n", encoding="utf-8")
    (root / "report.pdf").write_bytes(b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n")
    (root / "notes.md").write_text("# Plan notes\n\nSome **bold** words.\n\n<script>alert(1)</script>\n", encoding="utf-8")
    (root / "plain.txt").write_text("line one\nline two <b>\n", encoding="utf-8")
    (root / "table.csv").write_text("name,score\n" + "".join(f"row{i},<i>{i}</i>\n" for i in range(700)), encoding="utf-8")
    (root / "pic.png").write_bytes(PNG)
    (root / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    (root / "page.html").write_text("<script>alert('x')</script>", encoding="utf-8")
    (root / "logo.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', encoding="utf-8")
    _docx(root / "draft.docx")
    _xlsx(root / "budget.xlsx")
    _pptx(root / "deck.pptx")
    for rel in (".env", "server.pem", "id_rsa", "vault.kdbx", ".git/config", ".ssh/known_hosts", "AppData/x.txt", "Secure docs/x.txt", ".claude/x.txt"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("private\n", encoding="utf-8")
    cfg = store.home / "desk.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8") + f"\n[files]\nallowed_roots = ['{root}']\n", encoding="utf-8")
    return root, outside


@pytest.fixture
def client(store, roots):
    return TestClient(create_app(store.home), base_url=BASE, client=LOCAL)


def _local(c, path, what="preview", **kw):
    url = "/local" if what == "raw" else f"/local/{what}" if what != "card" else "/card/local"
    return c.get(f"{url}?path={quote(str(path), safe='')}", **kw)


# -- every type renders ---------------------------------------------------------------------------------------------------------------

def test_each_local_type_renders_in_the_panel(client, roots):
    """PDF, Markdown, text, CSV, picture, video, Word, Excel and PowerPoint each preview with their own content."""
    root, _ = roots
    want = {
        "report.pdf": ['<iframe class="pv-pdf"', "/local?path="],
        "notes.md": ["<h1>Plan notes</h1>", "<strong>bold</strong>", "&lt;script&gt;"],
        "plain.txt": ['<pre class="pv-text">', "line two &lt;b&gt;"],
        "table.csv": ["<th>name</th>", "<td>row0</td>", "&lt;i&gt;0&lt;/i&gt;"],
        "pic.png": ['<img class="pv-img"'],
        "clip.mp4": ['<video class="pv-video"'],
        "draft.docx": ["First paragraph of the draft", "Second &lt;b&gt;line"],
        "budget.xlsx": ["Budget", "<td>Paint</td>", "<td>42</td>"],
        "deck.pptx": ["Slide 1", "Slide text 10"],
    }
    for name, needles in want.items():
        r = _local(client, root / name)
        assert r.status_code == 200, (name, r.text)
        for n in needles:
            assert n in r.text, (name, n)
        assert "Open in Explorer" in r.text and "<script>" not in r.text


def test_csv_shows_the_first_500_rows_only(client, roots):
    """A large CSV previews quickly: 500 rows (the header and 499 data rows), then a note."""
    root, _ = roots
    r = _local(client, root / "table.csv")
    assert "<td>row498</td>" in r.text and "<td>row499</td>" not in r.text
    assert "Showing the start of the file only." in r.text


def test_office_parts_come_out_in_slide_order(roots):
    """Slides are read by number (1, 2, 10), not by name order, and the sheet keeps its name."""
    root, _ = roots
    assert [s["n"] for s in officetext.extract(root / "deck.pptx")["slides"]] == [1, 2, 10]
    sheet = officetext.extract(root / "budget.xlsx")["sheets"][0]
    assert sheet["name"] == "Budget" and sheet["rows"] == [["Item", "Cost"], ["Paint", "", "42"]]


def test_attachment_panel_and_raw_bytes(client, store):
    """An attachment opens in the panel through /preview/<ID>/<file>; its bytes come from /preview/<ID>/<file>/raw, read-only."""
    t = ops.new(store, "Has a PDF", "desk")
    d = store.ticket_assets_dir(t.id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "proof.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    r = client.get(f"/preview/{t.id}/proof.pdf")
    assert r.status_code == 200 and f'src="/preview/{t.id}/proof.pdf/raw"' in r.text and f"Attached to {t.id}" in r.text
    raw = client.get(f"/preview/{t.id}/proof.pdf/raw")
    assert raw.status_code == 200 and raw.headers["content-type"] == "application/pdf" and raw.headers["content-disposition"] == "inline"
    assert raw.headers["x-content-type-options"] == "nosniff" and raw.content.startswith(b"%PDF")
    assert client.get(f"/preview/{t.id}/..%2Fdesk.toml").status_code == 404
    assert client.get(f"/preview/{t.id}/missing.pdf").status_code == 404


def test_pages_and_svg_are_never_drawn_as_pages(client, roots):
    """HTML is served as plain text in a sandbox; SVG downloads when opened directly; everything carries nosniff."""
    root, _ = roots
    r = _local(client, root / "page.html", "raw")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain") and "sandbox" in r.headers["content-security-policy"]
    s = _local(client, root / "logo.svg", "raw")
    assert s.headers["content-disposition"] == "attachment" and "sandbox" in s.headers["content-security-policy"]
    assert s.headers["x-content-type-options"] == "nosniff"


# -- refusals -------------------------------------------------------------------------------------------------------------------------

def _refused(r):
    return r.status_code in (400, 403, 404) and "secret" not in r.text and "private" not in r.text


def test_traversal_unc_device_stream_and_short_names_are_refused(client, roots):
    """Paths Windows reads in surprising ways are refused before anything is read."""
    root, outside = roots
    bad = [
        f"{root}\\..\\outside\\secret.txt", f"{root}/../outside/secret.txt", f"{root}/%2e%2e/outside/secret.txt", f"{root}/%252e%252e/outside/secret.txt",
        r"\\server\share\secret.txt", "//server/share/secret.txt", "file://server/share/secret.txt",
        r"\\.\C:\Windows\win.ini", r"\\?\C:\Windows\win.ini", "\\\\?\\" + str(outside / "secret.txt"),
        f"{root}\\plain.txt:stream", f"{root}\\plain.txt::$DATA", f"{root}\\PROGRA~1\\plain.txt",
        f"{root}\\server.pem.", f"{root}\\nul.txt", "plain.txt", "",
    ]
    for p in bad:
        for what in ("raw", "preview", "card"):
            r = _local(client, p, what)
            assert _refused(r), (p, what, r.status_code)


def test_outside_the_allowed_folders_is_refused(client, roots):
    """A real file outside the allowed folders gets 403 with the plain reason."""
    _, outside = roots
    r = _local(client, outside / "secret.txt", "raw")
    assert r.status_code == 403 and "outside the folders Desk may open" in r.text
    assert _local(client, "C:/Windows/win.ini", "raw").status_code == 403


def test_sensitive_names_are_refused_inside_the_allowed_folder(client, roots):
    """Keys, password stores and private folders are never served, even inside an allowed folder."""
    root, _ = roots
    for rel in (".env", "server.pem", "id_rsa", "vault.kdbx", ".git/config", ".ssh/known_hosts", "AppData/x.txt", "Secure docs/x.txt", ".claude/x.txt"):
        r = _local(client, root / rel, "raw")
        assert r.status_code == 403 and "private" not in r.text, rel


def _link_out(link: Path, target: Path) -> bool:
    """A symbolic link, else a directory junction; False when this machine allows neither."""
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
        return True
    except (OSError, NotImplementedError):
        pass
    if sys.platform == "win32" and target.is_dir():
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        return r.returncode == 0 and link.exists()
    return False


def test_a_link_that_leads_outside_is_refused(client, roots):
    """A link or junction inside the allowed folder that points outside it is refused, by the resolved path."""
    root, outside = roots
    made = _link_out(root / "escape", outside)
    if not made:
        pytest.skip("this machine refuses to create links and junctions")
    r = _local(client, root / "escape" / "secret.txt", "raw")
    assert r.status_code == 403 and "link that leads outside" in r.text
    if _link_out(root / "inside-key.txt", root / "server.pem"):
        assert _local(client, root / "inside-key.txt", "raw").status_code == 403  # a link named like a text file to a key file


def test_other_sites_and_renamed_hosts_are_refused(store, roots, client):
    """Cross-site requests, a Host that is not 127.0.0.1 or localhost on Desk's port, and callers off this computer get 403."""
    root, _ = roots
    p = root / "plain.txt"
    assert _local(client, p, "raw").status_code == 200
    assert _local(client, p, "raw", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200
    assert _local(client, p, "raw", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert _local(client, p, "raw", headers={"Origin": "http://evil.example"}).status_code == 403
    assert _local(client, p, "raw", headers={"Host": "evil.example:8765"}).status_code == 403  # DNS rebinding
    assert _local(client, p, "raw", headers={"Host": "127.0.0.1:9999"}).status_code == 403
    assert _local(client, p, "raw", headers={"Host": "localhost:8765"}).status_code == 200
    far = TestClient(create_app(store.home), base_url=BASE, client=("192.168.1.20", 50000))
    assert _local(far, p, "raw").status_code == 403
    t = ops.new(store, "Has a file", "desk")
    d = store.ticket_assets_dir(t.id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "a.txt").write_text("hello\n", encoding="utf-8")
    assert client.get(f"/preview/{t.id}/a.txt", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get(f"/card/asset/{t.id}/a.txt", headers={"Host": "evil.example:8765"}).status_code == 403


def test_office_bombs_and_entities_are_refused(roots, tmp_path, monkeypatch):
    """A zip bomb, an XML entity trick and a file with too many parts are refused with a plain sentence, never unpacked."""
    root, _ = roots
    bomb = _zip(root / "bomb.docx", {"word/document.xml": b"<a>" + b" " * (3 * 1024 * 1024) + b"</a>"})
    with pytest.raises(officetext.OfficeError, match="unpacks to far more"):
        officetext.extract(bomb)
    lol = _docx(root / "lol.docx", text="&lol;", extra='<!DOCTYPE d [<!ENTITY lol "lol">]>')
    with pytest.raises(officetext.OfficeError, match="document type"):
        officetext.extract(lol)
    monkeypatch.setattr(officetext, "MAX_ENTRIES", 2)
    many = _zip(root / "many.docx", {f"p{i}.xml": "<a/>" for i in range(3)})
    with pytest.raises(officetext.OfficeError, match="too many parts"):
        officetext.extract(many)


def test_bomb_preview_shows_the_reason_not_an_error_page(client, roots):
    """The panel says why an Office file cannot be shown."""
    root, _ = roots
    _docx(root / "lol.docx", text="&lol;", extra='<!DOCTYPE d [<!ENTITY lol "lol">]>')
    r = _local(client, root / "lol.docx")
    assert r.status_code == 200 and "document type declaration" in r.text


# -- Open in Explorer -----------------------------------------------------------------------------------------------------------------

@pytest.fixture
def launched(monkeypatch):
    calls = []
    monkeypatch.setattr(lf, "launch", calls.append)
    monkeypatch.setattr(lf.sys, "platform", "win32")
    return calls


def test_open_in_explorer_selects_the_checked_file(client, roots, launched):
    """POST /local/reveal starts Explorer with the file selected, only for an allowed path and only from Desk's own page."""
    root, outside = roots
    ok = client.post("/local/reveal", json={"path": str(root / "plain.txt")}, headers={"Origin": BASE})
    assert ok.status_code == 200 and "plain.txt selected" in ok.json()["message"]
    assert len(launched) == 1 and "/select," in launched[0] and str(root / "plain.txt") in launched[0]
    assert client.post("/local/reveal", json={"path": str(outside / "secret.txt")}, headers={"Origin": BASE}).status_code == 403
    assert client.post("/local/reveal", json={"path": str(root / "plain.txt")}).status_code == 403  # no Origin: not from a Desk page
    assert client.post("/local/reveal", json={"path": str(root / "plain.txt")}, headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.post("/local/reveal", data={"path": str(root / "plain.txt")}, headers={"Origin": BASE}).status_code == 415
    assert len(launched) == 1


# -- links in ticket text, hover cards, page wiring -------------------------------------------------------------------------------------

def test_paths_in_ticket_text_become_preview_links(client, store, roots):
    """A file:/// link and a bare Windows path in a ticket open the preview; an attachment link carries the panel and the card."""
    root, _ = roots
    t = ops.new(store, "Links a file", "desk")
    t.set_section("Context", f"The plan is at {root}\\notes.md and [the PDF](file:///{str(root / 'report.pdf').replace(os.sep, '/')}).\n")
    t.set_section("Attachments", f"[proof](assets/{t.id}/proof.pdf)\n")
    store.save(t)
    page = client.get(f"/ticket/{t.id}").text
    q = quote(str(root / "notes.md").replace("\\", "/"), safe="/:")
    assert f'data-preview="/local/preview?path={q}"' in page and f'href="/local?path={q}"' in page
    assert "report.pdf" in page and 'data-card="/card/local?path=' in page
    assert f'data-preview="/preview/{t.id}/proof.pdf"' in page and f'data-card="/card/asset/{t.id}/proof.pdf"' in page
    assert "preview.js" in page and "preview.css" in page
    assert client.get("/static/preview.js").status_code == 200


def test_hover_card_shows_first_lines_or_a_thumbnail(client, store, roots):
    """Text shows its first lines; a picture shows a thumbnail."""
    root, _ = roots
    r = _local(client, root / "plain.txt", "card")
    assert r.status_code == 200 and "line one" in r.text and "hc-lines" in r.text
    t = ops.new(store, "Has a picture", "desk")
    d = store.ticket_assets_dir(t.id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "shot.png").write_bytes(PNG)
    c = client.get(f"/card/asset/{t.id}/shot.png")
    assert c.status_code == 200 and f'src="/preview/{t.id}/shot.png/raw"' in c.text and "hc-thumb" in c.text


# -- validate ----------------------------------------------------------------------------------------------------------------------------

def _review(store, t):
    t.meta["status"] = "review"
    t.set_section("Report", "Built and checked.")
    store.save(t)


def test_validate_warns_on_ui_work_in_review_with_no_image(store):
    """A ticket tagged ui or ux, or a feature in a ui: true project, in review with no image attached gets a warning."""
    t = ops.new(store, "A ui change", "desk")
    t.meta["tags"] = ["ux"]
    _review(store, t)
    assert any(t.id in w and "no image attached" in w for w in validate_store(store)[1])
    t.set_section("Attachments", f"![after](assets/{t.id}/after.png)\n")
    store.save(t)
    assert not any(t.id in w and "no image attached" in w for w in validate_store(store)[1])
    (store.projects_dir / "jre.md").write_text("---\nkey: jre\nui: true\n---\n# jRE\n", encoding="utf-8")
    f = ops.new(store, "A screen feature", "jre")
    f.meta["type"] = "feature"
    _review(store, f)
    assert any(f.id in w and "no image attached" in w for w in validate_store(store)[1])
    plain = ops.new(store, "Back-end work", "desk")
    _review(store, plain)
    assert not any(plain.id in w and "no image attached" in w for w in validate_store(store)[1])


# -- plain words ---------------------------------------------------------------------------------------------------------------------------

def test_refusal_sentences_are_plain(store, roots):
    """Every refusal a person can read passes the DESK-0253 plain-language checks (grade 10 or lower, no unexplained jargon)."""
    root, outside = roots
    reasons = set()
    for p in ["", "plain.txt", r"\\server\x", f"{root}/../x", f"{root}\\a.txt:s", f"{root}\\PROGRA~1\\x", f"{root}\\x.pem.", f"{root}\\nul",
              str(outside / "secret.txt"), str(root / ".env"), str(root / ".git" / "config"), str(root / "missing.txt")]:
        try:
            lf.check(store.home, p, store.config)
        except lf.Refused as e:
            reasons.add(str(e))
    text = " ".join(sorted(reasons))
    assert len(reasons) >= 9
    assert plainwords.grade(text) <= 10, (plainwords.grade(text), text)
    assert not plainwords.jargon(text)
    assert filepreview.kind_of("x.PDF") == "pdf"


# -- a real browser ------------------------------------------------------------------------------------------------------------------------

PANEL = """<script>addEventListener('load',function(){var a=document.querySelector('a[data-preview*="notes.txt"]');if(!a)return;
var out={},sleep=function(ms){return new Promise(function(r){setTimeout(r,ms);});};
(async function(){
 a.click();
 for(var i=0;i<50&&!(document.getElementById('pv')&&document.querySelector('#pv .pv-text'));i++){await sleep(100);}
 var p=document.getElementById('pv');out.open=!!p&&!p.hidden;out.text=p?p.textContent:'';out.dialog=p?p.getAttribute('role'):'';
 document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
 out.closed=p.hidden;out.focusBack=document.activeElement===a;
 a.dispatchEvent(new MouseEvent('mouseover',{bubbles:true}));
 for(var k=0;k<40&&!(document.getElementById('hcard')&&!document.getElementById('hcard').hidden);k++){await sleep(100);}
 var c=document.getElementById('hcard');out.card=c?c.textContent:'';
 fetch('/m',{method:'POST',body:JSON.stringify(out)});})();});</script>"""


@pytest.mark.skipif(browser() is None, reason="no Edge or Chrome installed")
def test_clicking_an_attachment_opens_the_panel_in_a_real_browser(store):
    """A click opens the side panel with the file's text, Esc closes it and gives focus back, and hovering shows the first lines."""
    t = ops.new(store, "Has notes", "desk")
    d = store.ticket_assets_dir(t.id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "notes.txt").write_text("first line of the notes\nsecond line\n", encoding="utf-8")
    t.set_section("Attachments", f"[the notes](assets/{t.id}/notes.txt)\n")
    store.save(t)
    got = drive(store, PANEL, f"/ticket/{t.id}")
    assert got["open"] and "first line of the notes" in got["text"] and got["dialog"] == "dialog", got
    assert got["closed"] and got["focusBack"], got
    assert "first line of the notes" in got["card"] and "click to preview" in got["card"], got


# -- review fixes (DESK-0291 QA) ------------------------------------------------------------------------------------------------------

def _damaged_docx(path: Path, method: int) -> Path:
    """A docx whose document part has one changed byte after packing: a bad checksum (stored) or broken compressed data (deflated)."""
    body = f'<?xml version="1.0"?><w:document {W}><w:body><w:p><w:r><w:t>{"Damaged draft text " * 40}</w:t></w:r></w:p></w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method) as z:
        z.writestr("word/document.xml", body)
    data = bytearray(buf.getvalue())
    at = data.index(b"word/document.xml") + len("word/document.xml") + 40  # inside the part's data, after its local header
    data[at] ^= 0xFF
    path.write_bytes(bytes(data))
    return path


def test_a_damaged_office_file_shows_a_sentence_not_an_error_page(client, roots):
    """A docx with a bad checksum or broken compressed data gives the panel's plain sentence and a hover card, never HTTP 500."""
    root, _ = roots
    for name, method in (("stored.docx", zipfile.ZIP_STORED), ("deflated.docx", zipfile.ZIP_DEFLATED)):
        path = _damaged_docx(root / name, method)
        with pytest.raises(officetext.OfficeError, match="damaged"):
            officetext.extract(path)
        panel = _local(client, path)
        assert panel.status_code == 200 and "damaged" in panel.text, (name, panel.status_code)
        card = _local(client, path, "card")
        assert card.status_code == 200 and name in card.text, (name, card.status_code)


def test_a_utf16_part_with_a_doctype_is_refused(roots):
    """A document type declaration is refused in UTF-16 parts too, so an entity is never expanded whatever the encoding."""
    root, _ = roots
    xml = (f'<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE d [<!ENTITY e "ENTITY-EXPANDED">]><w:document {W}><w:body><w:p><w:r>'
           f'<w:t>&e;</w:t></w:r></w:p></w:body></w:document>')
    for enc in ("utf-16", "utf-16-le", "utf-16-be"):
        data = xml.encode(enc)
        if enc != "utf-16":
            data = "\ufeff".encode(enc) + data
        path = _zip(root / f"u16-{enc}.docx", {"word/document.xml": data})
        with pytest.raises(officetext.OfficeError, match="document type"):
            officetext.extract(path)
    plain = xml.replace(' encoding="UTF-16"', "").replace('<!DOCTYPE d [<!ENTITY e "ENTITY-EXPANDED">]>', "").replace("&e;", "kept")
    ok = _zip(root / "u16-plain.docx", {"word/document.xml": "\ufeff".encode("utf-16-le") + plain.encode("utf-16-le")})
    assert officetext.extract(ok)["paragraphs"] == ["kept"]


def test_an_empty_allowed_list_turns_local_files_off(store, tmp_path):
    """desk.toml [files] allowed_roots = [] means no folder may be opened, not the default Documents and Downloads."""
    assert lf.allowed_roots(store.home, {"files": {"allowed_roots": []}}) == []
    assert lf.allowed_roots(store.home, {}) != []
    f = store.home / "note.txt"
    f.write_text("hello\n", encoding="utf-8")
    with pytest.raises(lf.Refused, match="opens no local files") as off:
        lf.check(store.home, str(f), {"files": {"allowed_roots": []}})
    assert plainwords.grade(str(off.value)) <= 10 and not plainwords.jargon(str(off.value))
    assert lf.check(store.home, str(f), {}) == Path(os.path.realpath(f))


def test_common_credential_files_are_never_served(client, roots, store):
    """Credential and token files that tools leave in Documents or Downloads are refused even inside an allowed folder."""
    root, _ = roots
    names = ("credentials.json", "client_secret_123.json", ".git-credentials", ".netrc", "_netrc", ".npmrc", ".pypirc", ".pgpass",
             "putty.ppk", "client.ovpn", "secrets.yaml", "secrets.toml", "token.json", "prod.env", "private.asc", ".htpasswd")
    for name in names:
        (root / name).write_text("token = value\n", encoding="utf-8")
        assert _local(client, root / name, "raw").status_code == 403, name
        assert _local(client, root / name, "card").status_code == 403, name
    (root / "meeting notes.txt").write_text("hello\n", encoding="utf-8")
    assert _local(client, root / "meeting notes.txt", "raw").status_code == 200
    (root / "budget.private").write_text("x\n", encoding="utf-8")
    cfg = {"files": {"allowed_roots": [str(root)]}}
    assert lf.check(store.home, str(root / "budget.private"), cfg)
    with pytest.raises(lf.Refused, match="never opens"):
        lf.check(store.home, str(root / "budget.private"), {"files": {**cfg["files"], "deny_names": ["*.PRIVATE"]}})


def test_markdown_preview_never_loads_pictures_from_the_web(client, roots):
    """A local Markdown file's web pictures are shown as a note, never fetched, so opening it tells no outside site anything."""
    root, _ = roots
    (root / "track.md").write_text("# Notes\n\n![pixel](https://tracker.example/p.png?u=1)\n\n![two](//other.example/q.png)\n\n"
                                   "![three](/\\third.example/r.png)\n\n![ok](/preview/DESK-0001/a.png)\n", encoding="utf-8")
    r = _local(client, root / "track.md")
    assert r.status_code == 200
    for host in ("tracker.example", "other.example", "third.example"):
        assert f'src="https://{host}' not in r.text and f'src="//{host}' not in r.text and f'src="/\\{host}' not in r.text, host
    assert "tracker.example" in r.text  # the address is still shown as text
    assert '<img src="/preview/DESK-0001/a.png"' in r.text
