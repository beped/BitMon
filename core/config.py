"""Centralized BitMon backend settings.

These are in-code defaults (the runtime values live in the config UI / keyring).
No ``.env`` file is read; the legacy dotenv support was removed."""

from pydantic_settings import BaseSettings, SettingsConfigDict

from core.secret_store import INWORLD_API_KEY_SECRET, get_secret, set_secret


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # Immutable output-format rules that are always applied and cannot be overridden
    # by the frontend. They keep model responses safe for TTS playback.
    SYSTEM_PROMPT_CORE: str = (
        "You are a real-time voice assistant. Your output is sent directly to a TTS engine, "
        "so follow these MANDATORY formatting rules:\n"
        "- Respond ONLY in plain text, ready for TTS playback.\n"
        "- NO markdown, asterisks, bold, italic, bullet points, headings, or code blocks.\n"
        "- NO emojis, emoticons, or special characters.\n"
        "- NO line breaks (\\n) - keep responses as a single flowing paragraph.\n"
        "- Keep answers short and direct (1 to 3 sentences max). Be conversational.\n"
        "- Follow the MANDATORY LANGUAGE configured by the backend session, even if the user speaks another language.\n"
    )

    # --- Inworld APIs ---
    INWORLD_VOICE: str = "Sarah"       # TTS voice (Sarah, etc.)
    INWORLD_ROUTER_BASE_URL: str = "https://api.inworld.ai/v1"
    INWORLD_ROUTER_VISION_MODEL: str = "openai/gpt-4o-mini"

    # --- Kokoro TTS ---
    KOKORO_LANG: str = "p"               # 'p' = Portuguese (pt-BR)
    KOKORO_VOICE: str = "pf_dora"        # Default pt-BR voice; change from the UI if needed
    KOKORO_SPEED: float = 1.0


settings = Settings()


def get_inworld_api_key_source() -> str:
    """Return the source currently used for the Inworld API key."""
    return "keyring" if get_secret(INWORLD_API_KEY_SECRET) else "none"


def get_inworld_api_key() -> str:
    """Return the Inworld API key from the OS credential store."""
    return get_secret(INWORLD_API_KEY_SECRET)


def get_inworld_keyring_api_key() -> str:
    """Return the raw Inworld API key stored in keyring, without fallback."""
    return get_secret(INWORLD_API_KEY_SECRET)


def set_inworld_api_key(api_key: str) -> None:
    """Persist the Inworld API key in the OS credential store."""
    set_secret(INWORLD_API_KEY_SECRET, api_key)


def is_inworld_api_key_configured() -> bool:
    """Return whether an Inworld API key is available without exposing it."""
    return bool(get_inworld_api_key())
