"""Anthropic (Claude) chat adapter using the official ``anthropic`` SDK.

BitMon's chat pipeline speaks OpenAI Chat Completions internally (history as
``role``/``content`` dicts, tools in the Chat Completions function format).
This module converts that into Anthropic Messages API calls and runs the
manual agentic loop, executing BitMon tools through the same
``execute_tool_call`` funnel used by the other providers.
"""

from __future__ import annotations

import json
from typing import Any

from services.tool_runtime import _tool_result_answer, execute_tool_call


MAX_TOOL_ROUNDS = 4


def _anthropic_module():
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError(
            "The 'anthropic' package is not installed. "
            "Run install.bat (or pip install anthropic) to use the Claude provider."
        ) from exc
    return anthropic


def split_system_and_messages(
    messages: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Split OpenAI-style history into (system_prompt, anthropic_messages)."""
    system_parts: list[str] = []
    converted: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        if role == "system":
            system_parts.append(content)
        elif role in {"user", "assistant"}:
            converted.append({"role": role, "content": content})
    # The Messages API requires the first message to be a user turn.
    while converted and converted[0]["role"] != "user":
        converted.pop(0)
    if not converted:
        converted.append({"role": "user", "content": "..."})
    return "\n".join(system_parts), converted


def convert_chat_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Chat Completions tool definitions to Anthropic tool format."""
    converted: list[dict[str, Any]] = []
    for tool in tools or []:
        function = tool.get("function") if isinstance(tool, dict) else None
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        converted.append({
            "name": name,
            "description": str(function.get("description") or ""),
            "input_schema": function.get("parameters") or {"type": "object", "properties": {}},
        })
    return converted


def _text_from_content(content: list[Any]) -> str:
    parts: list[str] = []
    for block in content or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    return " ".join(part.strip() for part in parts if part.strip()).strip()


async def complete(
    api_key: str,
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    user_request: str,
    max_tokens: int,
) -> tuple[str, list[str]]:
    """Run one Claude conversation turn, executing BitMon tools when requested.

    Same interface as the other provider adapters: returns (answer, tool names).
    """
    anthropic = _anthropic_module()
    client = anthropic.AsyncAnthropic(api_key=api_key)
    system_prompt, request_messages = split_system_and_messages(messages)
    anthropic_tools = convert_chat_tools(tools)
    called_tools: list[str] = []
    last_tool_result: dict[str, Any] | None = None

    request: dict[str, Any] = {
        "model": model,
        "max_tokens": max(32, int(max_tokens)),
        "messages": request_messages,
    }
    if system_prompt:
        request["system"] = system_prompt
    if anthropic_tools:
        request["tools"] = anthropic_tools

    try:
        for _round in range(MAX_TOOL_ROUNDS):
            response = await client.messages.create(**request)
            tool_uses = [block for block in response.content if getattr(block, "type", None) == "tool_use"]
            if response.stop_reason != "tool_use" or not tool_uses:
                answer = _text_from_content(response.content)
                if not answer and last_tool_result is not None:
                    answer = _tool_result_answer(last_tool_result)
                return answer, called_tools

            request_messages.append({"role": "assistant", "content": response.content})
            tool_results: list[dict[str, Any]] = []
            for tool_use in tool_uses:
                arguments = tool_use.input if isinstance(tool_use.input, dict) else {}
                result = await execute_tool_call(tool_use.name, arguments, user_request=user_request)
                called_tools.append(tool_use.name)
                last_tool_result = result
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": json.dumps(result, ensure_ascii=False),
                    "is_error": not bool(result.get("ok")),
                })
            request_messages.append({"role": "user", "content": tool_results})

        # Tool-round budget exhausted: ask for a final text-only answer.
        request.pop("tools", None)
        response = await client.messages.create(**request)
        answer = _text_from_content(response.content)
        if answer:
            return answer, called_tools
        if last_tool_result is not None:
            return _tool_result_answer(last_tool_result), called_tools
        return "", called_tools
    finally:
        await client.close()


def friendly_anthropic_error(exc: Exception) -> str:
    try:
        import anthropic
    except ImportError:
        return str(exc) or exc.__class__.__name__
    if isinstance(exc, anthropic.AuthenticationError):
        return "Anthropic API key is invalid or missing. Save a valid key in the configuration UI."
    if isinstance(exc, anthropic.RateLimitError):
        return "Anthropic API rate limit reached. Wait a moment and try again."
    if isinstance(exc, anthropic.NotFoundError):
        return "Anthropic model not found. Check the model id in Model > LLM (e.g. claude-opus-4-8)."
    return str(exc) or exc.__class__.__name__
