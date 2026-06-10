"""JSON-backed BitMon runtime configuration."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from copy import deepcopy
from pathlib import Path
from typing import Any

from core.config import get_inworld_api_key_source, is_inworld_api_key_configured, set_inworld_api_key
from core.config_defaults import DEFAULT_CONFIG, CURRENT_CONFIG_VERSION
from core.config_models import validate_config
from core.mcp_auth_store import (
    delete_bearer_token,
    delete_oauth_credentials,
    is_bearer_token_configured,
    is_oauth_connected,
    normalize_mcp_server_id,
    set_bearer_token,
)
from core.secret_store import SecretStoreError


LEGACY_NAME = "digi" + "mon"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "bitmon_config.json"
LEGACY_CONFIG_PATH = Path(__file__).resolve().parent.parent / f"{LEGACY_NAME}_config.json"


def _selected_config_path() -> Path:
    configured = os.environ.get("BITMON_CONFIG_PATH") or os.environ.get(f"{LEGACY_NAME.upper()}_CONFIG_PATH")
    if configured:
        return Path(configured)
    if not DEFAULT_CONFIG_PATH.exists() and LEGACY_CONFIG_PATH.exists():
        try:
            LEGACY_CONFIG_PATH.replace(DEFAULT_CONFIG_PATH)
        except OSError:
            return LEGACY_CONFIG_PATH
    return DEFAULT_CONFIG_PATH


CONFIG_PATH = _selected_config_path()
CONFIG_BACKUP_DIR = CONFIG_PATH.parent / "config_backups"
INWORLD_SECRET_INPUT_KEYS = {
    "apiKey",
    "api_key",
    "inworldApiKey",
    "inworld_api_key",
    "INWORLD_API_KEY",
}
MCP_BEARER_INPUT_KEYS = {"bearer_token", "token", "api_key", "apiKey"}
MCP_AUTH_TYPES = {"none", "bearer", "oauth"}


def _config_revision(config: dict[str, Any]) -> str:
    payload = json.dumps(
        config,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _normalize_hotkeys(microphone: dict[str, Any]) -> None:
    raw_hotkeys = microphone.get("whisper_hotkeys")
    if isinstance(raw_hotkeys, str):
        candidates = raw_hotkeys.split(",")
    elif isinstance(raw_hotkeys, list):
        candidates = list(raw_hotkeys)
    else:
        candidates = []

    legacy = microphone.get("whisper_hotkey")
    if legacy:
        candidates.insert(0, legacy)

    hotkeys: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        value = str(candidate or "").strip().lower()
        if not value or value in seen:
            continue
        seen.add(value)
        hotkeys.append(value)

    if not hotkeys:
        hotkeys = ["f8"]
    microphone["whisper_hotkeys"] = hotkeys
    microphone["whisper_hotkey"] = hotkeys[0]


def _extract_secret_updates(config: dict[str, Any]) -> dict[str, str]:
    updates: dict[str, str] = {}

    def pop_secret_fields(section: dict[str, Any]) -> None:
        for key in list(INWORLD_SECRET_INPUT_KEYS):
            value = section.pop(key, None)
            if isinstance(value, str) and value.strip():
                updates["inworld_api_key"] = value.strip()

    secrets = config.get("secrets")
    if isinstance(secrets, dict):
        pop_secret_fields(secrets)

    inworld = config.get("inworld")
    if isinstance(inworld, dict):
        pop_secret_fields(inworld)

    mcps = config.get("mcps")
    servers = mcps.get("servers") if isinstance(mcps, dict) else None
    if isinstance(servers, list):
        for index, server in enumerate(servers):
            if not isinstance(server, dict):
                continue
            server_id = normalize_mcp_server_id(server.get("id") or server.get("name"), f"mcp_{index + 1}")
            server["id"] = server_id
            auth_type = str(server.get("auth_type") or "none").strip().lower()
            server["auth_type"] = auth_type if auth_type in MCP_AUTH_TYPES else "none"
            for key in list(MCP_BEARER_INPUT_KEYS):
                value = server.pop(key, None)
                if isinstance(value, str) and value.strip():
                    updates[f"mcp_bearer_token:{server_id}"] = value.strip()

    pop_secret_fields(config)
    return updates


def _apply_secret_updates(config: dict[str, Any]) -> bool:
    updates = _extract_secret_updates(config)
    api_key = updates.get("inworld_api_key")
    if api_key:
        set_inworld_api_key(api_key)
    for key, value in updates.items():
        if key.startswith("mcp_bearer_token:"):
            set_bearer_token(key.split(":", 1)[1], value)
    return bool(updates)


def _set_secret_flags(config: dict[str, Any]) -> None:
    secrets = config.get("secrets")
    if not isinstance(secrets, dict):
        secrets = {}
        config["secrets"] = secrets
    secrets["inworld_api_key_configured"] = is_inworld_api_key_configured()
    secrets["inworld_api_key_source"] = get_inworld_api_key_source()


def _normalize_mcp_servers(config: dict[str, Any]) -> None:
    mcps = config.get("mcps")
    if not isinstance(mcps, dict):
        return
    servers = mcps.get("servers")
    if not isinstance(servers, list):
        mcps["servers"] = []
        return
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, server in enumerate(servers):
        if not isinstance(server, dict):
            continue
        server_id = normalize_mcp_server_id(server.get("id") or server.get("name"), f"mcp_{index + 1}")
        base_id = server_id
        suffix = 2
        while server_id in seen:
            server_id = f"{base_id}_{suffix}"
            suffix += 1
        seen.add(server_id)
        auth_type = str(server.get("auth_type") or "none").strip().lower()
        if auth_type not in MCP_AUTH_TYPES:
            auth_type = "none"
        if auth_type != "bearer":
            delete_bearer_token(server_id)
        if auth_type != "oauth":
            delete_oauth_credentials(server_id)
        clean = {
            **server,
            "id": server_id,
            "name": str(server.get("name") or server_id).strip(),
            "url": str(server.get("url") or "").strip(),
            "description": str(server.get("description") or "").strip(),
            "auth_type": auth_type,
            "enabled": bool(server.get("enabled")),
        }
        for key in MCP_BEARER_INPUT_KEYS:
            clean.pop(key, None)
        normalized.append(clean)
    mcps["servers"] = normalized


def _set_mcp_secret_flags(config: dict[str, Any]) -> None:
    mcps = config.get("mcps")
    servers = mcps.get("servers") if isinstance(mcps, dict) else None
    if not isinstance(servers, list):
        return
    for server in servers:
        if not isinstance(server, dict):
            continue
        server_id = normalize_mcp_server_id(server.get("id") or server.get("name"))
        auth_type = str(server.get("auth_type") or "none").strip().lower()
        server["bearer_token_configured"] = auth_type == "bearer" and is_bearer_token_configured(server_id)
        server["oauth_connected"] = auth_type == "oauth" and is_oauth_connected(server_id)


def _provider_value(value: Any, default: str = "inworld") -> str:
    provider = str(value or default).strip().lower()
    return provider if provider in {"inworld", "local"} else default


def _tts_provider_value(value: Any, default: str = "inworld") -> str:
    provider = str(value or default).strip().lower()
    if provider == "local":
        provider = "kokoro"
    return provider if provider in {"inworld", "kokoro"} else default


def _sync_voice_sections(config: dict[str, Any]) -> None:
    legacy_provider = _provider_value(config.get("provider"))
    inworld = config.setdefault("inworld", {})
    local = config.setdefault("local", {})
    provider_section = config.get(legacy_provider) if isinstance(config.get(legacy_provider), dict) else inworld

    llm = config.setdefault("llm", {})
    if not isinstance(llm, dict):
        llm = {}
        config["llm"] = llm
    llm["provider"] = _provider_value(llm.get("provider") or legacy_provider)
    config["provider"] = llm["provider"]

    tts = config.setdefault("tts", {})
    if not isinstance(tts, dict):
        tts = {}
        config["tts"] = tts
    enabled = tts.get("enabled", None)
    legacy_enabled = provider_section.get("voice_response", None) if isinstance(provider_section, dict) else None
    if legacy_enabled is None:
        legacy_enabled = inworld.get("voice_response", local.get("voice_response", None))
    if legacy_enabled is not None and bool(legacy_enabled) != bool(enabled):
        enabled = legacy_enabled
    if enabled is None:
        enabled = True
    tts["enabled"] = bool(enabled)
    if "provider" not in tts:
        tts["provider"] = "inworld" if legacy_provider == "inworld" else "kokoro"
    tts["provider"] = _tts_provider_value(tts.get("provider"))

    speech = config.setdefault("speech", {})
    if not isinstance(speech, dict):
        speech = {}
        config["speech"] = speech
    if "stt_language" not in speech:
        speech["stt_language"] = (
            provider_section.get("stt_language")
            or inworld.get("stt_language")
            or local.get("stt_language")
            or "pt"
        )
    if "tts_language" not in speech:
        speech["tts_language"] = (
            provider_section.get("tts_language")
            or inworld.get("tts_language")
            or local.get("tts_language")
            or "pt"
        )

    whisper = config.setdefault("whisper", {})
    if not isinstance(whisper, dict):
        whisper = {}
        config["whisper"] = whisper
    if "model" not in whisper:
        whisper["model"] = (
            provider_section.get("whisper_model")
            or inworld.get("whisper_model")
            or local.get("whisper_model")
            or "base"
        )

    for section in (inworld, local):
        if not isinstance(section, dict):
            continue
        section["voice_response"] = bool(tts["enabled"])
        section["stt_provider"] = "whisper"
        section["whisper_model"] = whisper["model"]
        section["stt_language"] = speech["stt_language"]
        section["tts_language"] = speech["tts_language"]


def _sync_legacy_voice_inputs(config: dict[str, Any]) -> None:
    tts = config.get("tts")
    if isinstance(tts, dict) and "enabled" in tts:
        enabled = bool(tts.get("enabled"))
        config.setdefault("inworld", {})["voice_response"] = enabled
        config.setdefault("local", {})["voice_response"] = enabled

    llm = config.get("llm")
    if isinstance(llm, dict) and llm.get("provider"):
        config["provider"] = _provider_value(llm.get("provider"))

    speech = config.get("speech")
    if isinstance(speech, dict):
        for key in ("stt_language", "tts_language"):
            if key in speech:
                config.setdefault("inworld", {})[key] = speech[key]
                config.setdefault("local", {})[key] = speech[key]

    whisper = config.get("whisper")
    if isinstance(whisper, dict) and whisper.get("model"):
        config.setdefault("inworld", {})["whisper_model"] = whisper["model"]
        config.setdefault("local", {})["whisper_model"] = whisper["model"]


def migrate_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return a migrated copy of a persisted config dict."""
    migrated = deepcopy(config)
    migrated.pop("openai", None)

    version = int(migrated.get("config_version") or 1)
    if version < 2:
        microphone = migrated.setdefault("microphone", {})
        if isinstance(microphone, dict):
            hotkeys = microphone.get("whisper_hotkeys")
            legacy = microphone.get("whisper_hotkey")
            if not hotkeys and legacy:
                microphone["whisper_hotkeys"] = [legacy]

        secrets = migrated.setdefault("secrets", {})
        if isinstance(secrets, dict):
            secrets.setdefault("inworld_api_key_configured", False)

    migrated["config_version"] = CURRENT_CONFIG_VERSION
    return migrated


def _normalize_config(config: dict[str, Any]) -> dict[str, Any]:
    config = migrate_config(config)
    config.pop("openai", None)
    _extract_secret_updates(config)
    _sync_voice_sections(config)
    _normalize_mcp_servers(config)
    microphone = config.setdefault("microphone", {})
    if isinstance(microphone, dict):
        _normalize_hotkeys(microphone)
    _set_secret_flags(config)
    _set_mcp_secret_flags(config)
    return validate_config(config)


def _deep_merge(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _read_config_file() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}

    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        data = {}

    if not isinstance(data, dict):
        data = {}
    return data


def _raw_config_has_secret_fields() -> bool:
    data = _read_config_file()
    if not data:
        return False
    original = deepcopy(data)
    _extract_secret_updates(data)
    return data != original


def _backup_existing_config(next_config: dict[str, Any]) -> None:
    if not CONFIG_PATH.exists():
        return
    try:
        current_text = CONFIG_PATH.read_text(encoding="utf-8")
    except OSError:
        return

    next_text = json.dumps(next_config, ensure_ascii=False, indent=2) + "\n"
    if current_text == next_text:
        return

    CONFIG_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = CONFIG_BACKUP_DIR / f"bitmon_config.{stamp}.json"
    backup_path.write_text(current_text, encoding="utf-8")
    _prune_old_backups()


def _prune_old_backups(keep: int = 10) -> None:
    """Keep only the ``keep`` most recent config backups; delete the rest."""
    backups = sorted(CONFIG_BACKUP_DIR.glob("*_config.*.json"))
    for stale in backups[:-keep]:
        try:
            stale.unlink()
        except OSError:
            pass


def get_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return save_config(DEFAULT_CONFIG)

    data = migrate_config(_read_config_file())
    migration_failed = False
    try:
        secrets_found = _apply_secret_updates(data)
    except SecretStoreError:
        migration_failed = True
        _extract_secret_updates(data)
        secrets_found = False

    merged = _normalize_config(_deep_merge(DEFAULT_CONFIG, data))
    if (merged != data or secrets_found) and not migration_failed:
        save_config(merged)
    return merged


def save_config(config: dict[str, Any]) -> dict[str, Any]:
    config_to_save = deepcopy(config)
    _sync_legacy_voice_inputs(config_to_save)
    _apply_secret_updates(config_to_save)
    merged = _normalize_config(_deep_merge(DEFAULT_CONFIG, config_to_save))
    _backup_existing_config(merged)
    CONFIG_PATH.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return merged


def update_config(config_update: dict[str, Any]) -> dict[str, Any]:
    if _raw_config_has_secret_fields():
        data = _read_config_file()
        _apply_secret_updates(data)
        save_config(data)

    safe_update = deepcopy(config_update)
    _sync_legacy_voice_inputs(safe_update)
    _apply_secret_updates(safe_update)
    merged = _deep_merge(get_config(), safe_update)
    return save_config(merged)


def get_client_config() -> dict[str, Any]:
    config = get_config()
    provider = str(config.get("llm", {}).get("provider") or config.get("provider") or "inworld").lower()
    return {
        "config_revision": _config_revision(config),
        "provider": provider,
        "character_name": config["character"]["name"],
        "mic_gain": config["microphone"]["gain"],
        "vad_threshold": config["microphone"]["vad_threshold"],
        "overlay_mode": config["overlay"]["enabled"],
        "overlay_always_on_top": config["overlay"]["always_on_top"],
        "debug_user_subtitle": config["debug"]["user_subtitle"],
        "debug_replay_audio": config["debug"]["replay_audio"],
        "stt_provider": "whisper",
        "whisper_model": config.get("whisper", {}).get("model", "base"),
        "stt_language": config.get("speech", {}).get("stt_language", "pt"),
        "whisper_hotkey": config["microphone"].get("whisper_hotkey", "f8"),
        "whisper_hotkeys": config["microphone"].get("whisper_hotkeys", ["f8"]),
        "wake_word": config.get("wake_word", {}),
    }
