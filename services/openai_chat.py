"""OpenAI chat adapter (Chat Completions + the shared BitMon tool loop)."""

from __future__ import annotations

from typing import Any

from openai import APIStatusError, AsyncOpenAI

from services.tool_runtime import create_chat_completion_with_tools


_client: AsyncOpenAI | None = None
_client_api_key: str = ""


def _get_client(api_key: str) -> AsyncOpenAI:
    global _client, _client_api_key
    if _client is None or _client_api_key != api_key:
        _client = AsyncOpenAI(api_key=api_key)
        _client_api_key = api_key
    return _client


async def complete(
    api_key: str,
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    user_request: str,
    max_tokens: int,
) -> tuple[str, list[str]]:
    """Run one OpenAI conversation turn. Returns (answer, called tool names)."""
    return await create_chat_completion_with_tools(
        _get_client(api_key),
        model=model,
        messages=messages,
        tools=tools,
        user_request=user_request,
        max_tokens=max_tokens,
    )


def friendly_openai_error(exc: Exception) -> str:
    if isinstance(exc, APIStatusError) and exc.status_code == 401:
        return "OpenAI API key is invalid or missing. Save a valid key in the configuration UI."
    if isinstance(exc, APIStatusError) and exc.status_code == 429:
        return "OpenAI API rate limit or quota reached. Check your OpenAI account usage."
    return str(exc) or exc.__class__.__name__
