"""Inworld Text-to-Speech synthesis helpers."""

from __future__ import annotations

import base64
import json
import struct
import time

import httpx
import numpy as np

from core.config import get_inworld_api_key, settings
from services.inworld_auth import inworld_authorization_header


TARGET_SAMPLE_RATE = 24000
INWORLD_TTS_URL = "https://api.inworld.ai/tts/v1/voice"
INWORLD_TTS_MODEL = "inworld-tts-2"


def _resample_pcm16(pcm: bytes, source_rate: int, target_rate: int) -> bytes:
    if not pcm or source_rate == target_rate:
        return pcm
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    if samples.size == 0:
        return b""
    target_count = max(1, int(samples.size * target_rate / float(source_rate)))
    src_x = np.linspace(0.0, 1.0, samples.size, endpoint=False)
    dst_x = np.linspace(0.0, 1.0, target_count, endpoint=False)
    resampled = np.interp(dst_x, src_x, samples)
    return (np.clip(resampled, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def _extract_wav_pcm16(audio: bytes) -> tuple[bytes, int]:
    if not audio.startswith(b"RIFF") or audio[8:12] != b"WAVE":
        return audio, TARGET_SAMPLE_RATE

    offset = 12
    sample_rate = TARGET_SAMPLE_RATE
    channels = 1
    bits_per_sample = 16
    data = b""
    while offset + 8 <= len(audio):
        chunk_id = audio[offset:offset + 4]
        chunk_size = struct.unpack_from("<I", audio, offset + 4)[0]
        chunk_start = offset + 8
        chunk_end = min(chunk_start + chunk_size, len(audio))
        chunk = audio[chunk_start:chunk_end]
        if chunk_id == b"fmt " and len(chunk) >= 16:
            channels = struct.unpack_from("<H", chunk, 2)[0]
            sample_rate = struct.unpack_from("<I", chunk, 4)[0]
            bits_per_sample = struct.unpack_from("<H", chunk, 14)[0]
        elif chunk_id == b"data":
            data = chunk
            break
        offset = chunk_end + (chunk_size % 2)

    if not data:
        return b"", sample_rate
    if bits_per_sample != 16:
        raise ValueError(f"Unsupported Inworld TTS bit depth: {bits_per_sample}")
    if channels == 1:
        return data, sample_rate
    samples = np.frombuffer(data, dtype="<i2").reshape(-1, channels).astype(np.float32)
    mono = np.mean(samples, axis=1)
    return np.clip(mono, -32768, 32767).astype("<i2").tobytes(), sample_rate


async def synthesize_inworld_pcm16(
    text: str,
    *,
    voice_id: str,
    language_code: str,
    model_id: str = INWORLD_TTS_MODEL,
    log_prefix: str = "[Inworld TTS]",
) -> bytes:
    """Synthesize text with Inworld TTS and return 24 kHz mono PCM16."""
    text = " ".join(str(text or "").split())
    if not text:
        return b""
    api_key = get_inworld_api_key()
    if not api_key:
        raise ValueError("INWORLD_API_KEY is not configured.")

    payload = {
        "text": text[:2000],
        "voiceId": voice_id or settings.INWORLD_VOICE,
        "modelId": model_id,
        "audioConfig": {
            "audioEncoding": "LINEAR16",
            "sampleRateHertz": TARGET_SAMPLE_RATE,
            "language": language_code,
        },
        "deliveryMode": "BALANCED",
        "applyTextNormalization": "ON",
    }
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=90.0) as client:
        response = await client.post(
            INWORLD_TTS_URL,
            headers={
                "Authorization": inworld_authorization_header(api_key),
                "Content-Type": "application/json",
            },
            json=payload,
        )
    if response.status_code >= 400:
        raise ValueError(f"Inworld TTS error {response.status_code}: {response.text[:300]}")
    data = response.json()
    audio_content = str(data.get("audioContent") or "")
    if not audio_content:
        raise ValueError(f"Inworld TTS returned no audio: {json.dumps(data)[:300]}")
    wav_or_pcm = base64.b64decode(audio_content)
    pcm, sample_rate = _extract_wav_pcm16(wav_or_pcm)
    pcm = _resample_pcm16(pcm, sample_rate, TARGET_SAMPLE_RATE)
    audio_seconds = len(pcm) / 2 / TARGET_SAMPLE_RATE if pcm else 0.0
    print(
        f"{log_prefix} generated in {time.perf_counter() - started:.2f}s"
        f"  |  Audio: {audio_seconds:.1f}s  |  voice={voice_id or settings.INWORLD_VOICE}"
    )
    return pcm
