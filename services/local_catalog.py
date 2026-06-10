"""Discovery helpers for local LM Studio and Kokoro options."""

from __future__ import annotations

import json
import urllib.request
from typing import Any


KOKORO_VOICES: list[dict[str, str]] = [
    {"id": "af_heart", "lang_code": "a", "language": "English US", "name": "Heart"},
    {"id": "af_alloy", "lang_code": "a", "language": "English US", "name": "Alloy"},
    {"id": "af_aoede", "lang_code": "a", "language": "English US", "name": "Aoede"},
    {"id": "af_bella", "lang_code": "a", "language": "English US", "name": "Bella"},
    {"id": "af_jessica", "lang_code": "a", "language": "English US", "name": "Jessica"},
    {"id": "af_kore", "lang_code": "a", "language": "English US", "name": "Kore"},
    {"id": "af_nicole", "lang_code": "a", "language": "English US", "name": "Nicole"},
    {"id": "af_nova", "lang_code": "a", "language": "English US", "name": "Nova"},
    {"id": "af_river", "lang_code": "a", "language": "English US", "name": "River"},
    {"id": "af_sarah", "lang_code": "a", "language": "English US", "name": "Sarah"},
    {"id": "af_sky", "lang_code": "a", "language": "English US", "name": "Sky"},
    {"id": "am_adam", "lang_code": "a", "language": "English US", "name": "Adam"},
    {"id": "am_echo", "lang_code": "a", "language": "English US", "name": "Echo"},
    {"id": "am_eric", "lang_code": "a", "language": "English US", "name": "Eric"},
    {"id": "am_fenrir", "lang_code": "a", "language": "English US", "name": "Fenrir"},
    {"id": "am_liam", "lang_code": "a", "language": "English US", "name": "Liam"},
    {"id": "am_michael", "lang_code": "a", "language": "English US", "name": "Michael"},
    {"id": "am_onyx", "lang_code": "a", "language": "English US", "name": "Onyx"},
    {"id": "am_puck", "lang_code": "a", "language": "English US", "name": "Puck"},
    {"id": "am_santa", "lang_code": "a", "language": "English US", "name": "Santa"},
    {"id": "bf_alice", "lang_code": "b", "language": "English UK", "name": "Alice"},
    {"id": "bf_emma", "lang_code": "b", "language": "English UK", "name": "Emma"},
    {"id": "bf_isabella", "lang_code": "b", "language": "English UK", "name": "Isabella"},
    {"id": "bf_lily", "lang_code": "b", "language": "English UK", "name": "Lily"},
    {"id": "bm_daniel", "lang_code": "b", "language": "English UK", "name": "Daniel"},
    {"id": "bm_fable", "lang_code": "b", "language": "English UK", "name": "Fable"},
    {"id": "bm_george", "lang_code": "b", "language": "English UK", "name": "George"},
    {"id": "bm_lewis", "lang_code": "b", "language": "English UK", "name": "Lewis"},
    {"id": "ef_dora", "lang_code": "e", "language": "Spanish", "name": "Dora"},
    {"id": "em_alex", "lang_code": "e", "language": "Spanish", "name": "Alex"},
    {"id": "em_santa", "lang_code": "e", "language": "Spanish", "name": "Santa"},
    {"id": "ff_siwis", "lang_code": "f", "language": "French", "name": "Siwis"},
    {"id": "hf_alpha", "lang_code": "h", "language": "Hindi", "name": "Alpha"},
    {"id": "hf_beta", "lang_code": "h", "language": "Hindi", "name": "Beta"},
    {"id": "hm_omega", "lang_code": "h", "language": "Hindi", "name": "Omega"},
    {"id": "hm_psi", "lang_code": "h", "language": "Hindi", "name": "Psi"},
    {"id": "if_sara", "lang_code": "i", "language": "Italian", "name": "Sara"},
    {"id": "im_nicola", "lang_code": "i", "language": "Italian", "name": "Nicola"},
    {"id": "jf_alpha", "lang_code": "j", "language": "Japanese", "name": "Alpha"},
    {"id": "jf_gongitsune", "lang_code": "j", "language": "Japanese", "name": "Gongitsune"},
    {"id": "jf_nezumi", "lang_code": "j", "language": "Japanese", "name": "Nezumi"},
    {"id": "jf_tebukuro", "lang_code": "j", "language": "Japanese", "name": "Tebukuro"},
    {"id": "jm_kumo", "lang_code": "j", "language": "Japanese", "name": "Kumo"},
    {"id": "pf_dora", "lang_code": "p", "language": "Portuguese BR", "name": "Dora"},
    {"id": "pm_alex", "lang_code": "p", "language": "Portuguese BR", "name": "Alex"},
    {"id": "pm_santa", "lang_code": "p", "language": "Portuguese BR", "name": "Santa"},
    {"id": "zf_xiaobei", "lang_code": "z", "language": "Chinese", "name": "Xiaobei"},
    {"id": "zf_xiaoni", "lang_code": "z", "language": "Chinese", "name": "Xiaoni"},
    {"id": "zf_xiaoxiao", "lang_code": "z", "language": "Chinese", "name": "Xiaoxiao"},
    {"id": "zf_xiaoyi", "lang_code": "z", "language": "Chinese", "name": "Xiaoyi"},
    {"id": "zm_yunjian", "lang_code": "z", "language": "Chinese", "name": "Yunjian"},
    {"id": "zm_yunxi", "lang_code": "z", "language": "Chinese", "name": "Yunxi"},
    {"id": "zm_yunxia", "lang_code": "z", "language": "Chinese", "name": "Yunxia"},
    {"id": "zm_yunyang", "lang_code": "z", "language": "Chinese", "name": "Yunyang"},
]

KOKORO_MODELS: list[dict[str, str]] = [
    {"id": "hexgrad/Kokoro-82M", "name": "Kokoro 82M"},
]


def list_kokoro_voices() -> dict[str, Any]:
    return {"ok": True, "voices": KOKORO_VOICES}


def list_kokoro_models() -> dict[str, Any]:
    return {"ok": True, "models": KOKORO_MODELS}


def list_lmstudio_models(base_url: str) -> dict[str, Any]:
    base = str(base_url or "http://127.0.0.1:1234/v1").strip().rstrip("/")
    url = base if base.endswith("/models") else f"{base}/models"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=4.0) as response:
        payload = json.loads(response.read().decode("utf-8"))

    raw_models = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(raw_models, list):
        raw_models = []

    models = []
    for item in raw_models:
        if isinstance(item, dict):
            model_id = str(item.get("id") or item.get("name") or "").strip()
        else:
            model_id = str(item or "").strip()
        if model_id:
            models.append({"id": model_id})

    return {"ok": True, "url": url, "models": models}
