from __future__ import annotations

import asyncio
import base64
import json
import os
import queue
import random
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import sounddevice as sd
import websockets
from PySide6.QtCore import QFileSystemWatcher, QEasingCurve, QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFontDatabase, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsOpacityEffect,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QWidget,
)

from theme_config import THEME_CONFIG_PATH, get_theme_config
from wake_word import WAKE_WORD_RATE, WakeWordListener


PERSONA_DIR = Path(__file__).resolve().parent
ASSETS_DIR = PERSONA_DIR / "assets"
FONTS_DIR = PERSONA_DIR / "fonts"
ICONS_DIR = PERSONA_DIR / "icons"
CONFIG_PATH = PERSONA_DIR / "persona_config.json"
PERSONAS_DIR = PERSONA_DIR / "personas"
PERSONAS_MANIFEST_PATH = PERSONAS_DIR / "personas.json"
BACKEND_CONFIG_PATH = PERSONA_DIR.parent / "bitmon_config.json"
LEGACY_NAME = "digi" + "mon"
LEGACY_BACKEND_CONFIG_PATH = PERSONA_DIR.parent / f"{LEGACY_NAME}_config.json"
if str(PERSONA_DIR.parent) not in sys.path:
    sys.path.insert(0, str(PERSONA_DIR.parent))
from core.crash_logger import install_crash_logger

def _env(name: str, default: str = "") -> str:
    return os.environ.get(f"BITMON_{name}") or os.environ.get(f"{LEGACY_NAME.upper()}_{name}") or default


LOG_DIR = Path(_env("LOG_DIR") or PERSONA_DIR.parent / "logs")
install_crash_logger("bitmon-persona", LOG_DIR)

BACKEND_HOST = _env("HOST", "127.0.0.1")
BACKEND_PORT = int(_env("PORT", "8000"))
URL_SESSION = f"ws://{BACKEND_HOST}:{BACKEND_PORT}/session"
URL_CONFIG_CLIENT = f"http://{BACKEND_HOST}:{BACKEND_PORT}/api/config/client"
SESSION_RATE = 24000
DEFAULT_FRAME_SIZE = 512

# Animation kinds that play exactly once and then fall back to idle.
ONESHOT_KINDS = {"start", "poke", "error"}
ANIM_KINDS = ("idle", "talk", "thinking", "listening", "start", "poke", "error")
# When a persona has no sprites for a state, degrade gracefully along this chain.
ANIM_FALLBACKS = {"listening": "thinking", "thinking": "idle", "start": "idle"}
# Safety net: if the "thinking" loop never gets an answer (e.g. a failed
# request), fall back to idle after this many seconds so the pet doesn't hang.
THINKING_TIMEOUT = 30.0


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    text = str(color or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    try:
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except (ValueError, IndexError):
        return 0, 0, 0


def _rgba_css(color: str, opacity_pct: float) -> str:
    r, g, b = _hex_to_rgb(color)
    alpha = max(0, min(255, round(255 * float(opacity_pct) / 100.0)))
    return f"rgba({r}, {g}, {b}, {alpha})"


def _lighten(color: str, factor: float) -> str:
    r, g, b = (round(ch + (255 - ch) * factor) for ch in _hex_to_rgb(color))
    return f"#{r:02x}{g:02x}{b:02x}"


def active_package_dir() -> Path | None:
    """Folder of the manifest-active persona package, when it exists.

    The runtime folder (persona_config.json + assets/) is the config UI's
    EDITING workspace; the pet renders the ACTIVE persona from its package, so
    drafts under construction never break the pet on screen."""
    try:
        data = json.loads(PERSONAS_MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    active = str(data.get("active") or "").strip()
    if not active:
        return None
    package_dir = PERSONAS_DIR / active
    if (package_dir / "persona_config.json").is_file():
        return package_dir
    return None


def persona_config_path() -> Path:
    package = active_package_dir()
    return (package / "persona_config.json") if package else CONFIG_PATH


def persona_assets_dir() -> Path:
    package = active_package_dir()
    if package and (package / "assets").is_dir():
        return package / "assets"
    return ASSETS_DIR


def load_persona_config() -> dict[str, Any]:
    defaults = {
        "window": {"width": 540, "height": 600, "always_on_top": True, "transparent": True},
        "sprite": {"x": 249, "y": 219, "display_size": 282},
        "subtitle": {"x": 3, "y": 337, "width": 532, "height": 168, "font_size": 18},
        "input": {"x": 45, "y": 530, "width": 448, "height": 50},
        "animations": [],
    }
    try:
        data = json.loads(persona_config_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, dict) and isinstance(defaults.get(key), dict):
                    defaults[key].update(value)
                else:
                    defaults[key] = value
    except Exception as exc:
        print(f"[Persona] using default config: {exc}")
    return defaults


def fetch_client_config(timeout: float = 2.0) -> dict[str, Any] | None:
    with urllib.request.urlopen(URL_CONFIG_CLIENT, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data if isinstance(data, dict) else None


def default_client_config() -> dict[str, Any]:
    return {
        "config_revision": "",
        "character_name": "",
        "mic_gain": 10.0,
        "vad_threshold": 0.004,
        "overlay_mode": True,
        "overlay_always_on_top": True,
        "debug_user_subtitle": False,
        "debug_replay_audio": False,
        "stt_provider": "whisper",
        "whisper_hotkey": "f8",
        "whisper_hotkeys": ["f8"],
        "wake_word": {
            "enabled": False,
            "model_names": ["hey jarvis"],
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
    }


def merge_client_config(data: dict[str, Any] | None = None) -> dict[str, Any]:
    defaults = default_client_config()
    if isinstance(data, dict):
        defaults.update(data)
    return defaults


def load_client_config(timeout: float = 2.0) -> dict[str, Any]:
    defaults = default_client_config()
    try:
        data = fetch_client_config(timeout=timeout)
        if isinstance(data, dict):
            defaults.update(data)
    except Exception as exc:
        print(f"[Config] using defaults: {exc}")
    return defaults


def stable_signature(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def asset_signature() -> str:
    entries: list[tuple[str, int, int]] = []
    for directory in (persona_assets_dir(), FONTS_DIR, ICONS_DIR):
        if not directory.exists():
            continue
        for path in sorted(directory.iterdir()):
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append((str(path.relative_to(PERSONA_DIR)), stat.st_size, stat.st_mtime_ns))
    return stable_signature(entries)


def clean_text_for_subtitle(text: str) -> str:
    return (
        text.replace("\n", " ")
        .replace("\r", " ")
        .replace("**", "")
        .replace("*", "")
        .replace("__", "")
        .replace("_", "")
        .replace("`", "")
    )


def escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


class SpriteAnimation:
    def __init__(
        self,
        path: Path,
        frame_size: int = DEFAULT_FRAME_SIZE,
        columns: int = 1,
        rows: int = 1,
        used_frames: int | None = None,
        fps: int = 24,
        name: str = "",
        kind: str = "idle",
        idle_after: float = 0.0,
        oneshot: bool = False,
    ):
        # The sprite sheet itself is decoded lazily (off the GUI thread via
        # SheetLoader) so opening the pet / switching personas doesn't freeze
        # the whole machine while several ~100MB pixmaps are decoded at once.
        if not path.exists():
            raise FileNotFoundError(f"Could not find sprite: {path}")
        self.path = path
        self.name = name
        self.kind = kind
        self.idle_after = max(0.0, float(idle_after))
        self.oneshot = oneshot
        self.image: QImage | None = None
        self.loaded = False
        self.frame_w = frame_size
        self.frame_h = frame_size
        self.fps = fps
        self.columns = max(1, int(columns))
        self.rows = max(1, int(rows))
        self.total_frames = min(int(used_frames or self.columns * self.rows), self.columns * self.rows)
        self.total_frames = max(1, self.total_frames)
        self.index = 0
        self.just_looped = False

    @property
    def is_ready(self) -> bool:
        return self.image is not None and not self.image.isNull()

    @property
    def has_data(self) -> bool:
        return self.is_ready

    def set_image(self, image: QImage) -> None:
        # The full sheet is kept as a QImage (CPU side). We never convert the
        # whole ~100MB sheet to a pixmap; only the small current frame is.
        self.image = image
        self.loaded = True

    def reset(self) -> None:
        self.index = 0
        self.just_looped = False

    def next_frame(self) -> QPixmap | None:
        if not self.is_ready:
            return None
        x = (self.index % self.columns) * self.frame_w
        y = (self.index // self.columns) * self.frame_h
        # Crop just the current ~1MB frame from the sheet and convert that tile
        # to a pixmap. Converting the entire sheet up front was what briefly
        # hitched the GUI when the pet opened.
        frame = self.image.copy(QRect(x, y, self.frame_w, self.frame_h))
        self.index = (self.index + 1) % self.total_frames
        self.just_looped = self.index == 0
        return QPixmap.fromImage(frame)


class SheetLoader(QObject):
    """Decodes sprite sheets on a worker thread and hands back QImages.

    PNG decoding (16MB on disk -> ~100MB decoded) is the expensive part and is
    perfectly safe off the GUI thread with QImage. The cheap QImage -> QPixmap
    upload then happens back on the GUI thread in the connected slot.
    """

    loaded = Signal(str, object)

    def __init__(self) -> None:
        super().__init__()
        self._queue: queue.Queue[str] = queue.Queue()
        self._seen: set[str] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def reset(self) -> None:
        # Allow already-seen sheets to be re-decoded (e.g. after a persona
        # switch or a live asset edit changed the file on disk).
        with self._lock:
            self._seen.clear()

    def request(self, path: Path) -> None:
        key = str(path)
        with self._lock:
            if key in self._seen:
                return
            self._seen.add(key)
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()
        self._queue.put(key)

    def _run(self) -> None:
        while True:
            key = self._queue.get()
            try:
                image = QImage(key)
            except Exception:
                image = None
            self.loaded.emit(key, image)


class AudioInput:
    def __init__(
        self,
        out_queue: queue.Queue[bytes],
        gain: float,
        vad_threshold: float,
        send_silence_as_zero: bool = True,
        wake_word_listener: WakeWordListener | None = None,
    ):
        self.out_queue = out_queue
        self.gain = gain
        self.vad_threshold = vad_threshold
        self.send_silence_as_zero = send_silence_as_zero
        self.wake_word_listener = wake_word_listener
        self.forward_audio = False
        self.stream: sd.InputStream | None = None
        self.sample_rate = 48000
        self.speaking = False
        self.silence_seconds = 0.0
        self.preroll = bytearray()
        self.preroll_bytes = int(SESSION_RATE * 2 * 0.25)
        self.capture_lock = threading.Lock()
        self.capture_active = False
        self.capture_buffer = bytearray()

    def start(self, *, forward_audio: bool = False) -> None:
        self.forward_audio = self.forward_audio or forward_audio
        if self.stream is not None:
            return
        device_info = sd.query_devices(kind="input")
        self.sample_rate = int(device_info.get("default_samplerate", 48000))
        self.stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=1024,
            callback=self._callback,
        )
        self.stream.start()

    def update_settings(
        self,
        *,
        gain: float,
        vad_threshold: float,
        wake_word_listener: WakeWordListener | None,
    ) -> None:
        self.gain = gain
        self.vad_threshold = vad_threshold
        self.wake_word_listener = wake_word_listener

    def stop(self, *, keep_stream: bool = False) -> None:
        self.forward_audio = False
        if keep_stream:
            self.speaking = False
            self.silence_seconds = 0.0
            self.preroll.clear()
            with self.capture_lock:
                self.capture_active = False
                self.capture_buffer.clear()
            return
        if self.stream is None:
            return
        self.stream.stop()
        self.stream.close()
        self.stream = None
        self.speaking = False
        self.silence_seconds = 0.0
        self.preroll.clear()
        with self.capture_lock:
            self.capture_active = False
            self.capture_buffer.clear()

    def begin_capture(self) -> None:
        with self.capture_lock:
            self.capture_buffer.clear()
            self.capture_active = True

    def end_capture(self) -> bytes:
        with self.capture_lock:
            self.capture_active = False
            payload = bytes(self.capture_buffer)
            self.capture_buffer.clear()
        return payload

    def _callback(self, indata: np.ndarray, frames: int, _time_info: Any, status: sd.CallbackFlags) -> None:
        if status:
            print(f"[AudioInput] {status}")
        mono = np.asarray(indata[:, 0], dtype=np.float32) * float(self.gain)
        mono = np.clip(mono, -1.0, 1.0)
        rms = float(np.sqrt(np.mean(mono * mono))) if mono.size else 0.0

        pcm_real = self._resample_to_pcm16(mono)
        if not pcm_real:
            return
        if self.wake_word_listener is not None:
            pcm_wake = self._resample_to_pcm16(mono, target_rate=WAKE_WORD_RATE)
            self.wake_word_listener.feed(pcm_real, pcm_wake, rms)

        with self.capture_lock:
            if self.capture_active:
                self.capture_buffer.extend(pcm_real)
                return

        if not self.forward_audio:
            return

        self.preroll.extend(pcm_real)
        if len(self.preroll) > self.preroll_bytes:
            del self.preroll[: len(self.preroll) - self.preroll_bytes]

        close_threshold = self.vad_threshold * 0.4
        was_speaking = self.speaking
        if self.speaking:
            if rms < close_threshold:
                self.silence_seconds += frames / float(self.sample_rate)
                if self.silence_seconds >= 1.0:
                    self.speaking = False
                    self.silence_seconds = 0.0
            else:
                self.silence_seconds = 0.0
        elif rms >= self.vad_threshold:
            self.speaking = True
            self.silence_seconds = 0.0

        if not self.send_silence_as_zero:
            payload = pcm_real
        elif self.speaking and not was_speaking:
            payload = bytes(self.preroll)
        elif self.speaking:
            payload = pcm_real
        else:
            payload = bytes(len(pcm_real))

        try:
            self.out_queue.put_nowait(payload)
        except queue.Full:
            pass

    def _resample_to_pcm16(self, mono: np.ndarray, target_rate: int = SESSION_RATE) -> bytes:
        if mono.size == 0:
            return b""
        if self.sample_rate == target_rate:
            resampled = mono
        else:
            duration = mono.size / float(self.sample_rate)
            target_count = max(1, int(duration * target_rate))
            src_x = np.linspace(0.0, 1.0, mono.size, endpoint=False)
            dst_x = np.linspace(0.0, 1.0, target_count, endpoint=False)
            resampled = np.interp(dst_x, src_x, mono).astype(np.float32)
        pcm = np.clip(resampled, -1.0, 1.0)
        return (pcm * 32767.0).astype("<i2").tobytes()


class AudioOutput:
    def __init__(self):
        self.buffer = bytearray()
        self.lock = threading.Lock()
        self.stream: sd.OutputStream | None = None
        self.total_received_samples = 0

    def start(self) -> None:
        if self.stream is not None:
            return
        self.stream = sd.OutputStream(
            samplerate=SESSION_RATE,
            channels=1,
            dtype="float32",
            blocksize=1024,
            callback=self._callback,
        )
        self.stream.start()

    def stop(self) -> None:
        if self.stream is None:
            return
        self.stream.stop()
        self.stream.close()
        self.stream = None
        with self.lock:
            self.buffer.clear()

    def push_pcm(self, pcm: bytes) -> None:
        self.start()
        with self.lock:
            self.buffer.extend(pcm)
            self.total_received_samples += len(pcm) // 2

    def has_pending_audio(self) -> bool:
        with self.lock:
            return len(self.buffer) > 0

    def reset_counters(self) -> None:
        with self.lock:
            self.total_received_samples = 0
            self.buffer.clear()

    def pending_samples(self) -> int:
        with self.lock:
            return len(self.buffer) // 2

    def total_samples(self) -> int:
        with self.lock:
            return self.total_received_samples

    def _callback(self, outdata: np.ndarray, frames: int, _time_info: Any, status: sd.CallbackFlags) -> None:
        if status:
            print(f"[AudioOutput] {status}")
        needed = frames * 2
        with self.lock:
            chunk = bytes(self.buffer[:needed])
            del self.buffer[:needed]
        if len(chunk) < needed:
            chunk += bytes(needed - len(chunk))
        samples = np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32768.0
        outdata[:, 0] = samples[:frames]


class SessionBridge(QObject):
    user_speech_started = Signal()
    user_transcript = Signal(str)
    bot_response_started = Signal()
    bot_transcript_delta = Signal(str)
    bot_response_done = Signal()
    bot_response_interrupted = Signal()
    subtitle_clear = Signal()
    audio_delta = Signal(bytes)
    connected_changed = Signal(bool)
    error = Signal(str)

    def __init__(self, audio_queue: queue.Queue[bytes]):
        super().__init__()
        self.audio_queue = audio_queue
        self.control_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.thread: threading.Thread | None = None
        self.running = False
        self.connected = False

    def ensure_started(self) -> None:
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._thread_main, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if not self.running:
            return
        self.control_queue.put({"type": "close"})

    def send_text(self, text: str) -> None:
        self.ensure_started()
        self.control_queue.put({"type": "text", "text": text})

    def send_audio_clip(self, pcm: bytes) -> None:
        if not pcm:
            return
        self.ensure_started()
        self.control_queue.put({"type": "audio_clip", "pcm": pcm})

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        finally:
            self.running = False
            self.connected = False
            self.connected_changed.emit(False)

    async def _run(self) -> None:
        pending_texts: list[str] = []
        pending_audio_clips: list[bytes] = []
        session_ready = False
        try:
            async with websockets.connect(URL_SESSION) as ws:
                self.connected = True
                self.connected_changed.emit(True)

                async def send_user_text(text: str) -> None:
                    item_msg = {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": text}],
                        },
                    }
                    await ws.send(json.dumps(item_msg, ensure_ascii=False))
                    await ws.send(json.dumps({"type": "response.create"}))

                async def flush_pending_texts() -> None:
                    while pending_texts:
                        await send_user_text(pending_texts.pop(0))

                async def send_audio_clip(pcm: bytes) -> None:
                    await ws.send(json.dumps({
                        "type": "bitmon.whisper_audio",
                        "audio": base64.b64encode(pcm).decode("ascii"),
                    }))

                async def flush_pending_audio_clips() -> None:
                    while pending_audio_clips:
                        await send_audio_clip(pending_audio_clips.pop(0))

                async def sender() -> None:
                    nonlocal session_ready
                    while True:
                        while True:
                            try:
                                control = self.control_queue.get_nowait()
                            except queue.Empty:
                                break
                            if control.get("type") == "close":
                                await ws.close()
                                return
                            if control.get("type") == "text":
                                text = str(control.get("text") or "").strip()
                                if text:
                                    if session_ready:
                                        await send_user_text(text)
                                    else:
                                        pending_texts.append(text)
                            if control.get("type") == "audio_clip":
                                pcm = bytes(control.get("pcm") or b"")
                                if pcm:
                                    if session_ready:
                                        await send_audio_clip(pcm)
                                    else:
                                        pending_audio_clips.append(pcm)
                        await asyncio.sleep(0.01)

                async def receiver() -> None:
                    nonlocal session_ready
                    async for message in ws:
                        if isinstance(message, bytes):
                            message = message.decode("utf-8", errors="ignore")
                        try:
                            event = json.loads(message)
                        except json.JSONDecodeError:
                            continue
                        event_type = event.get("type", "")
                        if event_type == "session.updated":
                            session_ready = True
                            await flush_pending_texts()
                            await flush_pending_audio_clips()
                        elif event_type == "input_audio_buffer.speech_started":
                            self.user_speech_started.emit()
                        elif event_type == "conversation.item.input_audio_transcription.completed":
                            text = str(event.get("transcript") or "").strip()
                            if text:
                                self.user_transcript.emit(text)
                        elif event_type == "response.created":
                            self.bot_response_started.emit()
                        elif event_type in ("response.output_audio.delta", "response.audio.delta"):
                            b64 = event.get("delta") or ""
                            if isinstance(b64, str) and b64:
                                self.audio_delta.emit(base64.b64decode(b64))
                        elif event_type in ("response.output_audio_transcript.delta", "response.audio_transcript.delta"):
                            delta = event.get("delta") or ""
                            if isinstance(delta, str) and delta:
                                self.bot_transcript_delta.emit(clean_text_for_subtitle(delta))
                        elif event_type == "bitmon.response_interrupted":
                            self.bot_response_interrupted.emit()
                        elif event_type == "bitmon.clear_subtitle":
                            self.subtitle_clear.emit()
                        elif event_type in ("response.output_audio.done", "response.audio.done", "response.done"):
                            self.bot_response_done.emit()
                        elif event_type == "error":
                            error_text = str(event.get("error") or event)
                            self.error.emit(error_text)
                            if "unauthorized" in error_text.lower():
                                await ws.close()
                                return

                sender_task = asyncio.create_task(sender())
                receiver_task = asyncio.create_task(receiver())
                done, pending = await asyncio.wait(
                    [sender_task, receiver_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                for task in done:
                    task.result()
        except Exception as exc:
            self.error.emit(str(exc))


@dataclass
class SubtitleState:
    user_text: str = ""
    bot_text: str = ""


class OverlayWindow(QWidget):
    hotkey_pressed = Signal(str)
    hotkey_released = Signal(str)
    wake_word_detected = Signal(str, float)
    wake_word_command_ready = Signal(bytes, str)

    def __init__(self):
        super().__init__()
        self.config = load_client_config()
        self.persona_config = load_persona_config()
        self.theme_config = get_theme_config()
        self.config_signature = stable_signature(self.config)
        self.persona_config_signature = stable_signature(self.persona_config)
        self.theme_signature = stable_signature(self.theme_config)
        self.assets_signature = asset_signature()
        self.audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=200)
        self.stt_provider = str(self.config.get("stt_provider") or "whisper").lower()
        self.whisper_hotkeys = self._configured_hotkeys()
        self.whisper_hotkey = self.whisper_hotkeys[0]
        self.hotkey_listener = None
        self.hotkey_mouse_listener = None
        self.hotkeys_down: set[str] = set()
        self.active_hotkey_id: str | None = None
        self.push_to_talk_active = False
        self.wake_word_capture_active = False
        self.suppress_bridge_disconnect = False
        self.wake_word_listener = self._build_wake_word_listener(self.config)
        self.audio_input = AudioInput(
            self.audio_queue,
            gain=float(self.config.get("mic_gain", 10.0)),
            vad_threshold=float(self.config.get("vad_threshold", 0.004)),
            send_silence_as_zero=True,
            wake_word_listener=self.wake_word_listener,
        )
        self.audio_output = AudioOutput()
        self.bridge = SessionBridge(self.audio_queue)
        self.subtitle = SubtitleState()
        self.show_user_subtitle = bool(self.config.get("debug_user_subtitle", True))
        self.character_name = str(self.config.get("character_name", "") or "").strip()
        self.mic_active = False
        self._mic_icon_cache: dict[str, QIcon] = {}
        self.talking = False
        self.response_audio_done = True
        self._output_device_synced = False
        self.drag_start: QPoint | None = None

        self.anims: dict[str, list[SpriteAnimation]] = {kind: [] for kind in ANIM_KINDS}
        self.current_anim: SpriteAnimation | None = None
        self.anim_state = ""
        self.pending_anim_state: str | None = None
        self.anim_fast_finish = False
        self.anim_oneshot = False
        self.idle_since = time.monotonic()
        self.thinking_since = 0.0
        self.sheet_loader = SheetLoader()
        self.sheet_loader.loaded.connect(self._on_sheet_loaded)
        self.loaded_paths: set[str] = set()
        self.loading_total = 0
        self.loading_complete = False
        self.input_visible = False
        self.record_started_at = 0.0
        self.bot_text_target = ""
        self.subtitle_mode = "idle"
        self.subtitle_origin_char = 0
        self.subtitle_word_start = -1
        self.subtitle_word_end = -1
        self.subtitle_pos_smooth = 0.0
        self.subtitle_done_rendered = False
        self.subtitle_idle_seconds = 0.0
        self.last_subtitle_tick = time.monotonic()

        self._setup_window()
        self._setup_widgets()
        self._setup_bridge()
        self._setup_hotkey()
        self._setup_wake_word()
        self._load_animations()
        self._start_timers()
        self._setup_live_reload()

    def _build_wake_word_listener(self, config: dict[str, Any]) -> WakeWordListener:
        wake_word_config = dict(config.get("wake_word") or {})
        wake_word_config["command_vad_threshold"] = float(config.get("vad_threshold") or 0.004)
        return WakeWordListener(
            wake_word_config,
            self.wake_word_detected.emit,
            self.wake_word_command_ready.emit,
        )

    def _configured_hotkeys(self) -> list[str]:
        raw = self.config.get("whisper_hotkeys")
        if isinstance(raw, str):
            candidates = raw.split(",")
        elif isinstance(raw, list):
            candidates = list(raw)
        else:
            candidates = []
        legacy = self.config.get("whisper_hotkey")
        if legacy:
            candidates.insert(0, legacy)

        hotkeys: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            value = str(candidate or "").strip().lower()
            if not value or value in seen:
                continue
            seen.add(value)
            hotkeys.append(value)
        return hotkeys or ["f8"]

    def _setup_window(self) -> None:
        window_cfg = self.persona_config["window"]
        self.setFixedSize(int(window_cfg["width"]), int(window_cfg["height"]))
        self.setWindowTitle("BitMon Py")
        flags = Qt.FramelessWindowHint | Qt.Tool
        if bool(self.config.get("overlay_always_on_top", window_cfg.get("always_on_top", True))):
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        transparent = bool(self.config.get("overlay_mode", True)) and bool(window_cfg.get("transparent", True))
        self.setAttribute(Qt.WA_TranslucentBackground, transparent)
        self.setFocusPolicy(Qt.StrongFocus)
        # Keep a normal arrow over the pet; otherwise Windows shows the "busy"
        # spinning cursor while sprite sheets are still decoding in the worker.
        self.setCursor(Qt.ArrowCursor)

    def _setup_widgets(self) -> None:
        sprite_cfg = self.persona_config["sprite"]
        subtitle_cfg = self.persona_config["subtitle"]
        input_cfg = self.persona_config["input"]

        self.sprite_label = QLabel(self)
        sprite_size = int(sprite_cfg.get("display_size") or 282)
        sprite_x = int(sprite_cfg.get("x") or 249)
        sprite_y = int(sprite_cfg.get("y") or 219)
        self.sprite_label.setGeometry(sprite_x - sprite_size // 2, sprite_y - sprite_size // 2, sprite_size, sprite_size)
        self.sprite_label.setScaledContents(True)
        self.sprite_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        # Shown in place of the pet while its sprite sheets are still decoding.
        self.loading_bar = QProgressBar(self)
        self.loading_bar.setTextVisible(True)
        self.loading_bar.setFormat("Loading %p%")
        self.loading_bar.setAlignment(Qt.AlignCenter)
        self.loading_bar.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.loading_bar.setStyleSheet(
            """
            QProgressBar {
                background: rgba(19, 21, 34, 200);
                border: 1px solid rgba(99, 102, 241, 160);
                border-radius: 9px;
                color: #e2e8f0;
                font-size: 12px;
                font-weight: 800;
                text-align: center;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6366f1, stop:1 #a78bfa);
                border-radius: 8px;
            }
            """
        )
        self._position_loading_bar()
        self.loading_bar.hide()

        self.subtitle_box = QWidget(self)
        self.subtitle_box.setGeometry(
            int(subtitle_cfg.get("x") or 3),
            int(subtitle_cfg.get("y") or 337),
            int(subtitle_cfg.get("width") or 532),
            int(subtitle_cfg.get("height") or 168),
        )
        self.subtitle_box.setObjectName("subtitleBox")
        self.subtitle_box.hide()

        # Optional character-name tag drawn above the subtitle text.
        self.subtitle_name = QLabel(self.subtitle_box)
        self.subtitle_name.setObjectName("subtitleName")
        self.subtitle_name.hide()

        self.subtitle_label = QLabel(self.subtitle_box)
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setTextFormat(Qt.RichText)
        self.subtitle_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.input_group = QWidget(self)
        input_x = int(input_cfg.get("x") or 45)
        input_y = int(input_cfg.get("y") or 530)
        input_w = int(input_cfg.get("width") or 448)
        input_h = int(input_cfg.get("height") or 50)
        mic_w = 56
        self.input_group.setGeometry(input_x, input_y, input_w, input_h + 18)
        self.input_group.setMouseTracking(True)
        self.input_opacity = QGraphicsOpacityEffect(self.input_group)
        self.input_group.setGraphicsEffect(self.input_opacity)
        self.input_opacity.setOpacity(0.0)

        self.input_animation = None
        self.input_line = QLineEdit(self.input_group)
        self.input_line.setGeometry(0, 0, input_w, input_h)
        self.input_line.setPlaceholderText("Type your message")
        self.input_line.returnPressed.connect(self._send_text_from_input)

        self.mic_button = QPushButton(self.input_group)
        self.mic_button.setGeometry(input_w - mic_w, 0, mic_w, input_h)
        self.mic_button.setCursor(Qt.PointingHandCursor)
        self.mic_button.setToolTip("Talk")
        self.mic_button.setIcon(QIcon(str(ICONS_DIR / "mic.svg")))
        self.mic_button.setIconSize(QSize(20, 20))
        self.mic_button.pressed.connect(lambda: self._on_mic_pressed("button"))
        self.mic_button.released.connect(lambda: self._on_mic_released("button"))
        self.mic_button.clicked.connect(self._on_mic_clicked)

        self.timer_label = QLabel("0s", self.input_group)
        self.timer_label.setGeometry(input_w - mic_w, input_h + 1, mic_w, 15)
        self.timer_label.setAlignment(Qt.AlignCenter)
        self.timer_label.setStyleSheet("color: rgba(255,255,255,210); font-size: 10px; font-weight: 800; background: transparent;")
        self.timer_label.hide()

        self.input_group.installEventFilter(self)
        self.input_line.installEventFilter(self)
        self.mic_button.installEventFilter(self)
        self._apply_chat_theme()

    def _theme(self) -> dict[str, Any]:
        return self.theme_config

    def _bg_css(self, section: dict[str, Any]) -> str:
        color1 = _rgba_css(section["bg_color"], section["bg_opacity"])
        if not section.get("bg_gradient"):
            return color1
        color2 = _rgba_css(section.get("bg_color2") or section["bg_color"], section["bg_opacity"])
        direction = str(section.get("gradient_direction") or "vertical")
        x2, y2 = {"horizontal": (1, 0), "diagonal": (1, 1)}.get(direction, (0, 1))
        return f"qlineargradient(x1:0, y1:0, x2:{x2}, y2:{y2}, stop:0 {color1}, stop:1 {color2})"

    def _subtitle_box_style(self) -> str:
        theme = self._theme()["subtitle"]
        style = (
            f"background: {self._bg_css(theme)}; "
            f"border-radius: {int(theme['border_radius'])}px;"
        )
        if int(theme.get("border_width") or 0) > 0:
            style += f" border: {int(theme['border_width'])}px solid {theme['border_color']};"
        return f"#subtitleBox {{ {style} }}"

    def _subtitle_label_style(self, font_family: str) -> str:
        theme = self._theme()["subtitle"]
        return (
            f"font-family: '{font_family}'; font-size: {int(theme.get('font_size') or 18)}px; "
            f"font-weight: 700; color: {theme['text_color']}; background: transparent; border: none;"
        )

    def _input_line_style(self) -> str:
        theme = self._theme()["input"]
        radius = int(theme["border_radius"])
        border_width = max(0, int(theme.get("border_width", 1)))
        focus_opacity = min(float(theme["bg_opacity"]) + 25.0, 100.0)
        return f"""
            QLineEdit {{
                background: {self._bg_css(theme)};
                border: {border_width}px solid {_rgba_css(theme['border_color'], 80)};
                border-radius: {radius}px;
                color: {theme['text_color']};
                padding-left: 18px;
                padding-right: 82px;
                font-size: 16px;
                font-weight: 600;
            }}
            QLineEdit:focus {{
                background: {self._bg_css({**theme, 'bg_opacity': focus_opacity})};
                border: {border_width + 1}px solid {_rgba_css(theme['focus_border_color'], 80)};
            }}
            """

    def _mic_button_style(self, color: str, hover: str) -> str:
        radius = int(self._theme()["input"]["border_radius"])
        return f"""
        QPushButton {{
            background: {color};
            border: none;
            border-top-right-radius: {radius}px;
            border-bottom-right-radius: {radius}px;
        }}
        QPushButton:hover {{
            background: {hover};
        }}
        """

    def _mic_idle_style(self) -> str:
        color = str(self._theme()["input"]["mic_color"] or "#2563eb")
        return self._mic_button_style(color, _lighten(color, 0.15))

    def _mic_recording_style(self) -> str:
        color = str(self._theme()["input"].get("mic_recording_color") or "#16a34a")
        return self._mic_button_style(color, _lighten(color, 0.15))

    def _mic_icon(self, color: str) -> QIcon:
        """mic.svg tinted with the theme color (the file itself is white)."""
        cached = self._mic_icon_cache.get(color)
        if cached is not None:
            return cached
        try:
            svg = (ICONS_DIR / "mic.svg").read_text(encoding="utf-8")
            # Render at 64px so QIcon downscales crisply to the 20px icon size.
            svg = svg.replace("<svg ", '<svg width="64" height="64" ', 1)
            svg = svg.replace('"white"', f'"{color}"')
            pixmap = QPixmap()
            if not pixmap.loadFromData(svg.encode("utf-8"), "SVG"):
                raise ValueError("SVG render failed")
            icon = QIcon(pixmap)
        except (OSError, ValueError):
            icon = QIcon(str(ICONS_DIR / "mic.svg"))
        self._mic_icon_cache[color] = icon
        return icon

    def _apply_mic_style(self, recording: bool) -> None:
        input_theme = self._theme()["input"]
        if recording:
            self.mic_button.setStyleSheet(self._mic_recording_style())
            icon_color = str(input_theme.get("mic_icon_recording_color") or "#ffffff")
        else:
            self.mic_button.setStyleSheet(self._mic_idle_style())
            icon_color = str(input_theme.get("mic_icon_color") or "#ffffff")
        self.mic_button.setIcon(self._mic_icon(icon_color))

    def _apply_chat_theme(self) -> None:
        """(Re)apply every theme-driven style; layout geometry stays untouched."""
        font_family = self._load_subtitle_font()

        self.subtitle_box.setStyleSheet(self._subtitle_box_style())
        self.subtitle_label.setStyleSheet(self._subtitle_label_style(font_family))

        # The character name is shown inline (see _subtitle_name_prefix); the
        # separate header label is unused, keep it hidden.
        self.subtitle_name.setVisible(False)
        self.subtitle_label.setGeometry(
            12,
            10,
            self.subtitle_box.width() - 24,
            self.subtitle_box.height() - 20,
        )

        self.input_line.setStyleSheet(self._input_line_style())
        recording = self.push_to_talk_active or self.wake_word_capture_active or self.mic_active
        self._apply_mic_style(recording)

    def _load_subtitle_font(self) -> str:
        configured = str(self._theme().get("font_file") or "").strip()
        candidates = [configured, "DynaPuff.ttf"] if configured else ["DynaPuff.ttf"]
        for name in candidates:
            path = FONTS_DIR / Path(name).name
            if not path.exists():
                continue
            font_id = QFontDatabase.addApplicationFont(str(path))
            if font_id == -1:
                continue
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                return families[0]
        return "Arial"

    def _setup_bridge(self) -> None:
        self._connect_bridge_signals()
        self.hotkey_pressed.connect(self._on_hotkey_pressed)
        self.hotkey_released.connect(self._on_hotkey_released)
        self.wake_word_detected.connect(self._on_wake_word_detected)
        self.wake_word_command_ready.connect(self._on_wake_word_command_ready)

    def _connect_bridge_signals(self) -> None:
        self.bridge.connected_changed.connect(self._on_connected_changed)
        self.bridge.user_speech_started.connect(self._clear_subtitle_cycle)
        self.bridge.user_transcript.connect(self._on_user_transcript)
        self.bridge.bot_response_started.connect(self._on_bot_response_started)
        self.bridge.bot_transcript_delta.connect(self._on_bot_delta)
        self.bridge.bot_response_done.connect(self._on_bot_response_done)
        self.bridge.bot_response_interrupted.connect(self._on_bot_response_interrupted)
        self.bridge.subtitle_clear.connect(self._clear_subtitle_cycle)
        self.bridge.audio_delta.connect(self._on_audio_delta)
        self.bridge.error.connect(self._on_bridge_error)
        # Connect to the backend session right away (the launcher only starts the
        # pet after the backend is ready) so the Config chat can drive the pet
        # without needing a first message typed in the pet itself.
        self.bridge.ensure_started()

    def _setup_hotkey(self) -> None:
        if any(hotkey.startswith("mouse") for hotkey in self.whisper_hotkeys):
            self._setup_mouse_hotkey()
        keyboard_hotkeys = {hotkey for hotkey in self.whisper_hotkeys if not hotkey.startswith("mouse")}
        if not keyboard_hotkeys:
            print(f"[Hotkey] microphone push-to-talk: hold {', '.join(self.whisper_hotkeys)}")
            return
        try:
            from pynput import keyboard as pynput_keyboard
        except Exception as exc:
            print(f"[Hotkey] pynput unavailable: {exc}")
            return

        def key_name(key) -> str:
            char = getattr(key, "char", None)
            if char:
                return str(char).lower()
            name = getattr(key, "name", None)
            name = str(name or "").lower()
            aliases = {
                "ctrl_l": "ctrl",
                "ctrl_r": "ctrl",
                "alt_l": "alt",
                "alt_r": "alt",
                "shift_l": "shift",
                "shift_r": "shift",
                "cmd_l": "cmd",
                "cmd_r": "cmd",
            }
            return aliases.get(name, name)

        def on_press(key) -> None:
            value = key_name(key)
            if value not in keyboard_hotkeys or value in self.hotkeys_down:
                return
            self.hotkeys_down.add(value)
            self.hotkey_pressed.emit(value)

        def on_release(key) -> None:
            value = key_name(key)
            if value not in keyboard_hotkeys:
                return
            self.hotkeys_down.discard(value)
            self.hotkey_released.emit(value)

        self.hotkey_listener = pynput_keyboard.Listener(on_press=on_press, on_release=on_release)
        self.hotkey_listener.daemon = True
        self.hotkey_listener.start()
        print(f"[Hotkey] microphone push-to-talk: hold {', '.join(self.whisper_hotkeys)}")

    def _teardown_hotkey(self) -> None:
        for listener in (self.hotkey_listener, self.hotkey_mouse_listener):
            if listener is None:
                continue
            try:
                listener.stop()
            except Exception:
                pass
        self.hotkey_listener = None
        self.hotkey_mouse_listener = None
        self.hotkeys_down.clear()
        self.active_hotkey_id = None

    def _setup_wake_word(self) -> None:
        if not self.wake_word_listener.enabled:
            return
        self.wake_word_listener.start()
        self.audio_input.start(forward_audio=False)
        self._update_input_visibility()

    def _setup_mouse_hotkey(self) -> None:
        try:
            from pynput import mouse as pynput_mouse
        except Exception as exc:
            print(f"[Hotkey] pynput mouse unavailable: {exc}")
            return
        mapping = {
            "mouse1": "left",
            "mouse2": "middle",
            "mouse3": "right",
            "mouse4": "x1",
            "mouse5": "x2",
        }
        mouse_hotkeys = {
            mapping.get(hotkey, hotkey.removeprefix("mouse"))
            for hotkey in self.whisper_hotkeys
            if hotkey.startswith("mouse")
        }

        def button_name(button) -> str:
            return str(getattr(button, "name", button)).lower().replace("button.", "")

        def on_click(_x, _y, button, pressed) -> None:
            value = button_name(button)
            if value not in mouse_hotkeys:
                return
            hotkey_id = f"mouse:{value}"
            if pressed:
                if hotkey_id in self.hotkeys_down:
                    return
                self.hotkeys_down.add(hotkey_id)
                self.hotkey_pressed.emit(hotkey_id)
            else:
                self.hotkeys_down.discard(hotkey_id)
                self.hotkey_released.emit(hotkey_id)

        self.hotkey_mouse_listener = pynput_mouse.Listener(on_click=on_click)
        self.hotkey_mouse_listener.daemon = True
        self.hotkey_mouse_listener.start()
        print("[Hotkey] microphone mouse push-to-talk active")

    def _load_animations(self) -> None:
        assets_dir = persona_assets_dir()
        self.anims = {kind: [] for kind in ANIM_KINDS}
        disabled_idle_cfgs: list[dict[str, Any]] = []

        def build_animation(anim_cfg: dict[str, Any], kind: str) -> SpriteAnimation | None:
            try:
                return SpriteAnimation(
                    assets_dir / Path(str(anim_cfg.get("file") or "")).name,
                    frame_size=int(anim_cfg.get("frame_size") or DEFAULT_FRAME_SIZE),
                    columns=int(anim_cfg.get("columns") or 1),
                    rows=int(anim_cfg.get("rows") or 1),
                    used_frames=int(anim_cfg.get("used_frames") or 1),
                    fps=int(anim_cfg.get("fps") or 24),
                    name=str(anim_cfg.get("name") or ""),
                    kind=kind,
                    idle_after=float(anim_cfg.get("idle_after") or 0.0),
                    oneshot=kind in ONESHOT_KINDS,
                )
            except Exception as exc:
                print(f"[Persona] skipped animation {anim_cfg.get('name')}: {exc}")
                return None

        for anim_cfg in self.persona_config.get("animations", []):
            if not isinstance(anim_cfg, dict):
                continue
            kind = str(anim_cfg.get("kind") or "idle").lower()
            if kind not in self.anims:
                kind = "idle"
            if anim_cfg.get("enabled", True) is False:
                # Toggled off in the persona editor: never played. Disabled
                # idles are kept aside so the pet still has a base animation
                # if the user turns every idle off.
                if kind == "idle":
                    disabled_idle_cfgs.append(anim_cfg)
                continue
            anim = build_animation(anim_cfg, kind)
            if anim is not None:
                self.anims[kind].append(anim)
        if not self.anims["idle"] and disabled_idle_cfgs:
            print("[Persona] every idle animation is disabled; using them anyway")
            for anim_cfg in disabled_idle_cfgs:
                anim = build_animation(anim_cfg, "idle")
                if anim is not None:
                    self.anims["idle"].append(anim)
        # Built-in fallbacks, but only if the assets exist â€” a blank/draft
        # persona may legitimately have no sprites yet.
        if not self.anims["idle"]:
            try:
                self.anims["idle"].append(
                    SpriteAnimation(assets_dir / "idle_1.png", columns=4, rows=24, used_frames=96, kind="idle")
                )
            except Exception:
                pass
        if not self.anims["talk"]:
            try:
                self.anims["talk"].append(
                    SpriteAnimation(assets_dir / "fala_3.png", columns=10, rows=5, used_frames=48, kind="talk")
                )
            except Exception:
                pass

        # Reset the animation pointer; the chosen sheet starts rendering once
        # its bitmap has been decoded in the background (see _on_sheet_loaded).
        self.current_anim = None
        self.anim_state = ""
        self.pending_anim_state = None
        self.anim_oneshot = False
        self._begin_sheet_loading()
        if self.anims["start"]:
            self._play_start()
        else:
            self._play_idle()

    def _begin_sheet_loading(self) -> None:
        self.sheet_loader.reset()
        self.loaded_paths = set()
        # Visible-first ordering so the pet appears as soon as possible.
        ordered_paths: list[Path] = []
        seen: set[str] = set()
        for kind in ("start", "idle", "thinking", "listening", "talk", "poke", "error"):
            for anim in self.anims[kind]:
                key = str(anim.path)
                if key not in seen:
                    seen.add(key)
                    ordered_paths.append(anim.path)
        self.loading_total = len(ordered_paths)
        if self.loading_total == 0:
            # Blank/draft persona with no sprites yet: nothing to load.
            self._finish_loading()
            return
        self._start_loading_indicator()
        for path in ordered_paths:
            self.sheet_loader.request(path)

    def _on_sheet_loaded(self, path_str: str, image: object) -> None:
        if not isinstance(image, QImage) or image.isNull():
            return
        for pool in self.anims.values():
            for anim in pool:
                if not anim.has_data and str(anim.path) == path_str:
                    anim.set_image(image)
        self.loaded_paths.add(path_str)
        self._update_loading_progress()

    def _position_loading_bar(self) -> None:
        if not hasattr(self, "loading_bar"):
            return
        sprite_cfg = self.persona_config["sprite"]
        sprite_size = int(sprite_cfg.get("display_size") or 282)
        sprite_x = int(sprite_cfg.get("x") or 249)
        sprite_y = int(sprite_cfg.get("y") or 219)
        bar_w = max(160, min(sprite_size, 320))
        bar_h = 26
        self.loading_bar.setGeometry(sprite_x - bar_w // 2, sprite_y - bar_h // 2, bar_w, bar_h)

    def _start_loading_indicator(self) -> None:
        self.loading_complete = False
        self._position_loading_bar()
        self.loading_bar.setRange(0, max(1, self.loading_total))
        self.loading_bar.setValue(0)
        self.loading_bar.show()
        self.loading_bar.raise_()

    def _update_loading_progress(self) -> None:
        if self.loading_complete or not hasattr(self, "loading_bar"):
            return
        if self.loading_total > 0:
            self.loading_bar.setRange(0, self.loading_total)
            self.loading_bar.setValue(min(len(self.loaded_paths), self.loading_total))

    def _finish_loading(self) -> None:
        if self.loading_complete:
            return
        self.loading_complete = True
        if hasattr(self, "loading_bar"):
            self.loading_bar.hide()

    def _setup_live_reload(self) -> None:
        self.reload_debounce_timer = QTimer(self)
        self.reload_debounce_timer.setSingleShot(True)
        self.reload_debounce_timer.timeout.connect(self._reload_runtime_state)

        self.file_watcher = QFileSystemWatcher(self)
        self._refresh_file_watcher_paths()
        self.file_watcher.fileChanged.connect(lambda _path: self._schedule_runtime_reload())
        self.file_watcher.directoryChanged.connect(lambda _path: self._schedule_runtime_reload())

        self.reload_poll_timer = QTimer(self)
        self.reload_poll_timer.timeout.connect(self._reload_runtime_state)
        self.reload_poll_timer.start(1200)

    def _refresh_file_watcher_paths(self) -> None:
        watched = set(self.file_watcher.files()) | set(self.file_watcher.directories()) if hasattr(self, "file_watcher") else set()
        paths: list[str] = []
        for path in (
            BACKEND_CONFIG_PATH,
            LEGACY_BACKEND_CONFIG_PATH,
            CONFIG_PATH,
            PERSONAS_MANIFEST_PATH,
            persona_config_path(),
            THEME_CONFIG_PATH,
        ):
            if path.exists():
                paths.append(str(path))
        for path in (ASSETS_DIR, FONTS_DIR, ICONS_DIR):
            path.mkdir(parents=True, exist_ok=True)
            paths.append(str(path))
        package_assets = persona_assets_dir()
        if package_assets.exists():
            paths.append(str(package_assets))
        missing = [path for path in paths if path not in watched]
        if missing:
            self.file_watcher.addPaths(missing)

    def _schedule_runtime_reload(self) -> None:
        self.reload_debounce_timer.start(180)

    def _reload_runtime_state(self) -> None:
        self._refresh_file_watcher_paths()
        try:
            client_config = fetch_client_config(timeout=0.35)
        except Exception:
            client_config = None
        if isinstance(client_config, dict):
            merged_client = merge_client_config(client_config)
            signature = stable_signature(merged_client)
            if signature != self.config_signature:
                self._apply_client_config(merged_client)

        persona_config = load_persona_config()
        persona_signature = stable_signature(persona_config)
        current_assets_signature = asset_signature()
        if persona_signature != self.persona_config_signature or current_assets_signature != self.assets_signature:
            self.persona_config = persona_config
            self.persona_config_signature = persona_signature
            self.assets_signature = current_assets_signature
            self._apply_persona_layout()
            self._load_animations()
            print("[Persona] config/assets reloaded")

        theme_config = get_theme_config()
        theme_signature = stable_signature(theme_config)
        if theme_signature != self.theme_signature:
            self.theme_config = theme_config
            self.theme_signature = theme_signature
            self._apply_chat_theme()
            self._render_subtitle()
            print("[Theme] config reloaded")

    def _apply_persona_layout(self) -> None:
        was_visible = self.isVisible()
        self._setup_window()
        if was_visible:
            self.show()

        sprite_cfg = self.persona_config["sprite"]
        sprite_size = int(sprite_cfg.get("display_size") or 282)
        sprite_x = int(sprite_cfg.get("x") or 249)
        sprite_y = int(sprite_cfg.get("y") or 219)
        self.sprite_label.setGeometry(sprite_x - sprite_size // 2, sprite_y - sprite_size // 2, sprite_size, sprite_size)
        self._position_loading_bar()

        subtitle_cfg = self.persona_config["subtitle"]
        self.subtitle_box.setGeometry(
            int(subtitle_cfg.get("x") or 3),
            int(subtitle_cfg.get("y") or 337),
            int(subtitle_cfg.get("width") or 532),
            int(subtitle_cfg.get("height") or 168),
        )
        input_cfg = self.persona_config["input"]
        input_x = int(input_cfg.get("x") or 45)
        input_y = int(input_cfg.get("y") or 530)
        input_w = int(input_cfg.get("width") or 448)
        input_h = int(input_cfg.get("height") or 50)
        mic_w = 56
        self.input_group.setGeometry(input_x, input_y, input_w, input_h + 18)
        self.input_line.setGeometry(0, 0, input_w, input_h)
        self.mic_button.setGeometry(input_w - mic_w, 0, mic_w, input_h)
        self.timer_label.setGeometry(input_w - mic_w, input_h + 1, mic_w, 15)
        self._apply_chat_theme()
        self._render_subtitle()

    def _apply_client_config(self, new_config: dict[str, Any]) -> None:
        old_revision = str(self.config.get("config_revision") or "")
        old_hotkeys = list(self.whisper_hotkeys)
        old_wake_signature = stable_signature(self.config.get("wake_word") or {})
        old_vad = float(self.config.get("vad_threshold") or 0.004)
        self.config = new_config
        self.config_signature = stable_signature(new_config)
        self.stt_provider = str(self.config.get("stt_provider") or "whisper").lower()
        self.whisper_hotkeys = self._configured_hotkeys()
        self.whisper_hotkey = self.whisper_hotkeys[0]
        self.show_user_subtitle = bool(self.config.get("debug_user_subtitle", True))
        self.character_name = str(self.config.get("character_name", "") or "").strip()

        self._apply_persona_layout()
        self._cancel_active_capture()
        self.audio_input.stop(keep_stream=False)

        new_wake_signature = stable_signature(self.config.get("wake_word") or {})
        new_vad = float(self.config.get("vad_threshold") or 0.004)
        if old_wake_signature != new_wake_signature or old_vad != new_vad:
            self.wake_word_listener.stop()
            self.wake_word_listener = self._build_wake_word_listener(self.config)

        self.audio_input.update_settings(
            gain=float(self.config.get("mic_gain", 10.0)),
            vad_threshold=new_vad,
            wake_word_listener=self.wake_word_listener,
        )

        if old_hotkeys != self.whisper_hotkeys:
            self._teardown_hotkey()
            self._setup_hotkey()

        new_revision = str(self.config.get("config_revision") or "")
        if old_revision != new_revision:
            self._replace_bridge()

        self._setup_wake_word()
        self._update_input_visibility()
        print(f"[Config] live reloaded stt={self.stt_provider}")

    def _replace_bridge(self) -> None:
        self.suppress_bridge_disconnect = True
        self.bridge.stop()
        self.audio_output.reset_counters()
        self.response_audio_done = True
        self._drain_audio_queue()
        self.bridge = SessionBridge(self.audio_queue)
        self._connect_bridge_signals()
        QTimer.singleShot(800, lambda: setattr(self, "suppress_bridge_disconnect", False))

    def _drain_audio_queue(self) -> None:
        while True:
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                return

    def _cancel_active_capture(self) -> None:
        if self.push_to_talk_active:
            self.audio_input.end_capture()
        self.push_to_talk_active = False
        self.wake_word_capture_active = False
        self.hotkeys_down.clear()
        self.active_hotkey_id = None
        self.record_started_at = 0.0
        self.timer_label.hide()
        self.mic_active = False
        self._apply_mic_style(False)

    def _start_timers(self) -> None:
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._next_sprite_frame)
        self.anim_timer.start(1000 // 24)

        self.audio_timer = QTimer(self)
        self.audio_timer.timeout.connect(self._check_audio_state)
        self.audio_timer.start(80)

        self.record_timer = QTimer(self)
        self.record_timer.timeout.connect(self._update_record_timer)
        self.record_timer.start(200)

        self.input_state_timer = QTimer(self)
        self.input_state_timer.timeout.connect(self._update_input_visibility)
        self.input_state_timer.start(180)

        self.subtitle_reveal_timer = QTimer(self)
        self.subtitle_reveal_timer.timeout.connect(self._update_subtitle_karaoke)
        self.subtitle_reveal_timer.start(30)

    def _play_idle(self) -> None:
        self._request_anim_state("idle")

    def _play_talk(self) -> None:
        self._request_anim_state("talk")

    def _play_thinking(self) -> None:
        self._request_anim_state("thinking")

    def _play_listening(self) -> None:
        self._request_anim_state("listening")

    def _play_start(self) -> None:
        self._request_anim_state("start")

    def _play_poke(self) -> None:
        # Instant reaction, no graceful cross-fade wait.
        if self._resolve_anim_state("poke") == "poke":
            self._switch_anim_now("poke")

    def _play_error(self) -> None:
        if self._resolve_anim_state("error") == "error":
            self._switch_anim_now("error")

    def _resolve_anim_state(self, state: str) -> str | None:
        seen: set[str] = set()
        while state and state not in seen:
            if self.anims.get(state):
                return state
            seen.add(state)
            state = ANIM_FALLBACKS.get(state)  # poke/error have no fallback
        return None

    def _eligible_idle_anims(self) -> list[SpriteAnimation]:
        # Sonic-style: a "long idle" sprite only becomes a candidate once the
        # pet has been continuously idle for its idle_after threshold.
        idle = self.anims.get("idle") or []
        elapsed = time.monotonic() - self.idle_since
        eligible = [anim for anim in idle if anim.idle_after <= elapsed]
        return eligible or idle

    def _switch_anim_now(self, state: str) -> None:
        if state == "idle":
            pool = self._eligible_idle_anims()
        else:
            pool = self.anims.get(state) or self.anims.get("idle") or []
        if not pool:
            return
        candidates = [anim for anim in pool if anim is not self.current_anim]
        previous_state = self.anim_state
        self.current_anim = random.choice(candidates or pool)
        self.current_anim.reset()
        self.anim_state = state
        self.talking = state == "talk"
        self.anim_oneshot = self.current_anim.oneshot
        self.pending_anim_state = None
        self.anim_fast_finish = False
        if state == "idle" and previous_state != "idle":
            self.idle_since = time.monotonic()
        if state == "thinking" and previous_state != "thinking":
            self.thinking_since = time.monotonic()
        self._apply_anim_speed()

    def _request_anim_state(self, state: str) -> None:
        # Gracefully fall back when a persona has no sprites for this kind.
        resolved = self._resolve_anim_state(state)
        if resolved is None:
            return
        state = resolved
        if self.current_anim is None or not self.anim_state:
            self._switch_anim_now(state)
            return
        if self.anim_state == state and self.pending_anim_state is None and not self.anim_oneshot:
            self._apply_anim_speed()
            return
        self.pending_anim_state = state
        self.anim_fast_finish = True
        self._apply_anim_speed()

    def _apply_anim_speed(self) -> None:
        if not self.current_anim or not hasattr(self, "anim_timer"):
            return
        fps = max(1, int(self.current_anim.fps))
        if self.anim_fast_finish:
            fps *= 2
        self.anim_timer.setInterval(max(1, int(1000 / fps)))

    def _next_sprite_frame(self) -> None:
        if self.current_anim is None:
            return
        frame = self.current_anim.next_frame()
        if frame is None:
            return  # sheet still decoding in the background
        self.sprite_label.setPixmap(frame)
        if not self.loading_complete:
            self._finish_loading()  # pet is visible now, drop the loading bar
        if not self.current_anim.just_looped:
            return
        if self.pending_anim_state is not None:
            self._switch_anim_now(self.pending_anim_state)
        elif self.anim_oneshot:
            # One-shot intro (start) finished -> settle into idle.
            self._switch_anim_now("idle")
        elif self.anim_state == "idle":
            # Re-roll each loop so delayed (Sonic-style) idles can surface.
            self._switch_anim_now("idle")
        elif self.anim_state == "thinking" and self.anims.get("thinking"):
            self._switch_anim_now("thinking")

    def _check_audio_state(self) -> None:
        if self.talking and self.response_audio_done and not self.audio_output.has_pending_audio():
            self._play_idle()
        elif (
            self.anim_state == "thinking"
            and self.thinking_since
            and (time.monotonic() - self.thinking_since) > THINKING_TIMEOUT
        ):
            self._play_idle()

    def _fade_input(self, visible: bool) -> None:
        from PySide6.QtCore import QPropertyAnimation

        if self.input_visible == visible and abs(self.input_opacity.opacity() - (1.0 if visible else 0.0)) < 0.02:
            return
        self.input_visible = visible
        if self.input_animation is not None:
            self.input_animation.stop()
        self.input_animation = QPropertyAnimation(self.input_opacity, b"opacity", self)
        self.input_animation.setDuration(180)
        self.input_animation.setStartValue(self.input_opacity.opacity())
        self.input_animation.setEndValue(1.0 if visible else 0.0)
        self.input_animation.setEasingCurve(QEasingCurve.OutCubic)
        self.input_animation.start()

    def _should_show_input(self) -> bool:
        return (
            self.mic_active
            or self.push_to_talk_active
            or self.wake_word_capture_active
            or self.wake_word_listener.enabled
            or self.input_line.hasFocus()
            or self.input_group.underMouse()
            or self.isActiveWindow()
        )

    def _update_input_visibility(self) -> None:
        self._fade_input(self._should_show_input())

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched in {self.input_group, self.input_line, self.mic_button}:
            if event.type() in (QEvent.Enter, QEvent.FocusIn):
                self._fade_input(True)
            elif event.type() in (QEvent.Leave, QEvent.FocusOut):
                self._update_input_visibility()
        return super().eventFilter(watched, event)

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.ActivationChange:
            self._update_input_visibility()
        super().changeEvent(event)

    def _update_record_timer(self) -> None:
        active = self.mic_active or self.push_to_talk_active or self.wake_word_capture_active
        if not active:
            self.timer_label.hide()
            return
        elapsed = max(0, int(time.monotonic() - self.record_started_at)) if self.record_started_at else 0
        self.timer_label.setText(f"{elapsed}s")
        self.timer_label.show()

    def _on_mic_pressed(self, hotkey_id: str) -> None:
        self._start_push_to_talk(hotkey_id)

    def _on_mic_released(self, hotkey_id: str) -> None:
        self._finish_push_to_talk(hotkey_id)

    def _on_mic_clicked(self) -> None:
        return

    def _on_hotkey_pressed(self, hotkey_id: str) -> None:
        self._start_push_to_talk(hotkey_id)

    def _on_hotkey_released(self, hotkey_id: str) -> None:
        self._finish_push_to_talk(hotkey_id)

    def _start_push_to_talk(self, hotkey_id: str) -> None:
        if self.push_to_talk_active or self.wake_word_capture_active:
            return
        self.bridge.ensure_started()
        self.audio_input.start(forward_audio=False)
        self.audio_input.begin_capture()
        self.active_hotkey_id = hotkey_id
        self.push_to_talk_active = True
        self.record_started_at = time.monotonic()
        self._update_record_timer()
        self._apply_mic_style(True)
        self._fade_input(True)
        self._clear_subtitle_cycle()
        self._play_listening()

    def _finish_push_to_talk(self, hotkey_id: str) -> None:
        if (
            not self.push_to_talk_active
            or hotkey_id != self.active_hotkey_id
        ):
            return
        pcm = self.audio_input.end_capture()
        self.audio_input.stop(keep_stream=self.wake_word_listener.enabled)
        self.active_hotkey_id = None
        self.push_to_talk_active = False
        self.record_started_at = 0.0
        self.timer_label.hide()
        self._apply_mic_style(False)
        duration = len(pcm) / 2 / SESSION_RATE
        if duration < 0.15:
            print("[Hotkey] ignored capture: audio is too short")
            self._play_idle()
            self._update_input_visibility()
            return
        print(f"[Hotkey] sending {duration:.2f}s to Whisper")
        self._play_thinking()
        self.bridge.send_audio_clip(pcm)
        self._update_input_visibility()

    def _on_wake_word_detected(self, name: str, score: float) -> None:
        if self.push_to_talk_active or self.mic_active:
            return
        print(f"[WakeWord] command capture started from {name} ({score:.3f})")
        self.bridge.ensure_started()
        self.wake_word_capture_active = True
        self.record_started_at = time.monotonic()
        self._update_record_timer()
        self._apply_mic_style(True)
        self._fade_input(True)
        self._clear_subtitle_cycle()
        self._play_listening()

    def _on_wake_word_command_ready(self, pcm: bytes, reason: str) -> None:
        self.wake_word_capture_active = False
        self.record_started_at = 0.0
        self.timer_label.hide()
        self._apply_mic_style(False)
        if reason == "activation_timeout":
            print("[WakeWord] activation expired without command")
            self._play_idle()
            self._update_input_visibility()
            return
        duration = len(pcm) / 2 / SESSION_RATE
        if duration < 0.3:
            print("[WakeWord] ignored command: audio is too short")
            self._play_idle()
            self._update_input_visibility()
            return
        print(f"[WakeWord] sending {duration:.2f}s command to Whisper ({reason})")
        self._play_thinking()
        self.bridge.send_audio_clip(pcm)
        self._update_input_visibility()

    def _send_text_from_input(self) -> None:
        text = self.input_line.text().strip()
        if not text:
            self.input_line.setFocus()
            return
        self._clear_subtitle_cycle()
        self.input_line.clear()
        self.input_line.setFocus()
        self._on_user_transcript(text)
        self._play_thinking()
        self.bridge.send_text(text)

    def _clear_subtitle_cycle(self) -> None:
        self.subtitle = SubtitleState()
        self.bot_text_target = ""
        self.subtitle_mode = "idle"
        self.subtitle_origin_char = 0
        self.subtitle_word_start = -1
        self.subtitle_word_end = -1
        self.subtitle_pos_smooth = 0.0
        self.subtitle_done_rendered = False
        self.subtitle_idle_seconds = 0.0
        self.subtitle_label.clear()
        self.subtitle_box.hide()

    def _on_connected_changed(self, connected: bool) -> None:
        if not connected:
            if self.suppress_bridge_disconnect:
                return
            if self.push_to_talk_active:
                return
            self.mic_active = False
            self.push_to_talk_active = False
            self.wake_word_capture_active = False
            self.hotkeys_down.clear()
            self.active_hotkey_id = None
            self.record_started_at = 0.0
            self.timer_label.hide()
            self.audio_input.stop(keep_stream=self.wake_word_listener.enabled)
            self._apply_mic_style(False)
            if self.anim_state in ("thinking", "listening"):
                self._play_idle()
            self._update_input_visibility()

    def _on_user_transcript(self, text: str) -> None:
        self._clear_subtitle_cycle()
        if self.show_user_subtitle:
            self.subtitle_mode = "user"
            self.subtitle.user_text = text
            self._render_subtitle()

    def _refresh_audio_output_device(self) -> None:
        """Reopen audio on the current OS default device.

        PortAudio caches the default device at init, so once the OutputStream is
        open it keeps playing to whatever device was default then — even after the
        user switches headphones/speakers. Re-initialising PortAudio refreshes the
        default; we do it at the start of each reply (a rare, well-timed moment)
        and restart the mic stream if it was open."""
        terminate = getattr(sd, "_terminate", None)
        initialize = getattr(sd, "_initialize", None)
        if terminate is None or initialize is None:
            return
        input_was_open = self.audio_input.stream is not None
        try:
            self.audio_output.stop()
            if input_was_open:
                self.audio_input.stop(keep_stream=False)
            terminate()
            initialize()
        except Exception as exc:
            print(f"[Audio] device refresh failed: {exc}")
        if input_was_open:
            try:
                self.audio_input.start(forward_audio=False)
            except Exception as exc:
                print(f"[Audio] mic restart after refresh failed: {exc}")

    def _on_bot_response_started(self) -> None:
        # The output device is refreshed lazily on the first audio chunk, so
        # text-only replies don't needlessly churn the audio streams.
        self._output_device_synced = False
        self.subtitle.bot_text = ""
        self.bot_text_target = ""
        self.subtitle_mode = "bot"
        self.subtitle_origin_char = 0
        self.subtitle_word_start = -1
        self.subtitle_word_end = -1
        self.subtitle_pos_smooth = 0.0
        self.subtitle_done_rendered = False
        self.subtitle_idle_seconds = 0.0
        self.audio_output.reset_counters()
        self.response_audio_done = False
        self._render_subtitle()

    def _on_bot_delta(self, delta: str) -> None:
        self.bot_text_target += delta

    def _on_bot_response_done(self) -> None:
        self.response_audio_done = True
        # Response finished without ever producing audio -> leave the thinking
        # loop instead of spinning until the safety timeout.
        if self.anim_state == "thinking" and not self.audio_output.has_pending_audio():
            self._play_idle()

    def _on_bridge_error(self, text: str) -> None:
        print(f"[Session] {text}")
        # Don't hijack an in-progress reply; only react when we're waiting/idle.
        if self.anim_state in ("idle", "thinking", "listening", ""):
            self._play_error()

    def _on_bot_response_interrupted(self) -> None:
        user_text = self.subtitle.user_text
        self.audio_output.reset_counters()
        self.response_audio_done = True
        # This event precedes every reply (it cuts any audio still playing), so
        # a new answer is being generated right now: hold the thinking loop
        # until its audio arrives instead of dropping to idle. Keep listening
        # untouched in case the user is already recording a follow-up.
        if self.anim_state != "listening":
            self._play_thinking()
        self.bot_text_target = ""
        self.subtitle.bot_text = ""
        self.subtitle_mode = "user" if user_text.strip() else "idle"
        self.subtitle.user_text = user_text
        self.subtitle_origin_char = 0
        self.subtitle_word_start = -1
        self.subtitle_word_end = -1
        self.subtitle_pos_smooth = 0.0
        self.subtitle_done_rendered = False
        self.subtitle_idle_seconds = 0.0
        self._render_subtitle()

    def _update_subtitle_karaoke(self) -> None:
        now = time.monotonic()
        delta = max(0.0, min(now - self.last_subtitle_tick, 0.1))
        self.last_subtitle_tick = now
        if self.subtitle_mode != "bot":
            return
        total_chars = len(self.bot_text_target)
        if total_chars <= 0:
            return

        total_samples = self.audio_output.total_samples()
        pending_samples = self.audio_output.pending_samples()
        played_samples = max(0, total_samples - pending_samples)
        audio_finished = self.response_audio_done and pending_samples <= 50

        if total_samples > 0:
            seconds_played = played_samples / float(SESSION_RATE)
            chars_per_second = 13.0
            if self.response_audio_done:
                total_seconds = total_samples / float(SESSION_RATE)
                if total_seconds > 0.1:
                    chars_per_second = total_chars / total_seconds
            target_pos = min(int(seconds_played * chars_per_second), total_chars)
        else:
            target_pos = 0
        if audio_finished:
            target_pos = total_chars
        if target_pos < self.subtitle_pos_smooth:
            target_pos = int(self.subtitle_pos_smooth)
        self.subtitle_pos_smooth += (target_pos - self.subtitle_pos_smooth) * min(8.0 * delta, 1.0)
        pos_char = int(self.subtitle_pos_smooth)

        if pos_char - self.subtitle_origin_char > 95:
            new_origin = max(0, pos_char - 58)
            while new_origin < pos_char and new_origin < total_chars and self.bot_text_target[new_origin] != " ":
                new_origin += 1
            if new_origin < total_chars:
                new_origin += 1
            self.subtitle_origin_char = new_origin
            self.subtitle_word_start = -2

        if audio_finished:
            if not self.subtitle_done_rendered:
                self.subtitle.bot_text = self.bot_text_target[self.subtitle_origin_char:]
                self.subtitle_word_start = -1
                self.subtitle_word_end = -1
                self.subtitle_done_rendered = True
                self._render_subtitle()
            self.subtitle_idle_seconds += delta
            return

        start, end = self._word_bounds_at(pos_char)
        if start != self.subtitle_word_start or end != self.subtitle_word_end:
            self.subtitle_word_start = start
            self.subtitle_word_end = end
            self.subtitle.bot_text = self.bot_text_target[self.subtitle_origin_char:end]
            self._render_subtitle()
        self.subtitle_idle_seconds = 0.0

    def _word_bounds_at(self, pos_char: int) -> tuple[int, int]:
        text = self.bot_text_target
        n = len(text)
        pos_char = max(0, min(pos_char, n))
        start = pos_char
        while start > 0 and text[start - 1] != " ":
            start -= 1
        end = pos_char
        while end < n and text[end] != " ":
            end += 1
        return start, end

    def _on_audio_delta(self, pcm: bytes) -> None:
        self.response_audio_done = False
        if not self._output_device_synced:
            self._output_device_synced = True
            self._refresh_audio_output_device()
        self.audio_output.push_pcm(pcm)
        if self.anim_state in ("thinking", "listening"):
            # Audio is here: cut straight to talk for tighter lip-sync rather
            # than waiting for the current loop to finish.
            self._switch_anim_now("talk")
        elif not self.talking or self.pending_anim_state == "idle":
            self._play_talk()

    def _subtitle_text_color(self) -> str:
        return str(self._theme()["subtitle"].get("text_color") or "#ffffff")

    def _subtitle_highlight_color(self) -> str:
        # Karaoke colour for the word currently being spoken.
        return str(self._theme()["subtitle"].get("highlight_color") or "#ff5757")

    def _subtitle_name_prefix(self) -> str:
        # "Show character name" (name_tag): when ON, prefix the bot line with the
        # character name; when OFF, show no name at all.
        subtitle = self._theme()["subtitle"]
        if not bool(subtitle.get("name_tag")):
            return ""
        name = self.character_name.upper()
        if not name:
            return ""
        color = str(subtitle.get("name_color") or "#fbbf24")
        return f'<span style="color:{color};">{escape_html(name)}:</span> '

    def _render_subtitle(self) -> None:
        text_color = self._subtitle_text_color()
        parts: list[str] = []
        if self.show_user_subtitle and self.subtitle.user_text.strip():
            user_color = str(self._theme()["subtitle"].get("user_name_color") or "#7dd3fc")
            parts.append(
                f'<span style="color:{user_color};">Me:</span> '
                f'<span style="color:{text_color};">{escape_html(self.subtitle.user_text)}</span>'
            )
        if self.subtitle_mode == "bot" and self.bot_text_target:
            parts.append(self._render_bot_karaoke_html())
        elif self.subtitle.bot_text.strip():
            prefix = self._subtitle_name_prefix()
            parts.append(prefix + f'<span style="color:{text_color};">{escape_html(self.subtitle.bot_text)}</span>')
        html = "<br>".join(parts)
        self.subtitle_label.setText(html)
        self.subtitle_box.setVisible(bool(html))

    def _render_bot_karaoke_html(self) -> str:
        text_color = self._subtitle_text_color()
        prefix = self._subtitle_name_prefix()
        if self.subtitle_done_rendered:
            visible = self.bot_text_target[self.subtitle_origin_char:]
            return prefix + f'<span style="color:{text_color};">{escape_html(visible)}</span>'
        start = max(self.subtitle_word_start, self.subtitle_origin_char)
        end = max(self.subtitle_word_end, start)
        origin = max(0, min(self.subtitle_origin_char, start))
        spoken = self.bot_text_target[origin:start]
        current = self.bot_text_target[start:end]
        html = prefix
        if spoken:
            html += f'<span style="color:{text_color};">{escape_html(spoken)}</span>'
        if current:
            html += (
                f'<span style="color:{self._subtitle_highlight_color()}; font-weight:900;">'
                f'{escape_html(current)}</span>'
            )
        return html

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.setFocus(Qt.MouseFocusReason)
            self._fade_input(True)
        point = event.position().toPoint()
        child = self.childAt(point)
        if event.button() == Qt.LeftButton and child not in {self.input_group, self.input_line, self.mic_button}:
            self.drag_start = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            # Poking the pet body plays a one-shot reaction (only when idle, so
            # it doesn't interrupt talking/thinking).
            if self.anim_state == "idle" and self.sprite_label.geometry().contains(point):
                self._play_poke()

    def mouseMoveEvent(self, event) -> None:
        if self.drag_start is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_start)

    def mouseReleaseEvent(self, event) -> None:
        self.drag_start = None

    def closeEvent(self, event) -> None:
        if self.hotkey_listener is not None:
            self.hotkey_listener.stop()
        if self.hotkey_mouse_listener is not None:
            self.hotkey_mouse_listener.stop()
        self.wake_word_listener.stop()
        self.audio_input.stop()
        self.audio_output.stop()
        self.bridge.stop()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    window = OverlayWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
