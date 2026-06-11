"""LM Studio (local OpenAI-compatible server) chat adapter."""

from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI

from services.tool_runtime import create_chat_completion_with_tools


_client: AsyncOpenAI | None = None
_client_base_url: str = ""


def _get_client(base_url: str) -> AsyncOpenAI:
    global _client, _client_base_url
    if _client is None or _client_base_url != base_url:
        _client = AsyncOpenAI(api_key="lm-studio", base_url=base_url)
        _client_base_url = base_url
    return _client


async def complete(
    base_url: str,
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    user_request: str,
    max_tokens: int,
    temperature: float = 0.7,
) -> tuple[str, list[str]]:
    """Run one LM Studio conversation turn. Returns (answer, called tool names)."""
    return await create_chat_completion_with_tools(
        _get_client(base_url.rstrip("/")),
        model=model,
        messages=messages,
        tools=tools,
        user_request=user_request,
        temperature=temperature,
        max_tokens=max_tokens,
    )
