"""Convert a persona's sprite sheets to WebP to shrink disk size and speed loading.

Sprite sheets are stored as large RGBA PNGs (a 512px/frame idle sheet is ~16MB
on disk and ~100MB once decoded). Re-encoding them as WebP keeps the exact same
resolution and frame layout while cutting the file dramatically:

    WebP q90       ~85% smaller on disk, visually lossless for smooth sprite art
    WebP lossless  ~25-30% smaller, pixel-for-pixel identical

Resolution (and therefore decoded RAM) is intentionally left untouched — the
per-frame display size is controlled by ``frame_size`` in the persona config.

Originals are backed up to ``.sprite_backup/`` during the conversion (so a
mid-run failure stays recoverable) and removed once every sheet converts
successfully — keeping them would waste the disk space the conversion just
saved. The animation ``file`` references in each config are rewritten to the
.webp name.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from persona import persona_config as pc


BACKUP_DIR = pc.PERSONA_DIR / ".sprite_backup"
CONVERTIBLE_SUFFIXES = {".png", ".jpg", ".jpeg"}


def _convert_one(src: Path, dst: Path, *, lossless: bool, quality: int) -> None:
    # Imported lazily so the backend still boots if Pillow is not installed;
    # only this optimization feature requires it.
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise ValueError("Pillow is required for sprite optimization (pip install Pillow).") from exc
    with Image.open(src) as image:
        image = image.convert("RGBA")
        if lossless:
            image.save(dst, format="WEBP", lossless=True, method=6)
        else:
            image.save(dst, format="WEBP", quality=quality, method=6)


def _process(
    label: str,
    assets_dir: Path,
    config_path: Path,
    *,
    lossless: bool,
    quality: int,
) -> tuple[int, int, list[dict[str, Any]]]:
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0, 0, []
    animations = config.get("animations")
    if not isinstance(animations, list):
        return 0, 0, []

    rename: dict[str, str] = {}
    files: list[dict[str, Any]] = []
    before = after = 0
    for anim in animations:
        if not isinstance(anim, dict):
            continue
        name = str(anim.get("file") or "").strip()
        if not name or Path(name).suffix.lower() not in CONVERTIBLE_SUFFIXES:
            continue
        source = assets_dir / Path(name).name
        if not source.exists():
            continue
        if name not in rename:
            new_name = f"{Path(name).stem}.webp"
            rename[name] = new_name
            target = assets_dir / new_name
            source_size = source.stat().st_size
            _convert_one(source, target, lossless=lossless, quality=quality)
            backup = BACKUP_DIR / label.replace(":", "_") / Path(name).name
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(backup))
            target_size = target.stat().st_size if target.exists() else 0
            before += source_size
            after += target_size
            files.append({"from": name, "to": new_name, "before": source_size, "after": target_size})
        anim["file"] = rename[name]

    if rename:
        config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return before, after, files


def optimize_persona_sprites(persona_id: str, *, lossless: bool = False, quality: int = 90) -> dict[str, Any]:
    """Convert one persona's sprite sheets to WebP, updating its config(s).

    Operates on the persona's package; if it is the active persona, the live
    runtime assets/config are converted too so the change is consistent.
    """
    manifest = pc._load_manifest()
    safe_id = pc._safe_id(persona_id, "")
    if not safe_id or not pc._manifest_entry(manifest, safe_id):
        raise ValueError(f"Persona '{persona_id}' was not found.")

    active = str(manifest.get("active") or pc.DEFAULT_PERSONA_ID)
    package_dir = pc._package_dir(safe_id)
    targets: list[tuple[str, Path, Path]] = [
        (f"persona:{safe_id}", package_dir / "assets", package_dir / pc.PERSONA_CONFIG_NAME)
    ]
    if safe_id == active:
        targets.append(("runtime", pc.ASSETS_DIR, pc.CONFIG_PATH))

    total_before = total_after = 0
    files: list[dict[str, Any]] = []
    for label, assets_dir, config_path in targets:
        before, after, converted = _process(
            label, assets_dir, config_path, lossless=lossless, quality=quality
        )
        total_before += before
        total_after += after
        if converted and not files:
            files = converted

    if files:
        pc._touch_manifest_entry(safe_id)

    # Every sheet converted without error, so the .webp files are all in place.
    # Drop this run's backed-up originals to reclaim the saved disk space; any
    # other persona's leftover backups are left untouched.
    for label, _assets_dir, _config_path in targets:
        shutil.rmtree(BACKUP_DIR / label.replace(":", "_"), ignore_errors=True)
    try:
        BACKUP_DIR.rmdir()  # remove the parent only if it is now empty
    except OSError:
        pass

    return {
        "ok": True,
        "persona_id": safe_id,
        "sheet_count": len(files),
        "before_bytes": total_before,
        "after_bytes": total_after,
        "files": files,
    }
