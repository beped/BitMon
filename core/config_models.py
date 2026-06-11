"""Pydantic validation models for BitMon runtime configuration."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from core.config_defaults import CURRENT_CONFIG_VERSION


LANGUAGE_ALIASES = {
    "pt_br": "pt-BR",
    "ptbr": "pt-BR",
    "en_us": "en-US",
    "enus": "en-US",
}


class ConfigValidationError(ValueError):
    """Raised when a user config update is invalid."""


def _non_empty(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text or default


def _language(value: Any, default: str) -> str:
    text = str(value or default).strip()
    text = LANGUAGE_ALIASES.get(text.lower().replace("-", "_"), text)
    return text or default


class CharacterConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = "Pugna"
    personality_prompt: str = "Personality: helpful, concise, friendly virtual companion with a playful BitMon energy."

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value: Any) -> str:
        return _non_empty(value, "Pugna")

    @field_validator("personality_prompt", mode="before")
    @classmethod
    def validate_prompt(cls, value: Any) -> str:
        return _non_empty(value, "Personality: helpful, concise, friendly virtual companion.")


class InworldConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str = "deepseek-v4-flash"
    voice: str = "Hana"
    voice_response: bool = True
    max_tokens: int = Field(default=300, ge=32, le=4096)
    stt_provider: Literal["whisper"] = "whisper"
    whisper_model: Literal["tiny", "base", "small", "medium"] = "base"
    stt_language: str = "pt"
    tts_language: str = "en"
    # Whether this provider's chat model accepts images, for screen analysis.
    # "auto" = decide from the model name; "on"/"off" = force.
    vision: Literal["auto", "on", "off"] = "auto"

    @field_validator("model", mode="before")
    @classmethod
    def validate_model(cls, value: Any) -> str:
        return _non_empty(value, "deepseek-v4-flash")

    @field_validator("vision", mode="before")
    @classmethod
    def validate_vision(cls, value: Any) -> str:
        text = str(value or "auto").strip().lower()
        return text if text in {"auto", "on", "off"} else "auto"

    @field_validator("voice", mode="before")
    @classmethod
    def validate_voice(cls, value: Any) -> str:
        return _non_empty(value, "Hana")

    @field_validator("stt_provider", mode="before")
    @classmethod
    def validate_stt_provider(cls, _value: Any) -> str:
        return "whisper"

    @field_validator("whisper_model", mode="before")
    @classmethod
    def validate_whisper_model(cls, value: Any) -> str:
        model = str(value or "base").strip().lower()
        return model if model in {"tiny", "base", "small", "medium"} else "base"

    @field_validator("stt_language", "tts_language", mode="before")
    @classmethod
    def validate_language(cls, value: Any) -> str:
        return _language(value, "pt")


class LocalConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    base_url: str = "http://127.0.0.1:1234/v1"
    model: str = "local-model"
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=220, ge=32, le=4096)
    voice_response: bool = True
    stt_provider: Literal["whisper"] = "whisper"
    whisper_model: Literal["tiny", "base", "small", "medium"] = "base"
    stt_language: str = "pt"
    tts_language: str = "pt"
    kokoro_model: str = "hexgrad/Kokoro-82M"
    kokoro_lang: str = "p"
    kokoro_voice: str = "pf_dora"
    kokoro_speed: float = Field(default=1.0, ge=0.4, le=2.0)
    # Screen-analysis vision support for the local model. "auto" decides from the
    # model name; "on"/"off" force it. vision_model optionally points screen
    # analysis at a separate local VLM (e.g. a llava model loaded in LM Studio).
    vision: Literal["auto", "on", "off"] = "auto"
    vision_model: str = ""

    @field_validator("base_url", mode="before")
    @classmethod
    def validate_base_url(cls, value: Any) -> str:
        return _non_empty(value, "http://127.0.0.1:1234/v1")

    @field_validator("vision", mode="before")
    @classmethod
    def validate_vision(cls, value: Any) -> str:
        text = str(value or "auto").strip().lower()
        return text if text in {"auto", "on", "off"} else "auto"

    @field_validator("vision_model", mode="before")
    @classmethod
    def validate_vision_model(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("model", mode="before")
    @classmethod
    def validate_model(cls, value: Any) -> str:
        return _non_empty(value, "local-model")

    @field_validator("stt_provider", mode="before")
    @classmethod
    def validate_stt_provider(cls, _value: Any) -> str:
        return "whisper"

    @field_validator("whisper_model", mode="before")
    @classmethod
    def validate_whisper_model(cls, value: Any) -> str:
        model = str(value or "base").strip().lower()
        return model if model in {"tiny", "base", "small", "medium"} else "base"

    @field_validator("stt_language", "tts_language", mode="before")
    @classmethod
    def validate_language(cls, value: Any) -> str:
        return _language(value, "pt")

    @field_validator("kokoro_model", mode="before")
    @classmethod
    def validate_kokoro_model(cls, value: Any) -> str:
        return _non_empty(value, "hexgrad/Kokoro-82M")

    @field_validator("kokoro_lang", mode="before")
    @classmethod
    def validate_kokoro_lang(cls, value: Any) -> str:
        return _non_empty(value, "p")

    @field_validator("kokoro_voice", mode="before")
    @classmethod
    def validate_kokoro_voice(cls, value: Any) -> str:
        return _non_empty(value, "pf_dora")


LLM_PROVIDERS = {"inworld", "local", "openai", "anthropic"}


class OpenAiConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str = "gpt-4o-mini"
    max_tokens: int = Field(default=300, ge=32, le=4096)

    @field_validator("model", mode="before")
    @classmethod
    def validate_model(cls, value: Any) -> str:
        return _non_empty(value, "gpt-4o-mini")


class AnthropicConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str = "claude-opus-4-8"
    max_tokens: int = Field(default=300, ge=32, le=4096)

    @field_validator("model", mode="before")
    @classmethod
    def validate_model(cls, value: Any) -> str:
        return _non_empty(value, "claude-opus-4-8")


class LlmConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: Literal["inworld", "local", "openai", "anthropic"] = "inworld"

    @field_validator("provider", mode="before")
    @classmethod
    def validate_provider(cls, value: Any) -> str:
        provider = str(value or "inworld").strip().lower()
        return provider if provider in LLM_PROVIDERS else "inworld"


class TtsConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    provider: Literal["inworld", "kokoro"] = "inworld"

    @field_validator("provider", mode="before")
    @classmethod
    def validate_provider(cls, value: Any) -> str:
        provider = str(value or "inworld").strip().lower()
        if provider == "local":
            provider = "kokoro"
        return provider if provider in {"inworld", "kokoro"} else "inworld"


class SpeechConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    stt_language: str = "pt"
    tts_language: str = "pt"

    @field_validator("stt_language", "tts_language", mode="before")
    @classmethod
    def validate_language(cls, value: Any) -> str:
        return _language(value, "pt")


class WhisperConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: Literal["tiny", "base", "small", "medium"] = "base"

    @field_validator("model", mode="before")
    @classmethod
    def validate_model(cls, value: Any) -> str:
        model = str(value or "base").strip().lower()
        return model if model in {"tiny", "base", "small", "medium"} else "base"


class MicrophoneConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    gain: float = Field(default=10.0, ge=0.0, le=50.0)
    vad_threshold: float = Field(default=0.004, ge=0.0001, le=0.2)
    whisper_hotkey: str = "f8"
    whisper_hotkeys: list[str] = Field(default_factory=lambda: ["f8"])

    @field_validator("whisper_hotkey", mode="before")
    @classmethod
    def validate_hotkey(cls, value: Any) -> str:
        return _non_empty(value, "f8").lower()

    @field_validator("whisper_hotkeys", mode="before")
    @classmethod
    def validate_hotkeys(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            items = value.split(",")
        elif isinstance(value, list):
            items = value
        else:
            items = []
        normalized: list[str] = []
        seen: set[str] = set()
        for item in items:
            hotkey = str(item or "").strip().lower()
            if hotkey and hotkey not in seen:
                seen.add(hotkey)
                normalized.append(hotkey)
        return normalized or ["f8"]


class WakeWordConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    engine: Literal["openwakeword"] = "openwakeword"
    model_names: list[str] = Field(default_factory=lambda: ["hey jarvis"])
    model_paths: list[str] = Field(default_factory=list)
    threshold: float = Field(default=0.5, ge=0.05, le=0.99)
    vad_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    cooldown_seconds: float = Field(default=2.0, ge=0.0, le=30.0)
    activation_timeout_seconds: float = Field(default=1.0, ge=0.1, le=10.0)
    command_timeout_seconds: float = Field(default=8.0, ge=1.0, le=30.0)
    command_silence_seconds: float = Field(default=0.3, ge=0.1, le=5.0)
    preroll_seconds: float = Field(default=1.5, ge=0.0, le=5.0)
    auto_download_models: bool = True
    selected_model: str = "builtin:hey_jarvis"

    @field_validator("model_names", "model_paths", mode="before")
    @classmethod
    def validate_list(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            items = value.split(",")
        elif isinstance(value, list):
            items = value
        else:
            items = []
        normalized: list[str] = []
        seen: set[str] = set()
        for item in items:
            text = str(item or "").strip()
            if text and text not in seen:
                seen.add(text)
                normalized.append(text)
        return normalized


class OverlayConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    enabled: bool = True
    always_on_top: bool = True

    @field_validator("enabled", mode="before")
    @classmethod
    def force_enabled(cls, _value: Any) -> bool:
        return True


class DebugConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    user_subtitle: bool = False
    replay_audio: bool = False


class ToolsConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    screen_analysis: bool = True
    open_configuration: bool = True


class UiConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    locale: str = "en-US"

    @field_validator("locale", mode="before")
    @classmethod
    def validate_locale(cls, value: Any) -> str:
        text = str(value or "en-US").strip()
        text = LANGUAGE_ALIASES.get(text.lower().replace("-", "_"), text)
        if re.fullmatch(r"[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,8})*", text):
            return text
        return "en-US"


class SecretsConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    inworld_api_key_configured: bool = False
    inworld_api_key_source: str = "none"
    openai_api_key_configured: bool = False
    anthropic_api_key_configured: bool = False


class BitMonConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    config_version: int = CURRENT_CONFIG_VERSION
    provider: Literal["inworld", "local", "openai", "anthropic"] = "inworld"
    llm: LlmConfig = Field(default_factory=LlmConfig)
    tts: TtsConfig = Field(default_factory=TtsConfig)
    speech: SpeechConfig = Field(default_factory=SpeechConfig)
    whisper: WhisperConfig = Field(default_factory=WhisperConfig)
    character: CharacterConfig = Field(default_factory=CharacterConfig)
    inworld: InworldConfig = Field(default_factory=InworldConfig)
    local: LocalConfig = Field(default_factory=LocalConfig)
    openai: OpenAiConfig = Field(default_factory=OpenAiConfig)
    anthropic: AnthropicConfig = Field(default_factory=AnthropicConfig)
    microphone: MicrophoneConfig = Field(default_factory=MicrophoneConfig)
    wake_word: WakeWordConfig = Field(default_factory=WakeWordConfig)
    overlay: OverlayConfig = Field(default_factory=OverlayConfig)
    debug: DebugConfig = Field(default_factory=DebugConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    ui: UiConfig = Field(default_factory=UiConfig)
    secrets: SecretsConfig = Field(default_factory=SecretsConfig)
    mcps: dict[str, Any] = Field(default_factory=lambda: {"home_assistant": {"enabled": False, "url": ""}})

    @field_validator("provider", mode="before")
    @classmethod
    def validate_provider(cls, value: Any) -> str:
        provider = str(value or "inworld").strip().lower()
        return provider if provider in LLM_PROVIDERS else "inworld"

    @field_validator("config_version", mode="before")
    @classmethod
    def validate_version(cls, _value: Any) -> int:
        return CURRENT_CONFIG_VERSION


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a config dict for persistence."""
    try:
        model = BitMonConfig.model_validate(config)
    except ValidationError as exc:
        raise ConfigValidationError(str(exc)) from exc

    data = model.model_dump()
    hotkeys = data["microphone"].get("whisper_hotkeys") or ["f8"]
    data["microphone"]["whisper_hotkeys"] = hotkeys
    data["microphone"]["whisper_hotkey"] = hotkeys[0]
    return data
