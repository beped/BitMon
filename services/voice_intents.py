"""Deterministic smart-home voice intents, executed before any LLM call.

Direct commands like "desliga a luz do escritório" / "turn on the kitchen
light" are detected by keyword + device match and executed straight against
Home Assistant. This removes a whole class of LLM failures (fake success
confirmations, leaked tool markup, latency) for the most common commands.

Anything that does not match a verb AND an enabled HA device falls through to
the LLM untouched, so questions, compound requests and everything else keep
working as before. Verbs cover pt/en/es/fr/it/de; matching is accent- and
case-insensitive, and the device side reuses the Home Assistant alias scorer,
which is language-agnostic.
"""

from __future__ import annotations

import random
import re
import time
import unicodedata
from typing import Any

from core.config_defaults import HOME_ASSISTANT_DEFAULT_ANSWERS
from tools.home_assistant import _resolve_target, execute_home_assistant_request


# Verb phrases per action, pre-normalized (lowercase, accents stripped).
# turn_off is checked first; word boundaries keep "liga" from matching inside
# "desliga". When BOTH actions match the sentence is ambiguous/compound and we
# leave it to the LLM.
INTENT_PHRASES: dict[str, tuple[str, ...]] = {
    "turn_off": (
        # Portuguese
        "desliga", "desligar", "desligue", "apaga", "apagar", "apague",
        "desativa", "desativar", "desative", "fecha", "fechar", "feche",
        # English
        "turn off", "switch off", "power off", "shut off", "close",
        # Spanish
        "desactiva", "desactivar", "cierra", "cerrar",
        # French
        "eteins", "eteindre", "ferme", "fermer",
        # Italian
        "spegni", "spegnere", "chiudi", "chiudere",
        # German
        "ausschalten", "ausmachen", "schliesse", "schliessen",
    ),
    "turn_on": (
        # Portuguese
        "liga", "ligar", "ligue", "acende", "acender", "acenda",
        "ativa", "ativar", "ative", "abre", "abrir", "abra",
        # English
        "turn on", "switch on", "power on", "open",
        # Spanish
        "enciende", "encender", "prende", "prender", "activa", "activar",
        # French
        "allume", "allumer", "ouvre", "ouvrir",
        # Italian
        "accendi", "accendere", "apri", "aprire",
        # German
        "einschalten", "anschalten", "anmachen", "oeffne", "oeffnen",
    ),
}

# Filler tokens stripped from the edges of the device name.
_EDGE_STOPWORDS = {
    "a", "o", "as", "os", "um", "uma", "do", "da", "dos", "das", "de", "del",
    "the", "my", "me", "please",
    "la", "el", "le", "les", "los", "las", "lo", "il", "der", "die", "das", "du",
    "minha", "meu", "minhas", "meus", "mi", "mis",
    "por", "favor", "agora", "now", "bitte", "ya",
}

def pick_intent_answer(action: str, config: dict[str, Any]) -> str:
    """Pick a random spoken confirmation for a direct command.

    Answers are generic on purpose (no entity names): a small fixed phrase set
    sounds natural and lets the synthesized audio be cached and reused. The
    pool comes from the user-managed list in mcps.home_assistant.answers
    (Config UI "Answers" tab); when nothing is enabled for the TTS language we
    fall back to the built-in defaults so the pet always confirms out loud.
    """
    language = str(config.get("speech", {}).get("tts_language") or "en")[:2].lower()
    home_assistant = config.get("mcps", {}).get("home_assistant", {})
    answers = home_assistant.get("answers") if isinstance(home_assistant, dict) else None

    def matching(pool: list[Any], lang: str) -> list[str]:
        if not isinstance(pool, list):
            return []
        return [
            str(answer.get("text") or "").strip()
            for answer in pool
            if isinstance(answer, dict)
            and answer.get("enabled", True)
            and str(answer.get("action") or "") == action
            and str(answer.get("language") or "")[:2].lower() == lang
            and str(answer.get("text") or "").strip()
        ]

    texts = (
        matching(answers, language)
        or matching(HOME_ASSISTANT_DEFAULT_ANSWERS, language)
        or matching(HOME_ASSISTANT_DEFAULT_ANSWERS, "en")
    )
    return random.choice(texts)


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    cleaned = re.sub(r"[^\w\s]", " ", stripped.lower())
    return " ".join(cleaned.split())


def detect_intent(text: str) -> tuple[str, str] | None:
    """Return (action, device_text) for a direct on/off command, else None."""
    raw = str(text or "").strip()
    # Questions and long sentences are not direct commands.
    if not raw or len(raw) > 200 or "?" in raw:
        return None
    norm = _normalize(raw)
    if not norm:
        return None

    matched: dict[str, re.Match[str]] = {}
    for action, phrases in INTENT_PHRASES.items():
        best: re.Match[str] | None = None
        for phrase in phrases:
            found = re.search(rf"\b{re.escape(phrase)}\b", norm)
            if found and (best is None or found.start() < best.start()):
                best = found
        if best is not None:
            matched[action] = best
    if len(matched) != 1:
        # No verb, or both on+off in one sentence (compound/ambiguous).
        return None

    action, match = next(iter(matched.items()))
    target_tokens = norm[match.end():].split()
    while target_tokens and target_tokens[0] in _EDGE_STOPWORDS:
        target_tokens.pop(0)
    while target_tokens and target_tokens[-1] in _EDGE_STOPWORDS:
        target_tokens.pop()
    if not target_tokens:
        return None
    return action, " ".join(target_tokens)


async def try_direct_smart_home_intent(text: str, config: dict[str, Any]) -> str | None:
    """Execute a direct on/off command without the LLM. Returns the spoken
    answer on success, or None to fall through to the normal LLM flow."""
    ha_config = config.get("mcps", {}).get("home_assistant", {})
    if not ha_config.get("enabled") or not str(ha_config.get("url") or "").strip():
        return None

    detected = detect_intent(text)
    if not detected:
        return None
    action, target = detected

    # Resolve locally first: no verb+device match, no log noise, no HA call.
    if not _resolve_target(target, None):
        return None

    started = time.perf_counter()
    result = await execute_home_assistant_request(action=action, targets=[target], user_request=text)
    if not result.get("ok"):
        # Execution failed: let the LLM flow handle and explain it.
        print(f"[Intent] {action} {target!r} failed ({str(result.get('error'))[:120]}); falling back to LLM")
        return None

    names = ", ".join(result.get("entity_names") or [target])
    print(
        f"[Intent] {action} -> {result.get('entities')} ({names}) "
        f"(direct, LLM skipped, {time.perf_counter() - started:.2f}s)"
    )
    return pick_intent_answer(action, config)
