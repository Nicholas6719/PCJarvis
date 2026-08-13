"""Speech-to-text accuracy bench.

Synthesizes known phrases, degrades them to resemble a laptop microphone at
conversational distance (quiet, a little noise, band-limited), transcribes, and
scores word error rate. Then compares the settings that plausibly help, so
tuning is measured rather than guessed.

    python scripts/diag_stt.py            # the useful comparison
    python scripts/diag_stt.py --models   # also sweep model sizes (slow)
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from scipy import signal  # noqa: E402

from jarvis.config import CONFIG, MODELS_DIR  # noqa: E402

# What he actually says: commands, names, app names, numbers.
PHRASES = [
    "what is my battery at",
    "how much memory am I using",
    "what time is it",
    "pause the music",
    "open spotify",
    "search the web for ryzen ai news",
    "what is the weather in boston",
    "remember that I use brave as my main browser",
    "create a pdf of our conversation",
    "set the volume to forty percent",
    "take a screenshot and put it on my desktop",
    "what have I been working on lately",
]

# Domain vocabulary. Whisper is far more likely to produce these spellings if it
# has seen them in the prompt -- "brave" and "ryzen" in particular get mangled.
INITIAL_PROMPT = (
    "Jarvis, Nicholas, Windows, Spotify, Brave, VS Code, Ryzen, AMD, Lenovo, "
    "Yoga, PDF, CPU, RAM, GPU, screenshot, clipboard, playlist, battery."
)


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate by Levenshtein distance over words."""
    r = reference.lower().split()
    h = "".join(c for c in hypothesis.lower() if c.isalnum() or c.isspace()).split()
    if not r:
        return 0.0
    d = np.zeros((len(r) + 1, len(h) + 1), dtype=np.int32)
    d[:, 0] = np.arange(len(r) + 1)
    d[0, :] = np.arange(len(h) + 1)
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            cost = 0 if r[i - 1] == h[j - 1] else 1
            d[i, j] = min(d[i - 1, j] + 1, d[i, j - 1] + 1, d[i - 1, j - 1] + cost)
    return d[len(r), len(h)] / len(r)


def degrade(audio: np.ndarray, sr: int, level: float, noise: float) -> np.ndarray:
    """Make clean TTS resemble a laptop mic across a desk."""
    # Band-limit like a small mic capsule.
    sos = signal.butter(4, [180 / (sr / 2), 6800 / (sr / 2)],
                        btype="band", output="sos")
    out = signal.sosfilt(sos, audio).astype(np.float32)
    out = out / (np.max(np.abs(out)) + 1e-9) * level
    rng = np.random.default_rng(7)
    out = out + rng.standard_normal(len(out)).astype(np.float32) * noise
    return out.astype(np.float32)


def normalize(audio: np.ndarray, target_rms: float = 0.08) -> np.ndarray:
    """Bring a quiet capture up to the level Whisper was trained on."""
    rms = float(np.sqrt(np.mean(audio ** 2)))
    if rms < 1e-6:
        return audio
    gain = min(target_rms / rms, 30.0)   # cap so near-silence is not amplified
    return np.clip(audio * gain, -1.0, 1.0).astype(np.float32)


def build_clips() -> list[tuple[str, np.ndarray]]:
    from kokoro_onnx import Kokoro

    kokoro = Kokoro(str(MODELS_DIR / "kokoro" / "kokoro-v1.0.onnx"),
                    str(MODELS_DIR / "kokoro" / "voices-v1.0.bin"))
    clips = []
    # Two speakers, two difficulty levels: comfortable, and quiet-and-noisy.
    for i, phrase in enumerate(PHRASES):
        voice = ["am_adam", "af_heart"][i % 2]
        samples, sr = kokoro.create(phrase, voice=voice, speed=1.0, lang="en-us")
        audio = signal.resample(samples, int(len(samples) * 16000 / sr))
        hard = i % 3 == 0
        clips.append((phrase, degrade(audio.astype(np.float32), 16000,
                                      level=0.05 if hard else 0.16,
                                      noise=0.004 if hard else 0.0015)))
    return clips


def run(clips, model_name, beam, prompt, norm, compute="int8"):
    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device="cpu", compute_type=compute,
                         cpu_threads=CONFIG.get("stt.cpu_threads", 6),
                         download_root=str(MODELS_DIR / "whisper"))
    total, elapsed = 0.0, 0.0
    worst = ("", "", 0.0)
    for reference, audio in clips:
        clip = normalize(audio) if norm else audio
        t0 = time.perf_counter()
        segments, _ = model.transcribe(
            clip, beam_size=beam, language="en", vad_filter=False,
            condition_on_previous_text=False,
            initial_prompt=INITIAL_PROMPT if prompt else None,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        elapsed += time.perf_counter() - t0
        e = wer(reference, text)
        total += e
        if e > worst[2]:
            worst = (reference, text, e)
    return total / len(clips), elapsed / len(clips), worst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", action="store_true")
    args = ap.parse_args()

    print("=" * 78)
    print(" speech-to-text accuracy (lower WER is better)")
    print("=" * 78)
    clips = build_clips()
    print(f" {len(clips)} phrases, degraded to laptop-microphone conditions\n")

    configs = [
        ("small.en beam1                 (current)", "small.en", 1, False, False),
        ("small.en beam1 + normalise",              "small.en", 1, False, True),
        ("small.en beam1 + prompt",                 "small.en", 1, True,  False),
        ("small.en beam1 + both",                   "small.en", 1, True,  True),
        ("small.en beam5 + both",                   "small.en", 5, True,  True),
    ]
    if args.models:
        configs += [
            ("base.en  beam5 + both",  "base.en",   5, True, True),
            ("medium.en beam5 + both", "medium.en", 5, True, True),
        ]

    best = None
    for label, model, beam, prompt, norm in configs:
        try:
            score, secs, worst = run(clips, model, beam, prompt, norm)
        except Exception as e:
            print(f"  {label:42s}  FAILED {str(e)[:30]}")
            continue
        marker = ""
        if best is None or score < best[1]:
            best, marker = (label, score), "  <-- best"
        print(f"  {label:42s}  WER {score*100:5.1f}%   {secs*1000:5.0f}ms/clip{marker}")
        if score > 0.02:
            print(f"      worst: {worst[0]!r}")
            print(f"          -> {worst[1]!r}")

    print(f"\n best: {best[0]} at {best[1]*100:.1f}% WER")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
