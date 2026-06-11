"""Inworld chat proxy using the OpenAI-compatible Router API."""

from __future__ import annotations

import asyncio
import base64
import itertools
import json
import re
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from openai import APIStatusError

from core.config import get_inworld_api_key, settings
from core.config_store import get_config
from services.chat_bus import append_message, register_session, unregister_session
from services.inworld_auth import create_inworld_router_client, inworld_key_format_warning
from services.inworld_tts import synthesize_inworld_pcm16
from services.tts_service import synthesize_pcm16
from services.tool_runtime import (
    TOOL_INSTRUCTIONS,
    create_chat_completion_with_tools,
    get_chat_tools,
    get_session_tools,
)
from services.wake_phrase import strip_configured_wake_phrase
from services.whisper_service import transcribe_pcm16


SESSION_RATE = 24000
VOICE_RESPONSE_MAX_TOKENS = 120
VOICE_RESPONSE_MAX_CHARS = 360
_REQUEST_COUNTER = itertools.count(1)


def _friendly_provider_error(exc: Exception, api_key: str = "") -> str:
    if isinstance(exc, APIStatusError) and exc.status_code == 401:
        warning = inworld_key_format_warning(api_key)
        suffix = f" {warning}" if warning else " Check whether the saved key is active for Inworld API calls."
        return f"Inworld API unauthorized.{suffix}"
    message = str(exc) or exc.__class__.__name__
    if "unauthorized" in message.lower():
        warning = inworld_key_format_warning(api_key)
        suffix = f" {warning}" if warning else " Check whether the saved key is active for Inworld API calls."
        return f"Inworld API unauthorized.{suffix}"
    return message


def _system_prompt(config: dict[str, Any]) -> str:
    language_code = str(config.get("speech", {}).get("tts_language") or "en").strip()
    character = config.get("character") or {}
    name = str(character.get("name") or "BitMon").strip()
    personality = str(character.get("personality_prompt") or "").strip()
    prompt = (
        settings.SYSTEM_PROMPT_CORE
        + "\n"
        + f"MANDATORY LANGUAGE: Answer only in {language_code}. "
        + "This overrides the language used by the user and any personality text."
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


def _shorten_voice_answer(text: str, max_chars: int = VOICE_RESPONSE_MAX_CHARS) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= max_chars:
        return cleaned
    sentence_matches = list(re.finditer(r"(?<=[.!?])\s+", cleaned))
    for match in sentence_matches:
        candidate = cleaned[:match.start()].strip()
        if 80 <= len(candidate) <= max_chars:
            return candidate
    return cleaned[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:") + "."


async def inworld_chat_proxy(client_ws: WebSocket) -> None:
    await client_ws.accept()

    api_key = get_inworld_api_key()
    if not api_key:
        await client_ws.send_text(json.dumps({
            "type": "error",
            "error": "INWORLD_API_KEY is not configured. Save the key in the configuration UI.",
        }))
        await client_ws.close()
        return

    config = get_config()
    inworld = config.get("inworld") or {}
    client = create_inworld_router_client(api_key, settings.INWORLD_ROUTER_BASE_URL)
    history: list[dict[str, Any]] = [{"role": "system", "content": _system_prompt(config)}]
    active_config_signature = _config_signature(config)
    response_lock = asyncio.Lock()

    await client_ws.send_text(json.dumps({"type": "session.created"}))
    await client_ws.send_text(json.dumps({"type": "session.updated"}))

    async def answer_text(user_text: str, request_id: int | None = None) -> None:
        user_text = " ".join(str(user_text or "").split())
        if not user_text:
            return
        append_message("user", user_text)
        request_id = request_id or next(_REQUEST_COUNTER)
        flow_prefix = f"[VoiceFlow #{request_id}]"
        async with response_lock:
            nonlocal active_config_signature, history
            flow_started = time.perf_counter()
            config_now = get_config()
            next_config_signature = _config_signature(config_now)
            if next_config_signature != active_config_signature:
                active_config_signature = next_config_signature
                history = [{"role": "system", "content": _system_prompt(config_now)}]
                print(f"{flow_prefix} config changed; conversation history reset")
            inworld_now = config_now.get("inworld") or {}
            local_now = config_now.get("local") or {}
            tts_now = config_now.get("tts") or {}
            speech_now = config_now.get("speech") or {}
            model_id_now = str(inworld_now.get("model") or "deepseek-v4-flash")
            tts_language_now = str(speech_now.get("tts_language") or inworld_now.get("tts_language") or "en")
            voice_id_now = str(inworld_now.get("voice") or settings.INWORLD_VOICE)
            voice_response_now = bool(tts_now.get("enabled", inworld_now.get("voice_response", True)))
            tts_provider_now = str(tts_now.get("provider") or "inworld").lower()
            max_tokens_now = int(inworld_now.get("max_tokens") or VOICE_RESPONSE_MAX_TOKENS)
            # The spoken answer may use everything the token budget allows; the
            # char cap only exists to keep TTS playback bounded when the model
            # ignores the prompt's brevity rules.
            max_chars_now = max(VOICE_RESPONSE_MAX_CHARS, max_tokens_now * 4)
            print(
                f"{flow_prefix} mode=normal stt=whisper llm=inworld-router "
                f"tts={tts_provider_now if voice_response_now else 'off'} text_chars={len(user_text)}"
            )
            await client_ws.send_text(json.dumps({"type": "bitmon.response_interrupted"}))
            await client_ws.send_text(json.dumps({"type": "response.created"}))
            history[0] = {"role": "system", "content": _system_prompt(config_now)}
            response_started = time.perf_counter()
            history.append({"role": "user", "content": user_text})
            try:
                answer, tool_names = await create_chat_completion_with_tools(
                    client,
                    model=model_id_now,
                    messages=[history[0]] + history[1:][-12:],
                    tools=get_chat_tools(config_now),
                    user_request=user_text,
                    max_tokens=max_tokens_now,
                )
            except Exception as exc:
                # Recover from a single failed turn instead of letting the
                # exception bubble up and close the whole websocket. Log it so it
                # shows up in the launcher, drop the failed user turn, and keep going.
                friendly = _friendly_provider_error(exc, api_key)
                print(f"{flow_prefix} ERROR: {friendly}")
                history.pop()
                await client_ws.send_text(json.dumps({"type": "error", "error": friendly}))
                await client_ws.send_text(json.dumps({"type": "response.done"}))
                return
            if tool_names:
                print(
                    f"{flow_prefix} llm/tools={','.join(tool_names)} response "
                    f"in {time.perf_counter() - response_started:.2f}s"
                )
            else:
                print(f"{flow_prefix} llm response in {time.perf_counter() - response_started:.2f}s")
            answer = _shorten_voice_answer(answer, max_chars_now) or "I do not have an answer for that right now."
            history.append({"role": "assistant", "content": answer})
            append_message("assistant", answer)
            print(f"{flow_prefix} answer chars={len(answer)} voice={'on' if voice_response_now else 'off'}")
            print(f"{flow_prefix} BOT: {answer}")
            await client_ws.send_text(json.dumps({
                "type": "response.output_audio_transcript.delta",
                "delta": answer,
            }, ensure_ascii=False))
            if voice_response_now and tts_provider_now == "inworld":
                try:
                    pcm = await synthesize_inworld_pcm16(
                        answer,
                        voice_id=voice_id_now,
                        language_code=tts_language_now,
                        log_prefix=f"[Inworld TTS #{request_id}]",
                    )
                except Exception as exc:
                    await client_ws.send_text(json.dumps({"type": "error", "error": str(exc)}))
                    pcm = b""
                chunk_size = 4800
                for offset in range(0, len(pcm), chunk_size):
                    await client_ws.send_text(json.dumps({
                        "type": "response.output_audio.delta",
                        "delta": base64.b64encode(pcm[offset:offset + chunk_size]).decode("ascii"),
                    }))
                    await asyncio.sleep(0)
            elif voice_response_now and tts_provider_now == "kokoro":
                try:
                    pcm = await synthesize_pcm16(
                        answer,
                        lang_code=str(local_now.get("kokoro_lang") or "p"),
                        voice=str(local_now.get("kokoro_voice") or "pf_dora"),
                        model_id=str(local_now.get("kokoro_model") or "hexgrad/Kokoro-82M"),
                        speed=float(local_now.get("kokoro_speed") or 1.0),
                    )
                except Exception as exc:
                    await client_ws.send_text(json.dumps({"type": "error", "error": str(exc)}))
                    pcm = b""
                chunk_size = 4800
                for offset in range(0, len(pcm), chunk_size):
                    await client_ws.send_text(json.dumps({
                        "type": "response.output_audio.delta",
                        "delta": base64.b64encode(pcm[offset:offset + chunk_size]).decode("ascii"),
                    }))
                    await asyncio.sleep(0)
            await client_ws.send_text(json.dumps({"type": "response.output_audio.done"}))
            await client_ws.send_text(json.dumps({"type": "response.done"}))
            print(f"{flow_prefix} done total={time.perf_counter() - flow_started:.2f}s")

    async def transcribe_audio_clip(message: str) -> None:
        request_id = next(_REQUEST_COUNTER)
        flow_prefix = f"[VoiceFlow #{request_id}]"
        event = json.loads(message)
        pcm = base64.b64decode(str(event.get("audio") or ""))
        if not pcm:
            return
        await client_ws.send_text(json.dumps({"type": "input_audio_buffer.speech_started"}))
        await client_ws.send_text(json.dumps({"type": "input_audio_buffer.speech_stopped"}))
        stt_started = time.perf_counter()
        config_now = get_config()
        inworld_now = config_now.get("inworld") or {}
        whisper_now = config_now.get("whisper") or {}
        speech_now = config_now.get("speech") or {}
        text = await transcribe_pcm16(
            pcm,
            model_name=str(whisper_now.get("model") or inworld_now.get("whisper_model") or "base"),
            language_code=str(speech_now.get("stt_language") or inworld_now.get("stt_language") or "pt"),
        )
        text = strip_configured_wake_phrase(text, config_now)
        print(
            f"{flow_prefix} stt done in {time.perf_counter() - stt_started:.2f}s"
            f" text={text!r}"
        )
        await client_ws.send_text(json.dumps({
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": text,
        }, ensure_ascii=False))
        await answer_text(text, request_id=request_id)

    session_id, inject_queue = register_session()

    async def consume_injections() -> None:
        # Text typed in the Config chat is pushed here and answered exactly like
        # a spoken/typed turn, so the pet speaks and animates normally. We also
        # echo the text as a user transcript so the pet shows it as "Me: ...".
        while True:
            item = await inject_queue.get()
            # Control messages (dicts) are forwarded verbatim to the pet — e.g.
            # the "clear subtitle" signal sent when the Config chat is cleared.
            if isinstance(item, dict):
                try:
                    await client_ws.send_text(json.dumps(item, ensure_ascii=False))
                except Exception as exc:
                    print(f"[VoiceFlow] control message failed: {_friendly_provider_error(exc, api_key)}")
                continue
            text = item
            try:
                await client_ws.send_text(json.dumps({
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": text,
                }, ensure_ascii=False))
                await answer_text(text)
            except Exception as exc:
                print(f"[VoiceFlow] injected message failed: {_friendly_provider_error(exc, api_key)}")

    inject_task = asyncio.create_task(consume_injections())
    try:
        while True:
            message = await client_ws.receive_text()
            event = json.loads(message)
            event_type = event.get("type")
            if event_type == "bitmon.whisper_audio":
                await transcribe_audio_clip(message)
            elif event_type == "conversation.item.create":
                item = event.get("item") or {}
                if item.get("role") != "user":
                    continue
                parts = item.get("content") or []
                texts = [
                    str(part.get("text") or "")
                    for part in parts
                    if isinstance(part, dict) and part.get("type") == "input_text"
                ]
                await answer_text(" ".join(text.strip() for text in texts if text.strip()))
    except WebSocketDisconnect:
        return
    except Exception as exc:
        try:
            await client_ws.send_text(json.dumps({"type": "error", "error": _friendly_provider_error(exc, api_key)}))
            await client_ws.close()
        except Exception:
            return
    finally:
        inject_task.cancel()
        unregister_session(session_id)
