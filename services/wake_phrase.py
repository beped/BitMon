"""Wake phrase text helpers."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


WORD_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)


def _list_value(value: Any) -> list[str]:
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, list):
        items = value
    else:
        items = []
    return [str(item).strip() for item in items if str(item or "").strip()]


def _phrase_from_model_value(value: str) -> str:
    text = str(value or "").strip()
    if ":" in text:
        text = text.split(":", 1)[1]
    if "/" in text or "\\" in text:
        text = Path(text).stem
    return " ".join(part for part in re.split(r"[_\-\s]+", text) if part)


def configured_wake_phrases(config: dict[str, Any]) -> list[str]:
    """Return human-speakable wake phrases from the runtime config."""
    wake_word = config.get("wake_word") or {}
    candidates: list[str] = []
    selected = str(wake_word.get("selected_model") or "").strip()
    if selected:
        candidates.append(selected)
    candidates.extend(_list_value(wake_word.get("model_names")))
    candidates.extend(_list_value(wake_word.get("model_paths")))

    phrases: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        phrase = _phrase_from_model_value(candidate).strip()
        key = phrase.casefold()
        if phrase and key not in seen:
            seen.add(key)
            phrases.append(phrase)
    return phrases


def _words(text: str) -> list[str]:
    return [match.group(0).casefold() for match in WORD_PATTERN.finditer(text.replace("_", " "))]


def _word_spans(text: str) -> list[re.Match[str]]:
    return list(WORD_PATTERN.finditer(text))


def strip_configured_wake_phrase(text: str, config: dict[str, Any]) -> str:
    """Remove the configured wake phrase when it appears at the start of text."""
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return ""

    spans = _word_spans(cleaned)
    if not spans:
        return cleaned

    best_end = -1
    best_score = 0.0
    for phrase in configured_wake_phrases(config):
        phrase_words = _words(phrase)
        if not phrase_words:
            continue
        candidate_lengths = {len(phrase_words)}
        if len(phrase_words) > 1:
            candidate_lengths.add(len(phrase_words) - 1)
        for candidate_len in sorted(candidate_lengths, reverse=True):
            if candidate_len <= 0 or len(spans) < candidate_len:
                continue
            prefix = " ".join(match.group(0).casefold() for match in spans[:candidate_len])
            target = " ".join(phrase_words[-candidate_len:])
            score = SequenceMatcher(None, prefix, target).ratio()
            threshold = 0.8 if candidate_len > 1 else 0.9
            if score >= threshold and score > best_score:
                best_score = score
                best_end = spans[candidate_len - 1].end()

    if best_end < 0:
        return cleaned
    return cleaned[best_end:].lstrip(" ,.:;!?-")
