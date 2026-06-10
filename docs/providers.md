# Providers & requirements

BitMon's "brain" (the LLM) and its "voice" (TTS) are pluggable. You pick a
combination on the **Model** tab. This page explains the trade-offs and the
exact requirements/disclaimers for each, so you know what you're signing up for.

A full voice turn always flows through three stages:

```
🎙️  You speak ─▶ STT (WhisperX, local) ─▶ LLM (Inworld OR LM Studio) ─▶ TTS (Inworld OR Kokoro) ─▶ 🔊 pet speaks
```

**Speech-to-text (STT) is always WhisperX, running locally on your machine** —
regardless of which provider you pick. Only the LLM and TTS stages change.

---

## The two providers

### 1. Inworld (cloud)

| | |
|---|---|
| **LLM** | Inworld Router (OpenAI-compatible) — default model `deepseek-v4-flash` |
| **TTS** | Inworld TTS |
| **Vision** | Inworld Router vision for screen analysis |
| **Needs** | An **Inworld API key** |
| **Best for** | Lower local resource use, fast responses, no heavy local LLM |

> [!IMPORTANT]
> **Inworld requires an API key.** If you select the Inworld provider you must
> create an Inworld account, obtain an API key, and paste it into
> **Model → Inworld API key**. Calls to Inworld are billed/limited according to
> your Inworld plan. Without a valid key the Inworld provider cannot answer and
> you will see provider errors in the logs.
>
> The key is stored securely in the Windows Credential Manager (via `keyring`),
> **never** written to `bitmon_config.json`, and the configuration API never
> returns it — it only reports whether one is configured.

### 2. Local (fully offline)

| | |
|---|---|
| **LLM** | **LM Studio** (any model you load), via its OpenAI-compatible server |
| **TTS** | **Kokoro** (runs locally) |
| **Vision** | Your local vision-capable model, or it falls back gracefully |
| **Needs** | LM Studio installed and serving a model |
| **Best for** | Privacy, no API keys, no per-request cost, working offline |

> [!TIP]
> The Local provider keeps **everything on your computer** — audio, transcripts
> and replies never touch the internet. This is the recommended choice if you
> don't want to manage an API key.

#### Setting up LM Studio

1. Install [LM Studio](https://lmstudio.ai) and download a chat model.
2. Start its **local server** (default `http://127.0.0.1:1234/v1`).
3. In BitMon: **Model → LLM provider → LM Studio**, set the **LM Studio URL**,
   and click **Refresh** next to *LM Studio model* to list the loaded models.
4. (Optional) Set a **local vision model** if you want screen analysis offline.

---

## Mixing and matching

The LLM and TTS providers are independent. Common combinations:

| Goal | LLM | TTS |
|---|---|---|
| Fully offline | LM Studio | Kokoro |
| Cloud brain, cloud voice | Inworld | Inworld |
| Cloud brain, offline voice | Inworld | Kokoro |

Set **Voice response** off entirely if you only want text replies (no TTS at
all) — see [Configuration → Model](configuration.md#model).

---

## Whisper & GPU

Speech recognition uses **WhisperX**, which runs **locally**. You choose the
model size under **Model → Whisper**:

| Model | Accuracy | Speed | Recommended for |
|---|---|---|---|
| `tiny` | Lowest | Fastest | Very weak CPUs, quick tests |
| `base` *(default)* | Good | Fast | Most users on CPU |
| `small` | Better | Moderate | CPU with patience, or any GPU |
| `medium` | Best (of these) | Slow on CPU | **GPU recommended** |

> [!IMPORTANT]
> **A GPU is ideal — but not required.** WhisperX will use your **NVIDIA GPU
> (CUDA)** automatically when the CUDA build of PyTorch is installed (see
> [Installation](installation.md#3-create-the-virtual-environment)). On GPU,
> transcription is near-instant even at `medium`. On CPU it still works, but
> larger models add noticeable latency to every spoken turn — stick to `tiny`
> or `base` on CPU.

The first time a Whisper model is used it is **downloaded and cached**, so the
very first transcription (and the launcher's *"Loading models…"* step) is slower
than subsequent ones.

---

## Privacy

- The backend binds to **`127.0.0.1`** (localhost) by default and CORS is limited
  to local origins.
- Sensitive local endpoints require an auto-generated local token.
- `/docs`, `/redoc`, `/openapi.json` and the `/mcp` mount are **off by default**.
- With the **Local** provider, no audio or text leaves your machine.
- With the **Inworld** provider, your spoken text and (if screen vision is on)
  screenshots are sent to Inworld for processing.
- Home Assistant integration can expose device names and your local HA URL —
  review caches before sharing logs or forks.

To explicitly enable the developer endpoints:

```powershell
$env:BITMON_ENABLE_DOCS="1"
$env:BITMON_ENABLE_MCP="1"
python main.py
```

---

Next: **[The launcher](launcher.md)**.
