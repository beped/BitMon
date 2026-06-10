"""Global keyboard/mouse capture for config hotkeys."""

from __future__ import annotations

import threading
import time
from typing import Any


_lock = threading.Lock()
_state: dict[str, Any] = {
    "active": False,
    "value": "",
    "error": "",
    "expires_at": 0.0,
    "armed_at": 0.0,
}
_keyboard_listener = None
_mouse_listener = None


def _stop_listeners() -> None:
    global _keyboard_listener, _mouse_listener
    for listener in (_keyboard_listener, _mouse_listener):
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass
    _keyboard_listener = None
    _mouse_listener = None


def _set_result(value: str) -> None:
    if not value:
        return
    with _lock:
        if not _state.get("active") or time.monotonic() < float(_state.get("armed_at") or 0):
            return
        _state.update({"active": False, "value": value, "error": ""})
    _stop_listeners()


def _normalize_key(key: Any) -> str:
    char = getattr(key, "char", None)
    if char:
        return str(char).lower()
    name = str(getattr(key, "name", "") or "").lower()
    aliases = {
        "space": "space",
        "esc": "esc",
        "escape": "esc",
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


def _normalize_mouse(button: Any) -> str:
    raw = str(getattr(button, "name", button)).lower().replace("button.", "")
    aliases = {
        "left": "mouse1",
        "middle": "mouse2",
        "right": "mouse3",
        "x1": "mouse4",
        "x2": "mouse5",
        "button.x1": "mouse4",
        "button.x2": "mouse5",
    }
    return aliases.get(raw, "")


def start_hotkey_capture(timeout_seconds: float = 10.0) -> dict[str, Any]:
    global _keyboard_listener, _mouse_listener
    _stop_listeners()
    now = time.monotonic()
    with _lock:
        _state.update({
            "active": True,
            "value": "",
            "error": "",
            "expires_at": now + max(1.0, float(timeout_seconds)),
            "armed_at": now + 0.25,
        })
    try:
        from pynput import keyboard as pynput_keyboard
        from pynput import mouse as pynput_mouse
    except Exception as exc:
        with _lock:
            _state.update({"active": False, "error": f"pynput unavailable: {exc}"})
        return get_hotkey_capture_result()

    def on_press(key: Any) -> None:
        _set_result(_normalize_key(key))

    def on_click(_x: int, _y: int, button: Any, pressed: bool) -> None:
        if pressed:
            _set_result(_normalize_mouse(button))

    _keyboard_listener = pynput_keyboard.Listener(on_press=on_press)
    _mouse_listener = pynput_mouse.Listener(on_click=on_click)
    _keyboard_listener.daemon = True
    _mouse_listener.daemon = True
    _keyboard_listener.start()
    _mouse_listener.start()
    return get_hotkey_capture_result()


def cancel_hotkey_capture() -> dict[str, Any]:
    _stop_listeners()
    with _lock:
        _state.update({"active": False, "value": ""})
    return get_hotkey_capture_result()


def get_hotkey_capture_result() -> dict[str, Any]:
    now = time.monotonic()
    with _lock:
        if _state.get("active") and now > float(_state.get("expires_at") or 0):
            _state.update({"active": False, "error": "Tempo esgotado."})
            should_stop = True
        else:
            should_stop = False
        data = dict(_state)
    if should_stop:
        _stop_listeners()
    return {
        "active": bool(data.get("active")),
        "value": str(data.get("value") or ""),
        "error": str(data.get("error") or ""),
    }
