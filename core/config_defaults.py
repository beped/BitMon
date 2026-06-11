"""System defaults for BitMon runtime configuration."""

from __future__ import annotations

from typing import Any


CURRENT_CONFIG_VERSION = 2

# Spoken confirmations for direct smart-home commands. Generic on purpose:
# saying the entity name out loud ("Pronto, liguei: Escritório.") sounds
# robotic, and a fixed phrase set lets the TTS audio be cached and reused.
# Users manage this list (toggle/add/remove) in the Config UI "Answers" tab.
HOME_ASSISTANT_DEFAULT_ANSWERS: list[dict[str, Any]] = [
    # Portuguese
    {"id": "pt_on_1", "language": "pt", "action": "turn_on", "text": "Pronto, ligado.", "enabled": True},
    {"id": "pt_on_2", "language": "pt", "action": "turn_on", "text": "Feito, já liguei.", "enabled": True},
    {"id": "pt_on_3", "language": "pt", "action": "turn_on", "text": "Ligado!", "enabled": True},
    {"id": "pt_off_1", "language": "pt", "action": "turn_off", "text": "Pronto, desligado.", "enabled": True},
    {"id": "pt_off_2", "language": "pt", "action": "turn_off", "text": "Feito, já desliguei.", "enabled": True},
    {"id": "pt_off_3", "language": "pt", "action": "turn_off", "text": "Desligado!", "enabled": True},
    # English
    {"id": "en_on_1", "language": "en", "action": "turn_on", "text": "Done, it's on.", "enabled": True},
    {"id": "en_on_2", "language": "en", "action": "turn_on", "text": "All set, turned on.", "enabled": True},
    {"id": "en_on_3", "language": "en", "action": "turn_on", "text": "Turned on!", "enabled": True},
    {"id": "en_off_1", "language": "en", "action": "turn_off", "text": "Done, it's off.", "enabled": True},
    {"id": "en_off_2", "language": "en", "action": "turn_off", "text": "All set, turned off.", "enabled": True},
    {"id": "en_off_3", "language": "en", "action": "turn_off", "text": "Turned off!", "enabled": True},
    # Spanish
    {"id": "es_on_1", "language": "es", "action": "turn_on", "text": "Listo, encendido.", "enabled": True},
    {"id": "es_on_2", "language": "es", "action": "turn_on", "text": "Hecho, ya está encendido.", "enabled": True},
    {"id": "es_off_1", "language": "es", "action": "turn_off", "text": "Listo, apagado.", "enabled": True},
    {"id": "es_off_2", "language": "es", "action": "turn_off", "text": "Hecho, ya está apagado.", "enabled": True},
    # French
    {"id": "fr_on_1", "language": "fr", "action": "turn_on", "text": "Voilà, c'est allumé.", "enabled": True},
    {"id": "fr_on_2", "language": "fr", "action": "turn_on", "text": "C'est fait, allumé.", "enabled": True},
    {"id": "fr_off_1", "language": "fr", "action": "turn_off", "text": "Voilà, c'est éteint.", "enabled": True},
    {"id": "fr_off_2", "language": "fr", "action": "turn_off", "text": "C'est fait, éteint.", "enabled": True},
    # Italian
    {"id": "it_on_1", "language": "it", "action": "turn_on", "text": "Fatto, è acceso.", "enabled": True},
    {"id": "it_on_2", "language": "it", "action": "turn_on", "text": "Pronto, acceso.", "enabled": True},
    {"id": "it_off_1", "language": "it", "action": "turn_off", "text": "Fatto, è spento.", "enabled": True},
    {"id": "it_off_2", "language": "it", "action": "turn_off", "text": "Pronto, spento.", "enabled": True},
    # German
    {"id": "de_on_1", "language": "de", "action": "turn_on", "text": "Erledigt, eingeschaltet.", "enabled": True},
    {"id": "de_on_2", "language": "de", "action": "turn_on", "text": "Fertig, ist an.", "enabled": True},
    {"id": "de_off_1", "language": "de", "action": "turn_off", "text": "Erledigt, ausgeschaltet.", "enabled": True},
    {"id": "de_off_2", "language": "de", "action": "turn_off", "text": "Fertig, ist aus.", "enabled": True},
]

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
    "openai": {
        "model": "gpt-4o-mini",
        "max_tokens": 300,
    },
    "anthropic": {
        "model": "claude-opus-4-8",
        "max_tokens": 300,
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
        "openai_api_key_configured": False,
        "anthropic_api_key_configured": False,
    },
    "mcps": {
        "home_assistant": {
            "enabled": False,
            "url": "",
            "answers": HOME_ASSISTANT_DEFAULT_ANSWERS,
        },
        "servers": [],
    },
}
