"""
Kokoro TTS synthesis service.
Uses Kokoro's native generator to reduce first-audio latency and return PCM16 for
the local voice-session path.
"""

import asyncio
import time

import numpy as np
from core.config import settings

_pipelines = {}


def is_kokoro_ready() -> bool:
    return bool(_pipelines)


def _get_pipeline(lang_code: str | None = None, model_id: str | None = None):
    lang = str(lang_code or settings.KOKORO_LANG or "p").strip() or "p"
    repo_id = str(model_id or "hexgrad/Kokoro-82M").strip() or "hexgrad/Kokoro-82M"
    key = (repo_id, lang)
    pipeline = _pipelines.get(key)
    if pipeline is None:
        from kokoro import KPipeline
        print(f"[Kokoro] Loading pipeline (model={repo_id}, lang={lang})...")
        t0 = time.perf_counter()
        pipeline = KPipeline(lang_code=lang, repo_id=repo_id)
        _pipelines[key] = pipeline
        print(f"[Kokoro] Pipeline ready in {time.perf_counter() - t0:.2f}s")
    return pipeline


async def synthesize_pcm16(
    text: str,
    *,
    lang_code: str | None = None,
    voice: str | None = None,
    model_id: str | None = None,
    speed: float | None = None,
) -> bytes:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _synthesize_pcm16_sync, text, lang_code, voice, model_id, speed)


async def preload_kokoro_voice(
    *,
    lang_code: str | None = None,
    voice: str | None = None,
    model_id: str | None = None,
) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _preload_kokoro_voice_sync, lang_code, voice, model_id)


def _preload_kokoro_voice_sync(
    lang_code: str | None = None,
    voice: str | None = None,
    model_id: str | None = None,
) -> None:
    voice_id = str(voice or settings.KOKORO_VOICE).strip() or settings.KOKORO_VOICE
    t0 = time.perf_counter()
    pipeline = _get_pipeline(lang_code, model_id)
    pipeline.load_voice(voice_id)
    print(f"[Kokoro] Voice loaded in {time.perf_counter() - t0:.2f}s: {voice_id}")


def _synthesize_pcm16_sync(
    text: str,
    lang_code: str | None = None,
    voice: str | None = None,
    model_id: str | None = None,
    speed: float | None = None,
) -> bytes:
    audio = _generate_audio_sync(text, lang_code, voice, model_id, speed)
    audio = np.clip(audio, -1.0, 1.0)
    return (audio * 32767.0).astype("<i2").tobytes()


def _generate_audio_sync(
    text: str,
    lang_code: str | None = None,
    voice: str | None = None,
    model_id: str | None = None,
    speed: float | None = None,
) -> np.ndarray:
    pipeline = _get_pipeline(lang_code, model_id)
    voice_id = str(voice or settings.KOKORO_VOICE).strip() or settings.KOKORO_VOICE
    speed_value = float(speed if speed is not None else settings.KOKORO_SPEED)

    chunks: list[np.ndarray] = []
    started_at = time.perf_counter()
    first_chunk_at: float | None = None

    # Kokoro processes sentence-sized chunks internally and yields audio as each
    # segment finishes. This is the streaming behavior exposed by its API.
    generator = pipeline(
        text,
        voice=voice_id,
        speed=speed_value,
    )

    for i, (graphemes, phonemes, audio) in enumerate(generator):
        if first_chunk_at is None:
            first_chunk_at = time.perf_counter() - started_at
            print(f"[Kokoro] First chunk generated in {first_chunk_at:.2f}s  |  '{graphemes}'")
        chunks.append(audio)

    if not chunks:
        raise RuntimeError("Kokoro did not generate any audio")

    total_time = time.perf_counter() - started_at
    full_audio = np.concatenate(chunks)

    audio_duration = len(full_audio) / 24000
    print(
        f"[Kokoro] Total: {total_time:.2f}s  |  "
        f"Audio: {audio_duration:.1f}s  |  "
        f"RTF: {total_time / audio_duration:.2f}x  |  "
        f"voice={voice_id}"
    )
    return full_audio
