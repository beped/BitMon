"""Credential storage helpers for BitMon secrets."""

from __future__ import annotations

from functools import lru_cache
from typing import Any


SERVICE_NAME = "BitMon"
LEGACY_SERVICE_NAME = "Digi" + "Mon"
INWORLD_API_KEY_SECRET = "inworld_api_key"
OPENAI_API_KEY_SECRET = "openai_api_key"
ANTHROPIC_API_KEY_SECRET = "anthropic_api_key"


class SecretStoreError(RuntimeError):
    """Raised when a secret cannot be written to the OS credential store."""


@lru_cache(maxsize=1)
def _keyring_module() -> Any | None:
    try:
        import keyring  # type: ignore[import-not-found]
    except ImportError:
        return None
    return keyring


def get_secret(name: str) -> str:
    """Return a secret from keyring, or an empty string when unavailable."""
    keyring = _keyring_module()
    if keyring is None:
        return ""
    try:
        current = keyring.get_password(SERVICE_NAME, name) or ""
        if current:
            return current
        legacy = keyring.get_password(LEGACY_SERVICE_NAME, name) or ""
        if legacy:
            try:
                keyring.set_password(SERVICE_NAME, name, legacy)
            except Exception:
                pass
        return legacy
    except Exception:
        return ""


def set_secret(name: str, value: str) -> None:
    """Persist a secret in the OS credential store."""
    secret = str(value or "").strip()
    if not secret:
        return

    keyring = _keyring_module()
    if keyring is None:
        raise SecretStoreError(
            "The keyring package is not installed. Install backend requirements before saving secrets."
        )

    try:
        keyring.set_password(SERVICE_NAME, name, secret)
    except Exception as exc:
        raise SecretStoreError(
            "Could not save the secret in the OS credential store."
        ) from exc


def delete_secret(name: str) -> None:
    """Delete a secret from the OS credential store when possible."""
    keyring = _keyring_module()
    if keyring is None:
        return
    try:
        keyring.delete_password(SERVICE_NAME, name)
    except Exception:
        return
