"""Disk cache for TTS audio of deterministic (canned) answers.

Direct smart-home confirmations come from a small fixed phrase set, so their
synthesized audio is stored under cache/tts and reused on later turns instead
of paying another TTS request. The cache key is the full voice signature
(provider, model, voice, language, speed, ...) plus the exact text: changing
any voice setting produces a different key, so a different voice never replays
someone else's audio. Files are raw 24 kHz mono PCM16 with a .json sidecar
holding the signature for inspection.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Awaitable, Callable

TTS_CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "tts"
MAX_CACHE_FILES = 400
SAMPLE_RATE = 24000


def _cache_paths(signature: dict[str, Any]) -> tuple[Path, Path]:
    payload = json.dumps(signature, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return TTS_CACHE_DIR / f"{digest}.pcm", TTS_CACHE_DIR / f"{digest}.json"


def get_cached_pcm(signature: dict[str, Any]) -> bytes | None:
    pcm_path, _ = _cache_paths(signature)
    try:
        pcm = pcm_path.read_bytes()
    except OSError:
        return None
    if not pcm:
        return None
    try:
        # Touch so pruning evicts least-recently-used entries first.
        os.utime(pcm_path, None)
    except OSError:
        pass
    return pcm


def store_cached_pcm(signature: dict[str, Any], pcm: bytes) -> None:
    if not pcm:
        return
    pcm_path, meta_path = _cache_paths(signature)
    try:
        TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        pcm_path.write_bytes(pcm)
        meta_path.write_text(
            json.dumps(signature, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"[TTS cache] store failed: {exc}")
        return
    _prune_cache()


def _prune_cache(keep: int = MAX_CACHE_FILES) -> None:
    try:
        entries = sorted(TTS_CACHE_DIR.glob("*.pcm"), key=lambda path: path.stat().st_mtime)
    except OSError:
        return
    for stale in entries[:-keep] if len(entries) > keep else []:
        for path in (stale, stale.with_suffix(".json")):
            try:
                path.unlink()
            except OSError:
                pass


async def synthesize_cached(
    signature: dict[str, Any],
    synthesize: Callable[[], Awaitable[bytes]],
    *,
    enabled: bool = True,
) -> bytes:
    """Return cached PCM for the signature, synthesizing (and storing) on miss.

    With enabled=False this is a plain passthrough, so call sites keep a
    single code path for cacheable (canned) and non-cacheable (LLM) answers.
    """
    if not enabled:
        return await synthesize()
    cached = get_cached_pcm(signature)
    if cached is not None:
        seconds = len(cached) / 2 / SAMPLE_RATE
        print(f"[TTS cache] hit ({seconds:.1f}s audio, no TTS request): {str(signature.get('text'))[:60]!r}")
        return cached
    pcm = await synthesize()
    store_cached_pcm(signature, pcm)
    return pcm
