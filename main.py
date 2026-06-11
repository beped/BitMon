"""BitMon backend server."""

import asyncio
import json
import logging
import os
import secrets as token_secrets
import tempfile
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from core.crash_logger import install_crash_logger
from core.config_models import ConfigValidationError
from core.config_store import get_client_config, get_config, update_config
from core.mcp_auth_store import normalize_mcp_server_id
from persona.persona_config import (
    ASSETS_DIR as PERSONA_ASSETS_DIR,
    FONTS_DIR as PERSONA_FONTS_DIR,
    ICONS_DIR as PERSONA_ICONS_DIR,
    activate_persona,
    create_persona,
    delete_persona,
    edit_persona,
    delete_persona_asset,
    export_persona_package,
    get_persona_config,
    import_persona_package,
    list_assets as list_persona_assets,
    list_personas,
    rename_persona,
    rename_persona_asset,
    save_persona_config,
    save_uploaded_asset,
)
from persona.theme_config import (
    delete_theme_from_library,
    get_theme_config,
    list_theme_library,
    save_theme_config,
    save_theme_to_library,
)
from services.mcp_external import validate_home_assistant_mcp, validate_streamable_http_mcp
from services.sprite_optimizer import optimize_persona_sprites
from services.chat_bus import broadcast_clear, clear_history, get_history, has_active_session, inject_text
from services.inworld_chat import inworld_chat_proxy
from services.input_capture import (
    cancel_hotkey_capture,
    get_hotkey_capture_result,
    start_hotkey_capture,
)
from services.local_catalog import list_kokoro_models, list_kokoro_voices, list_lmstudio_models
from services.local_preload import preload_local_provider
from services.local_session import local_session_proxy
from services.tts_service import is_kokoro_ready
from services.whisper_service import preload_configured_whisper
from core.secret_store import SecretStoreError
from core.security import (
    COOKIE_NAME,
    RedactingLogFilter,
    allowed_cors_origins,
    docs_enabled,
    get_local_token,
    inject_local_token,
    is_sensitive_api_path,
    mcp_enabled,
    redact_for_log,
    request_has_valid_local_token,
)
from tools.home_assistant import (
    list_home_assistant_devices,
    refresh_home_assistant_devices_cache,
    save_home_assistant_devices,
)


os.environ.setdefault("NO_COLOR", "1")

_whisper_ready = False
_last_error: str | None = None
logging.getLogger("mcp").setLevel(logging.WARNING)
logging.getLogger("mcp.server").setLevel(logging.WARNING)
logging.getLogger("watchfiles").setLevel(logging.WARNING)

BASE_DIR = Path(__file__).resolve().parent
LEGACY_NAME = "digi" + "mon"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(f"BITMON_{name}") or os.environ.get(f"{LEGACY_NAME.upper()}_{name}") or default


WEB_CONFIG_PATH = BASE_DIR / "web" / "config.html"
WEB_TOAST_PATH = BASE_DIR / "web" / "toast.js"
WEB_I18N_DIR = BASE_DIR / "web" / "i18n"
WEB_VENDOR_DIR = BASE_DIR / "web" / "vendor"
APP_ICON_PNG_PATH = BASE_DIR / "web" / "app-icon.png"
APP_ICON_ICO_PATH = BASE_DIR / "web" / "app-icon.ico"
WAKEWORD_DIR = BASE_DIR / "wakeword"
DEFAULT_LOCALE = "en-US"
LOG_DIR = Path(_env("LOG_DIR") or BASE_DIR / "logs")
DOCS_ENABLED = docs_enabled()
MCP_ENABLED = mcp_enabled()
MCP_OAUTH_FLOWS: dict[str, dict[str, Any]] = {}


def _setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        LOG_DIR / "backend.log",
        maxBytes=1_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    handler.addFilter(RedactingLogFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    if not any(isinstance(existing, RotatingFileHandler) for existing in root_logger.handlers):
        root_logger.addHandler(handler)


_setup_logging()
install_crash_logger("bitmon-backend", LOG_DIR, redact_for_log)


def _record_error(context: str, exc: object) -> None:
    global _last_error
    _last_error = redact_for_log(f"{context}: {exc}")
    logging.exception("%s: %s", context, redact_for_log(exc))


def _available_locales() -> list[str]:
    if not WEB_I18N_DIR.exists():
        return [DEFAULT_LOCALE]
    locales = sorted(path.stem for path in WEB_I18N_DIR.glob("*.json") if path.is_file())
    return locales or [DEFAULT_LOCALE]


def _read_locale_catalog(locale: str) -> dict[str, Any]:
    available = set(_available_locales())
    selected = locale if locale in available else DEFAULT_LOCALE
    path = WEB_I18N_DIR / f"{selected}.json"
    if not path.exists():
        return {"_meta": {"locale": DEFAULT_LOCALE, "name": "English"}}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


try:
    if not MCP_ENABLED:
        raise RuntimeError("MCP HTTP mount disabled. Set BITMON_ENABLE_MCP=1 to enable it.")
    from services.mcp_server import mcp as bitmon_mcp
except Exception as exc:
    bitmon_mcp = None
    logging.info("[MCP] unavailable: %s", redact_for_log(exc))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _whisper_ready

    async def preload_after_startup() -> None:
        global _whisper_ready
        await asyncio.sleep(0.5)
        config = get_config()
        try:
            if str(config.get("provider") or "").lower() == "local":
                await preload_local_provider(config)
            else:
                await preload_configured_whisper(config)
        except Exception as exc:
            _record_error("[Preload] failed to load models", exc)
        finally:
            _whisper_ready = True

    preload_task = asyncio.create_task(preload_after_startup())
    if bitmon_mcp is None:
        try:
            yield
        finally:
            if not preload_task.done():
                preload_task.cancel()
        return
    try:
        async with bitmon_mcp.session_manager.run():
            yield
    finally:
        if not preload_task.done():
            preload_task.cancel()


app = FastAPI(
    title="BitMon AI Backend",
    description="Voice proxy and BitMon configuration UI",
    version="4.0.0",
    lifespan=lifespan,
    docs_url="/docs" if DOCS_ENABLED else None,
    redoc_url="/redoc" if DOCS_ENABLED else None,
    openapi_url="/openapi.json" if DOCS_ENABLED else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def require_local_token(request: Request, call_next):
    if is_sensitive_api_path(request.url.path) and not request_has_valid_local_token(request):
        return JSONResponse(status_code=401, content={"detail": "Invalid or missing BitMon local token."})
    return await call_next(request)


@app.get("/")
async def root():
    return RedirectResponse("/config")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready():
    """Return 200 only after the configured speech model has finished loading."""
    from fastapi.responses import JSONResponse
    if _whisper_ready:
        return {"status": "ready"}
    return JSONResponse(status_code=503, content={"status": "loading"})


@app.get("/api/status")
async def api_status():
    config = get_config()
    provider = str(config.get("provider") or "inworld").lower()
    local_config = config.get("local") or {}
    ha_config = config.get("mcps", {}).get("home_assistant", {})

    lm_studio_connected = False
    lm_studio_error = ""
    if provider == "local":
        try:
            lm_result = await asyncio.to_thread(list_lmstudio_models, str(local_config.get("base_url") or ""))
            lm_studio_connected = bool(lm_result.get("ok"))
        except Exception as exc:
            lm_studio_error = redact_for_log(exc)

    ha_connected = False
    ha_error = ""
    if ha_config.get("enabled") and str(ha_config.get("url") or "").strip():
        try:
            result = await validate_home_assistant_mcp(str(ha_config.get("url") or ""))
            ha_connected = bool(result.get("ok"))
            ha_error = str(result.get("error") or "")
        except Exception as exc:
            ha_error = redact_for_log(exc)

    return {
        "backend_online": True,
        "provider": provider,
        "whisper_loaded": _whisper_ready,
        "kokoro_ready": is_kokoro_ready(),
        "lm_studio_connected": lm_studio_connected,
        "lm_studio_error": lm_studio_error,
        "inworld_api_key_configured": bool(config.get("secrets", {}).get("inworld_api_key_configured")),
        "home_assistant_mcp_connected": ha_connected,
        "home_assistant_error": redact_for_log(ha_error),
        "last_error": _last_error,
    }


@app.get("/config")
async def config_page():
    response = HTMLResponse(inject_local_token(WEB_CONFIG_PATH.read_text(encoding="utf-8")))
    response.set_cookie(
        COOKIE_NAME,
        value=get_local_token(),
        httponly=True,
        samesite="strict",
    )
    return response


@app.get("/app-icon.png")
async def app_icon_png():
    if not APP_ICON_PNG_PATH.exists():
        raise HTTPException(status_code=404, detail="App icon not found.")
    return FileResponse(APP_ICON_PNG_PATH, media_type="image/png")


@app.get("/app-icon.ico")
async def app_icon_ico():
    if not APP_ICON_ICO_PATH.exists():
        raise HTTPException(status_code=404, detail="App icon not found.")
    return FileResponse(APP_ICON_ICO_PATH, media_type="image/x-icon")


@app.get("/favicon.ico")
async def favicon_ico():
    if APP_ICON_ICO_PATH.exists():
        return FileResponse(APP_ICON_ICO_PATH, media_type="image/x-icon")
    if APP_ICON_PNG_PATH.exists():
        return FileResponse(APP_ICON_PNG_PATH, media_type="image/png")
    raise HTTPException(status_code=404, detail="App icon not found.")


@app.get("/toast.js")
async def toast_js():
    return FileResponse(WEB_TOAST_PATH, media_type="application/javascript")


@app.get("/api/i18n/locales")
async def api_i18n_locales():
    locales = []
    for locale in _available_locales():
        catalog = _read_locale_catalog(locale)
        meta = catalog.get("_meta") if isinstance(catalog.get("_meta"), dict) else {}
        locales.append(
            {
                "locale": locale,
                "name": meta.get("name") or locale,
            }
        )
    return {"default_locale": DEFAULT_LOCALE, "locales": locales}


@app.get("/api/i18n/{locale}")
async def api_i18n_catalog(locale: str):
    available = set(_available_locales())
    selected = locale if locale in available else DEFAULT_LOCALE
    catalog = _read_locale_catalog(selected)
    fallback = _read_locale_catalog(DEFAULT_LOCALE) if selected != DEFAULT_LOCALE else {}
    merged = {**fallback, **catalog}
    merged["_meta"] = {
        **(fallback.get("_meta") if isinstance(fallback.get("_meta"), dict) else {}),
        **(catalog.get("_meta") if isinstance(catalog.get("_meta"), dict) else {}),
        "locale": selected,
        "default_locale": DEFAULT_LOCALE,
    }
    return merged


@app.get("/api/config")
async def api_get_config():
    return get_config()


@app.put("/api/config")
async def api_save_config(config: dict[str, Any]):
    try:
        saved = update_config(config)
    except (ConfigValidationError, SecretStoreError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    provider = str(saved.get("provider") or "inworld").lower()
    provider_section = saved.get(provider) or saved.get("inworld") or {}
    if provider == "local":
        async def preload_saved_local() -> None:
            try:
                await preload_local_provider(saved)
            except Exception as exc:
                _record_error("[Preload] failed to load Local", exc)

        asyncio.create_task(preload_saved_local())
    elif str(provider_section.get("stt_provider") or provider).lower() == "whisper":
        async def preload_saved_whisper() -> None:
            try:
                await preload_configured_whisper(saved)
            except Exception as exc:
                _record_error("[Whisper] failed to load model", exc)

        asyncio.create_task(preload_saved_whisper())
    return saved


@app.get("/api/config/client")
async def api_get_client_config():
    return get_client_config()


@app.get("/api/chat/history")
async def api_chat_history():
    return {
        "ok": True,
        "messages": get_history(),
        "pet_connected": has_active_session(),
    }


@app.post("/api/chat/send")
async def api_chat_send(payload: dict[str, Any]):
    text = " ".join(str(payload.get("text") or "").split())
    if not text:
        return {"ok": False, "error": "Empty message."}
    if not inject_text(text):
        return {
            "ok": False,
            "pet_connected": False,
            "error": "The pet is not connected. Start the pet to chat.",
        }
    return {"ok": True, "pet_connected": True}


@app.post("/api/chat/clear")
async def api_chat_clear():
    clear_history()
    broadcast_clear()
    return {"ok": True, "messages": []}


@app.get("/api/persona/config")
async def api_get_persona_config():
    return get_persona_config()


@app.put("/api/persona/config")
async def api_save_persona_config(config: dict[str, Any]):
    try:
        return save_persona_config(config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/theme/config")
async def api_get_theme_config():
    return get_theme_config()


@app.put("/api/theme/config")
async def api_save_theme_config(theme: dict[str, Any]):
    try:
        return save_theme_config(theme)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/theme/library")
async def api_theme_library():
    return list_theme_library()


@app.post("/api/theme/library")
async def api_save_theme_to_library(payload: dict[str, Any]):
    try:
        return save_theme_to_library(str(payload.get("name") or ""), payload.get("theme") or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/theme/library/delete")
async def api_delete_theme_from_library(payload: dict[str, Any]):
    try:
        return delete_theme_from_library(str(payload.get("id") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/persona/assets")
async def api_persona_assets():
    return {"assets": list_persona_assets()}


@app.post("/api/persona/assets")
async def api_upload_persona_asset(file: UploadFile = File(...)):
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        while chunk := await file.read(1024 * 1024):
            tmp.write(chunk)
        tmp_path = Path(tmp.name)
    try:
        return save_uploaded_asset(file.filename or "asset.bin", tmp_path)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


@app.post("/api/persona/assets/delete")
async def api_delete_persona_asset(payload: dict[str, Any]):
    try:
        return delete_persona_asset(str(payload.get("name") or ""))
    except ValueError as exc:
        status = 404 if "not found" in str(exc).lower() else 409
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@app.post("/api/persona/assets/rename")
async def api_rename_persona_asset(payload: dict[str, Any]):
    try:
        return rename_persona_asset(str(payload.get("name") or ""), str(payload.get("new_name") or ""))
    except ValueError as exc:
        status = 404 if "not found" in str(exc).lower() else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@app.get("/api/persona/library")
async def api_persona_library():
    return list_personas()


@app.post("/api/persona")
async def api_create_persona(payload: dict[str, Any]):
    try:
        return create_persona(str(payload.get("name") or ""), str(payload.get("source_id") or "") or None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/persona/optimize")
async def api_optimize_persona(payload: dict[str, Any]):
    try:
        return await asyncio.to_thread(
            optimize_persona_sprites,
            str(payload.get("id") or ""),
            lossless=bool(payload.get("lossless")),
            quality=int(payload.get("quality") or 90),
        )
    except ValueError as exc:
        status = 404 if "not found" in str(exc).lower() else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@app.post("/api/persona/activate")
async def api_activate_persona(payload: dict[str, Any]):
    try:
        return activate_persona(str(payload.get("id") or ""))
    except ValueError as exc:
        status = 404 if "not found" in str(exc).lower() else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@app.post("/api/persona/edit")
async def api_edit_persona(payload: dict[str, Any]):
    try:
        return edit_persona(str(payload.get("id") or ""))
    except ValueError as exc:
        status = 404 if "not found" in str(exc).lower() else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@app.put("/api/persona/{persona_id}")
async def api_rename_persona(persona_id: str, payload: dict[str, Any]):
    try:
        return rename_persona(persona_id, str(payload.get("name") or ""))
    except ValueError as exc:
        status = 400 if "required" in str(exc).lower() else 404
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@app.delete("/api/persona/{persona_id}")
async def api_delete_persona(persona_id: str):
    try:
        return delete_persona(persona_id)
    except ValueError as exc:
        status = 400 if persona_id == "default" else 404
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@app.post("/api/persona/import")
async def api_import_persona(file: UploadFile = File(...)):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
        while chunk := await file.read(1024 * 1024):
            tmp.write(chunk)
        tmp_path = Path(tmp.name)
    try:
        return import_persona_package(tmp_path)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


@app.get("/api/persona/export")
async def api_export_persona(persona_id: str | None = None):
    try:
        zip_path, filename = export_persona_package(persona_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(lambda path: Path(path).unlink(missing_ok=True), zip_path),
    )


@app.post("/api/mcps/home-assistant/validate")
async def api_validate_home_assistant_mcp(payload: dict[str, Any]):
    url = str(payload.get("url") or "")
    return await validate_home_assistant_mcp(url)


@app.post("/api/mcps/validate")
async def api_validate_external_mcp(payload: dict[str, Any]):
    url = str(payload.get("url") or "")
    try:
        return await validate_streamable_http_mcp(
            url,
            auth_type=str(payload.get("auth_type") or "none"),
            server_id=str(payload.get("id") or payload.get("name") or ""),
            bearer_token=str(payload.get("bearer_token") or ""),
        )
    except Exception as exc:
        return {"ok": False, "url": url.strip(), "error": redact_for_log(exc)}


def _oauth_callback_url(request: Request, flow_id: str) -> str:
    return str(request.url_for("api_mcp_oauth_callback", flow_id=flow_id))


@app.post("/api/mcps/oauth/start")
async def api_start_mcp_oauth(payload: dict[str, Any], request: Request):
    url = str(payload.get("url") or "").strip()
    server_id = normalize_mcp_server_id(payload.get("id") or payload.get("name") or url)
    flow_id = token_secrets.token_urlsafe(18)
    loop = asyncio.get_running_loop()
    flow: dict[str, Any] = {
        "flow_id": flow_id,
        "server_id": server_id,
        "url": url,
        "auth_url": "",
        "result": None,
        "error": "",
        "event": asyncio.Event(),
        "callback": loop.create_future(),
    }
    MCP_OAUTH_FLOWS[flow_id] = flow

    async def redirect_handler(auth_url: str) -> None:
        flow["auth_url"] = auth_url
        flow["event"].set()

    async def callback_handler() -> tuple[str, str | None]:
        return await asyncio.wait_for(flow["callback"], timeout=300)

    async def run_oauth_validation() -> None:
        try:
            flow["result"] = await validate_streamable_http_mcp(
                url,
                timeout_seconds=20,
                auth_type="oauth",
                server_id=server_id,
                oauth_redirect_uri=_oauth_callback_url(request, flow_id),
                oauth_redirect_handler=redirect_handler,
                oauth_callback_handler=callback_handler,
            )
        except Exception as exc:
            flow["error"] = redact_for_log(exc)
        finally:
            flow["event"].set()

    flow["task"] = asyncio.create_task(run_oauth_validation())
    try:
        await asyncio.wait_for(flow["event"].wait(), timeout=20)
    except asyncio.TimeoutError:
        return {"ok": False, "flow_id": flow_id, "error": "OAuth authorization URL was not returned."}

    if flow.get("result"):
        return {"ok": True, "flow_id": flow_id, "connected": True, **flow["result"]}
    if flow.get("error"):
        return {"ok": False, "flow_id": flow_id, "error": flow["error"]}
    return {"ok": True, "flow_id": flow_id, "auth_url": flow.get("auth_url", "")}


@app.get("/api/mcps/oauth/status/{flow_id}")
async def api_mcp_oauth_status(flow_id: str):
    flow = MCP_OAUTH_FLOWS.get(flow_id)
    if not flow:
        raise HTTPException(status_code=404, detail="OAuth flow not found.")
    if flow.get("result"):
        return {"ok": True, "flow_id": flow_id, "connected": True, **flow["result"]}
    if flow.get("error"):
        return {"ok": False, "flow_id": flow_id, "error": flow["error"]}
    return {"ok": True, "flow_id": flow_id, "pending": True, "auth_url": flow.get("auth_url", "")}


@app.get("/api/mcps/oauth/callback/{flow_id}", name="api_mcp_oauth_callback")
async def api_mcp_oauth_callback(flow_id: str, request: Request):
    flow = MCP_OAUTH_FLOWS.get(flow_id)
    if not flow:
        return HTMLResponse("<h1>BitMon MCP OAuth</h1><p>OAuth flow not found.</p>", status_code=404)
    callback = flow.get("callback")
    error = request.query_params.get("error")
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if callback and not callback.done():
        if error:
            callback.set_exception(RuntimeError(error))
        elif code:
            callback.set_result((code, state))
        else:
            callback.set_exception(RuntimeError("OAuth callback did not include a code."))
    return HTMLResponse(
        "<h1>BitMon MCP OAuth</h1><p>Authorization received. You can close this tab and return to BitMon.</p>"
    )


@app.get("/api/local/kokoro/voices")
async def api_local_kokoro_voices():
    return list_kokoro_voices()


@app.get("/api/local/kokoro/models")
async def api_local_kokoro_models():
    return list_kokoro_models()


@app.post("/api/local/lmstudio/models")
async def api_local_lmstudio_models(payload: dict[str, Any]):
    base_url = str(payload.get("base_url") or get_config().get("local", {}).get("base_url") or "")
    try:
        return await asyncio.to_thread(list_lmstudio_models, base_url)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "models": []}


@app.post("/api/input/hotkey-capture/start")
async def api_hotkey_capture_start():
    return start_hotkey_capture()


@app.get("/api/input/hotkey-capture/result")
async def api_hotkey_capture_result():
    return get_hotkey_capture_result()


@app.post("/api/input/hotkey-capture/cancel")
async def api_hotkey_capture_cancel():
    return cancel_hotkey_capture()


@app.get("/api/wake-word/models")
async def api_wake_word_models():
    WAKEWORD_DIR.mkdir(parents=True, exist_ok=True)
    builtins: list[dict[str, str]] = []
    try:
        from openwakeword import MODELS

        for name in sorted(MODELS):
            builtins.append({
                "value": f"builtin:{name}",
                "label": name.replace("_", " ").title(),
                "type": "builtin",
                "model_name": name,
            })
    except Exception:
        builtins = []

    files = []
    for path in sorted(WAKEWORD_DIR.glob("*.onnx")):
        files.append({
            "value": f"file:{path.name}",
            "label": path.stem.replace("_", " ").title(),
            "type": "file",
            "path": f"wakeword/{path.name}",
        })
    return {"models": builtins + files}


@app.get("/api/home-assistant/devices")
async def api_home_assistant_devices():
    return list_home_assistant_devices()


@app.put("/api/home-assistant/devices")
async def api_save_home_assistant_devices(payload: dict[str, Any]):
    devices = payload.get("devices") or []
    if not isinstance(devices, list):
        devices = []
    return save_home_assistant_devices(devices)


@app.post("/api/home-assistant/devices/refresh")
async def api_refresh_home_assistant_devices(payload: dict[str, Any]):
    url = str(payload.get("url") or "") or None
    return await refresh_home_assistant_devices_cache(url)


if bitmon_mcp is not None:
    app.mount("/mcp", bitmon_mcp.streamable_http_app())

app.mount("/persona/assets", StaticFiles(directory=PERSONA_ASSETS_DIR), name="persona_assets")
app.mount("/persona/fonts", StaticFiles(directory=PERSONA_FONTS_DIR), name="persona_fonts")
app.mount("/persona/icons", StaticFiles(directory=PERSONA_ICONS_DIR), name="persona_icons")
app.mount("/vendor", StaticFiles(directory=WEB_VENDOR_DIR), name="web_vendor")


@app.websocket("/session")
async def session(ws: WebSocket):
    """Bidirectional voice session used by the persona client."""
    voice = ws.query_params.get("voice") or None
    prompt = ws.query_params.get("prompt") or None
    model = ws.query_params.get("model") or None
    name = ws.query_params.get("name") or None
    config = get_config()
    provider = str(config.get("llm", {}).get("provider") or config.get("provider") or "inworld").lower()
    tts = config.get("tts") or {}
    tts_provider = str(tts.get("provider") or "inworld").lower() if bool(tts.get("enabled", True)) else "off"
    if provider == "local":
        print(f"[VoiceFlow] mode=normal stt=whisper llm=local tts={tts_provider}")
        await local_session_proxy(ws, voice=voice, prompt=prompt, model=model, name=name)
    else:
        print(f"[VoiceFlow] mode=normal stt=whisper llm=inworld-router tts={tts_provider}")
        await inworld_chat_proxy(ws)


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=_env("HOST", "127.0.0.1"),
        port=int(_env("PORT", "8000")),
        reload=_env("RELOAD", "0").strip() == "1",
        use_colors=False,
        log_level="warning",
    )
