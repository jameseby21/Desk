"""Plain text out of Word, Excel and PowerPoint files (DESK-0291), with the standard library only: zipfile and xml.etree.

An Office file is a zip of XML parts. A hostile file can be a zip bomb (tiny on disk, huge unpacked) or carry XML entity tricks, so:
the file, the number of parts, each part and the total unpacked size are capped (the real bytes read are counted, not the sizes the
zip claims), a part that packs more than 200 to 1 is refused, and any part with a DOCTYPE or ENTITY is refused before it is parsed."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

MAX_FILE = 25 * 1024 * 1024
MAX_ENTRIES = 3000
MAX_PART = 16 * 1024 * 1024
MAX_TOTAL = 48 * 1024 * 1024
MAX_RATIO = 200
MAX_ROWS = 500

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_DTD = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.I)


class OfficeError(Exception):
    """The file cannot be shown as text; args[0] is the plain sentence for the person."""


class _Zip:
    def __init__(self, path: Path):
        if path.stat().st_size > MAX_FILE:
            raise OfficeError(f"That file is larger than {MAX_FILE // (1024 * 1024)} MB, so Desk does not unpack it.")
        try:
            self.z = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError):
            raise OfficeError("That file is not a readable Office file.")
        infos = self.z.infolist()
        if len(infos) > MAX_ENTRIES:
            raise OfficeError("That file holds too many parts to unpack safely.")
        self.names = {i.filename: i for i in infos}
        self.total = 0

    def has(self, name: str) -> bool:
        return name in self.names

    def xml(self, name: str):
        info = self.names.get(name)
        if info is None:
            return None
        if info.file_size > MAX_PART or (info.compress_size and info.file_size > 1024 * 1024 and info.file_size / info.compress_size > MAX_RATIO):
            raise OfficeError("That file unpacks to far more than its size, so Desk does not open it.")
        with self.z.open(info) as fh:
            data = fh.read(MAX_PART + 1)
        self.total += len(data)
        if len(data) > MAX_PART or self.total > MAX_TOTAL:
            raise OfficeError("That file unpacks to far more than its size, so Desk does not open it.")
        if _DTD.search(data):
            raise OfficeError("That file carries a document type declaration, which Desk refuses for safety.")
        try:
            return ET.fromstring(data)
        except ET.ParseError:
            raise OfficeError("Part of that file is damaged, so Desk cannot read it.")

    def close(self):
        self.z.close()


def _num(name: str) -> int:
    m = re.search(r"(\d+)\.xml$", name)
    return int(m.group(1)) if m else 0


def _para_text(p, t_tag: str, extra: dict) -> str:
    out = []
    for el in p.iter():
        if el.tag == t_tag:
            out.append(el.text or "")
        elif el.tag in extra:
            out.append(extra[el.tag])
    return "".join(out)


def docx(z: _Zip) -> dict:
    root = z.xml("word/document.xml")
    if root is None:
        raise OfficeError("That Word file has no document part.")
    paras = [_para_text(p, f"{W}t", {f"{W}tab": "\t", f"{W}br": "\n"}) for p in root.iter(f"{W}p")]
    return {"kind": "paragraphs", "paragraphs": [p for p in paras if p.strip()]}


def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]*", ref or "").group(0):
        n = n * 26 + ord(ch) - 64
    return max(n - 1, 0)


def xlsx(z: _Zip) -> dict:
    shared: list[str] = []
    ss = z.xml("xl/sharedStrings.xml")
    if ss is not None:
        shared = ["".join(t.text or "" for t in si.iter(f"{S}t")) for si in ss.iter(f"{S}si")]
    names: dict[str, str] = {}
    wb, rels = z.xml("xl/workbook.xml"), z.xml("xl/_rels/workbook.xml.rels")
    if wb is not None and rels is not None:
        target = {r.get("Id"): r.get("Target", "") for r in rels}
        for sh in wb.iter(f"{S}sheet"):
            t = target.get(sh.get(f"{R}id"), "")
            if t:
                names["xl/" + t.lstrip("/").removeprefix("xl/")] = sh.get("name", "")
    sheets = []
    for name in sorted((n for n in z.names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)), key=_num):
        root = z.xml(name)
        rows, more = [], False
        for row in root.iter(f"{S}row"):
            if len(rows) >= MAX_ROWS:
                more = True
                break
            cells: list[str] = []
            for c in row.iter(f"{S}c"):
                kind, v = c.get("t"), c.find(f"{S}v")
                if kind == "s" and v is not None and (v.text or "").isdigit():
                    i = int(v.text)
                    val = shared[i] if i < len(shared) else ""
                elif kind == "inlineStr":
                    val = "".join(t.text or "" for t in c.iter(f"{S}t"))
                else:
                    val = v.text if v is not None and v.text else ""
                col = _col(c.get("r", ""))
                if col >= 200:
                    continue
                cells += [""] * (col - len(cells)) if col > len(cells) else []
                cells.append(val)
            rows.append(cells)
        sheets.append({"name": names.get(name) or f"Sheet {_num(name)}", "rows": rows, "more": more})
    return {"kind": "sheets", "sheets": sheets}


def pptx(z: _Zip) -> dict:
    slides = []
    for name in sorted((n for n in z.names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)), key=_num):
        root = z.xml(name)
        lines = [_para_text(p, f"{A}t", {f"{A}br": "\n"}) for p in root.iter(f"{A}p")]
        slides.append({"n": _num(name), "lines": [x for x in lines if x.strip()]})
    return {"kind": "slides", "slides": slides}


def extract(path: Path) -> dict:
    """{'kind': 'paragraphs'|'sheets'|'slides', ...} for a .docx, .xlsx or .pptx file; raises OfficeError with a plain sentence."""
    kind = path.suffix.lower()
    fn = {".docx": docx, ".xlsx": xlsx, ".pptx": pptx}.get(kind)
    if fn is None:
        raise OfficeError("Desk reads Word (.docx), Excel (.xlsx) and PowerPoint (.pptx) files.")
    z = _Zip(path)
    try:
        return fn(z)
    finally:
        z.close()
