"""Authentication helpers for Inworld API calls."""

from __future__ import annotations

import base64
import re

from openai import AsyncOpenAI


def _raw_inworld_key(api_key: str) -> str:
    return re.sub(r"^(basic|bearer)\s+", "", str(api_key or "").strip(), flags=re.I).strip()


def inworld_authorization_header(api_key: str) -> str:
    """Return an Authorization header value accepted by Inworld APIs."""
    value = str(api_key or "").strip()
    if not value:
        return ""
    lower_value = value.lower()
    if lower_value.startswith("basic ") or lower_value.startswith("bearer "):
        return value
    return f"Basic {value}"


def inworld_key_format_warning(api_key: str) -> str:
    """Return a user-facing warning when a key does not look like Inworld Basic credentials."""
    value = str(api_key or "").strip()
    if not value:
        return "No Inworld API key is saved."
    if value.lower().startswith("bearer "):
        return "The saved key is a Bearer token, but this backend uses Inworld Basic/Base64 API credentials."

    raw = _raw_inworld_key(value)
    try:
        decoded = base64.b64decode(raw + "=" * (-len(raw) % 4), validate=True)
    except Exception:
        return "The saved key does not look like the Basic/Base64 authorization signature copied from Inworld Portal."
    if b":" not in decoded:
        return "The saved key is Base64, but it does not look like a Basic credential pair from Inworld Portal."
    return ""


def create_inworld_router_client(api_key: str, base_url: str) -> AsyncOpenAI:
    """Create an OpenAI-compatible client using Inworld Basic auth."""
    return AsyncOpenAI(
        api_key="inworld-router",
        base_url=base_url,
        default_headers={"Authorization": inworld_authorization_header(api_key)},
    )
