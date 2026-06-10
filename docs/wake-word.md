# Wake word

The wake word lets you talk to your pet **hands-free** — say a trigger phrase
("hey ...") and the pet starts listening for your command, no hotkey needed. It's
powered by [openWakeWord](https://github.com/dscripka/openWakeWord) and runs
entirely **locally**.

Settings live under **Microphone → Wake word** (see
[Configuration](configuration.md#wake-word-hands-free)).

---

## Turning it on

1. Open **Microphone** in the config page.
2. Enable **Wake word**.
3. Pick an **Activation phrase** from the dropdown.
4. Tune the timing (below) and **Save configuration**.

How a hands-free turn works:

```
"hey jarvis" ──▶ pet wakes & starts listening ──▶ you speak your command ──▶ trailing silence ends it ──▶ pet answers
```

---

## Timing settings

| Setting | What it controls | Tips |
|---|---|---|
| **Activation wait seconds** | After the wake word fires, how long to wait for you to begin speaking. | Raise it if the pet gives up before you start. |
| **Finish silence seconds** | How much silence at the end marks your command as complete and sends it. | Lower = snappier but may cut you off; higher = waits longer. |

> The pet plays its **listening** animation while it captures your command (or
> falls back to *thinking* / *idle* if your persona has no listening animation —
> see [Personas](personas.md#animation-types-kinds)).

---

## Choosing a phrase

The dropdown lists two kinds of models:

- **Built-in** phrases that ship with openWakeWord (e.g. *Hey Jarvis*, *Alexa*,
  *Hey Mycroft*). These download automatically the first time they're used.
- **Custom** models — any `.onnx` file you place in the `wakeword/` folder shows
  up here automatically (the label is derived from the filename).

The bundled **`hey_Noah.onnx`** model is the default activation phrase
("Hey Noah").

---

## Using your own wake word

**This project does not include a training tool** — but adding a custom phrase is
easy, because an openWakeWord wake word is just a small `.onnx` file. You have two
ways to get one:

**Option A — grab a ready-made model.** Plenty of pre-trained openWakeWord phrases
are shared online. Search for *"openWakeWord custom model"*: community repositories
and the openWakeWord [GitHub discussions/issues](https://github.com/dscripka/openWakeWord)
collect many `.onnx` phrases you can download and use as-is.

**Option B — train your own.** openWakeWord provides an automated training pipeline
that builds a model for any phrase in a few minutes — you only type the phrase, no
audio recording needed. Run it in this ready-made Google Colab notebook:

> 🔗 **[Wake word training notebook (Google Colab)](https://colab.research.google.com/drive/1q1oe2zOyZp7UsB3jJiQ1IFn8z5YfjwEb?usp=sharing)**

There are also many video walkthroughs online (search *"how to train an
openWakeWord model"*). Export the final **`.onnx`** when you're done.

Then, with either model:

1. Drop the **`.onnx`** file into the **`wakeword/`** folder.
2. Reopen **Microphone → Wake word**; your phrase appears in the **Activation
   phrase** dropdown.
3. Select it and **Save configuration**.

> [!TIP]
> Pick a phrase that is **two or three syllables and uncommon** in normal speech
> ("hey noah", "okay pixel") so it triggers when you mean it and stays quiet
> otherwise.

---

## Troubleshooting

| Symptom | Try |
|---|---|
| Never triggers | Speak the phrase clearly; raise **Gain** on the Microphone tab; confirm the right phrase is selected. |
| Triggers on random speech | Choose a less common phrase, or a better-trained custom model. |
| Cuts you off mid-sentence | Increase **Finish silence seconds**. |
| Gives up before you speak | Increase **Activation wait seconds**. |

More general fixes in [Troubleshooting](troubleshooting.md).

---

Next: **[Home Assistant](home-assistant.md)**.
