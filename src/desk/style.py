"""Per-person UI style: plain (default), dogs (Drew or Fuzz) or cat (Soot). Stored in `.desk/settings.toml`."""

from __future__ import annotations

import tomllib
from pathlib import Path

from . import nav

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


def save(home: Path, name: str, dog: str, profanity: bool | None = None, autorefresh: bool | None = None, _nav: tuple[str, str] | None = None) -> dict:
    """Write the style and [ui]; profanity / autorefresh None keep the current switch (both default on)."""
    if name not in STYLES or dog not in DOGS:
        raise ValueError("unknown style")
    if profanity is None:
        profanity = load(home)["profanity"]
    if autorefresh is None:
        autorefresh = load(home)["autorefresh"]
    scope, order = _nav or load_nav(home)  # the navigation choice survives a style save
    p = settings_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    nl = chr(10)
    nav_text = "" if (scope, order) == (nav.DEFAULT_SCOPE, nav.DEFAULT_ORDER) else nl + "[nav]" + nl + f'scope = "{scope}"' + nl + f'order = "{order}"' + nl
    p.write_text(f'[style]\nname = "{name}"\ndog = "{dog}"\nprofanity_filter = {str(bool(profanity)).lower()}\n\n[ui]\nautorefresh = {str(bool(autorefresh)).lower()}\n' + nav_text, encoding="utf-8")
    return load(home)


# -- ticket navigation: which walk Previous and Next follow (DESK-0148), kept per person beside the style --

def load_nav(home: Path) -> tuple[str, str]:
    """(scope, order) chosen for Previous and Next; the project by date when nothing valid is saved."""
    scope, order = nav.DEFAULT_SCOPE, nav.DEFAULT_ORDER
    try:
        data = tomllib.loads(settings_path(home).read_text(encoding="utf-8")).get("nav", {})
        if data.get("scope") in nav.SCOPES:
            scope = data["scope"]
        if data.get("order") in nav.ORDERS:
            order = data["order"]
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        pass
    return scope, order


def save_nav(home: Path, scope: str, order: str) -> tuple[str, str]:
    """Keep the choice in `.desk/settings.toml` under [nav], next to the style, which is rewritten unchanged."""
    if scope not in nav.SCOPES or order not in nav.ORDERS:
        raise ValueError("unknown scope or order")
    cur = load(home)
    save(home, cur["name"], cur["dog"], cur["profanity"], cur["autorefresh"], _nav=(scope, order))
    return load_nav(home)
