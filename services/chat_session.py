"""Cloud voice chat session: the provider-agnostic STT -> LLM -> TTS pipeline.

This is where the conversation actually happens for the cloud providers. Each
turn is transcribed with Whisper, tried against the deterministic smart-home
intents, then routed through services.llm_bridge to the active provider's
chat adapter (inworld_chat / openai_chat / anthropic_chat).

The LM Studio (local) pipeline lives in services.local_session and uses
services.local_chat the same way.
"""

from __future__ import annotations

import asyncio
import base64
import itertools
import json
import re
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from core.config import settings
from core.config_store import get_config
from services import llm_bridge
from services.chat_bus import append_message, register_session, unregister_session
from services.inworld_tts import INWORLD_TTS_MODEL, synthesize_inworld_pcm16
from services.tts_cache import synthesize_cached
from services.tts_service import synthesize_pcm16
from services.tool_runtime import (
    TOOL_INSTRUCTIONS,
    get_chat_tools,
    get_session_tools,
)
from services.voice_intents import try_direct_smart_home_intent
from services.wake_phrase import strip_configured_wake_phrase
from services.whisper_service import transcribe_pcm16


VOICE_RESPONSE_MAX_TOKENS = 120
VOICE_RESPONSE_MAX_CHARS = 360
_REQUEST_COUNTER = itertools.count(1)


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


async def chat_session_proxy(client_ws: WebSocket) -> None:
    await client_ws.accept()

    config = get_config()
    selection = llm_bridge.select_llm(config, VOICE_RESPONSE_MAX_TOKENS)
    if selection.provider != "local" and not selection.api_key:
        await client_ws.send_text(json.dumps({
            "type": "error",
            "error": f"{selection.key_name} is not configured. Save the key in the configuration UI.",
        }))
        await client_ws.close()
        return

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
            selection_now = llm_bridge.select_llm(config_now, VOICE_RESPONSE_MAX_TOKENS)
            tts_language_now = str(speech_now.get("tts_language") or inworld_now.get("tts_language") or "en")
            voice_id_now = str(inworld_now.get("voice") or settings.INWORLD_VOICE)
            voice_response_now = bool(tts_now.get("enabled", inworld_now.get("voice_response", True)))
            tts_provider_now = str(tts_now.get("provider") or "inworld").lower()
            # The spoken answer may use everything the token budget allows; the
            # char cap only exists to keep TTS playback bounded when the model
            # ignores the prompt's brevity rules.
            max_chars_now = max(VOICE_RESPONSE_MAX_CHARS, selection_now.max_tokens * 4)
            chat_tools_now = get_chat_tools(config_now)
            tool_names_now = ",".join(tool["function"]["name"] for tool in chat_tools_now) or "none"
            print(
                f"{flow_prefix} mode=normal stt=whisper llm={selection_now.label} "
                f"model={selection_now.model} "
                f"tts={tts_provider_now if voice_response_now else 'off'} "
                f"tools={tool_names_now} text_chars={len(user_text)}"
            )
            await client_ws.send_text(json.dumps({"type": "bitmon.response_interrupted"}))
            await client_ws.send_text(json.dumps({"type": "response.created"}))
            history[0] = {"role": "system", "content": _system_prompt(config_now)}
            response_started = time.perf_counter()
            history.append({"role": "user", "content": user_text})
            # Deterministic fast path: direct on/off commands matching an
            # enabled Home Assistant device are executed without the LLM.
            direct_answer = None
            try:
                direct_answer = await try_direct_smart_home_intent(user_text, config_now)
            except Exception as exc:
                print(f"{flow_prefix} direct intent fallback error: {exc}")
            # Canned intent answers come from a small fixed set, so their TTS
            # audio is worth caching on disk and replaying on repeats.
            cacheable_answer = direct_answer is not None
            if direct_answer is not None:
                answer = direct_answer
                print(
                    f"{flow_prefix} direct intent handled without LLM "
                    f"in {time.perf_counter() - response_started:.2f}s"
                )
            else:
                try:
                    answer, tool_names = await llm_bridge.complete(
                        selection_now,
                        messages=[history[0]] + history[1:][-12:],
                        tools=chat_tools_now,
                        user_request=user_text,
                    )
                except Exception as exc:
                    # Recover from a single failed turn instead of letting the
                    # exception bubble up and close the whole websocket. Log it so it
                    # shows up in the launcher, drop the failed user turn, and keep going.
                    friendly = llm_bridge.friendly_error(selection_now, exc)
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
                    pcm = await synthesize_cached(
                        {
                            "provider": "inworld",
                            "model": INWORLD_TTS_MODEL,
                            "voice": voice_id_now,
                            "language": tts_language_now,
                            "text": answer,
                        },
                        lambda: synthesize_inworld_pcm16(
                            answer,
                            voice_id=voice_id_now,
                            language_code=tts_language_now,
                            log_prefix=f"[Inworld TTS #{request_id}]",
                        ),
                        enabled=cacheable_answer,
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
                kokoro_lang = str(local_now.get("kokoro_lang") or "p")
                kokoro_voice = str(local_now.get("kokoro_voice") or "pf_dora")
                kokoro_model = str(local_now.get("kokoro_model") or "hexgrad/Kokoro-82M")
                kokoro_speed = float(local_now.get("kokoro_speed") or 1.0)
                try:
                    pcm = await synthesize_cached(
                        {
                            "provider": "kokoro",
                            "model": kokoro_model,
                            "voice": kokoro_voice,
                            "language": kokoro_lang,
                            "speed": kokoro_speed,
                            "text": answer,
                        },
                        lambda: synthesize_pcm16(
                            answer,
                            lang_code=kokoro_lang,
                            voice=kokoro_voice,
                            model_id=kokoro_model,
                            speed=kokoro_speed,
                        ),
                        enabled=cacheable_answer,
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
                    print(f"[VoiceFlow] control message failed: {exc}")
                continue
            text = item
            try:
                await client_ws.send_text(json.dumps({
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": text,
                }, ensure_ascii=False))
                await answer_text(text)
            except Exception as exc:
                print(f"[VoiceFlow] injected message failed: {exc}")

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
            await client_ws.send_text(json.dumps({
                "type": "error",
                "error": llm_bridge.friendly_error(llm_bridge.select_llm(get_config()), exc),
            }))
            await client_ws.close()
        except Exception:
            return
    finally:
        inject_task.cancel()
        unregister_session(session_id)
