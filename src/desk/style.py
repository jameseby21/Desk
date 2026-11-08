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
    name, dog, profanity, autorefresh = "plain", "drew", True, True
    try:
        raw = tomllib.loads(settings_path(home).read_text(encoding="utf-8"))
        if isinstance(raw.get("ui", {}).get("autorefresh"), bool):
            autorefresh = raw["ui"]["autorefresh"]
        data = raw.get("style", {})
        if data.get("name") in STYLES:
            name = data["name"]
        if data.get("dog") in DOGS:
            dog = data["dog"]
        if isinstance(data.get("profanity_filter"), bool):
            profanity = data["profanity_filter"]
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        pass
    return {"name": name, "dog": dog, "profanity": profanity, "autorefresh": autorefresh, "mascot": {"dogs": dog, "cat": "soot"}.get(name)}


def save(home: Path, name: str, dog: str, profanity: bool | None = None, autorefresh: bool | None = None) -> dict:
    """Write the style and [ui]; profanity / autorefresh None keep the current switch (both default on)."""
    if name not in STYLES or dog not in DOGS:
        raise ValueError("unknown style")
    if profanity is None:
        profanity = load(home)["profanity"]
    if autorefresh is None:
        autorefresh = load(home)["autorefresh"]
    p = settings_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f'[style]\nname = "{name}"\ndog = "{dog}"\nprofanity_filter = {str(bool(profanity)).lower()}\n\n[ui]\nautorefresh = {str(bool(autorefresh)).lower()}\n', encoding="utf-8")
    return load(home)
