"""What the preview panel shows for a file (DESK-0291), and the validate warning for UI work in review with no picture.

kind_of(path) picks one of: pdf, markdown, text, csv, image, video, audio, office, other. read_text and read_csv cap what they read,
so a large file previews its start quickly. Raw bytes are served with headers that keep a page or a picture from running code."""

from __future__ import annotations

import csv
import io
import mimetypes
import re
from pathlib import Path

from .ticket import Ticket

TEXT_CAP = 512 * 1024
CSV_CAP = 2 * 1024 * 1024
CSV_ROWS = 500

KINDS = {
    "pdf": {".pdf"},
    "markdown": {".md", ".markdown"},
    "csv": {".csv", ".tsv"},
    "image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif", ".ico", ".svg"},
    "video": {".mp4", ".webm", ".ogv", ".mov", ".m4v"},
    "audio": {".mp3", ".wav", ".ogg", ".m4a", ".flac"},
    "office": {".docx", ".xlsx", ".pptx"},
    "text": {".txt", ".log", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".py", ".js", ".ts", ".css", ".sql",
             ".ps1", ".sh", ".bat", ".cmd", ".xml", ".html", ".htm", ".rst", ".tex"},
}
# Types a browser would run as a page: served as plain text or as a download, never drawn.
ACTIVE = {".html", ".htm", ".xhtml", ".xht", ".shtml", ".svg", ".svgz", ".xml", ".xsl", ".xslt", ".js", ".mjs", ".mht", ".mhtml"}
_TYPES = {".md": "text/plain; charset=utf-8", ".markdown": "text/plain; charset=utf-8", ".csv": "text/plain; charset=utf-8", ".tsv": "text/plain; charset=utf-8",
          ".pdf": "application/pdf", ".webp": "image/webp", ".avif": "image/avif", ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
          ".m4v": "video/mp4", ".ogv": "video/ogg"}
RAW_CSP = "default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'; sandbox"


def kind_of(path: Path | str) -> str:
    ext = Path(str(path)).suffix.lower()
    return next((k for k, exts in KINDS.items() if ext in exts), "other")


def raw_headers(path: Path) -> tuple[str, dict]:
    """(media type, headers) for serving a file read-only. Pages and scripts become text or a download; nothing is sniffed."""
    ext = path.suffix.lower()
    kind = kind_of(path)
    if ext in ACTIVE:
        media = "image/svg+xml" if ext in (".svg", ".svgz") else "text/plain; charset=utf-8"
    elif kind in ("text", "markdown", "csv"):
        media = _TYPES.get(ext, "text/plain; charset=utf-8")
    else:
        media = _TYPES.get(ext) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
               "Cross-Origin-Resource-Policy": "same-origin", "X-Frame-Options": "SAMEORIGIN"}
    if kind != "pdf":  # a sandboxed PDF will not open in the browser's own viewer
        headers["Content-Security-Policy"] = RAW_CSP
    if ext in (".svg", ".svgz") or kind == "other":
        headers["Content-Disposition"] = "attachment"  # an <img> still draws an SVG; opening it directly downloads it
    else:
        headers["Content-Disposition"] = "inline"
    return media, headers


def read_text(path: Path) -> tuple[str, bool]:
    """(text, cut) for the first TEXT_CAP bytes, decoded as UTF-8 (a bad byte becomes a replacement mark)."""
    with path.open("rb") as fh:
        data = fh.read(TEXT_CAP + 1)
    cut = len(data) > TEXT_CAP
    return data[:TEXT_CAP].decode("utf-8", errors="replace").lstrip("﻿"), cut


def read_csv(path: Path) -> tuple[list[list[str]], bool]:
    """(rows, more) for the first CSV_ROWS rows of a CSV or TSV file."""
    with path.open("rb") as fh:
        data = fh.read(CSV_CAP + 1)
    text = data[:CSV_CAP].decode("utf-8", errors="replace").lstrip("﻿")
    rows, more = [], len(data) > CSV_CAP
    try:
        for row in csv.reader(io.StringIO(text), delimiter="\t" if path.suffix.lower() == ".tsv" else ","):
            if len(rows) >= CSV_ROWS:
                more = True
                break
            rows.append(row[:200])
    except csv.Error:
        pass
    return rows, more


def first_lines(path: Path, n: int = 6) -> str:
    """The first n non-empty lines of a text file, for the hover card."""
    text, _ = read_text(path)
    lines = [ln.rstrip() for ln in text[:4000].splitlines() if ln.strip()]
    return "\n".join(x[:160] for x in lines[:n])


# -- validate: UI work in review shows a picture ------------------------------------------------------------------------------------
_IMG = re.compile(r"\.(png|jpe?g|gif|webp|avif|svg)\b", re.I)
UI_TAGS = {"ui", "ux"}


def is_ui_work(t: Ticket, store) -> bool:
    """Tagged ui or ux, or a feature in a project whose profile says `ui: true`."""
    if UI_TAGS & {str(x).lower() for x in t.tags}:
        return True
    if str(t.meta.get("type") or "") == "feature":
        meta, _ = store.project_profile(t.project)
        return bool(meta.get("ui"))
    return False


def review_image_warning(t: Ticket, store) -> str | None:
    """`drew validate`: UI work in review with no image attachment. A ticket with `ui` in its dod is warned by commands.warnings already."""
    if t.status != "review" or "ui" in [str(x) for x in (t.meta.get("dod") or [])] or not is_ui_work(t, store):
        return None
    if _IMG.search(t.section("Attachments") or ""):
        return None
    return f"{t.id}: UI work in review with no image attached; add a screenshot under Attachments (`drew snap`, or paste one in a comment)"
