"""Inworld Router chat adapter.

The Router is OpenAI-compatible and accepts the Base64 API key from Inworld
Portal directly as the SDK ``api_key`` (per Inworld's official drop-in guide),
so no custom Authorization header is needed for LLM calls. Only the separate
TTS REST endpoint still requires ``Authorization: Basic <key>`` — see
``inworld_basic_header`` below, used by services.inworld_tts.
"""

from __future__ import annotations

import base64
import re
from typing import Any

from openai import APIStatusError, AsyncOpenAI

from core.config import settings
from services.tool_runtime import create_chat_completion_with_tools


_client: AsyncOpenAI | None = None
_client_api_key: str = ""


def clean_inworld_key(api_key: str) -> str:
    """Strip an accidental 'Basic '/'Bearer ' prefix pasted with the key."""
    return re.sub(r"^(basic|bearer)\s+", "", str(api_key or "").strip(), flags=re.I).strip()


def inworld_basic_header(api_key: str) -> str:
    """Authorization value for Inworld REST endpoints (TTS) that use Basic auth."""
    key = clean_inworld_key(api_key)
    return f"Basic {key}" if key else ""


def inworld_key_format_warning(api_key: str) -> str:
    """User-facing warning when a key does not look like Inworld credentials."""
    value = str(api_key or "").strip()
    if not value:
        return "No Inworld API key is saved."
    raw = clean_inworld_key(value)
    try:
        decoded = base64.b64decode(raw + "=" * (-len(raw) % 4), validate=True)
    except Exception:
        return "The saved key does not look like the Base64 API key copied from Inworld Portal."
    if b":" not in decoded:
        return "The saved key is Base64, but it does not look like the API key pair from Inworld Portal."
    return ""


def router_client(api_key: str) -> AsyncOpenAI:
    """Cached OpenAI-compatible client for the Inworld Router."""
    global _client, _client_api_key
    key = clean_inworld_key(api_key)
    if _client is None or _client_api_key != key:
        _client = AsyncOpenAI(api_key=key, base_url=settings.INWORLD_ROUTER_BASE_URL)
        _client_api_key = key
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
    """Run one Inworld Router conversation turn. Returns (answer, tool names)."""
    return await create_chat_completion_with_tools(
        router_client(api_key),
        model=model,
        messages=messages,
        tools=tools,
        user_request=user_request,
        max_tokens=max_tokens,
    )


def friendly_inworld_error(exc: Exception, api_key: str = "") -> str:
    message = str(exc) or exc.__class__.__name__
    unauthorized = (isinstance(exc, APIStatusError) and exc.status_code == 401) or "unauthorized" in message.lower()
    if unauthorized:
        warning = inworld_key_format_warning(api_key)
        suffix = f" {warning}" if warning else " Check whether the saved key is active for Inworld API calls."
        return f"Inworld API unauthorized.{suffix}"
    return message
