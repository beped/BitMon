"""Secure storage helpers for external MCP authentication."""

from __future__ import annotations

import json
import re
from typing import Any

from mcp.client.auth import TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from core.secret_store import delete_secret, get_secret, set_secret


def normalize_mcp_server_id(value: Any, fallback: str = "mcp_server") -> str:
    text = str(value or fallback).strip().lower()
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = text.strip("_")
    return text or fallback


def bearer_secret_name(server_id: str) -> str:
    return f"mcp_bearer_token:{normalize_mcp_server_id(server_id)}"


def oauth_tokens_secret_name(server_id: str) -> str:
    return f"mcp_oauth_tokens:{normalize_mcp_server_id(server_id)}"


def oauth_client_secret_name(server_id: str) -> str:
    return f"mcp_oauth_client:{normalize_mcp_server_id(server_id)}"


def get_bearer_token(server_id: str) -> str:
    return get_secret(bearer_secret_name(server_id))


def set_bearer_token(server_id: str, token: str) -> None:
    set_secret(bearer_secret_name(server_id), token)


def delete_bearer_token(server_id: str) -> None:
    delete_secret(bearer_secret_name(server_id))


def is_bearer_token_configured(server_id: str) -> bool:
    return bool(get_bearer_token(server_id))


def is_oauth_connected(server_id: str) -> bool:
    return bool(get_secret(oauth_tokens_secret_name(server_id)))


def delete_oauth_credentials(server_id: str) -> None:
    delete_secret(oauth_tokens_secret_name(server_id))
    delete_secret(oauth_client_secret_name(server_id))


class KeyringOAuthTokenStorage(TokenStorage):
    """MCP OAuth token storage backed by the OS credential store."""

    def __init__(self, server_id: str):
        self.server_id = normalize_mcp_server_id(server_id)

    async def get_tokens(self) -> OAuthToken | None:
        raw = get_secret(oauth_tokens_secret_name(self.server_id))
        if not raw:
            return None
        try:
            return OAuthToken.model_validate_json(raw)
        except Exception:
            return None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        set_secret(oauth_tokens_secret_name(self.server_id), tokens.model_dump_json(exclude_none=True))

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        raw = get_secret(oauth_client_secret_name(self.server_id))
        if not raw:
            return None
        try:
            return OAuthClientInformationFull.model_validate_json(raw)
        except Exception:
            return None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        payload = json.dumps(client_info.model_dump(mode="json", exclude_none=True), ensure_ascii=False)
        set_secret(oauth_client_secret_name(self.server_id), payload)
