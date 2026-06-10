"""Local security and redaction helpers for the BitMon backend."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
from pathlib import Path

from fastapi import Request


HEADER_NAME = "X-BitMon-Token"
COOKIE_NAME = "bitmon_token"
LOCAL_TOKEN_PATH = Path(__file__).resolve().parent.parent / ".local_token"
LEGACY_NAME = "digi" + "mon"

SENSITIVE_EXACT_PATHS = {"/api/config"}
SENSITIVE_PREFIXES = (
    "/api/runtime/",
    "/api/mcps/",
    "/api/home-assistant/",
)

_AUTH_HEADER_RE = re.compile(
    r"(?i)(authorization\s*[:=]\s*)(bearer|basic)?\s*[A-Za-z0-9._~+/=-]{8,}"
)
_TOKEN_QUERY_RE = re.compile(r"(?i)([?&](?:token|api_key|key|secret|access_token)=)[^&\s]+")
_PRIVATE_URL_RE = re.compile(r"(?i)(https?://[^\s\"']*private_[^\s\"']*)")
_API_KEY_RE = re.compile(r"(?i)\b(?:sk|rk|pk|inworld)[-_A-Za-z0-9]{16,}\b")


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None and name.startswith("BITMON_"):
        value = os.environ.get(f"{LEGACY_NAME.upper()}_{name.removeprefix('BITMON_')}")
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_local_token() -> str:
    """Return the local API token, creating it on first run."""
    try:
        token = LOCAL_TOKEN_PATH.read_text(encoding="utf-8").strip()
        if token:
            return token
    except OSError:
        pass

    token = secrets.token_urlsafe(32)
    LOCAL_TOKEN_PATH.write_text(token + "\n", encoding="utf-8")
    try:
        LOCAL_TOKEN_PATH.chmod(0o600)
    except OSError:
        pass
    return token


def is_sensitive_api_path(path: str) -> bool:
    """Return whether a route must require the local token."""
    if path.startswith("/api/mcps/oauth/callback/"):
        return False
    return path in SENSITIVE_EXACT_PATHS or any(path.startswith(prefix) for prefix in SENSITIVE_PREFIXES)


def request_has_valid_local_token(request: Request) -> bool:
    """Validate a token supplied by header or cookie."""
    expected = get_local_token()
    supplied = request.headers.get(HEADER_NAME) or request.cookies.get(COOKIE_NAME) or ""
    return secrets.compare_digest(supplied, expected)


def inject_local_token(html: str) -> str:
    """Inject the local token into the config page served by the backend."""
    snippet = (
        "<script>"
        f"window.BITMON_LOCAL_TOKEN = {json.dumps(get_local_token())};"
        f"window.BITMON_TOKEN_HEADER = {json.dumps(HEADER_NAME)};"
        "</script>"
    )
    if "</head>" in html:
        return html.replace("</head>", snippet + "\n</head>", 1)
    return snippet + html


def docs_enabled() -> bool:
    return _bool_env("BITMON_ENABLE_DOCS", False)


def mcp_enabled() -> bool:
    return _bool_env("BITMON_ENABLE_MCP", False)


def allowed_cors_origins() -> list[str]:
    raw = os.environ.get("BITMON_ALLOWED_ORIGINS") or os.environ.get(f"{LEGACY_NAME.upper()}_ALLOWED_ORIGINS") or ""
    origins = [item.strip() for item in raw.split(",") if item.strip()]
    if origins:
        return origins
    return [
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ]


def redact_for_log(value: object) -> str:
    """Mask common secret shapes before they are written to logs."""
    text = str(value)
    text = _AUTH_HEADER_RE.sub(r"\1[redacted]", text)
    text = _TOKEN_QUERY_RE.sub(r"\1[redacted]", text)
    text = _PRIVATE_URL_RE.sub("[redacted-private-url]", text)
    text = _API_KEY_RE.sub("[redacted-secret]", text)
    return text


class RedactingLogFilter(logging.Filter):
    """Logging filter that redacts secrets from message text and args."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_for_log(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    key: redact_for_log(value) if isinstance(value, str) else value
                    for key, value in record.args.items()
                }
            else:
                record.args = tuple(
                    redact_for_log(arg) if isinstance(arg, str) else arg
                    for arg in record.args
                )
        return True
