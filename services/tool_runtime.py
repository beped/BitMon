"""Tool execution bridge for model-selected BitMon tools."""

from __future__ import annotations

import asyncio
import ast
import json
import os
import re
import time
import webbrowser
from typing import Any

from services.mcp_external import call_mcp_tool
from tools.home_assistant import execute_home_assistant_request
from tools.screen_tools import analyze_screen


SCREEN_ANALYZE_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "screen_analyze",
    "description": (
        "Capture and analyze the user's current screen. Use this whenever the user asks, in any language, "
        "to look at, inspect, read, troubleshoot, describe, or reason about what is visible on their screen. "
        "Pass the user's actual task, not a generic description request."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The specific thing the user wants to know about the current screen.",
            }
        },
        "required": ["question"],
    },
}


OPEN_CONFIGURATION_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "open_configuration",
    "description": (
        "Open the BitMon configuration page in the user's browser. Use this whenever the user asks, "
        "in any language, to open BitMon settings, configuration, preferences, options, or the config page."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}


HOME_ASSISTANT_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "home_assistant",
    "description": (
        "Control or query Home Assistant smart-home devices, in any language. You understand the user's "
        "intent and translate it into a structured action; do not pass the raw sentence. Covers lights, "
        "switches, scenes, scripts, automations, climate, fans, covers, locks, media players, sensors and rooms. "
        "Use action='list' when the user asks which devices, lights or rooms you can see or control."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["turn_on", "turn_off", "toggle", "set", "query", "list"],
                "description": (
                    "Normalized action. Use 'set' to change a value (brightness, color, temperature, "
                    "cover position, fan speed, volume). Use 'query' to read the current state. "
                    "Use 'list' to enumerate the available devices; targets is not needed then."
                ),
            },
            "targets": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Device or room names exactly as the user referred to them, in their own language. "
                    "One entry per device or room (e.g. [\"kitchen light\", \"bedroom\"]). "
                    "Omit it (or pass []) with action='list'."
                ),
            },
            "domain": {
                "type": "string",
                "description": (
                    "Optional Home Assistant domain when unambiguous from the request: light, switch, "
                    "climate, fan, cover, lock, media_player, scene, script, automation, input_boolean."
                ),
            },
            "value": {
                "type": "object",
                "description": (
                    "Optional service data for action='set', as HA service fields. Examples: "
                    "{\"brightness_pct\": 50}, {\"color_name\": \"red\"}, {\"temperature\": 22}, "
                    "{\"position\": 30}, {\"percentage\": 40}, {\"volume_level\": 0.4}."
                ),
            },
        },
        "required": ["action"],
    },
}


SESSION_TOOLS: list[dict[str, Any]] = [SCREEN_ANALYZE_TOOL, OPEN_CONFIGURATION_TOOL, HOME_ASSISTANT_TOOL]
LEGACY_NAME = "digi" + "mon"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(f"BITMON_{name}") or os.environ.get(f"{LEGACY_NAME.upper()}_{name}") or default


TOOL_INSTRUCTIONS = (
    "You can use tools. If a tool is needed, call it immediately before producing any user-facing answer; "
    "do not say acknowledgements like ok, vou fazer isso, or let me check first. "
    "If the user asks you to look at, inspect, read, analyze, or describe their screen, call screen_analyze. "
    "Do not answer with a visibility disclaimer if screen_analyze returned ok=true. "
    "When calling screen_analyze, pass the user's actual request or goal as the question. "
    "If the user asks to open the BitMon configuration page or settings, call open_configuration. "
    "If the user asks to control or query smart-home devices, call home_assistant. "
    "NEVER claim a smart-home action was performed unless home_assistant was called in the CURRENT turn "
    "and returned ok=true; previous turns do not count. If you did not call it yet, call it now instead "
    "of answering. "
    "If the user asks which devices, lights, or rooms you can see or control, call home_assistant with "
    "action='list' and, when implied, the domain (e.g. domain='light' for lights). "
    "If the user asks for something that belongs to a configured external MCP server, call external_mcp "
    "with the configured server_id, exact MCP tool_name, and JSON arguments."
)


def _config_url() -> str:
    host = str(_env("HOST", "127.0.0.1")).strip() or "127.0.0.1"
    if host in {"0.0.0.0", "::", "[::]"}:
        host = "127.0.0.1"
    elif ":" in host and not host.startswith("["):
        host = f"[{host}]"
    raw_port = str(_env("PORT", "8000")).strip()
    try:
        port = int(raw_port)
    except ValueError:
        port = 8000
    return f"http://{host}:{port}/config"


def _safe_mcp_id(value: Any, fallback: str) -> str:
    text = str(value or fallback).strip().lower()
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = text.strip("_")
    return text or fallback


def _configured_external_mcps(config: dict[str, Any]) -> list[dict[str, Any]]:
    servers = config.get("mcps", {}).get("servers") or []
    if not isinstance(servers, list):
        return []
    configured: list[dict[str, Any]] = []
    for index, server in enumerate(servers):
        if not isinstance(server, dict):
            continue
        url = str(server.get("url") or "").strip()
        if not server.get("enabled") or not url:
            continue
        server_id = _safe_mcp_id(server.get("id") or server.get("name"), f"mcp_{index + 1}")
        configured.append({
            "id": server_id,
            "name": str(server.get("name") or server_id).strip() or server_id,
            "url": url,
            "description": str(server.get("description") or "").strip(),
            "auth_type": str(server.get("auth_type") or "none").strip().lower(),
            "tools": server.get("tools") if isinstance(server.get("tools"), list) else [],
        })
    return configured


def _external_mcp_tool(config: dict[str, Any]) -> dict[str, Any] | None:
    servers = _configured_external_mcps(config)
    if not servers:
        return None
    def server_description(server: dict[str, Any]) -> str:
        tools = []
        for tool in server.get("tools", [])[:30]:
            if not isinstance(tool, dict):
                continue
            name = str(tool.get("name") or "").strip()
            if not name:
                continue
            description = str(tool.get("description") or "").strip()
            tools.append(f"{name} - {description[:120]}" if description else name)
        tool_text = f" Available tools: {', '.join(tools)}." if tools else ""
        return f"{server['id']} ({server['name']}): {server['description'] or 'external MCP server'}.{tool_text}"

    server_descriptions = "; ".join(server_description(server) for server in servers)
    return {
        "type": "function",
        "name": "external_mcp",
        "description": (
            "Call a configured external MCP server when the user's request clearly belongs to one of these servers: "
            f"{server_descriptions}. Use a specific MCP tool name from that server."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "server_id": {
                    "type": "string",
                    "description": "Configured MCP server id.",
                },
                "tool_name": {
                    "type": "string",
                    "description": "Exact MCP tool name to call on that server.",
                },
                "arguments": {
                    "type": "object",
                    "description": "Arguments to pass to the MCP tool.",
                },
            },
            "required": ["server_id", "tool_name"],
        },
    }


def get_session_tools(config: dict[str, Any]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    if config.get("tools", {}).get("screen_analysis", False):
        tools.append(SCREEN_ANALYZE_TOOL)
    if config.get("tools", {}).get("open_configuration", False):
        tools.append(OPEN_CONFIGURATION_TOOL)
    ha_config = config.get("mcps", {}).get("home_assistant", {})
    if ha_config.get("enabled") and str(ha_config.get("url") or "").strip():
        tools.append(HOME_ASSISTANT_TOOL)
    external_mcp = _external_mcp_tool(config)
    if external_mcp:
        tools.append(external_mcp)
    return tools


def get_chat_tools(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return OpenAI Chat Completions tool definitions for enabled BitMon tools."""
    chat_tools: list[dict[str, Any]] = []
    for tool in get_session_tools(config):
        chat_tools.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("parameters", {"type": "object", "properties": {}}),
            },
        })
    return chat_tools


def _chat_message_to_dict(message: Any) -> dict[str, Any]:
    if hasattr(message, "model_dump"):
        return message.model_dump(exclude_none=True)
    if isinstance(message, dict):
        return dict(message)
    return {
        "role": getattr(message, "role", "assistant"),
        "content": getattr(message, "content", "") or "",
    }


def _tool_call_id(tool_call: Any) -> str:
    if isinstance(tool_call, dict):
        return str(tool_call.get("id") or "")
    return str(getattr(tool_call, "id", "") or "")


def _tool_call_function(tool_call: Any) -> tuple[str, str | dict[str, Any] | None]:
    if isinstance(tool_call, dict):
        function = tool_call.get("function") or {}
        return str(function.get("name") or ""), function.get("arguments")
    function = getattr(tool_call, "function", None)
    return str(getattr(function, "name", "") or ""), getattr(function, "arguments", None)


def _tool_result_answer(result: dict[str, Any]) -> str:
    for key in ("answer", "analysis", "error"):
        value = str(result.get(key) or "").strip()
        if value:
            return value
    return "Done." if result.get("ok") else "I could not complete that."


# Some models (e.g. DeepSeek via the Inworld router) leak raw tool-call markup
# into the text content when they want to call a tool but cannot — special
# tokens like <｜｜DSML｜｜tool_calls> (fullwidth pipes), <|tool_call|> or
# <tool_call>. That text must never reach the TTS, so everything from the
# first marker onward is dropped.
_LEAKED_TOOL_MARKUP_RE = re.compile(r"<\s*[|｜]|<\s*/?\s*tool[_▁ ]?calls?\b|[|｜]{2}\s*DSML", re.IGNORECASE)


def _strip_leaked_tool_markup(text: str) -> str:
    cleaned = str(text or "")
    match = _LEAKED_TOOL_MARKUP_RE.search(cleaned)
    if match:
        cleaned = cleaned[: match.start()]
    return cleaned.strip()


# When the provider rejects the tool definitions themselves (e.g. "tool
# calling is restricted on your plan"), skip sending them for a while instead
# of paying a failed request + retry on every single turn.
_TOOLS_REJECTED_UNTIL = 0.0
_TOOLS_REJECTED_TTL_SECONDS = 600.0


def _known_tool_names(tools: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for tool in tools:
        function = tool.get("function") if isinstance(tool, dict) else None
        name = str((function or {}).get("name") or tool.get("name") or "") if isinstance(tool, dict) else ""
        if name and name not in names:
            names.append(name)
    return names


def _parse_leaked_tool_calls(
    text: str,
    tools: list[dict[str, Any]],
) -> list[tuple[str, dict[str, Any]]]:
    """Recover tool calls from leaked tool-call markup.

    When a model emits its tool-call special tokens as plain text (see
    _strip_leaked_tool_markup) the intent is usually still recoverable: the
    leaked block carries the tool name followed by a JSON arguments object.
    Pair every known tool name found in the text with the first JSON object
    after it, so the call can be executed as if it had arrived structured.
    """
    raw = str(text or "")
    if not raw or not _LEAKED_TOOL_MARKUP_RE.search(raw):
        return []
    names = _known_tool_names(tools)
    if not names:
        return []
    decoder = json.JSONDecoder()
    calls: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for match in re.finditer("|".join(re.escape(name) for name in names), raw):
        index = raw.find("{", match.end())
        while index != -1:
            try:
                arguments, _length = decoder.raw_decode(raw[index:])
            except json.JSONDecodeError:
                index = raw.find("{", index + 1)
                continue
            if isinstance(arguments, dict):
                key = f"{match.group(0)}::{json.dumps(arguments, sort_keys=True, ensure_ascii=False)}"
                if key not in seen:
                    seen.add(key)
                    calls.append((match.group(0), arguments))
            break
    return calls


def _parse_pseudo_tool_calls(
    text: str,
    tools: list[dict[str, Any]],
) -> list[tuple[str, dict[str, Any]]]:
    """Recover calls written as plain text, e.g. home_assistant(action='turn_off').

    Models running without native tool calling (provider plan restriction)
    still announce the call this way, because the system prompt names the
    tools. That text must never reach the TTS — parse and execute it instead.
    """
    raw = str(text or "")
    names = _known_tool_names(tools)
    if not raw or not names:
        return []
    calls: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for name in names:
        for match in re.finditer(rf"\b{re.escape(name)}\s*\(([^()]*)\)", raw):
            inner = match.group(1).strip()
            arguments: dict[str, Any] = {}
            if inner:
                try:
                    call_expr = ast.parse(f"_call({inner})", mode="eval").body
                except SyntaxError:
                    continue
                for keyword in getattr(call_expr, "keywords", []):
                    if keyword.arg is None:
                        continue
                    try:
                        arguments[keyword.arg] = ast.literal_eval(keyword.value)
                    except (ValueError, SyntaxError):
                        continue
            key = f"{name}::{json.dumps(arguments, sort_keys=True, ensure_ascii=False)}"
            if key not in seen:
                seen.add(key)
                calls.append((name, arguments))
    return calls


def _tools_maybe_unsupported(exc: Exception) -> bool:
    text = str(exc).lower()
    return "tool" in text and any(
        marker in text
        for marker in (
            "unsupported",
            "not supported",
            "unknown parameter",
            "extra inputs are not permitted",
            "extra fields not permitted",
            "invalid_request_error",
        )
    )


async def create_chat_completion_with_tools(
    client: Any,
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    user_request: str,
    max_tool_rounds: int = 4,
    **kwargs: Any,
) -> tuple[str, list[str]]:
    """Run a Chat Completions request and execute model-selected BitMon tools."""
    global _TOOLS_REJECTED_UNTIL
    request_messages = [dict(message) for message in messages]
    called_tools: list[str] = []
    last_tool_result: dict[str, Any] | None = None
    executed_text_calls: set[str] = set()
    tools_active = list(tools or [])
    if tools_active and time.monotonic() < _TOOLS_REJECTED_UNTIL:
        print("[Tool] tool calling disabled by provider plan; using text fallback")
        tools_active = []

    for _round in range(max_tool_rounds):
        request: dict[str, Any] = {
            "model": model,
            "messages": request_messages,
            **kwargs,
        }
        if tools_active:
            request["tools"] = tools_active
            request["tool_choice"] = "auto"
        try:
            response = await client.chat.completions.create(**request)
        except Exception as exc:
            if tools_active and _tools_maybe_unsupported(exc):
                # Loud on purpose: from here on the model CANNOT call any tool
                # natively. If this shows up in the logs, the configured
                # model/provider rejects tool definitions (e.g. plan limits);
                # BitMon degrades to parsing calls the model writes as text.
                print(f"[Tool] provider rejected tool definitions; retrying WITHOUT tools: {str(exc)[:200]}")
                _TOOLS_REJECTED_UNTIL = time.monotonic() + _TOOLS_REJECTED_TTL_SECONDS
                tools_active = []
                response = await client.chat.completions.create(
                    model=model,
                    messages=request_messages,
                    **kwargs,
                )
            else:
                raise

        message = response.choices[0].message
        tool_calls: list[Any] = list(getattr(message, "tool_calls", None) or [])
        if tool_calls:
            request_messages.append(_chat_message_to_dict(message))
        else:
            raw_content = getattr(message, "content", None) or ""
            answer = _strip_leaked_tool_markup(raw_content)
            recovered = _parse_leaked_tool_calls(raw_content, tools)
            if not recovered and answer:
                # A model without native tool calling announces the call as
                # plain text (e.g. "home_assistant(action='turn_off')"); that
                # must never be spoken — parse and execute it instead.
                recovered = _parse_pseudo_tool_calls(answer, tools)
            if not recovered:
                if answer:
                    return answer, called_tools
                if raw_content.strip():
                    # Leaked tool markup we could not parse: retry the round
                    # instead of surfacing an empty answer.
                    print("[Tool] model leaked unparseable tool-call markup; retrying round")
                    continue
                if last_tool_result is not None:
                    return _tool_result_answer(last_tool_result), called_tools
                return "", called_tools
            print(f"[Tool] recovered {len(recovered)} tool call(s) from text output")
            if not tools_active:
                # Degraded mode (no native tool calling): execute the parsed
                # calls and feed the results back as plain context, so the
                # next round can phrase the spoken answer.
                pending = []
                for tool_name, arguments in recovered:
                    key = f"{tool_name}::{json.dumps(arguments, sort_keys=True, ensure_ascii=False)}"
                    if key not in executed_text_calls:
                        executed_text_calls.add(key)
                        pending.append((tool_name, arguments))
                if not pending:
                    # The model repeated an already-executed call instead of
                    # phrasing the result: answer from the result we have.
                    if last_tool_result is not None:
                        return _tool_result_answer(last_tool_result), called_tools
                    return answer, called_tools
                for tool_name, arguments in pending:
                    result = await execute_tool_call(tool_name, arguments, user_request=user_request)
                    called_tools.append(tool_name)
                    last_tool_result = result
                    request_messages.append({
                        "role": "system",
                        "content": (
                            f"Tool '{tool_name}' was executed with arguments "
                            f"{json.dumps(arguments, ensure_ascii=False)}. "
                            f"Result: {json.dumps(result, ensure_ascii=False)[:1500]} "
                            "Answer the user now based on this result, in the mandatory "
                            "language, plain text only — never repeat the tool syntax."
                        ),
                    })
                continue
            # Native mode: execute the recovered calls exactly like structured ones.
            tool_calls = [
                {
                    "id": f"leaked_{_round}_{index}",
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(arguments, ensure_ascii=False),
                    },
                }
                for index, (tool_name, arguments) in enumerate(recovered)
            ]
            request_messages.append({"role": "assistant", "content": "", "tool_calls": tool_calls})
        for tool_call in tool_calls:
            tool_name, raw_arguments = _tool_call_function(tool_call)
            result = await execute_tool_call(tool_name, raw_arguments, user_request=user_request)
            called_tools.append(tool_name)
            last_tool_result = result
            request_messages.append({
                "role": "tool",
                "tool_call_id": _tool_call_id(tool_call),
                "name": tool_name,
                "content": json.dumps(result, ensure_ascii=False),
            })

    response = await client.chat.completions.create(
        model=model,
        messages=request_messages,
        **kwargs,
    )
    answer = _strip_leaked_tool_markup(response.choices[0].message.content or "")
    if answer:
        return answer, called_tools
    if last_tool_result is not None:
        return _tool_result_answer(last_tool_result), called_tools
    return "", called_tools


# Log labels: BitMon capabilities vs external integrations, so the launcher
# log shows at a glance what the model invoked and how it went.
TOOL_LOG_LABELS = {
    "screen_analyze": "[Capability] Screen analysis",
    "open_configuration": "[Capability] Open configuration",
    "home_assistant": "[Tool] Home Assistant (MCP)",
    "external_mcp": "[Tool] External MCP",
}


def _compact_json(value: Any, limit: int = 220) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(value)
    return text if len(text) <= limit else text[:limit] + "..."


async def execute_tool_call(
    name: str,
    raw_arguments: str | dict[str, Any] | None,
    user_request: str = "",
) -> dict[str, Any]:
    """Execute a function call and return a JSON-serializable result."""
    if isinstance(raw_arguments, str):
        try:
            arguments = json.loads(raw_arguments) if raw_arguments.strip() else {}
        except json.JSONDecodeError:
            arguments = {}
    elif isinstance(raw_arguments, dict):
        arguments = raw_arguments
    else:
        arguments = {}

    label = TOOL_LOG_LABELS.get(name, f"[Tool] {name}")
    print(f"{label} call {_compact_json(arguments)}")
    started = time.perf_counter()
    result = await _dispatch_tool_call(name, arguments, user_request)
    elapsed = time.perf_counter() - started
    if result.get("ok"):
        print(f"{label} ok in {elapsed:.2f}s")
    else:
        error = str(result.get("error") or "unknown error")[:200]
        print(f"{label} FAILED in {elapsed:.2f}s: {error}")
    return result


async def _dispatch_tool_call(
    name: str,
    arguments: dict[str, Any],
    user_request: str,
) -> dict[str, Any]:
    if name == "screen_analyze":
        return await analyze_screen(str(arguments.get("question") or ""), user_request=user_request)

    if name == "open_configuration":
        url = _config_url()
        opened = await asyncio.to_thread(webbrowser.open, url, 2)
        if opened:
            return {"ok": True, "url": url, "answer": f"Opening configuration page: {url}"}
        return {"ok": False, "url": url, "error": f"Could not open configuration page: {url}"}

    if name == "home_assistant":
        raw_targets = arguments.get("targets")
        if isinstance(raw_targets, str):
            targets = [raw_targets]
        elif isinstance(raw_targets, list):
            targets = [str(target) for target in raw_targets]
        else:
            targets = []
        raw_domain = str(arguments.get("domain") or "").strip()
        raw_value = arguments.get("value")
        return await execute_home_assistant_request(
            action=str(arguments.get("action") or "turn_on"),
            targets=targets,
            domain=raw_domain or None,
            value=raw_value if isinstance(raw_value, dict) else None,
            user_request=user_request,
        )

    if name == "external_mcp":
        from core.config_store import get_config

        config = get_config()
        requested_server = _safe_mcp_id(arguments.get("server_id"), "")
        tool_name = str(arguments.get("tool_name") or "").strip()
        tool_arguments = arguments.get("arguments")
        if not isinstance(tool_arguments, dict):
            tool_arguments = {}
        for server in _configured_external_mcps(config):
            if server["id"] == requested_server:
                if not tool_name:
                    return {"ok": False, "error": "Missing MCP tool_name."}
                try:
                    text = await call_mcp_tool(
                        server["url"],
                        tool_name,
                        tool_arguments,
                        auth_type=server.get("auth_type") or "none",
                        server_id=server["id"],
                    )
                except Exception as exc:
                    return {"ok": False, "error": str(exc), "server_id": server["id"], "tool_name": tool_name}
                return {"ok": True, "server_id": server["id"], "tool_name": tool_name, "answer": text}
        return {"ok": False, "error": f"Unknown or disabled MCP server: {requested_server}"}

    return {
        "ok": False,
        "error": f"Unknown tool: {name}",
    }
