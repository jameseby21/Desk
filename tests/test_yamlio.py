"""One YAML helper: nothing else imports yaml, and libyaml and pure Python agree byte for byte."""

import re
from pathlib import Path

import yaml

from desk import bench, version, yamlio
from desk.ticket import Ticket

SRC = Path(__file__).resolve().parent.parent / "src" / "desk"

FIXTURES = [
    "id: X-0001\ntitle: \"Quoted: a colon, and 'quotes'\"\nproject: x\ntags: [a, b]\ndepends_on: []\ncreated: 2026-11-08\nupdated: 2026-11-08\n",
    "id: X-0002\ntitle: Café résumé \u2014 dash\nproject: x\ndod: [core, code]\nconfidence: 0.8\neffort_hours: [1, 3]\nblocked_on: \"\"\n",
    "id: X-0003\ntitle: 'It''s 12:30'\nproject: x\nsource: \"intake/2026-11-08-lily/SLICES.md\"\ntags: [a]\nassigned_at: 2026-11-08T09:30:00\n",
]


def test_nothing_else_imports_yaml():
    offenders = []
    for f in SRC.rglob("*.py"):
        if f.name == "yamlio.py":
            continue
        text = f.read_text(encoding="utf-8")
        if re.search(r"^\s*(import yaml|from yaml\b)", text, re.M) or re.search(r"\byaml\.(safe_)?(load|dump)\b", text):
            offenders.append(f.name)
    assert offenders == []


def test_loader_is_named():
    assert yamlio.LOADER
    lines, _ = version.doctor_report(None, pids=[])
    assert any(l.startswith("YAML loader:") and yamlio.LOADER in l for l in lines)


def test_c_and_pure_loaders_agree_and_dump_the_same_bytes():
    kw = dict(sort_keys=False, allow_unicode=True, default_flow_style=None, width=1000)
    for text in FIXTURES:
        data = yamlio.load(text)
        assert data == yaml.load(text, Loader=yaml.SafeLoader)
        assert yamlio.dump(data, **kw) == yaml.dump(data, Dumper=yaml.SafeDumper, **kw)


def test_ticket_files_round_trip_byte_identical(tmp_path):
    s = bench.build_store(tmp_path / "h", 30, projects=5)
    for p in s.ticket_paths():
        text = p.read_text(encoding="utf-8")
        assert Ticket.parse(text, p).render() == text, p.name
