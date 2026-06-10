# Architecture

A high-level map of how BitMon fits together, for contributors and the curious.

---

## The two processes

BitMon runs as **two cooperating processes**, supervised by the launcher:

```
┌─────────────────────────────────────────────────────────────┐
│  launcher.py  (PySide6 tray app)                             │
│   • creates/updates the venv, installs requirements          │
│   • starts & health-checks the backend                       │
│   • starts the persona overlay                               │
│   • streams both logs, lives in the tray                     │
└───────────────┬───────────────────────────┬─────────────────┘
                │ starts                     │ starts
                ▼                            ▼
┌───────────────────────────┐   ┌────────────────────────────┐
│  main.py  (FastAPI)        │   │  persona/personagem.py     │
│   • config UI + REST API   │◀──│   (PySide6 overlay pet)    │
│   • /session websocket     │ws │   • renders sprite anims    │
│   • voice flows & tools    │──▶│   • captures mic / hotkey   │
└───────────────────────────┘   └────────────────────────────┘
```

- **Backend** (`main.py`): the brains. Serves the configuration UI, the REST API,
  and the `/session` WebSocket that carries the live voice flow. It owns the LLM,
  STT, TTS and tool execution.
- **Overlay** (`persona/personagem.py`): the face. A frameless always-on-top
  window that plays the active persona's sprite animations, captures the
  microphone / push-to-talk / wake word, and talks to the backend over the
  WebSocket.

They communicate locally over HTTP/WebSocket on `127.0.0.1`.

---

## A voice turn, end to end

```
mic audio ─▶ overlay ──ws──▶ backend:
   WhisperX (STT)  ─▶  LLM (Inworld Router OR LM Studio)  ─▶  optional tool calls
   ─▶  TTS (Inworld OR Kokoro)  ──ws──▶  overlay plays audio + talk animation
```

The voice flow is implemented in `services/inworld_chat.py` (Inworld path) and
`services/local_session.py` (local path). Tool calls (screen analysis, Home
Assistant, open config, external MCPs) are dispatched by
`services/tool_runtime.py`.

---

## Backend modules

| Area | Files | Responsibility |
|---|---|---|
| **App & routing** | `main.py` | FastAPI app, config UI, REST endpoints, `/session` websocket, health/status |
| **Config** | `core/config_*.py`, `core/config_store.py` | Defaults, models, persistence, migration, backup |
| **Secrets & security** | `core/secret_store.py`, `core/mcp_auth_store.py`, `core/security.py` | `keyring`-backed key storage, local token, CORS |
| **Voice flows** | `services/inworld_chat.py`, `services/local_session.py` | The STT→LLM→TTS pipelines |
| **Speech** | `services/whisper_service.py`, `services/tts_service.py`, `services/inworld_tts.py` | WhisperX STT, Kokoro/Inworld TTS |
| **Tools** | `services/tool_runtime.py`, `tools/screen_tools.py`, `tools/home_assistant.py` | Tool dispatch, screen vision, Home Assistant |
| **MCP** | `services/mcp_external.py`, `services/mcp_server.py` | External MCP clients and the optional `/mcp` mount |
| **Input** | `services/input_capture.py`, `services/wake_phrase.py` | Hotkey capture, wake-phrase handling |
| **Persona system** | `persona/persona_config.py`, `persona/personagem.py`, `persona/wake_word.py` | Persona packages, overlay rendering, wake word |
| **Sprites** | `services/sprite_optimizer.py` | WebP optimization |
| **Web** | `web/config.html`, `web/toast.js`, `web/i18n/*` | The configuration UI and translations |

---

## Configuration & state

- **User config:** `bitmon_config.json` (created from
  `bitmon_config.example.json` on first run). Holds everything except secrets.
- **Secrets:** the Inworld API key lives in the OS credential store via
  `keyring`, never in the config file. The API only reports whether a key is
  configured.
- **Personas:** each persona is a package (a `persona_config.json` + sprites);
  the active one is rendered by the overlay.
- **Caches & logs:** `cache/` and `logs/` next to the backend (relocatable via
  `BITMON_CACHE_DIR` / `BITMON_LOG_DIR`).

The config store handles **versioned migration** so older config files are
upgraded forward automatically.

---

## Security model

- Binds to `127.0.0.1`; CORS limited to local origins.
- Sensitive endpoints require an auto-generated **local token** (the config page
  injects it into requests for you).
- `/docs`, `/redoc`, `/openapi.json` and `/mcp` are **opt-in** via
  `BITMON_ENABLE_DOCS` / `BITMON_ENABLE_MCP`.

See [Providers → Privacy](providers.md#privacy) and the repository `SECURITY.md`.

---

## Tests & checks

```powershell
pip install pytest httpx ruff mypy
python -m compileall .
python -m ruff check .
python -m pytest ..\tests        # tests live at the repo root
```

> Test paths assume the repository layout. Adjust if you've published the backend
> standalone.
