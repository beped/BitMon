"""BitMon desktop launcher."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

from core.crash_logger import install_crash_logger


LAUNCHER_DIR = Path(__file__).resolve().parent
ROOT_DIR = LAUNCHER_DIR.parent
BACKEND_MAIN = LAUNCHER_DIR / "main.py"
PERSONA_MAIN = LAUNCHER_DIR / "persona" / "personagem.py"
REQUIREMENTS = LAUNCHER_DIR / "requirements.txt"
I18N_DIR = LAUNCHER_DIR / "web" / "i18n"
# Icons ship inside the backend (web/app-icon.*) so the launcher stays
# self-contained whether backend is nested in a larger repo or published on its
# own. The parent-folder PNG is only used as a last resort when present.
APP_ICON_PNG = LAUNCHER_DIR / "web" / "app-icon.png"
APP_ICON_ICO = LAUNCHER_DIR / "web" / "app-icon.ico"
APP_ICON_SOURCE = ROOT_DIR / "3dicon.png"
LEGACY_NAME = "digi" + "mon"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(f"BITMON_{name}") or os.environ.get(f"{LEGACY_NAME.upper()}_{name}") or default


HOST = _env("HOST", "127.0.0.1")
PORT = int(_env("PORT", "8000"))
CONFIG_URL = f"http://{HOST}:{PORT}/config"
HEALTH_URL = f"http://{HOST}:{PORT}/health"
READY_URL = f"http://{HOST}:{PORT}/health/ready"
SILENT = _env("SILENT", "0").strip() == "1"
INSTALLED = bool(getattr(sys, "frozen", False)) or _env("INSTALLED", "0").strip() == "1"
DEFAULT_LOCALE = "en-US"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _set_windows_app_id() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("BitMon.Launcher")
    except Exception:
        pass


def _app_data_dir() -> Path:
    configured = _env("APP_DATA")
    if configured:
        return Path(configured)
    if INSTALLED:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "BitMon"
    return LAUNCHER_DIR


APP_DATA_DIR = _app_data_dir()
LOG_DIR = APP_DATA_DIR / "logs"
# The venv always lives next to the app (the app folder when running from source,
# LocalAppData when installed). This keeps a published clone fully self-contained.
VENV_DIR = APP_DATA_DIR / "venv"
VENV_PYTHON = VENV_DIR / "Scripts" / "python.exe"
CONFIG_PATH = APP_DATA_DIR / "bitmon_config.json"
LEGACY_CONFIG_PATH = APP_DATA_DIR / f"{LEGACY_NAME}_config.json"
FIRST_RUN_MARKER = APP_DATA_DIR / ".bitmon_first_run_done"
PYTHON_EXE = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
install_crash_logger("bitmon-launcher", LOG_DIR)


def _relaunch_with_pythonw_if_needed() -> None:
    if os.name != "nt" or getattr(sys, "frozen", False):
        return
    if _env("SHOW_CONSOLE", "0").strip() == "1":
        return
    if _env("LAUNCHER_RELAUNCHED", "0").strip() == "1":
        return
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        return
    pythonw = executable.with_name("pythonw.exe")
    if not pythonw.exists():
        return
    env = os.environ.copy()
    env["BITMON_LAUNCHER_RELAUNCHED"] = "1"
    subprocess.Popen(
        [str(pythonw), str(Path(__file__).resolve()), *sys.argv[1:]],
        cwd=str(ROOT_DIR),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
        env=env,
    )
    sys.exit(0)


if __name__ == "__main__":
    _relaunch_with_pythonw_if_needed()


_SVG_CFG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
<path d="M19.14 12.94c.04-.3.06-.61.06-.94s-.02-.64-.07-.94l2.03-1.58c.18-.14.23-.41.12-.61l-1.92-3.32c-.12-.22-.37-.29-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54c-.04-.24-.24-.41-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58c-.18.14-.23.41-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/>
</svg>"""

_SVG_LOGS = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
<path d="M4 5h16v2H4V5zm0 4h16v2H4V9zm0 4h10v2H4v-2zm0 4h16v2H4v-2z"/>
</svg>"""

_SVG_TRAY = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
<path d="M20 18H4v2h16v-2zM12 16l-6-6 1.41-1.41L11 12.17V4h2v8.17l3.59-3.58L18 10l-6 6z"/>
</svg>"""

_SVG_CLOSE = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
<path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/>
</svg>"""

# Outline icons for the advanced-view status cards. They carry a "#FFFFFF"
# placeholder stroke so each card can recolor them to its own accent.
_SVG_CARD_SYSTEM = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>
<polyline points="3.27 6.96 12 12.01 20.73 6.96"/>
<line x1="12" y1="22.08" x2="12" y2="12"/>
</svg>"""

_SVG_CARD_VOICE = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" stroke-width="1.8" stroke-linecap="round">
<line x1="4" y1="10" x2="4" y2="14"/>
<line x1="8" y1="7" x2="8" y2="17"/>
<line x1="12" y1="3" x2="12" y2="21"/>
<line x1="16" y1="7" x2="16" y2="17"/>
<line x1="20" y1="10" x2="20" y2="14"/>
</svg>"""

_SVG_CARD_PERSONA = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
<circle cx="12" cy="7" r="4"/>
</svg>"""


def ensure_user_files() -> None:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        return
    if LEGACY_CONFIG_PATH.exists():
        try:
            LEGACY_CONFIG_PATH.replace(CONFIG_PATH)
            return
        except OSError:
            pass
    example = LAUNCHER_DIR / "bitmon_config.example.json"
    if example.exists():
        CONFIG_PATH.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")


def _read_config() -> dict[str, Any]:
    ensure_user_files()
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def get_character_name() -> str:
    data = _read_config()
    return str(data.get("character", {}).get("name", "BitMon")).strip() or "BitMon"


PERSONA_LIBRARY_PATH = LAUNCHER_DIR / "persona" / "personas" / "personas.json"


def get_active_persona_name() -> str:
    """Name of the currently active persona package (e.g. "Default").

    This is independent of the character name shown in the title: the persona is
    the active sprite/animation package selected in the Persona library.
    """
    try:
        data = json.loads(PERSONA_LIBRARY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return "Default"
    if not isinstance(data, dict):
        return "Default"
    active = str(data.get("active") or "").strip()
    for entry in data.get("personas") or []:
        if isinstance(entry, dict) and str(entry.get("id") or "").strip() == active:
            name = str(entry.get("name") or "").strip()
            if name:
                return name
    return "Default"


def get_config_locale() -> str:
    data = _read_config()
    return str(data.get("ui", {}).get("locale", DEFAULT_LOCALE)).strip() or DEFAULT_LOCALE


def _load_catalog(locale: str) -> dict[str, str]:
    path = I18N_DIR / f"{locale}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {str(key): str(value) for key, value in data.items() if isinstance(value, str)}


class Localizer:
    def __init__(self, locale: str):
        fallback = _load_catalog(DEFAULT_LOCALE)
        current = fallback if locale == DEFAULT_LOCALE else {**fallback, **_load_catalog(locale)}
        self.locale = locale
        self.catalog = current

    def t(self, key: str, values: dict[str, Any] | None = None) -> str:
        text = self.catalog.get(key, key)
        for name, value in (values or {}).items():
            text = text.replace("{" + name + "}", str(value))
        return text


try:
    from PySide6.QtCore import Qt, QTimer, QPoint, QRectF, QPointF, Signal, QObject, QByteArray
    from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QRadialGradient, QBrush, QConicalGradient, QPen, QAction
    from PySide6.QtWidgets import (
        QApplication,
        QWidget,
        QLabel,
        QSystemTrayIcon,
        QMenu,
        QGraphicsDropShadowEffect,
        QHBoxLayout,
        QVBoxLayout,
        QFrame,
        QPlainTextEdit,
        QTabWidget,
    )
    from PySide6.QtSvg import QSvgRenderer
except ImportError as exc:
    print(f"[Launcher] Missing dependency: {exc}\npip install PySide6")
    sys.exit(1)


def _launcher_icon() -> QIcon:
    icon = QIcon()
    # Prefer the icons that ship inside the backend (web/app-icon.*). Only fall
    # back to the parent-folder PNG when neither in-backend icon is present, so a
    # published-standalone backend never depends on files outside its tree.
    in_backend = [path for path in (APP_ICON_ICO, APP_ICON_PNG) if path.exists()]
    for path in in_backend or ([APP_ICON_SOURCE] if APP_ICON_SOURCE.exists() else []):
        icon.addFile(str(path))
    return icon


class ManagedStartupWorker(QObject):
    status_changed = Signal(str, str)
    phase_changed = Signal(str)
    error = Signal(str)
    log_line = Signal(str, str)

    def __init__(self, localizer: Localizer):
        super().__init__()
        self._backend_proc: subprocess.Popen | None = None
        self._frontend_proc: subprocess.Popen | None = None
        self._stopping = False
        self._log_threads: list[threading.Thread] = []
        self._t = localizer.t

    def start_all(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.setdefault("BITMON_HOST", HOST)
        env.setdefault("BITMON_PORT", str(PORT))
        env["BITMON_CONFIG_PATH"] = str(CONFIG_PATH)
        env["BITMON_LOG_DIR"] = str(LOG_DIR)
        env["BITMON_CACHE_DIR"] = str(APP_DATA_DIR / "cache")
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def _requirements_hash(self) -> str:
        digest = hashlib.sha256()
        for filename in ("requirements.txt", "requirements-core.txt"):
            path = LAUNCHER_DIR / filename
            if not path.exists():
                continue
            digest.update(filename.encode("utf-8"))
            digest.update(b"\n")
            digest.update(path.read_bytes())
            digest.update(b"\n")
        return digest.hexdigest()

    def _ensure_environment(self) -> None:
        global PYTHON_EXE
        if not VENV_PYTHON.exists():
            self.status_changed.emit(self._t("launcher.creatingEnvironment"), "#94a3b8")
            subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)
            PYTHON_EXE = str(VENV_PYTHON)

        marker = VENV_DIR / ".bitmon_requirements_installed"
        hash_marker = VENV_DIR / ".bitmon_requirements_hash"
        requirement_hash = self._requirements_hash()
        needs_install = (
            REQUIREMENTS.exists()
            and (
                not marker.exists()
                or not hash_marker.exists()
                or hash_marker.read_text(encoding="utf-8", errors="ignore").strip() != requirement_hash
            )
        )
        if not needs_install:
            return

        self.status_changed.emit(self._t("launcher.installingDependencies"), "#94a3b8")
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with (LOG_DIR / "setup.log").open("a", encoding="utf-8") as log:
            subprocess.run(
                [PYTHON_EXE, "-m", "pip", "install", "-r", str(REQUIREMENTS)],
                cwd=str(LAUNCHER_DIR),
                stdout=log,
                stderr=log,
                check=True,
                env=self._env(),
                creationflags=CREATE_NO_WINDOW,
            )
        marker.write_text(str(time.time()), encoding="utf-8")
        hash_marker.write_text(requirement_hash, encoding="utf-8")

    def _popen(self, args: list[str], cwd: Path, log_name: str, stream_name: str) -> subprocess.Popen:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(
            args,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
            env=self._env(),
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        thread = threading.Thread(target=self._read_process_log, args=(proc, log_name, stream_name), daemon=True)
        thread.start()
        self._log_threads.append(thread)
        return proc

    def _read_process_log(self, proc: subprocess.Popen, log_name: str, stream_name: str) -> None:
        path = LOG_DIR / log_name
        with path.open("a", encoding="utf-8", buffering=1) as log:
            stdout = proc.stdout
            if stdout is None:
                return
            for line in stdout:
                log.write(line)
                text = line.rstrip("\r\n")
                if text:
                    self.log_line.emit(stream_name, text)
        code = proc.poll()
        if code is not None and not self._stopping:
            self.log_line.emit(stream_name, self._t("launcher.processExited", {"code": code}))

    def _emit_log_tail(self, stream_name: str, log_name: str, max_lines: int = 200) -> None:
        path = LOG_DIR / log_name
        if not path.exists():
            self.log_line.emit(stream_name, self._t("launcher.noPreviousLog", {"path": path}))
            return
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-max_lines:]
        except Exception as exc:
            self.log_line.emit(stream_name, self._t("launcher.logReadError", {"error": exc}))
            return
        if not lines:
            self.log_line.emit(stream_name, self._t("launcher.emptyPreviousLog", {"path": path}))
            return
        self.log_line.emit(stream_name, self._t("launcher.previousLog", {"path": path}))
        for line in lines:
            if line:
                self.log_line.emit(stream_name, line)

    def _port_is_open(self) -> bool:
        try:
            with socket.create_connection((HOST, PORT), timeout=0.5):
                return True
        except OSError:
            return False

    def _wait(self, url: str, timeout: float) -> bool:
        import urllib.error
        import urllib.request

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self._stopping:
            try:
                with urllib.request.urlopen(url, timeout=2) as response:
                    if response.status == 200:
                        return True
            except urllib.error.HTTPError as exc:
                if exc.code != 503:
                    time.sleep(0.3)
            except Exception:
                pass
            time.sleep(1)
        return self._stopping

    def _run(self) -> None:
        ensure_user_files()
        try:
            self._ensure_environment()
        except Exception as exc:
            self.error.emit(self._t("launcher.setupFailed", {"error": exc, "log": LOG_DIR / "setup.log"}))
            return

        self.status_changed.emit(self._t("launcher.checkingBackend"), "#94a3b8")
        backend_reused = False
        if self._wait(HEALTH_URL, 2):
            backend_reused = True
            self.status_changed.emit(self._t("launcher.existingBackendFound"), "#94a3b8")
            self._emit_log_tail("backend", "backend-process.log")
        elif self._port_is_open():
            self.error.emit(self._t("launcher.portBusy", {"port": PORT, "url": HEALTH_URL}))
            return
        else:
            self.status_changed.emit(self._t("launcher.startingBackend"), "#94a3b8")
            try:
                self._backend_proc = self._popen([PYTHON_EXE, str(BACKEND_MAIN)], LAUNCHER_DIR, "backend-process.log", "backend")
            except Exception as exc:
                self.error.emit(self._t("launcher.backendStartFailed", {"error": exc}))
                return

        self.status_changed.emit(self._t("launcher.waitingServer"), "#94a3b8")
        if not self._wait(HEALTH_URL, 60):
            if not self._stopping:
                self.error.emit(self._t("launcher.backendTimeout", {"log": LOG_DIR / "backend-process.log"}))
            return

        self.status_changed.emit(self._t("launcher.loadingModels"), "#a78bfa")
        if not self._wait(READY_URL, 180):
            if not self._stopping:
                self.error.emit(self._t("launcher.readyTimeout", {"log": LOG_DIR / "backend-process.log"}))
            return

        self.status_changed.emit(self._t("launcher.startingPersona"), "#94a3b8")
        time.sleep(0.4)
        try:
            self._frontend_proc = self._popen([PYTHON_EXE, str(PERSONA_MAIN)], LAUNCHER_DIR / "persona", "persona-process.log", "persona")
        except Exception as exc:
            self.error.emit(self._t("launcher.personaStartFailed", {"error": exc, "log": LOG_DIR / "persona-process.log"}))
            return

        status_key = "launcher.readyBackendReused" if backend_reused else "launcher.ready"
        self.status_changed.emit(self._t(status_key), "#4ade80")
        self.phase_changed.emit("done")

    def _kill_process_tree(self, proc: subprocess.Popen | None) -> None:
        if not proc or proc.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )
            return
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def stop_all(self) -> None:
        self._stopping = True
        self._kill_process_tree(self._frontend_proc)
        self._kill_process_tree(self._backend_proc)


class Spinner(QWidget):
    def __init__(self, size: int = 30, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._angle = 0
        self._active = True
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

    def _tick(self) -> None:
        self._angle = (self._angle + 6) % 360
        self.update()

    def stop(self) -> None:
        self._active = False
        self._timer.stop()
        self.update()

    def paintEvent(self, _event) -> None:
        if not self._active:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        size = self.width()
        center = size / 2
        radius = size / 2 - 3
        painter.setPen(QPen(QColor(40, 42, 60), 3, Qt.SolidLine, Qt.RoundCap))
        painter.drawEllipse(QPointF(center, center), radius, radius)
        gradient = QConicalGradient(center, center, -self._angle)
        c1 = QColor("#6366f1")
        c2 = QColor("#a78bfa")
        c2.setAlpha(40)
        gradient.setColorAt(0.0, c1)
        gradient.setColorAt(0.8, c2)
        gradient.setColorAt(1.0, c1)
        painter.setPen(QPen(QBrush(gradient), 3, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(QRectF(center - radius, center - radius, radius * 2, radius * 2), int(-self._angle * 16), int(270 * 16))
        painter.end()


class IconButton(QWidget):
    clicked = Signal()

    def __init__(self, svg_data: bytes, tooltip: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(28, 28)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tooltip)
        self._hovered = False
        self._active = False
        self._renderer = QSvgRenderer(QByteArray(svg_data), self)

    def set_active(self, active: bool) -> None:
        self._active = active
        self.update()

    def enterEvent(self, _event) -> None:
        self._hovered = True
        self.update()

    def leaveEvent(self, _event) -> None:
        self._hovered = False
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self._active:
            painter.setBrush(QColor(99, 102, 241, 70))
            painter.setPen(QPen(QColor(129, 140, 248, 120), 1))
            painter.drawRoundedRect(1, 1, 26, 26, 5, 5)
        elif self._hovered:
            painter.setBrush(QColor(255, 255, 255, 20))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(1, 1, 26, 26, 5, 5)
        painter.setOpacity(1.0 if self._hovered or self._active else 0.55)
        self._renderer.render(painter, QRectF(4, 4, 20, 20))
        painter.end()


def _render_svg(svg_data: bytes, color_hex: str, px: int) -> QPixmap:
    """Render an outline SVG recolored to ``color_hex`` at the given size."""
    tinted = svg_data.replace(b"#FFFFFF", color_hex.encode("ascii"))
    renderer = QSvgRenderer(QByteArray(tinted))
    ratio = 2  # render at 2x for crisp edges on hi-dpi displays
    pixmap = QPixmap(px * ratio, px * ratio)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter, QRectF(0, 0, px, px))
    painter.end()
    return pixmap


class StatCard(QFrame):
    """A small status card: an accent icon badge plus a title and a value."""

    def __init__(self, svg_data: bytes, accent: str, title: str, value: str, value_color: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("statCard")
        self.setStyleSheet(
            "#statCard{"
            "background:rgba(255,255,255,0.025);"
            "border:1px solid rgba(99,102,241,0.22);"
            "border-radius:12px;}"
        )

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 12, 14, 12)
        row.setSpacing(12)

        badge = QLabel()
        badge.setFixedSize(40, 40)
        badge.setAlignment(Qt.AlignCenter)
        badge.setPixmap(_render_svg(svg_data, accent, 22))
        accent_color = QColor(accent)
        badge.setStyleSheet(
            f"background:rgba({accent_color.red()},{accent_color.green()},{accent_color.blue()},0.14);"
            "border-radius:10px;"
        )
        row.addWidget(badge, 0, Qt.AlignVCenter)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        self._title = QLabel(title)
        self._title.setStyleSheet("color:#94a3b8;font-size:12px;font-weight:600;background:transparent;")
        self._value = QLabel(value)
        self._value.setStyleSheet(f"color:{value_color};font-size:13px;font-weight:800;background:transparent;")
        self._value.setWordWrap(True)
        text_col.addWidget(self._title)
        text_col.addWidget(self._value)
        row.addLayout(text_col, 1)

    def set_value(self, value: str, color: str) -> None:
        self._value.setText(value)
        self._value.setStyleSheet(f"color:{color};font-size:13px;font-weight:800;background:transparent;")


class LauncherWindow(QWidget):
    BASIC_SIZE = (370, 140)
    ADVANCED_SIZE = (760, 600)

    def __init__(self, name: str, localizer: Localizer, app_icon: QIcon):
        super().__init__()
        self.char_name = name
        self.i18n = localizer
        self._app_icon = app_icon
        self._worker = ManagedStartupWorker(localizer)
        self._drag_pos: QPoint | None = None
        self._advanced = False
        self._build()
        self._build_tray()
        self._worker.status_changed.connect(self._on_status)
        self._worker.phase_changed.connect(self._on_phase)
        self._worker.error.connect(self._on_error)
        self._worker.log_line.connect(self._append_log)
        QTimer.singleShot(200, self._worker.start_all)

    def t(self, key: str, values: dict[str, Any] | None = None) -> str:
        return self.i18n.t(key, values)

    def _build(self) -> None:
        self.setWindowTitle(f"BitMon - {self.char_name}")
        if not self._app_icon.isNull():
            self.setWindowIcon(self._app_icon)
        self.setFixedSize(*self.BASIC_SIZE)
        # The launcher behaves like a normal window (no always-on-top). Only the
        # persona overlay stays on top, and only when its config flag is set.
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 5)
        shadow.setColor(QColor(0, 0, 0, 160))
        self.setGraphicsEffect(shadow)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        self._card = QFrame()
        self._card.setObjectName("card")
        self._card.setStyleSheet("""
            #card {
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #1e2138, stop:1 #131522);
                border-radius: 14px;
                border: 1px solid rgba(99,102,241,0.45);
            }
        """)
        root.addWidget(self._card)

        layout = QVBoxLayout(self._card)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)

        dot = QFrame()
        dot.setFixedSize(10, 10)
        dot.setStyleSheet("background:transparent;border:1px solid #818cf8;border-radius:5px;")
        title_row.addWidget(dot, 0, Qt.AlignVCenter)

        title = QLabel(self.char_name)
        title.setStyleSheet("color:#e2e8f0;font-size:14px;font-weight:800;letter-spacing:0.8px;")
        title_row.addWidget(title, 0, Qt.AlignVCenter)
        title_row.addStretch()

        self._btn_cfg = IconButton(_SVG_CFG, self.t("launcher.openConfig"))
        self._btn_advanced = IconButton(_SVG_LOGS, self.t("launcher.showAdvanced"))
        self._btn_tray = IconButton(_SVG_TRAY, self.t("launcher.toTray"))
        self._btn_close = IconButton(_SVG_CLOSE, self.t("launcher.closeAll"))
        self._btn_cfg.clicked.connect(self._open_config)
        self._btn_advanced.clicked.connect(self._toggle_advanced)
        self._btn_tray.clicked.connect(self._to_tray)
        self._btn_close.clicked.connect(self._quit)
        for button in (self._btn_cfg, self._btn_advanced, self._btn_tray, self._btn_close):
            title_row.addWidget(button, 0, Qt.AlignVCenter)
        layout.addLayout(title_row)

        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setStyleSheet("background:rgba(99,102,241,0.2);min-height:1px;max-height:1px;")
        layout.addWidget(separator)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 4, 0, 4)
        status_row.setSpacing(12)
        self._spinner = Spinner(size=28)
        status_row.addWidget(self._spinner, 0, Qt.AlignVCenter)
        self._status = QLabel(self.t("launcher.initializing"))
        self._status.setStyleSheet("color:#94a3b8;font-size:13px;font-weight:600;")
        self._status.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        status_row.addWidget(self._status, 1, Qt.AlignVCenter)
        layout.addLayout(status_row)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid rgba(99,102,241,0.26);
                border-radius: 8px;
                background: rgba(4,8,14,0.72);
                top: -1px;
            }
            QTabBar::tab {
                color: #94a3b8;
                background: transparent;
                border: 1px solid transparent;
                padding: 7px 14px;
                margin-right: 4px;
                font-weight: 800;
            }
            QTabBar::tab:selected {
                color: #e2e8f0;
                border-color: rgba(99,102,241,0.34);
                border-bottom-color: rgba(4,8,14,0.72);
                background: rgba(99,102,241,0.16);
                border-top-left-radius: 7px;
                border-top-right-radius: 7px;
            }
        """)
        self._backend_log = self._make_log_view(self.t("launcher.backendLogPlaceholder"))
        self._persona_log = self._make_log_view(self.t("launcher.personaLogPlaceholder"))
        self._tabs.addTab(self._backend_log, self.t("launcher.backendLogs"))
        self._tabs.addTab(self._persona_log, self.t("launcher.personaLogs"))
        self._tabs.hide()
        layout.addWidget(self._tabs, 1)

        self._cards_row = QHBoxLayout()
        self._cards_row.setContentsMargins(0, 2, 0, 2)
        self._cards_row.setSpacing(10)
        self._card_system = StatCard(
            _SVG_CARD_SYSTEM, "#38bdf8",
            self.t("launcher.cardSystemStatus"), self.t("launcher.systemStarting"), "#fbbf24",
        )
        self._card_voice = StatCard(
            _SVG_CARD_VOICE, "#818cf8",
            self.t("launcher.cardVoiceEngine"), self.t("launcher.voiceLoading"), "#fbbf24",
        )
        self._card_persona = StatCard(
            _SVG_CARD_PERSONA, "#f472b6",
            self.t("launcher.cardPersona"), get_active_persona_name(), "#e2e8f0",
        )
        self._cards = (self._card_system, self._card_voice, self._card_persona)
        for card in self._cards:
            self._cards_row.addWidget(card, 1)
            card.hide()
        layout.addLayout(self._cards_row)

        footer = QLabel(self.t("launcher.footer"))
        footer.setAlignment(Qt.AlignCenter)
        footer.setStyleSheet("color:rgba(148,163,184,0.28);font-size:10px;")
        layout.addWidget(footer)

    def _make_log_view(self, placeholder: str) -> QPlainTextEdit:
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlaceholderText(placeholder)
        view.document().setMaximumBlockCount(2500)
        view.setStyleSheet("""
            QPlainTextEdit {
                border: 0;
                background: transparent;
                color: #cbd5e1;
                selection-background-color: #4f46e5;
                font-family: Consolas, "Cascadia Mono", monospace;
                font-size: 11px;
                padding: 10px;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 10px;
                margin: 2px 2px 2px 0;
            }
            QScrollBar::handle:vertical {
                background: rgba(99,102,241,0.45);
                border-radius: 5px;
                min-height: 28px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(129,140,248,0.80);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
                background: transparent;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: transparent;
            }
            QScrollBar:horizontal {
                background: transparent;
                height: 10px;
                margin: 0 2px 2px 2px;
            }
            QScrollBar::handle:horizontal {
                background: rgba(99,102,241,0.45);
                border-radius: 5px;
                min-width: 28px;
            }
            QScrollBar::handle:horizontal:hover {
                background: rgba(129,140,248,0.80);
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                width: 0;
                background: transparent;
            }
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
                background: transparent;
            }
        """)
        return view

    def _build_tray(self) -> None:
        tray_icon = self._app_icon
        if tray_icon.isNull():
            pixmap = QPixmap(32, 32)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)
            gradient = QRadialGradient(16, 16, 16)
            gradient.setColorAt(0, QColor("#a78bfa"))
            gradient.setColorAt(1, QColor("#4f46e5"))
            painter.setBrush(QBrush(gradient))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(1, 1, 30, 30)
            painter.end()
            tray_icon = QIcon(pixmap)

        self._tray = QSystemTrayIcon(tray_icon, self)
        self._tray.setToolTip(f"BitMon - {self.char_name}")
        menu = QMenu()
        menu.setStyleSheet("""
            QMenu {
                background:#1a1d2e;
                color:#e2e8f0;
                border:1px solid rgba(99,102,241,0.4);
                border-radius:8px;
                padding:4px;
                font-size:13px;
            }
            QMenu::item { padding:6px 16px; border-radius:4px; }
            QMenu::item:selected { background:#6366f1; }
        """)
        actions: list[tuple[str | None, Any]] = [
            (self.t("launcher.trayOpen", {"name": self.char_name}), self._restore),
            (self.t("launcher.openConfig"), self._open_config),
            (None, None),
            (self.t("launcher.closeAll"), self._quit),
        ]
        for label, callback in actions:
            if label is None:
                menu.addSeparator()
                continue
            action = QAction(label, self)
            action.triggered.connect(callback)
            menu.addAction(action)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(
            lambda reason: self._restore() if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger) else None
        )
        self._tray.show()

    def _append_log(self, stream_name: str, line: str) -> None:
        view = self._backend_log if stream_name == "backend" else self._persona_log
        stamp = time.strftime("%H:%M:%S")
        view.appendPlainText(f"{stamp}  {line}")
        scrollbar = view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _on_status(self, message: str, color: str) -> None:
        self._status.setText(message)
        self._status.setStyleSheet(f"color:{color};font-size:13px;font-weight:600;")

    def _on_phase(self, phase: str) -> None:
        if phase != "done":
            return
        self._spinner.stop()
        self._spinner.hide()
        self._status.setAlignment(Qt.AlignCenter)
        self._card_system.set_value(self.t("launcher.systemOperational"), "#4ade80")
        self._card_voice.set_value(self.t("launcher.voiceActive"), "#4ade80")
        self._open_config_on_first_run()
        if not self._advanced:
            QTimer.singleShot(500 if SILENT else 3000, self._auto_to_tray)

    def _open_config_on_first_run(self) -> None:
        # The very first successful start opens the config page on the Model tab
        # so a new user lands straight on the provider/model setup.
        if FIRST_RUN_MARKER.exists():
            return
        try:
            FIRST_RUN_MARKER.write_text(str(time.time()), encoding="utf-8")
        except OSError:
            pass
        webbrowser.open(f"{CONFIG_URL}?tab=model")

    def _on_error(self, message: str) -> None:
        self._spinner.stop()
        self._spinner.hide()
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setText(message)
        self._status.setStyleSheet("color:#f87171;font-size:12px;font-weight:600;")
        self._card_system.set_value(self.t("launcher.systemError"), "#f87171")
        self._card_voice.set_value(self.t("launcher.voiceOffline"), "#f87171")
        self._toggle_advanced(True)

    def _toggle_advanced(self, force: bool | None = None) -> None:
        self._advanced = (not self._advanced) if force is None else force
        self._tabs.setVisible(self._advanced)
        for card in self._cards:
            card.setVisible(self._advanced)
        self._btn_advanced.set_active(self._advanced)
        self._btn_advanced.setToolTip(self.t("launcher.hideAdvanced" if self._advanced else "launcher.showAdvanced"))
        current = self.geometry()
        new_width, new_height = self.ADVANCED_SIZE if self._advanced else self.BASIC_SIZE
        self.setFixedSize(new_width, new_height)
        self.move(current.x(), current.y())

    def _open_config(self) -> None:
        webbrowser.open(CONFIG_URL)

    def _auto_to_tray(self) -> None:
        if not self._advanced:
            self._to_tray()

    def _to_tray(self) -> None:
        self.hide()
        if SILENT:
            return
        self._tray.showMessage(
            f"BitMon - {self.char_name}",
            self.t("launcher.runningBackground"),
            QSystemTrayIcon.Information,
            1500,
        )

    def _restore(self) -> None:
        self.showNormal()
        self.activateWindow()

    def _quit(self) -> None:
        self._worker.stop_all()
        self._tray.hide()
        QApplication.quit()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_pos and event.buttons() == Qt.LeftButton:
            next_pos = event.globalPosition().toPoint()
            self.move(self.pos() + next_pos - self._drag_pos)
            self._drag_pos = next_pos

    def mouseReleaseEvent(self, _event) -> None:
        self._drag_pos = None

    def closeEvent(self, event) -> None:
        event.ignore()
        self._to_tray()


def main() -> None:
    _set_windows_app_id()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app_icon = _launcher_icon()
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)
    localizer = Localizer(get_config_locale())
    window = LauncherWindow(get_character_name(), localizer, app_icon)
    window.show()
    screen = app.primaryScreen().availableGeometry()
    window.move(screen.center().x() - window.width() // 2, screen.center().y() - window.height() // 2)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
