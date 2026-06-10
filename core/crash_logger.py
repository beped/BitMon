"""Crash logging helpers for BitMon processes."""

from __future__ import annotations

import faulthandler
import os
import platform
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Callable


Redactor = Callable[[object], str]

_installed_components: set[str] = set()
_fault_files: list[object] = []


def _default_redact(value: object) -> str:
    return str(value)


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _write_all_thread_stacks(handle) -> None:
    frames = sys._current_frames()
    handle.write("\n\n=== Thread stacks ===\n")
    for thread in threading.enumerate():
        handle.write(f"\n--- Thread {thread.name} ({thread.ident}) ---\n")
        frame = frames.get(thread.ident)
        if frame is None:
            handle.write("No Python frame available.\n")
            continue
        handle.write("".join(traceback.format_stack(frame)))


def _write_crash_file(
    component: str,
    log_dir: Path,
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_traceback: TracebackType | None,
    redact: Redactor,
    context: str,
) -> Path:
    crash_dir = log_dir / "crashes"
    crash_dir.mkdir(parents=True, exist_ok=True)
    path = crash_dir / f"{component}-crash-{_timestamp()}-pid{os.getpid()}.txt"
    with path.open("w", encoding="utf-8", errors="replace") as handle:
        handle.write(f"Component: {component}\n")
        handle.write(f"Context: {context}\n")
        handle.write(f"Timestamp: {datetime.now().isoformat(timespec='seconds')}\n")
        handle.write(f"PID: {os.getpid()}\n")
        handle.write(f"Executable: {sys.executable}\n")
        handle.write(f"Arguments: {redact(sys.argv)}\n")
        handle.write(f"Working directory: {Path.cwd()}\n")
        handle.write(f"Platform: {platform.platform()}\n")
        handle.write(f"Python: {sys.version}\n\n")
        handle.write("=== Exception ===\n")
        exception_text = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        handle.write(redact(exception_text))
        _write_all_thread_stacks(handle)
    return path


def install_crash_logger(component: str, log_dir: Path | str, redact: Redactor | None = None) -> None:
    """Install process-wide crash hooks and fatal-error traceback logging."""
    normalized_component = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in component).lower()
    if normalized_component in _installed_components:
        return
    _installed_components.add(normalized_component)

    target_log_dir = Path(log_dir)
    target_log_dir.mkdir(parents=True, exist_ok=True)
    crash_dir = target_log_dir / "crashes"
    crash_dir.mkdir(parents=True, exist_ok=True)
    redactor = redact or _default_redact

    fault_path = crash_dir / f"{normalized_component}-fatal-pid{os.getpid()}.txt"
    fault_handle = fault_path.open("a", encoding="utf-8", errors="replace")
    fault_handle.write(f"\n\n=== Fatal logger started {_timestamp()} pid {os.getpid()} ===\n")
    fault_handle.flush()
    _fault_files.append(fault_handle)
    try:
        faulthandler.enable(file=fault_handle, all_threads=True)
    except Exception:
        pass

    original_sys_hook = sys.excepthook

    def sys_hook(exc_type, exc_value, exc_traceback) -> None:
        path = _write_crash_file(
            normalized_component,
            target_log_dir,
            exc_type,
            exc_value,
            exc_traceback,
            redactor,
            "sys.excepthook",
        )
        print(f"[CrashLogger] Wrote crash log: {path}", file=sys.stderr)
        original_sys_hook(exc_type, exc_value, exc_traceback)

    sys.excepthook = sys_hook

    if hasattr(threading, "excepthook"):
        original_thread_hook = threading.excepthook

        def thread_hook(args) -> None:
            if args.exc_type is not SystemExit:
                path = _write_crash_file(
                    normalized_component,
                    target_log_dir,
                    args.exc_type,
                    args.exc_value,
                    args.exc_traceback,
                    redactor,
                    f"threading.excepthook:{args.thread.name if args.thread else 'unknown'}",
                )
                print(f"[CrashLogger] Wrote thread crash log: {path}", file=sys.stderr)
            original_thread_hook(args)

        threading.excepthook = thread_hook
