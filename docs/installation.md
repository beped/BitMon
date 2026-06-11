# Installation

This guide takes you from a clean Windows machine to a talking pet on your
desktop.

> [!NOTE]
> BitMon targets **Windows 10/11**. The persona overlay, hotkey capture and the
> launcher are built and tested for Windows. The backend itself is cross-platform
> Python, but the desktop experience assumes Windows.

---

## 1. Prerequisites

| Requirement | Why | Notes |
|---|---|---|
| **Windows 10 or 11** | Overlay, tray launcher, global hotkeys | — |
| **Python 3.11 or 3.12** | Runs the backend and overlay | Tick *"Add Python to PATH"* in the installer |
| **A microphone** | Speech input | Any input device Windows recognises |
| **NVIDIA GPU + CUDA** *(optional)* | Much faster speech recognition | Strongly recommended for `medium` Whisper or low latency — see [Providers](providers.md#whisper--gpu) |
| **An Inworld API key** *(optional)* | Only for the **Inworld** provider | Skip it entirely if you use the **Local** provider |
| **LM Studio** *(optional)* | Only for the **Local** (offline) provider | Free download, runs a local LLM server |

You only need **one** of *Inworld key* **or** *LM Studio*, depending on which
provider you choose. See [Providers & requirements](providers.md).

---

## 2. Get the code

Clone or download this `backend` folder. Everything BitMon needs lives inside
it — the launcher, the config UI, the persona overlay and the bundled assets.

```powershell
git clone https://github.com/beped/BitMon
cd BitMon
```

---

## 3. Create the virtual environment

```powershell
python -m venv venv
.\venv\Scripts\activate
```

### CPU-only install (simplest)

```powershell
pip install -r requirements.txt
```

### NVIDIA GPU install (recommended for speed)

Install the CUDA PyTorch wheels **first**, then the rest:

```powershell
pip install -r requirements-gpu.txt
pip install -r requirements.txt
```

> The GPU requirements pin a CUDA 12.8 build of PyTorch. If you have a different
> CUDA toolkit, change the `--extra-index-url` in `requirements-gpu.txt` to the
> matching channel from <https://pytorch.org>.

The first install pulls in WhisperX, Kokoro and PySide6 and can take several
minutes.

---

## 4. First run

The friendliest way to start everything is the launcher:

```powershell
python launcher.py
```

> Or simply **double-click `start.bat`** — it runs the launcher for you and, if
> there's no virtual environment yet, lets the launcher create one with your
> system Python.

On first launch it will:

1. Create/verify the virtual environment and install dependencies (if you ran
   `pip install` above this is instant).
2. Start the **backend** (FastAPI) and wait for it to become healthy.
3. **Load the speech models** — the first time, WhisperX downloads its model, so
   this step can take a minute. The launcher shows *"Loading models…"*.
4. Start the **persona overlay** — your pet appears.
5. Minimise itself to the system tray.

> [!TIP]
> Prefer to run the pieces by hand? Start the backend with `python main.py` and
> the overlay with `python persona/personagem.py`. The launcher just supervises
> both for you. See [The launcher](launcher.md).

---

## 5. Configure it

Open the configuration page:

```
http://127.0.0.1:8000/config
```

(The launcher's ⚙️ button opens it for you.) At minimum:

- **Model tab** → pick your **LLM provider** (Inworld or LM Studio) and, for
  Inworld, paste your **API key**.
- **Character tab** → name your pet and give it a personality.
- **Microphone tab** → set a **push-to-talk hotkey** (or enable the wake word).

Everything is covered field-by-field in
[Configuration UI — every tab](configuration.md).

---

## 6. Where your files live

When you start BitMon **from source** (the normal case), runtime files are kept
next to the backend:

| File / folder | What it is |
|---|---|
| `venv/` | The Python virtual environment |
| `bitmon_config.json` | Your saved settings (created from the example on first run) |
| `logs/` | Backend, persona and setup logs |
| `cache/` | Integration caches (e.g. Home Assistant) |
| `persona/` packages | Your personas and their sprites |

Your **Inworld API key is not** in `bitmon_config.json` — it is stored in the
Windows Credential Manager via `keyring`.

> These paths can be relocated with environment variables — see
> [Configuration → Environment variables](configuration.md#environment-variables).

---

## 7. Updating

Pull the latest code and let the launcher reconcile dependencies:

```powershell
git pull
python launcher.py
```

The launcher hashes `requirements.txt` + `requirements-core.txt` and only
reinstalls when they change, so updates are usually fast.

---

Next: **[Providers & requirements](providers.md)** to decide between the cloud
and offline brains.
