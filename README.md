# J.A.R.V.I.S.

A local, offline voice assistant for Windows, modelled on the JARVIS of the Iron
Man films. Runs entirely on this machine: no API keys, no subscriptions, no data
leaving the laptop. Web search is a tool he can call, not a dependency he needs
to think.

Built for a Lenovo Yoga 7 (Ryzen 7 8840HS, Radeon 780M, 14 GB RAM, no CUDA).

```
  microphone ──▶ openWakeWord "hey jarvis" ──▶ silero VAD ──▶ faster-whisper
                                                                    │
                                                                    ▼
                              Ollama · qwen2.5:7b-instruct  (Vulkan, iGPU)
                                    ↕ 37 tools   ↕ SQLite memory
                                                                    │
                                                                    ▼
                                  Kokoro-82M (bm_daniel) ──▶ speakers
```

## Setup

```powershell
.\scripts\setup.ps1
```

Installs Python 3.12, Ollama, ffmpeg and the VC++ runtime via winget, creates
the virtual environment, pulls the language models, and downloads the wake word,
VAD, speech-recognition and speech-synthesis models (~1.5 GB total).

## Running

```powershell
.\run_jarvis.ps1
```

| Flag | Effect |
|---|---|
| *(none)* | Desktop window with the reactor interface |
| `--no-ui` | Headless in the terminal |
| `--quiet` | Skip the spoken greeting |
| `--ask "..."` | Ask one question and exit |
| `--say "..."` | Speak one line and exit |

Say **"Hey JARVIS"**, wait for the chime, then speak. `Ctrl+Alt+J` wakes him
from any application; the space bar does the same when the window has focus.
Talking over him cuts him off.

## What he can do

37 tools across five areas:

- **System** — open and close apps, volume, brightness, screenshots, clipboard,
  window focus, CPU/memory/disk/battery, lock, sleep, shutdown, PowerShell.
- **Web** — DuckDuckGo search, fetch and summarize pages, weather (Open-Meteo),
  news. All key-less.
- **Files** — search by name, read, summarize, open, list recent.
- **Media** — play/pause/skip/previous/now-playing for Spotify or any player,
  through the Windows media transport controls. No account linking.
- **Memory** — remembers facts about you permanently, in SQLite, with both
  keyword (FTS5) and semantic (ONNX embeddings) recall.

Shutdown, sleep and arbitrary shell commands require a spoken confirmation.

## Configuration

Everything lives in [`config.yaml`](config.yaml). Put overrides in
`config.local.yaml` (gitignored) so they survive updates.

The **voice is deliberately pinned** to Kokoro's `bm_daniel` at speed 1.0 in
`jarvis/voice/tts.py`. It was chosen by ear against every other British male
voice, against blends of them, and against six post-processing treatments —
all of which lost to the untreated read. Config cannot override it; it can only
agree with it.

`jarvis/voice/jarvis_chain.py` still holds the full DSP chain (pitch/formant
shifting, EQ, compression, doubling, convolution reverb) and is disabled by
default, because processing made the voice sound *more* synthetic, not less.

## Performance notes

These were measured, not assumed, and each one is a large effect:

**Enable the integrated GPU.** Ollama detects the 780M and silently drops it
because it is integrated. `OLLAMA_IGPU_ENABLE=1` moves inference to 100% GPU.
`run_jarvis.ps1` sets it.

**Keep the prompt prefix stable.** An unchanged prefix replays from Ollama's KV
cache at ~3,000 tokens/second; a changed one is re-evaluated at ~90. This is why
the tool set is fixed rather than routed per query, and why the system prompt
contains no clock.

| Tool strategy | Latency/turn | Tools called |
|---|---|---|
| all 37, stable | 1.9s | 0/5 — too many options to choose from |
| 10, re-routed each turn | 8.6s | 5/5, but cache missed every turn |
| **22, stable** | **2.3s** | **5/5** |

**Demonstrate, do not instruct.** qwen2.5 acts on imperatives ("pause the
music") only 2 times in 10 — it says "Pausing the music" and calls nothing.
Adding a system-prompt rule made it *worse* (1/10). Two worked examples in the
message history took it to 8/10. The most common commands additionally bypass
the model entirely (`jarvis/brain/intents.py`), which is both faster and exact.

**Flush the cache on start.** Editing the persona or tool set while the model is
loaded corrupts Ollama's cached prefix, and it begins emitting raw `<tool_call>`
markup and unrelated words as speech. `Brain.warm()` unloads first.

## Layout

```
jarvis/
  main.py          entry point, owns the event loop
  config.py        config.yaml + config.local.yaml
  bus.py           async pub/sub; the UI renders off this
  audio/           mic · wake · vad · stt · player · listener
  voice/           tts (Kokoro) · jarvis_chain (DSP, off by default)
  brain/           llm · persona · memory · intents
  tools/           registry · router · system · web · files · media · memory
  ui/              pywebview window + the reactor interface
scripts/
  setup.ps1            one-shot install
  download_models.py   fetch all models (idempotent)
  voice_lab.py         render voice samples
  smoke_brain.py       end-to-end brain check
  diag_*.py            the diagnostics behind the notes above
```

## Adding a tool

One decorated function. The schema is derived from the type hints and docstring.

```python
@tool(category="system")
def set_wallpaper(path: str) -> str:
    """Change the desktop wallpaper.

    Args:
        path: Full path to the image.
    """
    ...
```

Add its name to `CORE` in `jarvis/tools/router.py` for it to be offered every
turn, or to `EXTRAS` with trigger words if it is rarely needed.

## Troubleshooting

**He does not respond to the wake word.** Lower `wake.threshold` in
`config.yaml` (default 0.5). Check the microphone with
`python -c "from jarvis.audio.mic import list_input_devices; print(list_input_devices())"`
and set `audio.input_device`.

**Replies take 30 seconds.** The first question after starting pays for the
prompt evaluation. If every turn is slow, the iGPU is probably not enabled —
check `ollama ps` says `100% GPU`, not `100% CPU`.

**He speaks nonsense with `<tool_call>` in it.** Corrupted prefix cache. Restart;
`Brain.warm()` unloads the model first to prevent it.

**He claims to have done something but did not.** Add the command to
`jarvis/brain/intents.py` so it stops being the model's decision.
