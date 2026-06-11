"""Home Assistant tool helpers backed by the configured HA MCP server.

Intent understanding (which action, which language) is done by the LLM, which
hands us a normalized action plus the device/room names in the user's own words.
This module only does what Python is good at: resolving those names against the
local device cache and calling the Home Assistant MCP services deterministically.
"""

from __future__ import annotations

import json
import os
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from core.config_store import get_config
from core.security import redact_for_log
from services.mcp_external import call_mcp_tool, list_mcp_tools


LEGACY_NAME = "digi" + "mon"
HA_CACHE_DIR = Path(
    os.environ.get("BITMON_CACHE_DIR")
    or os.environ.get(f"{LEGACY_NAME.upper()}_CACHE_DIR")
    or Path(__file__).parent
)
HA_CACHE_PATH = HA_CACHE_DIR / "home_assistant_cache.json"
HA_ENTITIES_CACHE_PATH = HA_CACHE_DIR / "home_assistant_entities_cache.json"
LEGACY_HA_CACHE_DIR = Path(__file__).parent
LEGACY_HA_CACHE_PATH = LEGACY_HA_CACHE_DIR / "home_assistant_cache.json"
LEGACY_HA_ENTITIES_CACHE_PATH = LEGACY_HA_CACHE_DIR / "home_assistant_entities_cache.json"
CONTROL_DOMAINS = [
    "light",
    "switch",
    "climate",
    "fan",
    "cover",
    "lock",
    "media_player",
    "scene",
    "script",
    "automation",
    "input_boolean",
]

# Actions the LLM may request. Everything language-specific lives in the model,
# never in this file.
CONTROL_ACTIONS = {"turn_on", "turn_off", "toggle", "set"}
QUERY_ACTION = "query"
LIST_ACTION = "list"
MATCH_THRESHOLD = 100


def _log_ha(text: str) -> None:
    print(f"{datetime.now().strftime('%H:%M:%S')} - [ha] {redact_for_log(text)}")


def _ha_config() -> dict[str, Any]:
    return get_config().get("mcps", {}).get("home_assistant", {})


def _ha_url() -> str:
    return str(_ha_config().get("url") or "").strip()


def _copy_legacy_cache_if_needed(target: Path, legacy: Path) -> None:
    if target.exists() or target == legacy or not legacy.exists():
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(legacy.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError:
        pass


def _load_cache() -> dict[str, Any]:
    _copy_legacy_cache_if_needed(HA_CACHE_PATH, LEGACY_HA_CACHE_PATH)
    if not HA_CACHE_PATH.exists():
        return {"tools_cached_at": 0, "tools": []}
    try:
        data = json.loads(HA_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"tools_cached_at": 0, "tools": []}
    if not isinstance(data, dict):
        return {"tools_cached_at": 0, "tools": []}
    data.setdefault("tools_cached_at", 0)
    data.setdefault("tools", [])
    return data


def _save_cache(cache: dict[str, Any]) -> None:
    HA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    HA_CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _load_entities_cache() -> dict[str, Any]:
    _copy_legacy_cache_if_needed(HA_ENTITIES_CACHE_PATH, LEGACY_HA_ENTITIES_CACHE_PATH)
    if not HA_ENTITIES_CACHE_PATH.exists():
        return {"devices": []}
    try:
        data = json.loads(HA_ENTITIES_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"devices": []}
    if not isinstance(data, dict):
        return {"devices": []}
    return _normalize_entities_cache(data)


def _save_entities_cache(cache: dict[str, Any]) -> None:
    cache = _normalize_entities_cache(cache)
    HA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    HA_ENTITIES_CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _default_aliases_for_device(device: dict[str, Any]) -> list[str]:
    aliases: list[str] = []
    for value in (
        device.get("name"),
        device.get("friendly_name"),
        device.get("original_name"),
        str(device.get("entity_id") or "").split(".", 1)[-1].replace("_", " "),
    ):
        text = str(value or "").strip()
        if text and _canonical_key(text) not in {_canonical_key(alias) for alias in aliases}:
            aliases.append(text)
    return aliases


def _device_payload(entity: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    existing = existing or {}
    entity_id = str(entity.get("entity_id") or existing.get("entity_id") or "").strip()
    domain = str(entity.get("domain") or (entity_id.split(".", 1)[0] if entity_id else "")).strip()
    name = str(
        entity.get("friendly_name")
        or entity.get("name")
        or entity.get("original_name")
        or existing.get("name")
        or entity_id
    ).strip()
    device = {
        "entity_id": entity_id,
        "name": name,
        "domain": domain,
        "enabled": bool(existing.get("enabled", False)),
        "aliases": list(existing.get("aliases") or []),
        "area": entity.get("area_id") or entity.get("area_name") or existing.get("area") or "",
        "updated_at": int(time.time()),
    }
    if not device["aliases"]:
        device["aliases"] = _default_aliases_for_device(device)
    return device


def _normalize_entities_cache(data: dict[str, Any]) -> dict[str, Any]:
    raw_devices = data.get("devices")
    devices_by_id: dict[str, dict[str, Any]] = {}
    if isinstance(raw_devices, list):
        for item in raw_devices:
            if not isinstance(item, dict):
                continue
            entity_id = str(item.get("entity_id") or "").strip()
            if not entity_id:
                continue
            domain = str(item.get("domain") or entity_id.split(".", 1)[0]).strip()
            aliases = [
                str(alias).strip()
                for alias in item.get("aliases") or []
                if str(alias).strip()
            ]
            payload = {
                "entity_id": entity_id,
                "name": str(item.get("name") or entity_id).strip(),
                "domain": domain,
                "enabled": bool(item.get("enabled", False)),
                "aliases": [],
                "area": str(item.get("area") or "").strip(),
                "updated_at": int(item.get("updated_at") or time.time()),
            }
            for alias in aliases or _default_aliases_for_device(payload):
                if _canonical_key(alias) not in {_canonical_key(existing) for existing in payload["aliases"]}:
                    payload["aliases"].append(alias)
            devices_by_id[entity_id] = payload

    legacy_aliases = data.get("entity_aliases") or {}
    if isinstance(legacy_aliases, dict):
        for alias_key, value in legacy_aliases.items():
            if not isinstance(value, dict):
                continue
            entity_id = str(value.get("entity_id") or "").strip()
            if not entity_id:
                continue
            device = devices_by_id.get(entity_id) or _device_payload(value)
            device["enabled"] = True
            alias = str(alias_key).split("::", 1)[-1].strip()
            for candidate in [alias, value.get("name")]:
                text = str(candidate or "").strip()
                if text and _canonical_key(text) not in {_canonical_key(existing) for existing in device["aliases"]}:
                    device["aliases"].append(text)
            devices_by_id[entity_id] = device

    return {
        "devices": sorted(devices_by_id.values(), key=lambda item: (item["domain"], item["name"].lower(), item["entity_id"])),
        "updated_at": int(data.get("updated_at") or time.time()),
    }


def _cached_tools() -> list[dict[str, Any]]:
    tools = _load_cache().get("tools") or []
    return tools if isinstance(tools, list) else []


async def refresh_home_assistant_tools_cache(url: str) -> dict[str, Any]:
    tools = [
        {
            "name": tool["name"],
            "description": (tool.get("description") or "")[:700],
            "input_schema": tool.get("input_schema") or {},
        }
        for tool in await list_mcp_tools(url)
    ]
    cache = _load_cache()
    cache["url"] = url.strip()
    cache["tools"] = tools
    cache["tools_cached_at"] = int(time.time())
    _save_cache(cache)
    return {
        "ok": True,
        "url": url.strip(),
        "tool_count": len(tools),
        "sample_tools": [
            {"name": tool["name"], "description": (tool.get("description") or "")[:160]}
            for tool in tools[:8]
        ],
    }


def _cached_tool(name: str) -> dict[str, Any] | None:
    for tool in _cached_tools():
        if tool.get("name") == name:
            return tool
    return None


def _has_tool(name: str) -> bool:
    return _cached_tool(name) is not None


# ---------------------------------------------------------------------------
# Version-agnostic tool resolution
#
# The HA MCP add-on renames and regroups its tools across releases: 7.6 exposed
# ha_search_entities / ha_get_state / ha_call_service directly, while 7.7
# renamed the search tool to ha_search and moved state/service calls behind the
# ha_call_read_tool / ha_call_write_tool proxies. Instead of hardcoding one
# layout, each purpose is resolved against the cached tool list (direct names
# first, then proxies), and a stale cache triggers one refresh + retry.
# ---------------------------------------------------------------------------

_SEARCH_TOOL_NAMES = ("ha_search_entities", "ha_search")
_STATE_TOOL_NAMES = ("ha_get_state",)
_SERVICE_TOOL_NAMES = ("ha_call_service",)
_READ_PROXY_NAMES = ("ha_call_read_tool",)
_WRITE_PROXY_NAMES = ("ha_call_write_tool",)

# A resolved call is (outer_tool, inner_tool|None): when inner_tool is set the
# outer tool is a proxy that receives {"name": inner_tool, "arguments": {...}}.
ResolvedTool = tuple[str, str | None]


def _resolve_direct_or_proxy(
    direct_names: tuple[str, ...],
    proxy_names: tuple[str, ...],
) -> ResolvedTool | None:
    for name in direct_names:
        if _has_tool(name):
            return name, None
    for proxy in proxy_names:
        if _has_tool(proxy):
            return proxy, direct_names[0]
    return None


def _resolve_search_tool() -> ResolvedTool | None:
    resolved = _resolve_direct_or_proxy(_SEARCH_TOOL_NAMES, ())
    if resolved:
        return resolved
    # Future renames: accept any ha_* search tool that mentions entities, but
    # never the tool-discovery tool itself (ha_search_tools).
    for tool in _cached_tools():
        name = str(tool.get("name") or "")
        text = f"{name} {str(tool.get('description') or '')}".lower()
        if "search" in name and "tools" not in name and "entit" in text:
            return name, None
    return _resolve_direct_or_proxy(_SEARCH_TOOL_NAMES[:1], _READ_PROXY_NAMES)


def _resolve_state_tool() -> ResolvedTool | None:
    return _resolve_direct_or_proxy(_STATE_TOOL_NAMES, _READ_PROXY_NAMES)


def _resolve_service_tool() -> ResolvedTool | None:
    return _resolve_direct_or_proxy(_SERVICE_TOOL_NAMES, _WRITE_PROXY_NAMES)


def _filter_tool_arguments(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Drop arguments the cached schema does not declare, so renamed/removed
    parameters never break a call against a different add-on version."""
    schema = (_cached_tool(name) or {}).get("input_schema") or {}
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return dict(arguments)
    return {key: value for key, value in arguments.items() if key in properties}


async def _call_resolved_tool(
    url: str,
    resolved: ResolvedTool,
    arguments: dict[str, Any],
    timeout_seconds: float = 20.0,
) -> str:
    outer, inner = resolved
    if inner is None:
        payload = _filter_tool_arguments(outer, arguments)
        return await call_mcp_tool(url, outer, payload, timeout_seconds=timeout_seconds)
    # Proxied tools live behind the add-on, so their schemas are not in the
    # cache; pass the arguments through untouched.
    return await call_mcp_tool(
        url,
        outer,
        {"name": inner, "arguments": dict(arguments)},
        timeout_seconds=timeout_seconds,
    )


def _looks_like_unknown_tool_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "tool" in text and any(
        marker in text
        for marker in ("unknown", "not found", "unrecognized", "does not exist", "invalid")
    )


async def _call_ha_tool(
    url: str,
    resolver: Any,
    arguments: dict[str, Any],
    *,
    timeout_seconds: float = 20.0,
    label: str = "tool",
) -> str:
    """Resolve and call a tool, refreshing the cache when it looks stale.

    The cache goes stale whenever the add-on is updated: resolution can fail
    (no matching name cached) or the server can reject a cached name. Both
    paths refresh the tool cache once and retry before giving up.
    """
    resolved = resolver()
    if resolved is None:
        await refresh_home_assistant_tools_cache(url)
        resolved = resolver()
    if resolved is None:
        raise RuntimeError(
            f"No Home Assistant MCP tool found for {label}. "
            "The configured MCP server does not expose a compatible tool."
        )
    try:
        return await _call_resolved_tool(url, resolved, arguments, timeout_seconds=timeout_seconds)
    except Exception as exc:
        if not _looks_like_unknown_tool_error(exc):
            raise
        _log_ha(f"{label}: {resolved[0]} rejected ({exc}); refreshing tool cache")
        await refresh_home_assistant_tools_cache(url)
        retried = resolver()
        if retried is None or retried == resolved:
            raise
        return await _call_resolved_tool(url, retried, arguments, timeout_seconds=timeout_seconds)


def _extract_json_objects(text: str) -> list[Any]:
    found: list[Any] = []
    decoder = json.JSONDecoder()
    i = 0
    while i < len(text):
        if text[i] not in "[{":
            i += 1
            continue
        try:
            obj, end = decoder.raw_decode(text[i:])
            found.append(obj)
            i += max(end, 1)
        except json.JSONDecodeError:
            i += 1
    return found


def _flatten_entities(value: Any) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    if isinstance(value, dict):
        entity_id = value.get("entity_id")
        if isinstance(entity_id, str):
            entities.append(value)
        for nested in value.values():
            entities.extend(_flatten_entities(nested))
    elif isinstance(value, list):
        for item in value:
            entities.extend(_flatten_entities(item))
    return entities


def _entities_from_text(text: str) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    for obj in _extract_json_objects(text):
        entities.extend(_flatten_entities(obj))
    return entities


# ---------------------------------------------------------------------------
# Device-name resolution (language-agnostic: pure token matching on aliases)
# ---------------------------------------------------------------------------


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _canonical_key(text: str) -> str:
    return _strip_accents(text).lower()


def _canonical_aliases(device: dict[str, Any]) -> set[str]:
    values = [device.get("entity_id"), device.get("name"), *(device.get("aliases") or [])]
    return {_canonical_key(str(value).replace("_", " ").strip()) for value in values if str(value or "").strip()}


def _tokens(text: str) -> set[str]:
    return {token for token in _canonical_key(text).split() if token}


def _device_match_score(device: dict[str, Any], query: str) -> int:
    query_key = _canonical_key(query)
    query_tokens = _tokens(query)
    if not query_key or not query_tokens:
        return 0

    best = 0
    for alias in _canonical_aliases(device):
        alias_tokens = _tokens(alias)
        if not alias_tokens:
            continue
        if query_key == alias:
            best = max(best, 1000)
        elif alias in query_key:
            best = max(best, 800 + len(alias_tokens))
        elif query_key in alias:
            best = max(best, 650 + len(query_tokens))
        elif alias_tokens.issubset(query_tokens):
            best = max(best, 500 + len(alias_tokens) * 10)
        else:
            overlap = len(alias_tokens & query_tokens)
            if overlap:
                best = max(best, overlap * 20 - len(alias_tokens - query_tokens) * 8)
    return best


def _cached_devices(enabled_only: bool = False) -> list[dict[str, Any]]:
    devices = _load_entities_cache().get("devices") or []
    if not isinstance(devices, list):
        return []
    if enabled_only:
        return [device for device in devices if isinstance(device, dict) and device.get("enabled")]
    return [device for device in devices if isinstance(device, dict)]


def _scored_devices(query: str, domain: str | None) -> list[tuple[int, dict[str, Any]]]:
    scored: list[tuple[int, dict[str, Any]]] = []
    for device in _cached_devices(enabled_only=True):
        entity_id = str(device.get("entity_id") or "")
        if domain and entity_id and entity_id.split(".", 1)[0] != domain:
            continue
        score = _device_match_score(device, query)
        if score > 0:
            scored.append((score, device))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored


def _device_ref(device: dict[str, Any]) -> dict[str, Any]:
    entity_id = str(device.get("entity_id") or "")
    return {
        "entity_id": entity_id,
        "name": device.get("name") or entity_id,
        "domain": device.get("domain") or (entity_id.split(".", 1)[0] if entity_id else ""),
    }


def _resolve_target(target: str, domain: str | None) -> list[dict[str, Any]]:
    """Resolve a single device/room name to one or more entities.

    The top-scoring tier is returned as a group, so "kitchen lights" or
    "bedroom" naturally act on every matching device at once.
    """
    query = _canonical_key(target)
    if not query:
        return []
    scored = _scored_devices(query, domain)
    if not scored and domain:
        scored = _scored_devices(query, None)
    top = [(score, device) for score, device in scored if score >= MATCH_THRESHOLD]
    if not top:
        return []
    best = top[0][0]
    matched = [_device_ref(device) for score, device in top if score == best]
    _log_ha(f"match {target!r} score={best} -> {[ref['entity_id'] for ref in matched]}")
    return matched


def _nearest_names(target: str, domain: str | None) -> list[str]:
    query = _canonical_key(target)
    if not query:
        return []
    scored = _scored_devices(query, domain) or _scored_devices(query, None)
    return [str(device.get("name") or device.get("entity_id")) for _score, device in scored[:5]]


def list_home_assistant_devices() -> dict[str, Any]:
    cache = _load_entities_cache()
    devices = cache.get("devices") or []
    return {
        "ok": True,
        "devices": devices,
        "count": len(devices),
        "enabled_count": sum(1 for device in devices if device.get("enabled")),
        "updated_at": cache.get("updated_at", 0),
    }


def save_home_assistant_devices(devices: list[dict[str, Any]]) -> dict[str, Any]:
    cache = {
        "devices": devices,
        "updated_at": int(time.time()),
    }
    _save_entities_cache(cache)
    return list_home_assistant_devices()


ENTITY_PAGE_SIZE = 200
ENTITY_MAX_PAGES = 50


async def refresh_home_assistant_devices_cache(url: str | None = None) -> dict[str, Any]:
    clean_url = (url or _ha_url()).strip()
    if not clean_url:
        return {"ok": False, "error": "Home Assistant MCP URL is empty."}

    existing_by_id = {device["entity_id"]: device for device in _cached_devices(enabled_only=False)}
    imported_by_id: dict[str, dict[str, Any]] = {}
    try:
        for domain in CONTROL_DOMAINS:
            for page in range(ENTITY_MAX_PAGES):
                text = await _call_ha_tool(
                    clean_url,
                    _resolve_search_tool,
                    {
                        "domain_filter": domain,
                        "exact_match": False,
                        "limit": ENTITY_PAGE_SIZE,
                        "offset": page * ENTITY_PAGE_SIZE,
                        "include_hidden": False,
                    },
                    timeout_seconds=20,
                    label="entity search",
                )
                entities = _entities_from_text(text)
                for entity in entities:
                    entity_id = str(entity.get("entity_id") or "")
                    if not entity_id:
                        continue
                    imported_by_id[entity_id] = _device_payload(entity, existing_by_id.get(entity_id))
                if len(entities) < ENTITY_PAGE_SIZE:
                    break
    except Exception as exc:
        return {"ok": False, "error": redact_for_log(str(exc))}

    _save_entities_cache({
        "devices": list(imported_by_id.values()),
        "updated_at": int(time.time()),
    })
    payload = list_home_assistant_devices()
    payload["imported_count"] = len(imported_by_id)
    return payload


# ---------------------------------------------------------------------------
# Action execution
# ---------------------------------------------------------------------------


def _resolve_service(action: str, domain: str, value: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Map a normalized action + entity domain to a concrete HA service + data."""
    data = dict(value or {})
    if domain == "lock":
        if action == "turn_off":
            return "unlock", data
        if action == "turn_on":
            return "lock", data
    if domain == "cover":
        if action == "turn_on":
            return "open_cover", data
        if action == "turn_off":
            return "close_cover", data
        if action == "set":
            return "set_cover_position", data
    if action == "set":
        if domain == "climate":
            return "set_temperature", data
        if domain == "fan":
            return "set_percentage", data
        if domain == "media_player":
            return "volume_set", data
        # light, switch and the generic case carry their attributes on turn_on
        # (brightness, color, color_temp, ...).
        return "turn_on", data
    # turn_on / turn_off / toggle pass straight through.
    return action, data


def _error(message: str, started: float, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "error": message,
        "elapsed_seconds": round(time.perf_counter() - started, 2),
    }
    if extra:
        result.update(extra)
    result["answer"] = message
    return result


def _summary(action: str, entity_names: list[str], unresolved: list[dict[str, Any]]) -> str:
    """Terse English last-resort line. The LLM normally rewrites this in the
    user's own language from the structured result, so we keep a single neutral
    fallback rather than per-language templates."""
    names = ", ".join(entity_names) or "the device"
    verb = {
        "turn_on": "Turned on",
        "turn_off": "Turned off",
        "toggle": "Toggled",
        "set": "Updated",
    }.get(action, "Updated")
    text = f"{verb} {names}."
    if unresolved:
        missing = ", ".join(str(item.get("target")) for item in unresolved)
        text += f" I could not find: {missing}."
    return text


def _dedupe_entities(resolved: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    entities: list[dict[str, Any]] = []
    for device in resolved:
        entity_id = str(device.get("entity_id") or "")
        if entity_id and entity_id not in seen:
            seen.add(entity_id)
            entities.append(device)
    return entities


async def _run_control(
    url: str,
    action: str,
    value: dict[str, Any],
    entities: list[dict[str, Any]],
    unresolved: list[dict[str, Any]],
    user_request: str,
    started: float,
) -> dict[str, Any]:
    # Group entities that need the same (domain, service, data) so we issue one
    # ha_call_service per group with a list of entity_ids — N devices, 1 call.
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for entity in entities:
        entity_id = str(entity["entity_id"])
        entity_domain = entity_id.split(".", 1)[0]
        service, data = _resolve_service(action, entity_domain, value)
        key = (entity_domain, service, json.dumps(data, sort_keys=True, ensure_ascii=False))
        group = groups.setdefault(
            key,
            {"domain": entity_domain, "service": service, "data": data, "entity_ids": []},
        )
        group["entity_ids"].append(entity_id)

    calls: list[dict[str, Any]] = []
    for group in groups.values():
        entity_ids = group["entity_ids"]
        arguments: dict[str, Any] = {
            "domain": group["domain"],
            "service": group["service"],
            "entity_id": entity_ids if len(entity_ids) > 1 else entity_ids[0],
            "wait": False,
        }
        if group["data"]:
            arguments["data"] = group["data"]
        try:
            text = await _call_ha_tool(
                url,
                _resolve_service_tool,
                arguments,
                timeout_seconds=12,
                label="service call",
            )
        except Exception as exc:
            return _error(redact_for_log(str(exc)), started, extra={"request": user_request})
        calls.append({
            "domain": group["domain"],
            "service": group["service"],
            "entity_ids": entity_ids,
            "data": group["data"],
            "result": text[:600],
        })

    entity_names = [str(entity.get("name") or entity["entity_id"]) for entity in entities]
    result: dict[str, Any] = {
        "ok": True,
        "request": user_request,
        "action": action,
        "service_calls": calls,
        "entities": [str(entity["entity_id"]) for entity in entities],
        "entity_names": entity_names,
        "elapsed_seconds": round(time.perf_counter() - started, 2),
    }
    if unresolved:
        result["unresolved"] = unresolved
    result["answer"] = _summary(action, entity_names, unresolved)
    _log_ha(f"{action} -> {result['entities']} ({result['elapsed_seconds']}s)")
    return result


def _run_list(domain: str | None, user_request: str, started: float) -> dict[str, Any]:
    """Answer "which devices/lights can you control?" from the local cache.

    Only enabled devices are listed (those are the ones BitMon can act on);
    disabled ones are just counted so the spoken answer can point the user at
    the config page instead of claiming nothing exists.
    """
    all_devices = _cached_devices(enabled_only=False)
    if domain:
        all_devices = [device for device in all_devices if str(device.get("domain") or "") == domain]
    enabled = [device for device in all_devices if device.get("enabled")]
    disabled_count = len(all_devices) - len(enabled)
    devices = [
        {
            "entity_id": str(device.get("entity_id") or ""),
            "name": str(device.get("name") or device.get("entity_id") or ""),
            "domain": str(device.get("domain") or ""),
            "area": str(device.get("area") or ""),
        }
        for device in enabled[:60]
    ]
    label = domain or "smart-home"
    names = ", ".join(device["name"] for device in devices[:15])
    if devices:
        answer = f"I can control these {label} devices: {names}."
        if disabled_count:
            answer += f" {disabled_count} more are imported but not enabled in the BitMon config."
    elif disabled_count:
        answer = (
            f"No {label} devices are enabled for me yet; {disabled_count} are imported but disabled. "
            "Enable them in the BitMon configuration page, Home Assistant section."
        )
    else:
        answer = (
            f"No {label} devices are in my cache. Import them in the BitMon configuration page, "
            "Home Assistant section."
        )
    result: dict[str, Any] = {
        "ok": True,
        "request": user_request,
        "action": LIST_ACTION,
        "domain": domain or "",
        "devices": devices,
        "count": len(enabled),
        "disabled_count": disabled_count,
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "answer": answer,
    }
    _log_ha(f"list domain={domain or '*'} -> {len(enabled)} enabled, {disabled_count} disabled")
    return result


async def _run_query(
    url: str,
    entities: list[dict[str, Any]],
    unresolved: list[dict[str, Any]],
    user_request: str,
    started: float,
) -> dict[str, Any]:
    entity_ids = [str(entity["entity_id"]) for entity in entities]
    try:
        text = await _call_ha_tool(
            url,
            _resolve_state_tool,
            {"entity_id": entity_ids if len(entity_ids) > 1 else entity_ids[0]},
            timeout_seconds=12,
            label="state query",
        )
    except Exception as exc:
        return _error(redact_for_log(str(exc)), started, extra={"request": user_request})
    result: dict[str, Any] = {
        "ok": True,
        "request": user_request,
        "action": QUERY_ACTION,
        "entities": entity_ids,
        "entity_names": [str(entity.get("name") or entity["entity_id"]) for entity in entities],
        "state": text[:2000],
        "elapsed_seconds": round(time.perf_counter() - started, 2),
    }
    if unresolved:
        result["unresolved"] = unresolved
    # Raw state is the context the LLM phrases the spoken answer from.
    result["answer"] = text[:2000]
    _log_ha(f"query -> {entity_ids} ({result['elapsed_seconds']}s)")
    return result


async def execute_home_assistant_request(
    action: str,
    targets: list[str] | None = None,
    domain: str | None = None,
    value: dict[str, Any] | None = None,
    user_request: str = "",
) -> dict[str, Any]:
    """Execute a structured smart-home action produced by the LLM.

    The model has already done the multilingual understanding and handed us a
    normalized ``action`` plus ``targets`` (device/room names in the user's own
    language). We only resolve those names and call Home Assistant.
    """
    started = time.perf_counter()
    if not _ha_config().get("enabled"):
        return _error("Home Assistant MCP is disabled in BitMon config.", started)

    url = _ha_url()
    if not url:
        return _error("Home Assistant MCP URL is empty.", started)

    action = str(action or "").strip().lower()
    if action not in CONTROL_ACTIONS and action not in {QUERY_ACTION, LIST_ACTION}:
        action = "turn_on"
    domain = (str(domain or "").strip().lower() or None)
    value = value if isinstance(value, dict) else {}

    if action == LIST_ACTION:
        return _run_list(domain, user_request, started)

    target_list = [str(target).strip() for target in (targets or []) if str(target).strip()]
    if not target_list and user_request.strip():
        target_list = [user_request.strip()]
    if not target_list:
        return _error("No target device was specified.", started)

    if not _cached_tools():
        refreshed = await refresh_home_assistant_tools_cache(url)
        if not refreshed.get("ok"):
            return refreshed

    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for target in target_list:
        matches = _resolve_target(target, domain)
        if matches:
            resolved.extend(matches)
        else:
            _log_ha(f"cache miss target={target!r} domain={domain or '*'}")
            unresolved.append({"target": target, "candidates": _nearest_names(target, domain)})

    entities = _dedupe_entities(resolved)
    if not entities:
        # Tell the model what IS available so it can answer "I have X and Y"
        # instead of claiming no devices exist.
        enabled = _cached_devices(enabled_only=True)
        scoped = [device for device in enabled if str(device.get("domain") or "") == domain] if domain else enabled
        scoped = scoped or enabled
        available = [str(device.get("name") or device.get("entity_id")) for device in scoped[:15]]
        message = "No matching enabled Home Assistant device was found."
        if available:
            message += " Devices I can control: " + ", ".join(available) + "."
        return _error(
            message,
            started,
            extra={"request": user_request, "unresolved": unresolved, "available_devices": available},
        )

    if action == QUERY_ACTION:
        return await _run_query(url, entities, unresolved, user_request, started)
    return await _run_control(url, action, value, entities, unresolved, user_request, started)
