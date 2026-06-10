"""Preload local provider dependencies."""

from __future__ import annotations

import time
from typing import Any

from openai import AsyncOpenAI

from core.security import redact_for_log
from services.tts_service import preload_kokoro_voice
from services.whisper_service import preload_whisper


async def preload_local_provider(config: dict[str, Any]) -> None:
    provider = str(config.get("provider") or "").lower()
    if provider != "local":
        return

    local = config.get("local") or {}
    await preload_whisper(
        str(local.get("whisper_model") or "base"),
        str(local.get("stt_language") or "pt"),
    )
    await preload_kokoro_voice(
        lang_code=str(local.get("kokoro_lang") or "p"),
        voice=str(local.get("kokoro_voice") or "pf_dora"),
        model_id=str(local.get("kokoro_model") or "hexgrad/Kokoro-82M"),
    )
    await preload_lmstudio_model(local)


async def preload_lmstudio_model(local: dict[str, Any]) -> None:
    base_url = str(local.get("base_url") or "http://127.0.0.1:1234/v1").rstrip("/")
    model_id = str(local.get("model") or "local-model").strip()
    if not model_id:
        return

    client = AsyncOpenAI(api_key="lm-studio", base_url=base_url, timeout=90.0)
    t0 = time.perf_counter()
    print(redact_for_log(f"[Local] Loading LM Studio model={model_id} base_url={base_url}"))
    await client.chat.completions.create(
        model=model_id,
        messages=[
            {"role": "system", "content": "Answer with one word."},
            {"role": "user", "content": "ping"},
        ],
        temperature=0,
        max_tokens=1,
    )
    print(f"[Local] LM Studio ready in {time.perf_counter() - t0:.2f}s")
