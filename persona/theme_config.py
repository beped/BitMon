"""Global overlay theme config, independent from personas.

The theme styles the overlay chat (speech bubble, text input, mic button) and
applies to every persona. It lives in its own JSON file so persona packages
never carry theme data: exporting/importing a persona does not touch the theme.

Besides the active theme (theme_config.json) there is a user library of saved
themes (theme_library.json) so users can keep several color schemes and switch.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


PERSONA_DIR = Path(__file__).resolve().parent
THEME_CONFIG_PATH = PERSONA_DIR / "theme_config.json"
THEME_LIBRARY_PATH = PERSONA_DIR / "theme_library.json"

# Colors are hex; opacities/strengths are 0-100 percentages. The defaults
# reproduce the original hardcoded look, so a missing file renders unchanged.
DEFAULT_THEME: dict[str, Any] = {
    "preset": "classic",
    "font_file": "",
    "subtitle": {
        "font_size": 18,
        "text_color": "#ffffff",
        "bg_color": "#000000",
        "bg_color2": "#000000",
        "bg_gradient": False,
        "gradient_direction": "vertical",
        "bg_opacity": 62,
        "border_radius": 18,
        "border_color": "#000000",
        "border_width": 0,
        "name_tag": False,
        "name_color": "#fbbf24",
        "highlight_color": "#ff5757",
    },
    "input": {
        "text_color": "#ffffff",
        "bg_color": "#2e2e2e",
        "bg_color2": "#2e2e2e",
        "bg_gradient": False,
        "gradient_direction": "vertical",
        "bg_opacity": 45,
        "border_radius": 14,
        "border_color": "#3d4f69",
        "border_width": 1,
        "focus_border_color": "#7399ff",
        "mic_color": "#2563eb",
    },
}


def merge_theme(theme: Any) -> dict[str, Any]:
    merged = {key: dict(value) if isinstance(value, dict) else value for key, value in DEFAULT_THEME.items()}
    if isinstance(theme, dict):
        for key, value in theme.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key].update(value)
            else:
                merged[key] = value
    return merged


def get_theme_config() -> dict[str, Any]:
    try:
        data = json.loads(THEME_CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        data = {}
    return merge_theme(data)


def save_theme_config(theme: dict[str, Any]) -> dict[str, Any]:
    merged = merge_theme(theme)
    THEME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    THEME_CONFIG_PATH.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return merged


def _safe_theme_id(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9._-]+", "_", text).strip("._-")
    return text or fallback


def list_theme_library() -> dict[str, Any]:
    try:
        data = json.loads(THEME_LIBRARY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        data = {}
    themes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in data.get("themes", []) if isinstance(data, dict) else []:
        if not isinstance(item, dict):
            continue
        theme_id = _safe_theme_id(item.get("id") or item.get("name"))
        if not theme_id or theme_id in seen:
            continue
        seen.add(theme_id)
        themes.append(
            {
                "id": theme_id,
                "name": str(item.get("name") or theme_id).strip() or theme_id,
                "theme": merge_theme(item.get("theme")),
            }
        )
    return {"themes": themes}


def _write_theme_library(library: dict[str, Any]) -> dict[str, Any]:
    THEME_LIBRARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    THEME_LIBRARY_PATH.write_text(
        json.dumps(library, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return library


def save_theme_to_library(name: str, theme: dict[str, Any]) -> dict[str, Any]:
    display_name = str(name or "").strip()
    if not display_name:
        raise ValueError("Theme name is required.")
    theme_id = _safe_theme_id(display_name, "theme")
    library = list_theme_library()
    entry = {"id": theme_id, "name": display_name, "theme": merge_theme(theme)}
    themes = [item for item in library["themes"] if item["id"] != theme_id]
    themes.append(entry)
    return _write_theme_library({"themes": themes})


def delete_theme_from_library(theme_id: str) -> dict[str, Any]:
    safe_id = _safe_theme_id(theme_id)
    library = list_theme_library()
    themes = [item for item in library["themes"] if item["id"] != safe_id]
    if len(themes) == len(library["themes"]):
        raise ValueError(f"Theme '{theme_id}' was not found.")
    return _write_theme_library({"themes": themes})
