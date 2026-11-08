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
    name, dog, profanity = "plain", "drew", True
    try:
        data = tomllib.loads(settings_path(home).read_text(encoding="utf-8")).get("style", {})
        if data.get("name") in STYLES:
            name = data["name"]
        if data.get("dog") in DOGS:
            dog = data["dog"]
        if isinstance(data.get("profanity_filter"), bool):
            profanity = data["profanity_filter"]
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        pass
    return {"name": name, "dog": dog, "profanity": profanity, "mascot": {"dogs": dog, "cat": "soot"}.get(name)}


def save(home: Path, name: str, dog: str, profanity: bool | None = None) -> dict:
    """Write the style; profanity=None keeps the current profanity_filter switch (default on)."""
    if name not in STYLES or dog not in DOGS:
        raise ValueError("unknown style")
    if profanity is None:
        profanity = load(home)["profanity"]
    p = settings_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f'[style]\nname = "{name}"\ndog = "{dog}"\nprofanity_filter = {str(bool(profanity)).lower()}\n', encoding="utf-8")
    return load(home)
