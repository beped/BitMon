"""External MCP client helpers."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from mcp import ClientSession, types
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.auth import OAuthClientMetadata

from core.mcp_auth_store import KeyringOAuthTokenStorage, get_bearer_token, normalize_mcp_server_id


def _validate_mcp_url(url: str) -> str:
    clean = url.strip()
    parsed = urlparse(clean)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("URL must start with http:// or https://.")
    if not parsed.netloc:
        raise ValueError("Incomplete URL.")
    return clean


def _bearer_headers(server_id: str, bearer_token: str = "") -> dict[str, str] | None:
    token = str(bearer_token or "").strip() or get_bearer_token(server_id)
    if not token:
        return None
    value = token if token.lower().startswith(("bearer ", "basic ")) else f"Bearer {token}"
    return {"Authorization": value}


def _oauth_provider(
    url: str,
    server_id: str,
    *,
    redirect_uri: str | None = None,
    redirect_handler: Any = None,
    callback_handler: Any = None,
) -> OAuthClientProvider:
    metadata = OAuthClientMetadata(
        redirect_uris=[redirect_uri or "http://127.0.0.1:8000/api/mcps/oauth/callback"],
        client_name="BitMon",
        token_endpoint_auth_method="none",
    )
    return OAuthClientProvider(
        server_url=url,
        client_metadata=metadata,
        storage=KeyringOAuthTokenStorage(server_id),
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )


def _auth_kwargs(
    url: str,
    *,
    auth_type: str = "none",
    server_id: str = "",
    bearer_token: str = "",
    oauth_redirect_uri: str | None = None,
    oauth_redirect_handler: Any = None,
    oauth_callback_handler: Any = None,
) -> dict[str, Any]:
    auth = str(auth_type or "none").strip().lower()
    safe_id = normalize_mcp_server_id(server_id or url)
    if auth == "bearer":
        headers = _bearer_headers(safe_id, bearer_token)
        return {"headers": headers} if headers else {}
    if auth == "oauth":
        return {
            "auth": _oauth_provider(
                url,
                safe_id,
                redirect_uri=oauth_redirect_uri,
                redirect_handler=oauth_redirect_handler,
                callback_handler=oauth_callback_handler,
            )
        }
    return {}


def _tool_payload(tool: Any) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": getattr(tool, "inputSchema", None) or {},
    }


async def validate_streamable_http_mcp(
    url: str,
    timeout_seconds: float = 8.0,
    *,
    auth_type: str = "none",
    server_id: str = "",
    bearer_token: str = "",
    oauth_redirect_uri: str | None = None,
    oauth_redirect_handler: Any = None,
    oauth_callback_handler: Any = None,
) -> dict[str, Any]:
    """Connect to a Streamable HTTP MCP endpoint and return a small status payload."""
    clean_url = _validate_mcp_url(url)
    async with streamablehttp_client(
        clean_url,
        timeout=timeout_seconds,
        sse_read_timeout=timeout_seconds,
        **_auth_kwargs(
            clean_url,
            auth_type=auth_type,
            server_id=server_id,
            bearer_token=bearer_token,
            oauth_redirect_uri=oauth_redirect_uri,
            oauth_redirect_handler=oauth_redirect_handler,
            oauth_callback_handler=oauth_callback_handler,
        ),
    ) as (read_stream, write_stream, get_session_id):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            listed = await session.list_tools()
            tools = listed.tools or []
            tool_list = [_tool_payload(tool) for tool in tools]
            sample = [
                {
                    "name": tool["name"],
                    "description": tool["description"][:160],
                }
                for tool in tool_list[:8]
            ]
            return {
                "ok": True,
                "url": clean_url,
                "session_id": get_session_id(),
                "tool_count": len(tools),
                "sample_tools": sample,
                "tools": tool_list,
            }


async def validate_home_assistant_mcp(url: str) -> dict[str, Any]:
    try:
        from tools.home_assistant import refresh_home_assistant_tools_cache

        return await refresh_home_assistant_tools_cache(_validate_mcp_url(url))
    except Exception as exc:
        return {
            "ok": False,
            "url": url.strip(),
            "error": str(exc),
        }


def tool_result_to_text(result: Any) -> str:
    parts: list[str] = []
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            parts.append(str(text))
        else:
            try:
                parts.append(item.model_dump_json())
            except Exception:
                parts.append(str(item))
    if not parts:
        try:
            return result.model_dump_json()
        except Exception:
            return str(result)
    return "\n".join(parts)


async def list_mcp_tools(
    url: str,
    timeout_seconds: float = 8.0,
    *,
    auth_type: str = "none",
    server_id: str = "",
) -> list[dict[str, Any]]:
    clean_url = _validate_mcp_url(url)
    async with streamablehttp_client(
        clean_url,
        timeout=timeout_seconds,
        sse_read_timeout=timeout_seconds,
        **_auth_kwargs(clean_url, auth_type=auth_type, server_id=server_id),
    ) as (read_stream, write_stream, _get_session_id):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            listed = await session.list_tools()
            return [_tool_payload(tool) for tool in listed.tools or []]


async def call_mcp_tool(
    url: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    timeout_seconds: float = 20.0,
    *,
    auth_type: str = "none",
    server_id: str = "",
) -> str:
    clean_url = _validate_mcp_url(url)
    async with streamablehttp_client(
        clean_url,
        timeout=timeout_seconds,
        sse_read_timeout=timeout_seconds,
        **_auth_kwargs(clean_url, auth_type=auth_type, server_id=server_id),
    ) as (read_stream, write_stream, _get_session_id):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.send_request(
                types.ClientRequest(
                    types.CallToolRequest(
                        params=types.CallToolRequestParams(
                            name=tool_name,
                            arguments=arguments or {},
                        )
                    )
                ),
                types.CallToolResult,
            )
            return tool_result_to_text(result)
