"""System defaults for BitMon runtime configuration."""

from __future__ import annotations

from typing import Any


CURRENT_CONFIG_VERSION = 2

DEFAULT_CONFIG: dict[str, Any] = {
    "config_version": CURRENT_CONFIG_VERSION,
    "provider": "inworld",
    "llm": {
        "provider": "inworld",
    },
    "tts": {
        "enabled": True,
        "provider": "inworld",
    },
    "speech": {
        "stt_language": "pt",
        "tts_language": "pt",
    },
    "whisper": {
        "model": "base",
    },
    "character": {
        "name": "Pugna",
        "personality_prompt": "Personality: helpful, concise, friendly virtual companion with a playful BitMon energy.",
    },
    "inworld": {
        "model": "deepseek-v4-flash",
        "voice": "Hana",
        "voice_response": True,
        "max_tokens": 300,
        "stt_provider": "whisper",
        "whisper_model": "base",
        "stt_language": "pt",
        "tts_language": "en",
        "vision": "auto",
    },
    "local": {
        "base_url": "http://127.0.0.1:1234/v1",
        "model": "local-model",
        "temperature": 0.7,
        "max_tokens": 220,
        "voice_response": True,
        "stt_provider": "whisper",
        "whisper_model": "base",
        "stt_language": "pt",
        "tts_language": "pt",
        "kokoro_model": "hexgrad/Kokoro-82M",
        "kokoro_lang": "p",
        "kokoro_voice": "pf_dora",
        "kokoro_speed": 1.0,
        "vision": "auto",
        "vision_model": "",
    },
    "microphone": {
        "gain": 10.0,
        "vad_threshold": 0.004,
        "whisper_hotkey": "f8",
        "whisper_hotkeys": ["f8"],
    },
    "wake_word": {
        "enabled": False,
        "engine": "openwakeword",
        "selected_model": "builtin:hey_jarvis",
        "model_names": ["hey_jarvis"],
        "model_paths": [],
        "threshold": 0.5,
        "vad_threshold": 0.0,
        "cooldown_seconds": 2.0,
        "activation_timeout_seconds": 1.0,
        "command_timeout_seconds": 8.0,
        "command_silence_seconds": 0.3,
        "preroll_seconds": 1.5,
        "auto_download_models": True,
    },
    "overlay": {
        "enabled": True,
        "always_on_top": True,
    },
    "debug": {
        "user_subtitle": False,
        "replay_audio": False,
    },
    "tools": {
        "screen_analysis": True,
        "open_configuration": True,
    },
    "ui": {
        "locale": "en-US",
    },
    "secrets": {
        "inworld_api_key_configured": False,
    },
    "mcps": {
        "home_assistant": {
            "enabled": False,
            "url": "",
        },
        "servers": [],
    },
}
