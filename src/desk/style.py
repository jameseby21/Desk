"""Per-person UI style: plain (default), dogs (Drew or Fuzz) or cat (Soot). Stored in `.desk/settings.toml`."""

from __future__ import annotations

import tomllib
from pathlib import Path

STYLES = ("plain", "dogs", "cat")
DOGS = ("drew", "fuzz")


def settings_path(home: Path) -> Path:
    return Path(home) / ".desk" / "settings.toml"


def load(home: Path) -> dict:
    """{'name': plain|dogs|cat, 'dog': drew|fuzz, 'mascot': drew|fuzz|soot|None}. Bad or missing file means plain."""
    name, dog = "plain", "drew"
    try:
        data = tomllib.loads(settings_path(home).read_text(encoding="utf-8")).get("style", {})
        if data.get("name") in STYLES:
            name = data["name"]
        if data.get("dog") in DOGS:
            dog = data["dog"]
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        pass
    return {"name": name, "dog": dog, "mascot": {"dogs": dog, "cat": "soot"}.get(name)}


def save(home: Path, name: str, dog: str) -> dict:
    if name not in STYLES or dog not in DOGS:
        raise ValueError("unknown style")
    p = settings_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f'[style]\nname = "{name}"\ndog = "{dog}"\n', encoding="utf-8")
    return load(home)
