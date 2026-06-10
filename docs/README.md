# BitMon documentation

Welcome! This folder is the full manual for BitMon. If you just want to get it
running, start with **Installation** and then skim **The launcher** and
**Configuration UI**.

## Table of contents

1. **[Installation](installation.md)** — prerequisites, GPU vs CPU, virtual
   environment, first run, where your files live.
2. **[Providers & requirements](providers.md)** — Inworld (cloud) vs Local
   (offline), the Inworld API-key disclaimer, and the GPU/Whisper notes.
3. **[The launcher](launcher.md)** — the tray app that boots and supervises
   everything.
4. **[Configuration UI — every tab](configuration.md)** — a detailed,
   field-by-field walkthrough of every tab.
5. **[Personas & the animation editor](personas.md)** — create, import, export
   and activate your own pets.
6. **[Wake word](wake-word.md)** — hands-free activation and training a custom
   phrase.
7. **[Home Assistant](home-assistant.md)** — connect the MCP and pick which
   devices the pet may control.
8. **[Troubleshooting](troubleshooting.md)** — the errors you are most likely to
   hit, and the fix for each.
9. **[Architecture](architecture.md)** — how the backend, voice flows and
   overlay fit together (for contributors).

## Conventions used in these docs

- Commands are written for **Windows PowerShell** (the supported platform).
- `127.0.0.1:8000` is the default address; if you changed `BITMON_PORT`, adjust
  accordingly.
- Settings are referenced by the **tab → label** they appear under in the
  configuration page, e.g. *Model → LLM provider*.
- Image placeholders (`docs/images/*.png`) are optional — see
  [images/README.md](images/README.md).
