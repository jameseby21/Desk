"""Rich ticket bodies: Markdown rendering, attachments, screen snaps, mermaid."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from desk import ops
from desk.cli import main
from desk.ticket import SECTIONS, Ticket
from desk.ui.app import create_app
from desk.ui.markdown import render

BODY = """### Plan

Some *words* and a [link](https://example.com).

- [x] first done
- [ ] second open

| col | n |
| --- | - |
| a   | 1 |

```python
print("hi")
```

```mermaid
graph TD; A-->B
```

<script>alert(1)</script>
"""


def test_markdown_renders_headings_lists_checkboxes_tables_code_links():
    out = render(BODY)
    assert "<h3>Plan</h3>" in out and "<ul" in out and "<em>words</em>" in out
    assert out.count('type="checkbox"') == 2 and 'checked="checked"' in out and 'disabled="disabled"' in out
    assert "<table>" in out and "<td>a</td>" in out
    assert '<code class="language-python">' in out
    assert '<pre class="mermaid">graph TD; A--&gt;B' in out
    assert 'href="https://example.com" target="_blank" rel="noopener noreferrer"' in out
    assert "<script>" not in out and "&lt;script&gt;" in out
    assert '<img src="/assets/X-0001/a.png"' in render("![a](assets/X-0001/a.png)")
    assert 'href="javascript:' not in render("[x](javascript:alert(1))")


def test_attachments_section_is_canonical_and_tolerated_missing(store):
    assert "Attachments" in SECTIONS and SECTIONS.index("Attachments") < SECTIONS.index("Report")
    t = ops.new(store, "Old file", "jre")
    text = t.path.read_text(encoding="utf-8").replace("\n## Attachments\n", "")
    t.path.write_text(text, encoding="utf-8")
    again = Ticket.load(t.path)
    assert again.section("Attachments") == "" and "Attachments" not in again.sections
    ops.comment(store, again, "still fine")
    assert "## Attachments" in t.path.read_text(encoding="utf-8")


def test_attach_copies_file_and_lists_it(store, tmp_path, capsys):
    t = ops.new(store, "With a picture", "jre")
    img = tmp_path / "shot one.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    note = tmp_path / "notes.txt"
    note.write_text("hello", encoding="utf-8")
    dest = ops.attach(store, t, img, caption="The screen")
    assert dest == store.tickets_dir / "assets" / t.id / "shot-one.png" and dest.read_bytes().startswith(b"\x89PNG")
    main(["--home", str(store.home), "attach", t.id, str(note)])
    assert capsys.readouterr().out.strip().endswith("notes.txt")
    ops.attach(store, store.get(t.id), img)  # same name again: no overwrite
    att = store.get(t.id).section("Attachments").splitlines()
    assert att == [
        f"![The screen](assets/{t.id}/shot-one.png)",
        f"[notes.txt](assets/{t.id}/notes.txt)",
        f"![shot one.png](assets/{t.id}/shot-one-2.png)",
    ]
    assert len(list(dest.parent.iterdir())) == 3
    assert store.tickets() and all(p.suffix == ".md" for p in store.ticket_paths())  # assets never look like tickets


def test_ui_renders_markdown_and_serves_assets_safely(store, tmp_path):
    t = ops.new(store, "Rich body", "jre")
    t.set_section("Context", BODY)
    store.save(t)
    img = tmp_path / "pic.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    ops.attach(store, t, img, caption="A pic")
    client = TestClient(create_app(store.home))
    page = client.get(f"/ticket/{t.id}").text
    assert "<h3>Plan</h3>" in page and 'type="checkbox"' in page and "<table>" in page
    assert '<pre class="mermaid">' in page and "mermaid.min.js" in page and "mermaid.initialize" in page
    assert f'<img src="/assets/{t.id}/pic.png"' in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page

    r = client.get(f"/assets/{t.id}/pic.png")
    assert r.status_code == 200 and r.content.startswith(b"\x89PNG")
    for bad in (f"/assets/{t.id}/../../desk.toml", f"/assets/{t.id}/..%2F..%2Fdesk.toml", "/assets/..%2F..%2Fdesk.toml/x",
                f"/assets/{t.id}/nope.png", f"/assets/{t.id}/%2e%2e/%2e%2e/desk.toml"):
        r = client.get(bad)
        assert r.status_code == 404, bad
        assert b"owner" not in r.content

    plain = ops.new(store, "No diagram", "jre")
    assert "mermaid.initialize" not in client.get(f"/ticket/{plain.id}").text
    r = client.get("/static/mermaid.min.js")
    assert r.status_code == 200 and b"mermaid" in r.content[:4000]


def test_snap_attaches_a_png(store, monkeypatch):
    t = ops.new(store, "Snapshot", "jre")

    def fake_capture(dest: Path) -> Path:
        dest.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        return dest

    monkeypatch.setattr(ops, "capture_screen", fake_capture)
    dest = ops.snap(store, t, caption="Desktop now")
    assert dest.parent == store.tickets_dir / "assets" / t.id and dest.name.startswith("snap-") and dest.suffix == ".png"
    assert store.get(t.id).section("Attachments") == f"![Desktop now](assets/{t.id}/{dest.name})"


def test_snap_refuses_without_a_tool(store, monkeypatch):
    t = ops.new(store, "Snapshot", "jre")
    monkeypatch.setattr(ops.platform, "system", lambda: "Plan9")
    with pytest.raises(ops.RuleError, match="not supported"):
        ops.snap(store, t)
