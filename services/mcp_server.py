"""BitMon MCP server.

The FastAPI backend mounts this server at /mcp when the MCP SDK is installed.
It exposes the same safe local tools used by the voice assistant.
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from core.config_store import get_config
from services.tool_runtime import execute_tool_call
from tools.screen_tools import analyze_screen, capture_screen


mcp = FastMCP(
    "BitMon Tools",
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
)


@mcp.tool()
def screen_capture(monitor: int = 1) -> str:
    """Capture the user's screen and return PNG metadata plus base64 image data."""
    if not get_config().get("tools", {}).get("screen_analysis", False):
        return json.dumps(
            {"ok": False, "error": "Screen analysis is disabled in BitMon config."},
            ensure_ascii=False,
        )
    screenshot = capture_screen(monitor=monitor)
    return json.dumps(
        {
            "ok": True,
            "width": screenshot.width,
            "height": screenshot.height,
            "mime_type": screenshot.mime_type,
            "data_base64": screenshot.data_base64,
        },
        ensure_ascii=False,
    )


@mcp.tool()
async def screen_analyze(question: str = "What is visible on my screen?") -> str:
    """Capture and analyze the user's screen with a vision model."""
    return json.dumps(await analyze_screen(question), ensure_ascii=False)


@mcp.tool()
async def open_configuration() -> str:
    """Open the BitMon configuration page in the user's browser."""
    if not get_config().get("tools", {}).get("open_configuration", False):
        return json.dumps(
            {"ok": False, "error": "Open configuration is disabled in BitMon config."},
            ensure_ascii=False,
        )
    return json.dumps(await execute_tool_call("open_configuration", {}, user_request=""), ensure_ascii=False)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
