"""Local process controls for Persona restarts."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
PERSONA_MAIN = BACKEND_DIR / "persona" / "personagem.py"
# The venv lives inside the app folder (self-contained). Fall back to the
# interpreter currently running the backend, which is already the venv python.
PYTHON_EXE = BACKEND_DIR / "venv" / "Scripts" / "python.exe"
PYTHON = str(PYTHON_EXE) if PYTHON_EXE.exists() else sys.executable


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _start_python(script: Path, cwd: Path) -> None:
    subprocess.Popen(
        [PYTHON, str(script)],
        cwd=str(cwd),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=_creation_flags(),
    )


def _kill_python_script(script_name: str) -> int:
    script_name = script_name.lower()
    killed = 0
    try:
        import psutil
    except Exception:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Get-CimInstance Win32_Process | "
                    f"Where-Object {{ $_.CommandLine -like '*{script_name}*' -and $_.ProcessId -ne $PID }} | "
                    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
                ),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return -1

    current_pid = os.getpid()
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            if proc.info["pid"] == current_pid:
                continue
            cmdline = " ".join(proc.info.get("cmdline") or []).lower()
            if script_name in cmdline:
                proc.kill()
                killed += 1
        except Exception:
            continue
    return killed


def restart_persona() -> dict[str, Any]:
    killed = _kill_python_script("personagem.py")
    time.sleep(0.4)
    _start_python(PERSONA_MAIN, PERSONA_MAIN.parent)
    return {"ok": True, "killed": killed}
