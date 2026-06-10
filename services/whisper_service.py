"""Local Whisper STT service.

Loads the configured WhisperX model once and reuses it for voice-session turns.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import shutil
import threading
import time
import warnings
from typing import Any

import numpy as np


ALLOWED_MODELS = {"tiny", "base", "small", "medium"}
_model_cache: dict[tuple[str, str], tuple[Any, str]] = {}
_model_lock = threading.Lock()

for _logger_name in (
    "whisperx",
    "whisperx.vads",
    "whisperx.vads.pyannote",
    "pyannote",
    "pyannote.audio",
    "lightning",
    "lightning.pytorch",
    "pytorch_lightning",
):
    logging.getLogger(_logger_name).setLevel(logging.ERROR)


@contextlib.contextmanager
def _quiet_third_party():
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            yield


def normalize_whisper_model(model_name: str | None) -> str:
    model = str(model_name or "base").strip().lower()
    return model if model in ALLOWED_MODELS else "base"


def normalize_whisper_language(language_code: str | None) -> str:
    code = str(language_code or "pt").strip().lower().replace("_", "-")
    if code in {"pt", "pt-br", "ptbr"}:
        return "pt"
    if "-" in code:
        return code.split("-", 1)[0]
    return code or "pt"


def display_whisper_language(language_code: str | None) -> str:
    code = str(language_code or "pt").strip().lower().replace("_", "-")
    if code in {"pt", "pt-br", "ptbr"}:
        return "pt-br"
    return code or "pt"


def _warn_if_gpu_unused(torch: Any) -> None:
    """When falling back to CPU, tell the user how to enable an NVIDIA GPU.

    The most common cause is the CPU-only PyTorch wheel being installed on a
    machine that does have an NVIDIA card."""
    cuda_build = bool(getattr(getattr(torch, "version", None), "cuda", None))
    has_nvidia = shutil.which("nvidia-smi") is not None
    if has_nvidia and not cuda_build:
        print(
            "[Whisper] NVIDIA GPU detected but PyTorch is the CPU-only build, so STT runs on CPU. "
            "To use the GPU, reinstall PyTorch with CUDA: "
            "pip install -r backend/requirements-gpu.txt"
        )
    elif has_nvidia:
        print(
            "[Whisper] NVIDIA GPU detected but CUDA is unavailable to PyTorch "
            "(check your NVIDIA driver / CUDA install); running STT on CPU."
        )


def _load_model_sync(model_name: str, language_code: str) -> tuple[Any, str]:
    model_name = normalize_whisper_model(model_name)
    whisper_language = normalize_whisper_language(language_code)
    key = (model_name, whisper_language)

    with _model_lock:
        cached = _model_cache.get(key)
        if cached is not None:
            return cached

        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"
        if device == "cpu":
            _warn_if_gpu_unused(torch)
        print(
            f"[Whisper] loading model={model_name} language={display_whisper_language(language_code)} "
            f"device={device} compute={compute_type}"
        )
        t0 = time.perf_counter()
        with _quiet_third_party():
            import whisperx

            model = whisperx.load_model(
                model_name,
                device,
                compute_type=compute_type,
                language=whisper_language,
            )
        elapsed = time.perf_counter() - t0
        print(f"[Whisper] model loaded in {elapsed:.2f}s")
        _model_cache[key] = (model, device)
        return model, device


async def preload_whisper(model_name: str, language_code: str) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _load_model_sync, model_name, language_code)


async def preload_configured_whisper(config: dict[str, Any]) -> None:
    provider = str(config.get("provider") or "inworld").lower()
    section = config.get(provider) or config.get("inworld") or {}
    if str(section.get("stt_provider") or provider).lower() != "whisper":
        return
    await preload_whisper(
        str(section.get("whisper_model") or "base"),
        str(section.get("stt_language") or "pt"),
    )


async def transcribe_pcm16(
    pcm: bytes,
    *,
    model_name: str,
    language_code: str,
    sample_rate: int = 24000,
) -> str:
    if not pcm:
        return ""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        _transcribe_pcm16_sync,
        pcm,
        normalize_whisper_model(model_name),
        normalize_whisper_language(language_code),
        int(sample_rate),
    )


def _transcribe_pcm16_sync(pcm: bytes, model_name: str, language_code: str, sample_rate: int) -> str:
    model, _device = _load_model_sync(model_name, language_code)
    t0 = time.perf_counter()
    audio = _pcm16_to_float32(pcm, sample_rate=sample_rate, target_rate=16000)
    with _quiet_third_party():
        try:
            result = model.transcribe(audio, batch_size=16, language=language_code)
        except TypeError:
            result = model.transcribe(audio, batch_size=16)
    elapsed = time.perf_counter() - t0
    text = " ".join(str(seg.get("text") or "").strip() for seg in result.get("segments", []))
    text = " ".join(text.split())
    print(f"[Whisper] transcricao em {elapsed:.2f}s: {text!r}")
    return text


def _pcm16_to_float32(pcm: bytes, *, sample_rate: int, target_rate: int) -> np.ndarray:
    usable = pcm[: len(pcm) - (len(pcm) % 2)]
    if not usable:
        return np.zeros(0, dtype=np.float32)
    audio = np.frombuffer(usable, dtype="<i2").astype(np.float32) / 32768.0
    if sample_rate == target_rate or audio.size == 0:
        return audio
    duration = audio.size / float(sample_rate)
    target_count = max(1, int(duration * target_rate))
    src_x = np.linspace(0.0, 1.0, audio.size, endpoint=False)
    dst_x = np.linspace(0.0, 1.0, target_count, endpoint=False)
    return np.interp(dst_x, src_x, audio).astype(np.float32)


def pcm16_rms(pcm: bytes) -> float:
    if len(pcm) < 2:
        return 0.0
    samples = np.frombuffer(pcm[: len(pcm) - (len(pcm) % 2)], dtype="<i2")
    if samples.size == 0:
        return 0.0
    values = samples.astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(values * values)))
