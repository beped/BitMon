"""Local voice-session proxy using Whisper, LM Studio, and Kokoro."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from datetime import datetime
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from core.config import settings
from core.config_store import get_config
from core.security import redact_for_log
from services import local_chat
from services.chat_bus import append_message, register_session, unregister_session
from services.inworld_tts import INWORLD_TTS_MODEL, synthesize_inworld_pcm16
from services.tts_cache import synthesize_cached
from services.tool_runtime import (
    TOOL_INSTRUCTIONS,
    get_chat_tools,
    get_session_tools,
)
from services.tts_service import synthesize_pcm16
from services.voice_intents import try_direct_smart_home_intent
from services.wake_phrase import strip_configured_wake_phrase
from services.whisper_service import transcribe_pcm16


SESSION_RATE = 24000

LANGUAGE_NAMES = {
    "en": "English",
    "en-US": "English",
    "en-GB": "English",
    "pt": "Brazilian Portuguese",
    "pt-BR": "Brazilian Portuguese",
    "es": "Spanish",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "fr": "French",
    "de": "German",
    "it": "Italian",
}


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _log_system(text: str) -> None:
    print(f"{_ts()} - [Local] {redact_for_log(text)}")


def _log_user(text: str) -> None:
    print(f"{_ts()} - User: {redact_for_log(text)}")


def _log_bot(text: str) -> None:
    print(f"{_ts()} - BOT: {redact_for_log(text)}")


def _log_error(text: str) -> None:
    print(f"{_ts()} - [ERROR] {redact_for_log(text)}")


def _event_type(message: str) -> str:
    try:
        data = json.loads(message)
    except json.JSONDecodeError:
        return "?"
    return str(data.get("type") or "?")


def _extract_user_text(message: str) -> str:
    try:
        event = json.loads(message)
    except json.JSONDecodeError:
        return ""
    item = event.get("item") or {}
    if item.get("role") != "user":
        return ""
    parts = item.get("content") or []
    texts = [
        str(part.get("text") or "").strip()
        for part in parts
        if isinstance(part, dict) and part.get("type") == "input_text"
    ]
    return " ".join(text for text in texts if text)


def _system_prompt(
    config: dict[str, Any],
    local_cfg: dict[str, Any],
    prompt_override: str | None = None,
    name_override: str | None = None,
) -> str:
    character = config.get("character") or {}
    personality = str(prompt_override or character.get("personality_prompt") or "").strip()
    name = str(name_override or character.get("name") or "").strip()
    language_code = str(config.get("speech", {}).get("tts_language") or local_cfg.get("tts_language") or "pt").strip()
    response_language = LANGUAGE_NAMES.get(language_code, language_code)
    prompt = (
        settings.SYSTEM_PROMPT_CORE
        + "\n"
        + f"MANDATORY LANGUAGE: Answer only in {response_language} ({language_code}). "
        + "This overrides the language used by the user and any personality text. "
        + "Never switch languages unless this backend config changes."
    )
    if name:
        prompt += (
            f"\nIDENTITY: Your current name is {name}. "
            f"If the user asks your name, answer that your name is {name}. "
            "Ignore any previous conversation history that mentions a different name."
        )
    if get_session_tools(config):
        prompt += "\n" + TOOL_INSTRUCTIONS
    if personality:
        prompt += "\n" + personality
    return prompt


def _config_signature(config: dict[str, Any]) -> str:
    return json.dumps(config, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


async def local_session_proxy(
    client_ws: WebSocket,
    voice: str | None = None,
    prompt: str | None = None,
    model: str | None = None,
    name: str | None = None,
) -> None:
    await client_ws.accept()

    config = get_config()
    local_cfg = config.get("local") or {}
    speech_cfg = config.get("speech") or {}
    whisper_cfg = config.get("whisper") or {}
    model_id = model or local_cfg.get("model") or "local-model"
    base_url = str(local_cfg.get("base_url") or "http://127.0.0.1:1234/v1").rstrip("/")
    whisper_model = str(whisper_cfg.get("model") or local_cfg.get("whisper_model") or "base")
    stt_language = str(speech_cfg.get("stt_language") or local_cfg.get("stt_language") or "pt")
    kokoro_model = str(local_cfg.get("kokoro_model") or "hexgrad/Kokoro-82M")
    kokoro_lang = str(local_cfg.get("kokoro_lang") or "p")
    kokoro_voice = str(voice or local_cfg.get("kokoro_voice") or "pf_dora")
    kokoro_speed = float(local_cfg.get("kokoro_speed") or 1.0)
    temperature = float(local_cfg.get("temperature") or 0.7)
    max_tokens = int(local_cfg.get("max_tokens") or 220)
    response_lock = asyncio.Lock()
    history: list[dict[str, Any]] = [{"role": "system", "content": _system_prompt(config, local_cfg, prompt, name)}]
    active_config_signature = _config_signature(config)

    _log_system(
        "session opened "
        f"name={name or config.get('character', {}).get('name')} "
        f"model={model_id} base_url={base_url} stt=whisper/{whisper_model} "
        f"kokoro={kokoro_model}/{kokoro_voice}"
    )

    await client_ws.send_text(json.dumps({"type": "session.created"}))
    await client_ws.send_text(json.dumps({"type": "session.updated"}))

    async def generate_text(user_text: str) -> tuple[str, bool]:
        """Return (answer, from_intent); intent answers have cacheable TTS."""
        nonlocal active_config_signature, history
        config_now = get_config()
        next_config_signature = _config_signature(config_now)
        if next_config_signature != active_config_signature:
            active_config_signature = next_config_signature
            local_reset = config_now.get("local") or {}
            history = [{"role": "system", "content": _system_prompt(config_now, local_reset, prompt, name)}]
            _log_system("config changed; conversation history reset")
        local_now = config_now.get("local") or {}
        current_model = model or local_now.get("model") or model_id
        current_temperature = float(local_now.get("temperature") or temperature)
        current_max_tokens = int(local_now.get("max_tokens") or max_tokens)
        history[0] = {"role": "system", "content": _system_prompt(config_now, local_now, prompt, name)}

        history.append({"role": "user", "content": user_text})
        # Deterministic fast path: direct on/off commands matching an enabled
        # Home Assistant device are executed without the LLM.
        try:
            direct_answer = await try_direct_smart_home_intent(user_text, config_now)
        except Exception as exc:
            _log_error(f"direct intent fallback: {exc}")
            direct_answer = None
        if direct_answer is not None:
            history.append({"role": "assistant", "content": direct_answer})
            _log_system("direct intent handled without LLM")
            return direct_answer, True
        limited_history = [history[0]] + history[1:][-12:]
        started = time.perf_counter()
        answer, tool_names = await local_chat.complete(
            str(local_now.get("base_url") or base_url),
            model=str(current_model),
            messages=limited_history,
            tools=get_chat_tools(config_now),
            user_request=user_text,
            max_tokens=current_max_tokens,
            temperature=current_temperature,
        )
        elapsed = time.perf_counter() - started
        history.append({"role": "assistant", "content": answer})
        if tool_names:
            _log_system(f"LM Studio tools={','.join(tool_names)} responded in {elapsed:.2f}s")
        else:
            _log_system(f"LM Studio responded in {elapsed:.2f}s")
        return answer, False

    async def send_response(user_text: str) -> None:
        user_text = " ".join(str(user_text or "").split())
        user_text = strip_configured_wake_phrase(user_text, get_config())
        if not user_text:
            return
        append_message("user", user_text)
        async with response_lock:
            _log_user(user_text)
            await client_ws.send_text(json.dumps({
                "type": "bitmon.response_interrupted",
            }))
            await client_ws.send_text(json.dumps({
                "type": "response.created",
            }))
            cacheable_answer = False
            try:
                answer, cacheable_answer = await generate_text(user_text)
            except Exception as exc:
                _log_error(f"Local LLM/tool: {exc}")
                await client_ws.send_text(json.dumps({"type": "error", "error": str(exc)}))
                answer = "I could not generate the local response right now."

            answer = " ".join(answer.split())
            if not answer:
                answer = "I do not have an answer for that right now."
            append_message("assistant", answer)
            _log_bot(answer)
            await client_ws.send_text(json.dumps({
                "type": "response.output_audio_transcript.delta",
                "delta": answer,
            }, ensure_ascii=False))

            config_now = get_config()
            local_now = config_now.get("local") or {}
            inworld_now = config_now.get("inworld") or {}
            tts_now = config_now.get("tts") or {}
            speech_now = config_now.get("speech") or {}
            voice_response_now = bool(tts_now.get("enabled", local_now.get("voice_response", True)))
            tts_provider_now = str(tts_now.get("provider") or "kokoro").lower()
            kokoro_model_now = str(local_now.get("kokoro_model") or kokoro_model)
            kokoro_lang_now = str(local_now.get("kokoro_lang") or kokoro_lang)
            kokoro_voice_now = str(voice or local_now.get("kokoro_voice") or kokoro_voice)
            kokoro_speed_now = float(local_now.get("kokoro_speed") or kokoro_speed)

            if voice_response_now and tts_provider_now == "kokoro":
                try:
                    pcm = await synthesize_cached(
                        {
                            "provider": "kokoro",
                            "model": kokoro_model_now,
                            "voice": kokoro_voice_now,
                            "language": kokoro_lang_now,
                            "speed": kokoro_speed_now,
                            "text": answer,
                        },
                        lambda: synthesize_pcm16(
                            answer,
                            lang_code=kokoro_lang_now,
                            voice=kokoro_voice_now,
                            model_id=kokoro_model_now,
                            speed=kokoro_speed_now,
                        ),
                        enabled=cacheable_answer,
                    )
                except Exception as exc:
                    _log_error(f"Kokoro: {exc}")
                    await client_ws.send_text(json.dumps({"type": "error", "error": str(exc)}))
                    pcm = b""

                chunk_size = 4800
                for offset in range(0, len(pcm), chunk_size):
                    chunk = pcm[offset:offset + chunk_size]
                    await client_ws.send_text(json.dumps({
                        "type": "response.output_audio.delta",
                        "delta": base64.b64encode(chunk).decode("ascii"),
                    }))
                    await asyncio.sleep(0)
            elif voice_response_now and tts_provider_now == "inworld":
                inworld_voice_now = str(inworld_now.get("voice") or settings.INWORLD_VOICE)
                inworld_language_now = str(speech_now.get("tts_language") or inworld_now.get("tts_language") or "en")
                try:
                    pcm = await synthesize_cached(
                        {
                            "provider": "inworld",
                            "model": INWORLD_TTS_MODEL,
                            "voice": inworld_voice_now,
                            "language": inworld_language_now,
                            "text": answer,
                        },
                        lambda: synthesize_inworld_pcm16(
                            answer,
                            voice_id=inworld_voice_now,
                            language_code=inworld_language_now,
                        ),
                        enabled=cacheable_answer,
                    )
                except Exception as exc:
                    _log_error(f"Inworld TTS: {exc}")
                    await client_ws.send_text(json.dumps({"type": "error", "error": str(exc)}))
                    pcm = b""

                chunk_size = 4800
                for offset in range(0, len(pcm), chunk_size):
                    chunk = pcm[offset:offset + chunk_size]
                    await client_ws.send_text(json.dumps({
                        "type": "response.output_audio.delta",
                        "delta": base64.b64encode(chunk).decode("ascii"),
                    }))
                    await asyncio.sleep(0)

            await client_ws.send_text(json.dumps({"type": "response.output_audio.done"}))
            await client_ws.send_text(json.dumps({"type": "response.done"}))

    async def handle_audio_clip(message: str) -> None:
        try:
            event = json.loads(message)
            pcm = base64.b64decode(str(event.get("audio") or ""))
        except Exception as exc:
            await client_ws.send_text(json.dumps({"type": "error", "error": f"Invalid audio: {exc}"}))
            return
        if not pcm:
            return
        await client_ws.send_text(json.dumps({"type": "input_audio_buffer.speech_started"}))
        await client_ws.send_text(json.dumps({"type": "input_audio_buffer.speech_stopped"}))
        try:
            config_now = get_config()
            whisper_now = config_now.get("whisper") or {}
            speech_now = config_now.get("speech") or {}
            text = await transcribe_pcm16(
                pcm,
                model_name=str(whisper_now.get("model") or whisper_model),
                language_code=str(speech_now.get("stt_language") or stt_language),
                sample_rate=SESSION_RATE,
            )
        except Exception as exc:
            _log_error(f"Whisper: {exc}")
            await client_ws.send_text(json.dumps({"type": "error", "error": str(exc)}))
            return
        if text:
            text = strip_configured_wake_phrase(text, get_config())
            await client_ws.send_text(json.dumps({
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": text,
            }, ensure_ascii=False))
            await send_response(text)

    session_id, inject_queue = register_session()

    async def consume_injections() -> None:
        # Text typed in the Config chat is answered exactly like a spoken turn,
        # so the pet speaks and animates normally. We also echo the text as a
        # user transcript so the pet shows it as "Me: ...".
        while True:
            item = await inject_queue.get()
            # Control messages (dicts) are forwarded verbatim to the pet — e.g.
            # the "clear subtitle" signal sent when the Config chat is cleared.
            if isinstance(item, dict):
                try:
                    await client_ws.send_text(json.dumps(item, ensure_ascii=False))
                except Exception as exc:
                    _log_error(f"control message failed: {exc}")
                continue
            text = item
            try:
                await client_ws.send_text(json.dumps({
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": text,
                }, ensure_ascii=False))
                await send_response(text)
            except Exception as exc:
                _log_error(f"injected message failed: {exc}")

    inject_task = asyncio.create_task(consume_injections())
    try:
        while True:
            message = await client_ws.receive_text()
            event_type = _event_type(message)
            if event_type == "conversation.item.create":
                text = _extract_user_text(message)
                if text:
                    await send_response(text)
            elif event_type == "bitmon.whisper_audio":
                await handle_audio_clip(message)
            elif event_type == "response.cancel":
                await client_ws.send_text(json.dumps({"type": "bitmon.response_interrupted"}))
    except WebSocketDisconnect:
        _log_system("cliente desconectou")
    except Exception as exc:
        _log_error(f"Local session: {exc}")
        try:
            await client_ws.send_text(json.dumps({"type": "error", "error": str(exc)}))
        except Exception:
            pass
    finally:
        inject_task.cancel()
        unregister_session(session_id)
        try:
            await client_ws.close()
        except Exception:
            pass
        _log_system("session closed")
