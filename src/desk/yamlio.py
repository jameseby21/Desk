"""The one place Desk reads and writes YAML.

libyaml's C loader and dumper (CSafeLoader, CSafeDumper) when PyYAML was built with them, the pure-Python safe ones otherwise.
Both give the same data and the same bytes; the C pair is about seven times faster, which is what a full scan of the store pays for.
Nothing else in Desk imports yaml (tests/test_yamlio.py greps for it)."""

from __future__ import annotations

import yaml

YAMLError = yaml.YAMLError

try:
    from yaml import CSafeDumper as _Dumper, CSafeLoader as _Loader

    LOADER = "libyaml (CSafeLoader, CSafeDumper)"
except ImportError:  # PyYAML without libyaml
    from yaml import SafeDumper as _Dumper, SafeLoader as _Loader

    LOADER = "pure Python (SafeLoader, SafeDumper): slower, same results"


def load(text):
    return yaml.load(text, Loader=_Loader)


def dump(data, **kw) -> str:
    return yaml.dump(data, Dumper=_Dumper, **kw)

