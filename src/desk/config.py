"""Locate the ticket store (DESK_HOME) and read desk.toml."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

CONFIG_NAME = "desk.toml"

DEFAULTS: dict = {
    "desk": {"owner": "james"},
    "approval": {"delegated_types": ["task"], "delegated_max_size": "s"},
    "git": {"sync": False, "debounce_seconds": 30},
    "stale": {"days": 21},
    "life": {"combos": False},
    "attachments": {"max_mb": 8},
}

DEFAULT_TOML = """[desk]
owner = "james"            # the human who approves

[approval]
delegated_types = ["task"]  # main may approve these ...
delegated_max_size = "s"     # ... up to this size

[git]
sync = false               # when true: pull at the start of a command; commit and push after the store has been idle for debounce_seconds, or on `drew sync`
debounce_seconds = 30      # idle time before pending changes are committed

[stale]
days = 21

[life]
combos = false             # when true: the Today page celebrates two tagged tickets done within 30 minutes

[attachments]
max_mb = 8                 # largest image the UI accepts when you paste or drop one into a comment
"""


class HomeNotFound(Exception):
    pass


def find_home(explicit: str | os.PathLike | None = None, cwd: Path | None = None) -> Path:
    """--home flag, then DESK_HOME, then walk up from cwd to the nearest desk.toml."""
    if explicit:
        p = Path(explicit).expanduser().resolve()
        if not (p / CONFIG_NAME).exists():
            raise HomeNotFound(f"No {CONFIG_NAME} in {p}")
        return p
    env = os.environ.get("DESK_HOME")
    if env:
        p = Path(env).expanduser().resolve()
        if not (p / CONFIG_NAME).exists():
            raise HomeNotFound(f"DESK_HOME={env} has no {CONFIG_NAME}")
        return p
    here = (cwd or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / CONFIG_NAME).exists():
            return candidate
    raise HomeNotFound(
        f"No {CONFIG_NAME} found. Pass --home, set DESK_HOME, or run `desk init` in the store directory."
    )


def _merge(base: dict, over: dict) -> dict:
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in base.items()}
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def load_config(home: Path) -> dict:
    path = home / CONFIG_NAME
    data = {}
    if path.exists():
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    return _merge(DEFAULTS, data)
