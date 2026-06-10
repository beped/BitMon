from __future__ import annotations

import queue
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

import numpy as np


SESSION_RATE = 24000
WAKE_WORD_RATE = 16000


WakeDetectedCallback = Callable[[str, float], None]
CommandReadyCallback = Callable[[bytes, str], None]


def _list_value(value: Any) -> list[str]:
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, list):
        items = value
    else:
        items = []
    return [str(item).strip() for item in items if str(item or "").strip()]


def _resolve_model_path(value: str) -> str:
    path = Path(value).expanduser()
    if path.is_absolute():
        return str(path)
    backend_dir = Path(__file__).resolve().parents[1]
    backend_candidate = backend_dir / path
    if backend_candidate.exists():
        return str(backend_candidate)
    return str(path)


def resample_pcm16(pcm: bytes, source_rate: int, target_rate: int) -> bytes:
    if not pcm or source_rate == target_rate:
        return pcm
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    if samples.size == 0:
        return b""
    duration = samples.size / float(source_rate)
    target_count = max(1, int(duration * target_rate))
    src_x = np.linspace(0.0, 1.0, samples.size, endpoint=False)
    dst_x = np.linspace(0.0, 1.0, target_count, endpoint=False)
    resampled = np.interp(dst_x, src_x, samples).astype(np.float32)
    return (np.clip(resampled, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


class WakeWordListener:
    def __init__(
        self,
        config: dict[str, Any],
        on_detected: WakeDetectedCallback,
        on_command_ready: CommandReadyCallback,
    ):
        self.enabled = bool(config.get("enabled", False))
        self.model_names = _list_value(config.get("model_names") or ["hey jarvis"])
        self.model_paths = [_resolve_model_path(path) for path in _list_value(config.get("model_paths"))]
        self.threshold = float(config.get("threshold") or 0.5)
        self.vad_threshold = float(config.get("vad_threshold") or 0.0)
        self.command_vad_threshold = max(0.001, float(config.get("command_vad_threshold") or 0.004))
        self.cooldown_seconds = float(config.get("cooldown_seconds") or 2.0)
        self.activation_timeout_seconds = float(config.get("activation_timeout_seconds") or 1.0)
        self.command_timeout_seconds = float(config.get("command_timeout_seconds") or 8.0)
        self.command_silence_seconds = float(config.get("command_silence_seconds") or 1.0)
        self.min_command_speech_seconds = max(0.08, float(config.get("min_command_speech_seconds") or 0.18))
        self.rearm_quiet_seconds = max(0.2, float(config.get("rearm_quiet_seconds") or 0.8))
        self.preroll_seconds = float(config.get("preroll_seconds") or 1.5)
        self.auto_download_models = bool(config.get("auto_download_models", True))
        self.on_detected = on_detected
        self.on_command_ready = on_command_ready
        self.queue: queue.Queue[tuple[bytes, bytes, float]] = queue.Queue(maxsize=100)
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.ready = False

    def start(self) -> None:
        if not self.enabled or self.thread is not None:
            return
        self.thread = threading.Thread(target=self._run, name="BitMonWakeWord", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        self.thread = None

    def feed(self, pcm24: bytes, pcm16: bytes, rms: float) -> None:
        if not self.enabled or not self.thread:
            return
        try:
            self.queue.put_nowait((pcm24, pcm16, rms))
        except queue.Full:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.queue.put_nowait((pcm24, pcm16, rms))
            except queue.Full:
                pass

    def _create_model(self):
        from openwakeword.model import Model

        if self.auto_download_models and self.model_names and not self.model_paths:
            import openwakeword.utils

            openwakeword.utils.download_models()

        wakeword_models = self.model_paths or self.model_names
        kwargs: dict[str, Any] = {"wakeword_models": wakeword_models}
        if self.vad_threshold > 0:
            kwargs["vad_threshold"] = self.vad_threshold
        try:
            kwargs["inference_framework"] = "onnx"
            return Model(**kwargs)
        except TypeError:
            kwargs.pop("inference_framework", None)
            return Model(**kwargs)

    def _drain_queue(self) -> None:
        while True:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                return

    @staticmethod
    def _reset_model(model: Any) -> None:
        reset = getattr(model, "reset", None)
        if callable(reset):
            reset()

    def _speech_threshold(self, recent_rms: deque[float]) -> float:
        if len(recent_rms) < 25:
            return self.command_vad_threshold
        noise_floor = float(np.percentile(np.asarray(recent_rms, dtype=np.float32), 20))
        adaptive_threshold = noise_floor + max(self.command_vad_threshold * 0.75, 0.002)
        threshold_cap = max(self.command_vad_threshold * 8.0, 0.02)
        capped_threshold = min(adaptive_threshold, threshold_cap)
        return max(self.command_vad_threshold, capped_threshold)

    def _run(self) -> None:
        try:
            model = self._create_model()
        except Exception as exc:
            print(f"[WakeWord] disabled: {exc}")
            self.enabled = False
            self.thread = None
            return

        self.ready = True
        print("[WakeWord] listening")
        recent_pcm = bytearray()
        max_recent_bytes = max(1, int(self.preroll_seconds * SESSION_RATE * 2))
        command_buffer = bytearray()
        capturing = False
        speech_seen = False
        speech_seconds = 0.0
        silence_seconds = 0.0
        capture_started_at = 0.0
        last_detection_at = 0.0
        capture_threshold = self.command_vad_threshold
        rearm_threshold = self.command_vad_threshold
        armed = True
        quiet_seconds = 0.0
        recent_rms: deque[float] = deque(maxlen=80)

        while not self.stop_event.is_set():
            try:
                pcm24, pcm16, rms = self.queue.get(timeout=0.2)
            except queue.Empty:
                continue

            recent_pcm.extend(pcm24)
            if len(recent_pcm) > max_recent_bytes:
                del recent_pcm[: len(recent_pcm) - max_recent_bytes]

            frame_seconds = (len(pcm24) // 2) / float(SESSION_RATE)

            if capturing:
                command_buffer.extend(pcm24)
                if rms >= capture_threshold:
                    speech_seconds += frame_seconds
                    if speech_seconds >= self.min_command_speech_seconds:
                        speech_seen = True
                    silence_seconds = 0.0
                else:
                    silence_seconds += frame_seconds
                elapsed = time.monotonic() - capture_started_at
                activation_expired = not speech_seen and elapsed >= self.activation_timeout_seconds
                timed_out = elapsed >= self.command_timeout_seconds
                ended = speech_seen and silence_seconds >= self.command_silence_seconds
                if activation_expired or timed_out or ended:
                    payload = b"" if activation_expired and not speech_seen else bytes(command_buffer)
                    command_buffer.clear()
                    recent_pcm.clear()
                    capturing = False
                    speech_seen = False
                    speech_seconds = 0.0
                    silence_seconds = 0.0
                    last_detection_at = time.monotonic()
                    armed = False
                    quiet_seconds = 0.0
                    self._reset_model(model)
                    self._drain_queue()
                    if activation_expired:
                        reason = "activation_timeout"
                    elif timed_out:
                        reason = "timeout"
                    else:
                        reason = "silence"
                    self.on_command_ready(payload, reason)
                continue

            recent_rms.append(rms)
            if not armed:
                elapsed_since_detection = time.monotonic() - last_detection_at
                if rms < rearm_threshold:
                    quiet_seconds += frame_seconds
                else:
                    quiet_seconds = 0.0
                forced_rearm = elapsed_since_detection >= self.cooldown_seconds + max(1.0, self.rearm_quiet_seconds * 2.0)
                quiet_rearm = quiet_seconds >= self.rearm_quiet_seconds and elapsed_since_detection >= self.cooldown_seconds
                if quiet_rearm or forced_rearm:
                    armed = True
                    quiet_seconds = 0.0
                    recent_pcm.clear()
                    self._reset_model(model)
                    print("[WakeWord] listening")
                continue

            if time.monotonic() - last_detection_at < self.cooldown_seconds:
                continue

            samples16 = np.frombuffer(pcm16, dtype="<i2")
            if samples16.size == 0:
                continue
            try:
                prediction = model.predict(samples16)
            except Exception as exc:
                print(f"[WakeWord] prediction error: {exc}")
                continue

            if not isinstance(prediction, dict) or not prediction:
                continue
            name, score = max(((str(key), float(value)) for key, value in prediction.items()), key=lambda item: item[1])
            if score < self.threshold:
                continue

            print(f"[WakeWord] detected {name} score={score:.3f}")
            self.on_detected(name, score)
            command_buffer = bytearray(recent_pcm)
            capturing = True
            capture_threshold = self._speech_threshold(recent_rms)
            rearm_threshold = max(self.command_vad_threshold * 1.5, capture_threshold * 0.75)
            speech_seen = False
            speech_seconds = 0.0
            silence_seconds = 0.0
            capture_started_at = time.monotonic()
            armed = False
            self._reset_model(model)
