"""Screen capture and analysis tools.

Screen analysis uses the model configured for the active provider whenever that
model can accept images. The fallback is provider-specific:

- inworld / local -> Inworld Router vision model (gpt-4o-mini, needs Inworld key)
- openai          -> gpt-4o-mini on the OpenAI API (same OpenAI key)
- anthropic       -> no fallback: every current Claude model accepts images,
                     so the configured model is used directly

A fully local setup works as long as the local model is vision-capable.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from openai import AsyncOpenAI

from core.config import get_anthropic_api_key, get_inworld_api_key, get_openai_api_key, settings
from core.config_store import get_config
from core.security import redact_for_log


_openai_client: AsyncOpenAI | None = None
_openai_client_api_key: str = ""

VISION_MAX_TOKENS = 120
VISION_TEMPERATURE = 0.2
OPENAI_VISION_FALLBACK_MODEL = "gpt-4o-mini"


LANGUAGE_NAMES = {
    "en": "English",
    "en-US": "English",
    "en-GB": "English",
    "pt": "Brazilian Portuguese",
    "pt-BR": "Brazilian Portuguese",
    "es": "Spanish",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "fr": "French",
    "de": "German",
    "it": "Italian",
}


# Substrings (lowercased model id) that indicate a vision-capable model. Covers
# the common hosted and local VLM families. Used only when vision mode is "auto".
_VISION_MODEL_MARKERS = (
    "gpt-4o", "gpt-4.1", "gpt-4-turbo", "gpt-4-vision", "chatgpt-4o",
    "vision", "-vl", "vl-", "_vl", "llava", "bakllava", "moondream",
    "minicpm-v", "cogvlm", "qwen-vl", "qwen2-vl", "qwen2.5-vl", "qwen3-vl",
    "pixtral", "internvl", "gemma-3", "gemma3", "llama-3.2-vision",
    "llama3.2-vision", "phi-3-vision", "phi-3.5-vision", "phi-4-multimodal",
    "idefics", "smolvlm", "molmo", "deepseek-vl", "kimi-vl", "glm-4v",
    "yi-vl", "florence", "paligemma", "ovis", "janus", "mistral-small-3.1",
)


@dataclass(frozen=True)
class Screenshot:
    width: int
    height: int
    mime_type: str
    data_base64: str


@dataclass(frozen=True)
class VisionAttempt:
    label: str
    client: Any
    model: str
    # "openai" attempts go through Chat Completions (OpenAI/Inworld/LM Studio);
    # "anthropic" attempts use the official anthropic SDK with image blocks.
    kind: str = "openai"
    api_key: str = field(default="", repr=False)


def _log_screen(text: str) -> None:
    print(f"{datetime.now().strftime('%H:%M:%S')} - [screen] {redact_for_log(text)}")


def _get_router_client() -> AsyncOpenAI:
    # Imported lazily: tool_runtime imports this module, and inworld_chat
    # imports tool_runtime — a top-level import here would close the cycle.
    from services.inworld_chat import router_client

    return router_client(get_inworld_api_key())


def _get_openai_client() -> AsyncOpenAI:
    global _openai_client, _openai_client_api_key
    api_key = get_openai_api_key()
    if _openai_client is None or _openai_client_api_key != api_key:
        _openai_client = AsyncOpenAI(api_key=api_key)
        _openai_client_api_key = api_key
    return _openai_client


def _model_supports_vision(model_id: str) -> bool:
    text = str(model_id or "").strip().lower()
    return any(marker in text for marker in _VISION_MODEL_MARKERS)


def _vision_capable(mode: str, model_id: str) -> bool:
    mode = str(mode or "auto").strip().lower()
    if mode == "on":
        return True
    if mode == "off":
        return False
    return _model_supports_vision(model_id)


def _selected_attempt(config: dict[str, Any]) -> tuple[VisionAttempt | None, bool]:
    """Build the vision attempt for the user's selected provider/model.

    Returns (attempt, capable). ``attempt`` is None when no client can be built
    (e.g. the provider's API key is missing)."""
    provider = str(config.get("provider") or "inworld").lower()
    if provider == "local":
        local = config.get("local") or {}
        base_url = str(local.get("base_url") or "http://127.0.0.1:1234/v1").rstrip("/")
        model = str(local.get("vision_model") or local.get("model") or "local-model").strip()
        capable = _vision_capable(local.get("vision", "auto"), model)
        client = AsyncOpenAI(api_key="lm-studio", base_url=base_url)
        return VisionAttempt(f"local:{model}", client, model), capable

    if provider == "openai":
        openai_section = config.get("openai") or {}
        model = str(openai_section.get("model") or "gpt-4o-mini").strip()
        capable = _vision_capable("auto", model)
        if not get_openai_api_key():
            return None, capable
        return VisionAttempt(f"openai:{model}", _get_openai_client(), model), capable

    if provider == "anthropic":
        anthropic_section = config.get("anthropic") or {}
        model = str(anthropic_section.get("model") or "claude-opus-4-8").strip()
        api_key = get_anthropic_api_key()
        if not api_key:
            return None, True
        # Every current Claude model accepts images, so it is always capable.
        return VisionAttempt(f"anthropic:{model}", None, model, kind="anthropic", api_key=api_key), True

    inworld = config.get("inworld") or {}
    model = str(inworld.get("model") or "deepseek-v4-flash").strip()
    capable = _vision_capable(inworld.get("vision", "auto"), model)
    if not get_inworld_api_key():
        return None, capable
    return VisionAttempt(f"inworld:{model}", _get_router_client(), model), capable


def _fallback_attempt(config: dict[str, Any]) -> VisionAttempt | None:
    """Provider-specific vision fallback.

    - openai    -> gpt-4o-mini on the OpenAI API (same key)
    - anthropic -> no fallback (the Claude model itself handles images)
    - inworld / local -> the Inworld Router vision model (needs an Inworld key)
    """
    provider = str(config.get("provider") or "inworld").lower()
    if provider == "openai":
        if not get_openai_api_key():
            return None
        model = OPENAI_VISION_FALLBACK_MODEL
        return VisionAttempt(f"openai:{model}", _get_openai_client(), model)
    if provider == "anthropic":
        return None
    if not get_inworld_api_key():
        return None
    model = settings.INWORLD_ROUTER_VISION_MODEL
    return VisionAttempt(f"inworld:{model}", _get_router_client(), model)


def _resolve_vision_attempts(config: dict[str, Any]) -> list[VisionAttempt]:
    """Ordered vision attempts: selected model first (when usable), then fallback.

    - selected model is vision-capable  -> try it, then fallback on error
    - selected model is not vision-capable, fallback exists -> skip straight to fallback
    - selected model is not vision-capable, no fallback (100% local) -> best-effort
      try the selected model anyway, since there is nothing to lose
    """
    selected, capable = _selected_attempt(config)
    fallback = _fallback_attempt(config)

    attempts: list[VisionAttempt] = []
    if selected is not None and (capable or fallback is None):
        attempts.append(selected)
    if fallback is not None:
        attempts.append(fallback)

    deduped: list[VisionAttempt] = []
    seen: set[str] = set()
    for attempt in attempts:
        if attempt.label in seen:
            continue
        seen.add(attempt.label)
        deduped.append(attempt)
    return deduped


def capture_screen(monitor: int = 1) -> Screenshot:
    """Capture the current screen and return PNG bytes as base64."""
    import mss
    import mss.tools

    with mss.mss() as sct:
        monitors = sct.monitors
        selected_monitor = monitor
        if selected_monitor < 0 or selected_monitor >= len(monitors):
            selected_monitor = 1 if len(monitors) > 1 else 0
        shot = sct.grab(monitors[selected_monitor])
        png_bytes = mss.tools.to_png(shot.rgb, shot.size)

    return Screenshot(
        width=shot.width,
        height=shot.height,
        mime_type="image/png",
        data_base64=base64.b64encode(png_bytes).decode("ascii"),
    )


def _answer_language(config: dict[str, Any]) -> str:
    provider = str(config.get("provider") or "inworld").lower()
    section = config.get(provider) or config.get("inworld") or {}
    speech = config.get("speech") or {}
    return str(speech.get("tts_language") or section.get("tts_language") or "en").strip()


def _build_prompt(config: dict[str, Any], question: str, request: str) -> str:
    language_code = _answer_language(config)
    response_language = LANGUAGE_NAMES.get(language_code, language_code)
    return (
        "You are a screen-understanding tool for a voice assistant. "
        f"You MUST answer only in {response_language}. "
        "This language instruction has priority over the user's language. "
        "Use the screenshot as the current state of the user's screen and answer the user's actual request. "
        "The user may be asking you to locate something, explain what to do next, troubleshoot an error, "
        "read visible text, compare options, or guide an action. Do not just describe the screen unless "
        "that is what the user asked. If giving directions, use practical visual references such as top right, "
        "left sidebar, bottom bar, button label, color, or nearby text. If the requested thing is not visible, "
        "say that clearly and suggest the next useful step. Keep the answer very short and suitable for TTS: "
        "maximum two short sentences, no lists, no step-by-step unless explicitly requested. "
        "Return the answer directly, without saying you cannot view the screen, because the screenshot is attached.\n\n"
        f"Answer language code: {language_code}\n"
        f"Answer language name: {response_language}\n"
        f"Original user request: {request}\n"
        f"Tool question from conversation model: {question.strip() or request}"
    )


def _vision_messages(prompt: str, screenshot: Screenshot) -> list[dict[str, Any]]:
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{screenshot.mime_type};base64,{screenshot.data_base64}",
                        "detail": "high",
                    },
                },
            ],
        }
    ]


async def _run_vision_attempt(
    attempt: VisionAttempt,
    messages: list[dict[str, Any]],
    prompt: str,
    screenshot: Screenshot,
) -> str:
    """Run one vision attempt and return the analysis text (may be empty)."""
    if attempt.kind == "anthropic":
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=attempt.api_key)
        try:
            response = await client.messages.create(
                model=attempt.model,
                max_tokens=VISION_MAX_TOKENS,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": screenshot.mime_type,
                                "data": screenshot.data_base64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }],
            )
        finally:
            await client.close()
        parts = [
            getattr(block, "text", "") or ""
            for block in response.content
            if getattr(block, "type", None) == "text"
        ]
        return " ".join(part.strip() for part in parts if part.strip()).strip()

    response = await attempt.client.chat.completions.create(
        model=attempt.model,
        messages=messages,
        max_tokens=VISION_MAX_TOKENS,
        temperature=VISION_TEMPERATURE,
    )
    return (response.choices[0].message.content or "").strip()


async def analyze_screen(
    question: str = "",
    user_request: str = "",
) -> dict[str, Any]:
    """Capture the screen, send it to a vision model, and return a concise analysis."""
    config = get_config()
    if not config.get("tools", {}).get("screen_analysis", False):
        return {
            "ok": False,
            "error": "Screen analysis is disabled in BitMon config.",
            "answer": "Screen analysis is turned off in the settings.",
        }

    started = time.perf_counter()
    attempts = _resolve_vision_attempts(config)
    if not attempts:
        message = (
            "No vision-capable model is available. Check that the active LLM provider's API key "
            "is saved, select a vision-capable model (or set its vision option to 'on'), or "
            "configure an Inworld API key to use the gpt-4o-mini router fallback."
        )
        _log_screen("no vision-capable model available")
        return {"ok": False, "error": message, "answer": "I do not have a model that can see the screen right now."}

    try:
        screenshot = capture_screen()
    except Exception as exc:
        _log_screen(f"capture failed: {exc}")
        return {
            "ok": False,
            "error": f"Could not capture the screen: {exc}",
            "answer": "I could not capture the screen right now.",
        }

    prompt = _build_prompt(config, question, user_request.strip() or question.strip() or "Help me with what is visible on my screen.")
    messages = _vision_messages(prompt, screenshot)

    last_error = ""
    for attempt in attempts:
        try:
            analysis = await _run_vision_attempt(attempt, messages, prompt, screenshot)
            if not analysis:
                last_error = f"{attempt.label} returned an empty response"
                _log_screen(last_error)
                continue
            elapsed = round(time.perf_counter() - started, 2)
            _log_screen(f"{attempt.label} answered ({elapsed}s)")
            return {
                "ok": True,
                "analysis": analysis,
                "model": attempt.model,
                "provider": attempt.label,
                "instruction": (
                    "Use the analysis field as the factual answer to the user. "
                    "Do not say you cannot see the screen; the screen was captured and analyzed successfully."
                ),
                "width": screenshot.width,
                "height": screenshot.height,
                "elapsed_seconds": elapsed,
            }
        except Exception as exc:
            last_error = f"{attempt.label}: {exc}"
            _log_screen(f"attempt failed {last_error}")
            continue

    return {
        "ok": False,
        "error": f"Screen analysis failed. Last error: {last_error}",
        "answer": "I could not analyze the screen right now.",
        "elapsed_seconds": round(time.perf_counter() - started, 2),
    }
