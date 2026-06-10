"""JSON config for the PySide persona frontend."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


PERSONA_DIR = Path(__file__).resolve().parent
ASSETS_DIR = PERSONA_DIR / "assets"
FONTS_DIR = PERSONA_DIR / "fonts"
ICONS_DIR = PERSONA_DIR / "icons"
CONFIG_PATH = PERSONA_DIR / "persona_config.json"
PERSONAS_DIR = PERSONA_DIR / "personas"
LIBRARY_PATH = PERSONAS_DIR / "personas.json"
DEFAULT_PERSONA_ID = "default"
PERSONA_PACKAGE_VERSION = 1
PERSONA_MANIFEST_NAME = "persona.json"
PERSONA_CONFIG_NAME = "persona_config.json"
PACKAGE_FOLDERS = ("assets",)
PACKAGE_SUFFIXES = {
    "assets": {".png", ".jpg", ".jpeg", ".webp"},
}


DEFAULT_CONFIG: dict[str, Any] = {
    "window": {
        "width": 540,
        "height": 600,
        "always_on_top": True,
        "transparent": True,
    },
    "sprite": {
        "x": 249,
        "y": 219,
        "display_size": 282,
    },
    "subtitle": {
        "x": 3,
        "y": 337,
        "width": 532,
        "height": 168,
        "font_size": 18,
    },
    "input": {
        "x": 45,
        "y": 530,
        "width": 448,
        "height": 50,
    },
    "animations": [
        {
            "name": "idle_1",
            "kind": "idle",
            "file": "idle_1.webp",
            "frame_size": 512,
            "columns": 4,
            "rows": 24,
            "used_frames": 96,
            "fps": 24,
        },
        {
            "name": "idle_2",
            "kind": "idle",
            "file": "idle_2.webp",
            "frame_size": 512,
            "columns": 4,
            "rows": 24,
            "used_frames": 96,
            "fps": 24,
        },
        {
            "name": "fala_3",
            "kind": "talk",
            "file": "fala_3.webp",
            "frame_size": 512,
            "columns": 10,
            "rows": 5,
            "used_frames": 48,
            "fps": 24,
        },
        {
            "name": "fala_4",
            "kind": "talk",
            "file": "fala_4.webp",
            "frame_size": 512,
            "columns": 10,
            "rows": 3,
            "used_frames": 24,
            "fps": 24,
        },
    ],
}


def _has_idle_animation(config: dict[str, Any], assets_dir: Path | None = None) -> bool:
    for anim in config.get("animations") or []:
        if not isinstance(anim, dict):
            continue
        kind = str(anim.get("kind") or "idle").strip().lower()
        file = str(anim.get("file") or "").strip()
        if kind == "idle" and file:
            # An idle animation that points at a sprite which was never uploaded
            # does not count: the persona would render empty once activated.
            if assets_dir is not None and not (assets_dir / file).is_file():
                continue
            return True
    return False


def _deep_merge(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _merged_persona_config(data: dict[str, Any]) -> dict[str, Any]:
    merged = _deep_merge(DEFAULT_CONFIG, data)
    # Themes are global (theme_config.py), never part of a persona: drop any
    # "theme" key left over from old configs or imported packages.
    merged.pop("theme", None)
    return merged


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _ensure_runtime_dirs() -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    FONTS_DIR.mkdir(parents=True, exist_ok=True)
    ICONS_DIR.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _safe_id(value: Any, fallback: str = "persona") -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9._-]+", "_", text)
    text = text.strip("._-")
    return text or fallback


def _persona_entry(persona_id: str, name: str, updated_at: str | None = None) -> dict[str, Any]:
    safe_id = _safe_id(persona_id)
    return {
        "id": safe_id,
        "name": str(name or safe_id).strip() or safe_id,
        "updated_at": updated_at or _utc_stamp(),
    }


def _package_dir(persona_id: str) -> Path:
    return PERSONAS_DIR / _safe_id(persona_id)


def _runtime_config_data() -> dict[str, Any]:
    return _merged_persona_config(_read_json(CONFIG_PATH))


def _copy_dir_contents(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    if source.exists():
        shutil.copytree(source, target, dirs_exist_ok=True)


def _copy_runtime_to_package(persona_id: str, name: str, *, copy_assets: bool) -> dict[str, Any]:
    _ensure_runtime_dirs()
    if not CONFIG_PATH.exists():
        _write_json(CONFIG_PATH, DEFAULT_CONFIG)

    entry = _persona_entry(persona_id, name)
    package_dir = _package_dir(entry["id"])
    package_dir.mkdir(parents=True, exist_ok=True)
    _write_json(package_dir / PERSONA_CONFIG_NAME, _runtime_config_data())

    target = package_dir / "assets"
    if copy_assets or not target.exists():
        _copy_dir_contents(ASSETS_DIR, target)
    for system_folder in ("fonts", "icons"):
        shutil.rmtree(package_dir / system_folder, ignore_errors=True)

    _write_json(
        package_dir / PERSONA_MANIFEST_NAME,
        {
            "package_version": PERSONA_PACKAGE_VERSION,
            "id": entry["id"],
            "name": entry["name"],
            "updated_at": entry["updated_at"],
        },
    )
    return entry


def _copy_package_to_runtime(persona_id: str) -> None:
    package_dir = _package_dir(persona_id)
    config_path = package_dir / PERSONA_CONFIG_NAME
    if not config_path.exists():
        raise ValueError(f"Persona package '{persona_id}' is missing {PERSONA_CONFIG_NAME}.")

    _ensure_runtime_dirs()
    shutil.copyfile(config_path, CONFIG_PATH)
    _copy_dir_contents(package_dir / "assets", ASSETS_DIR)


def _load_manifest() -> dict[str, Any]:
    PERSONAS_DIR.mkdir(parents=True, exist_ok=True)
    data = _read_json(LIBRARY_PATH)
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    raw_entries = data.get("personas") if isinstance(data.get("personas"), list) else []

    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        persona_id = _safe_id(item.get("id") or item.get("name"), "")
        if not persona_id or persona_id in seen:
            continue
        seen.add(persona_id)
        entries.append(
            _persona_entry(
                persona_id,
                str(item.get("name") or persona_id),
                str(item.get("updated_at") or _utc_stamp()),
            )
        )

    if not entries:
        entry = _copy_runtime_to_package(DEFAULT_PERSONA_ID, "Default", copy_assets=True)
        entries = [entry]
        active = entry["id"]
    else:
        active = _safe_id(data.get("active") or entries[0]["id"])
        if active not in {entry["id"] for entry in entries}:
            active = entries[0]["id"]

    # "active" is what the pet renders; "editing" is the persona mirrored into
    # the runtime folder for the config editor. They usually match, but a
    # draft under construction is editing-only until it can be activated.
    editing = _safe_id(data.get("editing") or active)
    if editing not in {entry["id"] for entry in entries}:
        editing = active

    manifest = {"active": active, "editing": editing, "personas": entries}
    if manifest != data:
        _write_json(LIBRARY_PATH, manifest)
    return manifest


def _save_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "active": _safe_id(manifest.get("active") or DEFAULT_PERSONA_ID),
        "editing": _safe_id(manifest.get("editing") or ""),
        "personas": [
            _persona_entry(item.get("id"), item.get("name"), item.get("updated_at"))
            for item in manifest.get("personas", [])
            if isinstance(item, dict)
        ],
    }
    ids = {item["id"] for item in normalized["personas"]}
    if normalized["active"] not in ids and normalized["personas"]:
        normalized["active"] = normalized["personas"][0]["id"]
    if normalized["editing"] not in ids:
        normalized["editing"] = normalized["active"]
    _write_json(LIBRARY_PATH, normalized)
    return normalized


def _manifest_entry(manifest: dict[str, Any], persona_id: str) -> dict[str, Any] | None:
    safe_id = _safe_id(persona_id)
    for entry in manifest.get("personas", []):
        if entry.get("id") == safe_id:
            return entry
    return None


def _touch_manifest_entry(persona_id: str, *, name: str | None = None) -> None:
    manifest = _load_manifest()
    entry = _manifest_entry(manifest, persona_id)
    if not entry:
        return
    if name:
        entry["name"] = name
    entry["updated_at"] = _utc_stamp()
    _save_manifest(manifest)


def sync_editing_persona_from_runtime(*, copy_assets: bool = False) -> dict[str, Any]:
    """Mirror the runtime folder (the editor workspace) into the package of the
    persona currently being edited."""
    manifest = _load_manifest()
    editing = str(manifest.get("editing") or manifest.get("active") or DEFAULT_PERSONA_ID)
    entry = _manifest_entry(manifest, editing) or _persona_entry(editing, editing)
    updated = _copy_runtime_to_package(editing, str(entry.get("name") or editing), copy_assets=copy_assets)
    entry.update(updated)
    _save_manifest(manifest)
    return entry


def get_persona_config() -> dict[str, Any]:
    _ensure_runtime_dirs()
    if not CONFIG_PATH.exists():
        save_persona_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
    data = _read_json(CONFIG_PATH)
    merged = _merged_persona_config(data)
    if merged != data:
        save_persona_config(merged)
    return merged


def save_persona_config(config: dict[str, Any]) -> dict[str, Any]:
    # A persona may be saved as a draft with no idle yet; the idle requirement
    # is only enforced at activation time (see activate_persona).
    _ensure_runtime_dirs()
    merged = _merged_persona_config(config)
    _write_json(CONFIG_PATH, merged)
    sync_editing_persona_from_runtime(copy_assets=False)
    return merged


def list_assets() -> list[dict[str, Any]]:
    _ensure_runtime_dirs()
    items: list[dict[str, Any]] = []
    for asset_type, directory in (
        ("sprite", ASSETS_DIR),
        ("font", FONTS_DIR),
        ("icon", ICONS_DIR),
    ):
        for path in sorted(directory.iterdir()):
            if path.is_file():
                items.append({"name": path.name, "size": path.stat().st_size, "type": asset_type})
    return items


def _asset_dir_for(name: str) -> tuple[Path, str]:
    """Return the runtime directory and asset type for a filename, by extension."""
    suffix = Path(name).suffix.lower()
    if suffix in {".ttf", ".otf"}:
        return FONTS_DIR, "font"
    if suffix == ".svg":
        return ICONS_DIR, "icon"
    return ASSETS_DIR, "sprite"


def _unique_asset_name(directory: Path, filename: str) -> str:
    """Return ``filename`` or a non-colliding ``name_2.ext`` variant for ``directory``."""
    safe = Path(filename).name
    if not (directory / safe).exists():
        return safe
    stem, suffix = Path(safe).stem, Path(safe).suffix
    counter = 2
    while (directory / f"{stem}_{counter}{suffix}").exists():
        counter += 1
    return f"{stem}_{counter}{suffix}"


def _animation_files(config: dict[str, Any]) -> list[str]:
    """All sprite filenames referenced by a config's animations."""
    files: list[str] = []
    for anim in config.get("animations") or []:
        if isinstance(anim, dict):
            name = str(anim.get("file") or "").strip()
            if name:
                files.append(name)
    return files


def _rewrite_animation_file(config_path: Path, old: str, new: str) -> int:
    """Point every animation referencing ``old`` at ``new``; returns how many."""
    config = _read_json(config_path)
    animations = config.get("animations")
    if not isinstance(animations, list):
        return 0
    count = 0
    for anim in animations:
        if isinstance(anim, dict) and str(anim.get("file") or "").strip() == old:
            anim["file"] = new
            count += 1
    if count:
        _write_json(config_path, config)
    return count


def save_uploaded_asset(filename: str, source_path: Path) -> dict[str, Any]:
    safe_name = Path(filename).name
    directory, asset_type = _asset_dir_for(safe_name)
    directory.mkdir(parents=True, exist_ok=True)
    # Never overwrite an existing asset: auto-rename collisions to name_2.ext.
    safe_name = _unique_asset_name(directory, safe_name)
    target = directory / safe_name
    shutil.copyfile(source_path, target)
    try:
        if asset_type == "sprite":
            manifest = _load_manifest()
            editing = str(manifest.get("editing") or manifest.get("active") or DEFAULT_PERSONA_ID)
            package_target = _package_dir(editing) / "assets" / safe_name
            package_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(target, package_target)
            _touch_manifest_entry(editing)
    except OSError:
        pass
    return {"name": safe_name, "size": target.stat().st_size, "type": asset_type}


def delete_persona_asset(filename: str) -> dict[str, Any]:
    """Delete a runtime asset, refusing if an active-persona animation uses it."""
    safe = Path(filename).name
    directory, asset_type = _asset_dir_for(safe)
    target = directory / safe
    if not target.exists():
        raise ValueError(f"Asset '{safe}' was not found.")
    if asset_type == "sprite" and safe in _animation_files(get_persona_config()):
        raise ValueError(f"'{safe}' is in use by an animation. Remove that animation first.")
    target.unlink()
    if asset_type == "sprite":
        manifest = _load_manifest()
        editing = str(manifest.get("editing") or manifest.get("active") or DEFAULT_PERSONA_ID)
        package_copy = _package_dir(editing) / "assets" / safe
        if package_copy.exists():
            try:
                package_copy.unlink()
            except OSError:
                pass
        _touch_manifest_entry(editing)
    return {"ok": True, "deleted": safe, "assets": list_assets()}


def rename_persona_asset(old_name: str, new_name: str) -> dict[str, Any]:
    """Rename a runtime asset and update every animation that references it.

    The asset belongs to the active persona (the runtime copy + its package copy),
    so references in the runtime config and the active package config are rewritten
    in lockstep — the link to the file is never lost."""
    old_safe = Path(old_name).name
    directory, asset_type = _asset_dir_for(old_safe)
    source = directory / old_safe
    if not source.exists():
        raise ValueError(f"Asset '{old_safe}' was not found.")

    new_safe = Path(str(new_name or "")).name.strip()
    if not new_safe:
        raise ValueError("New name is required.")
    # Keep the original extension so the image format/reference stays valid.
    new_safe = Path(new_safe).stem + Path(old_safe).suffix
    if new_safe == old_safe:
        return {"ok": True, "from": old_safe, "to": new_safe, "updated_refs": 0, "assets": list_assets()}
    if (directory / new_safe).exists():
        raise ValueError(f"'{new_safe}' already exists.")

    source.rename(directory / new_safe)
    updated = 0
    if asset_type == "sprite":
        manifest = _load_manifest()
        editing = str(manifest.get("editing") or manifest.get("active") or DEFAULT_PERSONA_ID)
        updated += _rewrite_animation_file(CONFIG_PATH, old_safe, new_safe)
        package_dir = _package_dir(editing)
        updated += _rewrite_animation_file(package_dir / PERSONA_CONFIG_NAME, old_safe, new_safe)
        package_copy = package_dir / "assets" / old_safe
        if package_copy.exists():
            try:
                package_copy.rename(package_dir / "assets" / new_safe)
            except OSError:
                pass
        _touch_manifest_entry(editing)
    return {"ok": True, "from": old_safe, "to": new_safe, "updated_refs": updated, "assets": list_assets()}


def list_personas() -> dict[str, Any]:
    manifest = _load_manifest()
    active = str(manifest.get("active") or DEFAULT_PERSONA_ID)
    editing = str(manifest.get("editing") or active)
    personas: list[dict[str, Any]] = []
    for entry in manifest.get("personas", []):
        persona_id = str(entry.get("id") or "")
        package_dir = _package_dir(persona_id)
        assets_dir = package_dir / "assets"
        asset_count = len([path for path in assets_dir.iterdir() if path.is_file()]) if assets_dir.exists() else 0
        package_config = _merged_persona_config(_read_json(package_dir / PERSONA_CONFIG_NAME))
        personas.append(
            {
                "id": persona_id,
                "name": str(entry.get("name") or persona_id),
                "updated_at": entry.get("updated_at") or "",
                "asset_count": asset_count,
                "active": persona_id == active,
                "editing": persona_id == editing,
                "can_delete": persona_id != DEFAULT_PERSONA_ID,
                "has_idle": _has_idle_animation(package_config, assets_dir),
            }
        )
    return {"active": active, "editing": editing, "personas": personas}


def activate_persona(persona_id: str) -> dict[str, Any]:
    manifest = _load_manifest()
    safe_id = _safe_id(persona_id, "")
    if not safe_id or not _manifest_entry(manifest, safe_id):
        raise ValueError(f"Persona '{persona_id}' was not found.")

    target_config = _merged_persona_config(_read_json(_package_dir(safe_id) / PERSONA_CONFIG_NAME))
    if not _has_idle_animation(target_config, _package_dir(safe_id) / "assets"):
        raise ValueError("This persona has no idle animation yet. Add one before activating.")

    sync_editing_persona_from_runtime(copy_assets=True)
    manifest = _load_manifest()
    _copy_package_to_runtime(safe_id)
    manifest["active"] = safe_id
    manifest["editing"] = safe_id
    _save_manifest(manifest)
    return list_personas()


def edit_persona(persona_id: str) -> dict[str, Any]:
    """Switch the editor workspace (runtime folder) to another persona without
    activating it on the pet. Lets drafts be built or resumed at any time."""
    manifest = _load_manifest()
    safe_id = _safe_id(persona_id, "")
    if not safe_id or not _manifest_entry(manifest, safe_id):
        raise ValueError(f"Persona '{persona_id}' was not found.")
    if str(manifest.get("editing") or "") == safe_id:
        return list_personas()

    sync_editing_persona_from_runtime(copy_assets=True)
    manifest = _load_manifest()
    _copy_package_to_runtime(safe_id)
    manifest["editing"] = safe_id
    _save_manifest(manifest)
    return list_personas()


def _blank_config() -> dict[str, Any]:
    # Full default layout but no animations — a brand-new persona starts empty
    # and is filled in by the user before it can be activated.
    return _deep_merge(DEFAULT_CONFIG, {"animations": []})


def create_persona(name: str, source_id: str | None = None) -> dict[str, Any]:
    # `source_id` is accepted for API compatibility but ignored: a new persona
    # is always created blank (no animations, no sprites), never cloned.
    display_name = str(name or "").strip()
    if not display_name:
        raise ValueError("Persona name is required.")

    # Preserve any unsaved edits to the persona currently in the editor first.
    sync_editing_persona_from_runtime(copy_assets=True)
    manifest = _load_manifest()

    persona_id = _unique_persona_id(display_name, manifest)
    entry = _persona_entry(persona_id, display_name)
    package_dir = _package_dir(entry["id"])
    if package_dir.exists():
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True, exist_ok=True)
    _write_json(package_dir / PERSONA_CONFIG_NAME, _blank_config())
    (package_dir / "assets").mkdir(parents=True, exist_ok=True)
    _write_json(
        package_dir / PERSONA_MANIFEST_NAME,
        {
            "package_version": PERSONA_PACKAGE_VERSION,
            "id": entry["id"],
            "name": entry["name"],
            "updated_at": entry["updated_at"],
        },
    )

    manifest["personas"].append(entry)
    # The new blank persona becomes the EDITING target only: the pet keeps the
    # current active persona until this one gains an idle and is activated.
    manifest["editing"] = entry["id"]
    _save_manifest(manifest)
    _copy_package_to_runtime(entry["id"])
    return list_personas()


def rename_persona(persona_id: str, name: str) -> dict[str, Any]:
    display_name = str(name or "").strip()
    if not display_name:
        raise ValueError("Persona name is required.")

    manifest = _load_manifest()
    safe_id = _safe_id(persona_id, "")
    entry = _manifest_entry(manifest, safe_id)
    if not safe_id or not entry:
        raise ValueError(f"Persona '{persona_id}' was not found.")

    entry["name"] = display_name
    entry["updated_at"] = _utc_stamp()
    package_manifest = _read_json(_package_dir(safe_id) / PERSONA_MANIFEST_NAME)
    package_manifest.update(
        {
            "package_version": PERSONA_PACKAGE_VERSION,
            "id": safe_id,
            "name": display_name,
            "updated_at": entry["updated_at"],
        }
    )
    _write_json(_package_dir(safe_id) / PERSONA_MANIFEST_NAME, package_manifest)
    _save_manifest(manifest)
    return list_personas()


def delete_persona(persona_id: str) -> dict[str, Any]:
    manifest = _load_manifest()
    safe_id = _safe_id(persona_id, "")
    if not safe_id or not _manifest_entry(manifest, safe_id):
        raise ValueError(f"Persona '{persona_id}' was not found.")
    if safe_id == DEFAULT_PERSONA_ID:
        raise ValueError("Default persona cannot be deleted.")

    if manifest.get("active") == safe_id:
        default_entry = _manifest_entry(manifest, DEFAULT_PERSONA_ID)
        if not default_entry:
            raise ValueError("Default persona package is missing.")
        manifest["active"] = DEFAULT_PERSONA_ID
    if manifest.get("editing") == safe_id:
        manifest["editing"] = str(manifest.get("active") or DEFAULT_PERSONA_ID)
        _copy_package_to_runtime(manifest["editing"])

    manifest["personas"] = [
        entry for entry in manifest.get("personas", [])
        if entry.get("id") != safe_id
    ]
    shutil.rmtree(_package_dir(safe_id), ignore_errors=True)
    _save_manifest(manifest)
    return list_personas()


def _zip_member_path(filename: str) -> PurePosixPath | None:
    if not filename or filename.endswith("/"):
        return None
    path = PurePosixPath(filename)
    if not path.parts or path.is_absolute() or path.parts[0] == "__MACOSX":
        return None
    if any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path


def _relative_to_package(path: PurePosixPath, root: tuple[str, ...]) -> PurePosixPath | None:
    if root and path.parts[: len(root)] != root:
        return None
    parts = path.parts[len(root) :]
    if not parts:
        return None
    return PurePosixPath(*parts)


def _unique_persona_id(base_id: str, manifest: dict[str, Any]) -> str:
    existing = {str(entry.get("id") or "") for entry in manifest.get("personas", [])}
    persona_id = _safe_id(base_id)
    if persona_id not in existing:
        return persona_id
    counter = 2
    while f"{persona_id}_{counter}" in existing:
        counter += 1
    return f"{persona_id}_{counter}"


def import_persona_package(zip_path: Path) -> dict[str, Any]:
    if not zipfile.is_zipfile(zip_path):
        raise ValueError("Invalid persona ZIP.")

    manifest = _load_manifest()
    PERSONAS_DIR.mkdir(parents=True, exist_ok=True)
    root_config_path: PurePosixPath | None = None

    with zipfile.ZipFile(zip_path) as package:
        members: dict[str, zipfile.ZipInfo] = {}
        for info in package.infolist():
            path = _zip_member_path(info.filename)
            if path:
                members[path.as_posix()] = info
                if path.name == PERSONA_CONFIG_NAME:
                    if root_config_path is None or len(path.parts) < len(root_config_path.parts):
                        root_config_path = path

        if root_config_path is None:
            raise ValueError(f"Package must include {PERSONA_CONFIG_NAME}.")

        root = root_config_path.parts[:-1]
        try:
            loaded_config = json.loads(package.read(members[root_config_path.as_posix()]).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"{PERSONA_CONFIG_NAME} must contain valid JSON.") from exc
        if not isinstance(loaded_config, dict):
            raise ValueError(f"{PERSONA_CONFIG_NAME} must contain a JSON object.")
        config = _merged_persona_config(loaded_config)

        package_manifest: dict[str, Any] = {}
        manifest_rel = PurePosixPath(*(root + (PERSONA_MANIFEST_NAME,))) if root else PurePosixPath(PERSONA_MANIFEST_NAME)
        manifest_info = members.get(manifest_rel.as_posix())
        if manifest_info:
            try:
                loaded = json.loads(package.read(manifest_info).decode("utf-8"))
                if isinstance(loaded, dict):
                    package_manifest = loaded
            except (json.JSONDecodeError, UnicodeDecodeError):
                package_manifest = {}

        display_name = str(package_manifest.get("name") or zip_path.stem or "Persona").strip() or "Persona"
        persona_id = _unique_persona_id(str(package_manifest.get("id") or display_name), manifest)
        package_dir = _package_dir(persona_id)
        if package_dir.exists():
            shutil.rmtree(package_dir)
        package_dir.mkdir(parents=True, exist_ok=True)

        try:
            _write_json(package_dir / PERSONA_CONFIG_NAME, config)
            for folder in PACKAGE_FOLDERS:
                (package_dir / folder).mkdir(parents=True, exist_ok=True)

            for path_text, info in members.items():
                path = PurePosixPath(path_text)
                rel = _relative_to_package(path, root)
                if rel is None or rel.name in {PERSONA_CONFIG_NAME, PERSONA_MANIFEST_NAME}:
                    continue
                folder = rel.parts[0]
                if folder not in PACKAGE_FOLDERS:
                    continue
                if Path(rel.name).suffix.lower() not in PACKAGE_SUFFIXES[folder]:
                    continue
                target = package_dir.joinpath(*rel.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(info) as source, target.open("wb") as dest:
                    shutil.copyfileobj(source, dest)

            entry = _persona_entry(persona_id, display_name)
            _write_json(
                package_dir / PERSONA_MANIFEST_NAME,
                {
                    "package_version": PERSONA_PACKAGE_VERSION,
                    "id": entry["id"],
                    "name": entry["name"],
                    "updated_at": entry["updated_at"],
                },
            )
        except Exception:
            shutil.rmtree(package_dir, ignore_errors=True)
            raise

    manifest["personas"].append(entry)
    _save_manifest(manifest)
    return list_personas()


def export_persona_package(persona_id: str | None = None) -> tuple[Path, str]:
    manifest = _load_manifest()
    active = str(manifest.get("active") or DEFAULT_PERSONA_ID)
    editing = str(manifest.get("editing") or active)
    selected_id = _safe_id(persona_id or active)
    entry = _manifest_entry(manifest, selected_id)
    if not entry:
        raise ValueError(f"Persona '{selected_id}' was not found.")
    if selected_id == editing:
        # The runtime folder holds this persona's latest edits; flush them
        # into the package before zipping.
        sync_editing_persona_from_runtime(copy_assets=True)
        manifest = _load_manifest()
        entry = _manifest_entry(manifest, selected_id) or entry

    package_dir = _package_dir(selected_id)
    if not (package_dir / PERSONA_CONFIG_NAME).exists():
        raise ValueError(f"Persona '{selected_id}' has no package config.")

    handle = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    zip_path = Path(handle.name)
    handle.close()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr(
            PERSONA_MANIFEST_NAME,
            json.dumps(
                {
                    "package_version": PERSONA_PACKAGE_VERSION,
                    "id": selected_id,
                    "name": str(entry.get("name") or selected_id),
                    "updated_at": entry.get("updated_at") or "",
                    "exported_at": _utc_stamp(),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        package.write(package_dir / PERSONA_CONFIG_NAME, PERSONA_CONFIG_NAME)
        for folder in PACKAGE_FOLDERS:
            source_dir = package_dir / folder
            if not source_dir.exists():
                continue
            for path in sorted(source_dir.rglob("*")):
                if path.is_file():
                    rel = path.relative_to(source_dir).as_posix()
                    package.write(path, f"{folder}/{rel}")

    filename = f"{_safe_id(entry.get('name') or selected_id)}.zip"
    return zip_path, filename
