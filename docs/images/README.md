# Documentation images

Drop your screenshots and artwork here so the Markdown files render nicely on
GitHub. The docs already reference the filenames below — just add a PNG with the
matching name and it will show up automatically. None of them are required for
the app to work; they only make the documentation look good.

| Filename | Where it is used | Suggested content | Recommended size (px) |
|---|---|---|---|
| `hero.png` | `README.md` (top banner) | The persona overlay on your desktop + the launcher, a wide "hero" shot | **1280 × 400** (wide horizontal banner, ~3.2:1). Export at 2× → 2560 × 800 for a crisp result. |
| `demo.gif` | `README.md` (demo block) | A few seconds of the pet talking / reacting — an animated GIF | **640 × 640** (or match the pet window, ~540 × 600). Keep it **under ~5 MB** and ~6–10 s so GitHub plays it inline. |
| `launcher.png` | `docs/launcher.md`, `README.md` | The launcher window (basic state) | The launcher window is **370 × 140**. Crop tight with a little desktop margin → ~**560 × 280**. |
| `launcher-advanced.png` | `docs/launcher.md` | The launcher expanded with the **Backend** / **Persona** log tabs | The expanded window is **760 × 520**. With a small margin → ~**900 × 620**. |
| `config-chat.png` | `docs/configuration.md` | The **Conversations** tab | **1280 × 800** (16:10). Use the same size for every `config-*` shot so they line up. |
| `config-character.png` | `docs/configuration.md` | The **Character** tab | **1280 × 800** |
| `config-model.png` | `docs/configuration.md`, `docs/providers.md` | The **Model** tab | **1280 × 800** |
| `config-microphone.png` | `docs/configuration.md` | The **Microphone** tab with the level meter running | **1280 × 800** |
| `config-capabilities.png` | `docs/configuration.md` | The **Capabilities** tab | **1280 × 800** |
| `config-persona.png` | `docs/personas.md` | The **Persona** tab / animation editor | **1280 × 800** |
| `config-mcps.png` | `docs/configuration.md`, `docs/home-assistant.md` | The **MCPs** tab | **1280 × 800** |
| `config-devices.png` | `docs/home-assistant.md` | The **Devices** tab | **1280 × 800** |
| `persona-editor.png` | `docs/personas.md` | The sprite sheet preview + animation settings | **1280 × 800** |

### Tips for good screenshots

- Use a dark desktop background so the overlay pops.
- Capture at 100% zoom; GitHub scales images down nicely but not up.
- Keep each image under ~1 MB (PNG export, or run them through the same WebP
  optimization the app uses if you want them tiny).

### Recording `demo.gif` (a clip of the pet in use)

You only need to capture a small region of the screen — the pet window — for a
few seconds, then turn it into a GIF.

**1. Record just the pet window**

- **Easiest (built-in):** Press <kbd>Win</kbd>+<kbd>Shift</kbd>+<kbd>R</kbd> to
  open the Windows Snipping Tool's screen recorder, drag a box around the pet,
  and record ~6–10 seconds. Save the `.mp4`.
- **More control:** [ScreenToGif](https://www.screentogif.com/) (free, Windows)
  records a selected region **straight to GIF** — no conversion step. Set the
  recorder rectangle over the pet, hit record, then *File → Save as → GIF*. Drop
  the frame rate to ~15 fps and use its built-in size/colors reduction to stay
  under ~5 MB.

**2. If you recorded an MP4, convert it to GIF with ffmpeg**

```powershell
# 12 fps, 600px wide, good colors via a 2-pass palette (smaller + cleaner)
ffmpeg -i clip.mp4 -vf "fps=12,scale=600:-1:flags=lanczos,palettegen" palette.png
ffmpeg -i clip.mp4 -i palette.png -filter_complex "fps=12,scale=600:-1:flags=lanczos[x];[x][1:v]paletteuse" demo.gif
```

Then save the result here as `demo.gif`.

**Keep it small:** GitHub only autoplays GIFs it can load quickly. Aim for
**6–10 s, ~12–15 fps, ≤ 5 MB**. If it's too big, lower the fps, shorten the
clip, or reduce the width (e.g. `scale=480:-1`).
