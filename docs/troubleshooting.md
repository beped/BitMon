# Troubleshooting

The fastest way to diagnose almost anything: open the **launcher → advanced view**
(the ▤ button) and read the **Backend logs** and **Persona logs** tabs. The same
text is on disk under `logs/`. Most problems announce themselves there.

---

## Startup

### The launcher says the port is busy

Something else is already on `127.0.0.1:8000`. Either stop it, or run BitMon on
another port:

```powershell
$env:BITMON_PORT="8123"
python launcher.py
```

If it's a *previous* BitMon backend that's still healthy, the launcher reuses it —
that's expected, not an error.

### "Loading models…" takes a long time on first run

Normal. WhisperX (and Kokoro, if you use it) download their model the first time.
Subsequent starts are fast because the models are cached.

### Dependencies fail to install

Check `logs/setup.log`. Make sure you're on **Python 3.11 or 3.12** and that
`pip` can reach the internet. To force a clean reinstall, delete the venv and run
the launcher again.

---

## Voice & audio

### The pet doesn't hear me

1. **Microphone tab → Start test** and speak. Does the level meter move?
   - No movement → wrong input device in Windows, or no mic permission.
   - Moves but stays below the threshold → raise **Gain** or lower
     **VAD sensitivity**.
2. Confirm your **push-to-talk hotkey** is set (hold it while speaking), or that
   the **wake word** is enabled and you're using the right phrase.

### Transcription is wrong or garbled

- Set **Model → STT language** to the language you actually speak.
- Use a larger **Whisper model** (`small`/`medium`) — much more accurate, but
  [a GPU is ideal](providers.md#whisper--gpu) for those on every turn.
- Reduce background noise; raise **Gain** a little if you're quiet.

### The pet thinks but never speaks

- Check **Model → Voice response** is **on**.
- If it's on but silent, look at the backend log for a TTS error (Inworld key,
  Kokoro model download, etc.).

---

## Provider / LLM errors

### `message content cannot be empty` / repeated provider errors

This means a previous turn produced an empty reply that poisoned the history. It's
fixed in current builds; if you see it on an old version, **Conversations → Clear
history** clears the bad turn.

### Inworld errors / no answer (Inworld provider)

- Make sure a valid **Inworld API key** is saved (**Model → Inworld API key**).
  Remember it's intentionally never shown back — the field being blank means
  "keep the current key", not "no key".
- Check your Inworld account quota/plan.
- See [Providers → Inworld](providers.md#1-inworld-cloud).

### LM Studio errors / no answer (Local provider)

- Is LM Studio running with a model **loaded** and its **server started**?
- Is the **LM Studio URL** correct (default `http://127.0.0.1:1234/v1`)?
- Click **Refresh** next to *LM Studio model* — if it can't list models, the
  connection is the problem.

---

## Screen vision

### "Screen analysis" doesn't work

- Enable it under **Capabilities → Screen analysis**.
- On the Local provider, set a **local vision model** (or use a chat model that
  accepts images), or adjust **Screen vision** to `On`/`Off` as appropriate.

---

## Personas

### I can't activate my new persona

A persona needs a **working idle animation** — type `idle` with a sprite file that
**actually exists**. A brand-new persona is empty. Upload a sprite, add an idle
animation pointing at it, **Save configuration**, then activate. The summary line
shows *"needs an idle animation to activate"* until this is satisfied. Full steps
in [Personas](personas.md#building-a-persona--step-by-step).

### My sprite animation looks wrong (jumps, blanks, wrong speed)

The grid description doesn't match the sheet. Recheck **Columns**, **Rows**,
**Frame size**, and **Used frames** against your actual sheet, watching the live
preview. Adjust **FPS** for speed.

---

## Home Assistant

### Devices tab is missing

It only appears after you configure **and enable** the Home Assistant MCP and it
validates. See [Home Assistant](home-assistant.md#1-connect-the-mcp).

### The pet can't find a device

- Make sure the device is **enabled** and you pressed **Save devices**.
- Add **aliases** matching how you actually refer to it.

---

## Still stuck?

Capture the relevant section of `logs/backend-process.log` (or the launcher's
advanced log view) when the problem happens — it almost always names the exact
failure (provider, model, key, connection).
