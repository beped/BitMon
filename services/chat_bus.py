"""In-memory conversation log plus a bus to inject text into active voice sessions.

The pet (persona client) holds the only ``/session`` websocket. The Config page
talks REST, so to let it drive the pet we register each live session here and
push typed text into it. Both the spoken and the injected turns append to a
shared, in-memory history that the Config chat reads back. History is not
persisted: it resets when the backend restarts.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from typing import Any


MAX_MESSAGES = 200

_history: list[dict[str, Any]] = []
_message_counter = itertools.count(1)
_sessions: dict[int, asyncio.Queue[Any]] = {}
_session_counter = itertools.count(1)


def append_message(role: str, text: str) -> dict[str, Any] | None:
    """Record a turn. ``role`` is "user" or "assistant". Returns the stored row."""
    text = " ".join(str(text or "").split())
    if not text:
        return None
    message = {
        "id": next(_message_counter),
        "role": role,
        "text": text,
        "ts": int(time.time()),
    }
    _history.append(message)
    if len(_history) > MAX_MESSAGES:
        del _history[: len(_history) - MAX_MESSAGES]
    return message


def get_history() -> list[dict[str, Any]]:
    return list(_history)


def clear_history() -> None:
    _history.clear()


def broadcast_clear() -> None:
    """Tell every live pet session to wipe its on-screen subtitle.

    Pushes a control message (a dict, distinct from the plain-text user turns the
    queue normally carries) onto each session queue. The session proxy forwards
    it verbatim to the pet, which clears its subtitle."""
    for queue in _sessions.values():
        queue.put_nowait({"type": "bitmon.clear_subtitle"})


def register_session() -> tuple[int, "asyncio.Queue[Any]"]:
    """Register a live voice session and return its id and injection queue."""
    session_id = next(_session_counter)
    queue: asyncio.Queue[Any] = asyncio.Queue()
    _sessions[session_id] = queue
    return session_id, queue


def unregister_session(session_id: int) -> None:
    _sessions.pop(session_id, None)


def has_active_session() -> bool:
    return bool(_sessions)


def inject_text(text: str) -> bool:
    """Queue ``text`` on every active session. Returns False if none are live."""
    text = " ".join(str(text or "").split())
    if not text or not _sessions:
        return False
    for queue in _sessions.values():
        queue.put_nowait(text)
    return True
