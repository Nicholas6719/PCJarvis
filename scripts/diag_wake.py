"""Does the pretrained hey_jarvis model respond to the bare word "Jarvis"?

Cheap to answer, and it decides whether a custom model needs training. Feeds
synthesized speech straight into the wake model, no speakers or microphone
involved, so the numbers are about the model rather than the room.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from scipy import signal  # noqa: E402

from jarvis.config import CONFIG, MODELS_DIR  # noqa: E402

PHRASES = [
    ("hey jarvis", True),
    ("Hey Jarvis.", True),
    ("Hey Jarvis, what time is it?", True),
    ("jarvis", True),
    ("Jarvis.", True),
    ("Jarvis, what time is it?", True),
    ("Jarvis, play some music.", True),
    # Should NOT fire -- false-positive probes.
    ("service", False),
    ("nervous", False),
    ("harvest the data", False),
    ("what time is it", False),
    ("the weather looks nice today", False),
]


def main() -> int:
    from kokoro_onnx import Kokoro
    from openwakeword.model import Model

    kokoro = Kokoro(str(MODELS_DIR / "kokoro" / "kokoro-v1.0.onnx"),
                    str(MODELS_DIR / "kokoro" / "voices-v1.0.bin"))
    wdir = MODELS_DIR / "openwakeword"

    voices = ["bm_daniel", "bm_george", "bf_emma", "am_adam"]
    print(f"{'phrase':34s} " + " ".join(f"{v[:9]:>9s}" for v in voices) + "   peak")
    print("-" * 86)

    rows = []
    for text, should_fire in PHRASES:
        scores = []
        for v in voices:
            try:
                samples, sr = kokoro.create(text, voice=v, speed=1.0, lang="en-us")
            except Exception:
                scores.append(0.0)
                continue
            audio = signal.resample(samples, int(len(samples) * 16000 / sr))
            pcm = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
            pcm = np.concatenate([np.zeros(8000, np.int16), pcm,
                                  np.zeros(8000, np.int16)])

            model = Model(
                wakeword_models=[str(wdir / "hey_jarvis_v0.1.onnx")],
                inference_framework="onnx",
                melspec_model_path=str(wdir / "melspectrogram.onnx"),
                embedding_model_path=str(wdir / "embedding_model.onnx"),
            )
            key = list(model.models.keys())[0]
            peak = 0.0
            for i in range(0, len(pcm) - 1280, 1280):
                peak = max(peak, float(model.predict(pcm[i:i + 1280]).get(key, 0)))
            scores.append(peak)

        rows.append((text, should_fire, scores))
        print(f"{text[:33]:34s} " + " ".join(f"{s:9.3f}" for s in scores)
              + f"   {max(scores):.3f}  {'<- want fire' if should_fire else ''}")

    print("\nthreshold sweep:")
    for th in (0.3, 0.5, 0.6, 0.75):
        hits = sum(1 for _, want, sc in rows if want and max(sc) >= th)
        wants = sum(1 for _, want, _ in rows if want)
        false = sum(1 for _, want, sc in rows if not want and max(sc) >= th)
        print(f"  {th:.2f}: fires {hits}/{wants} wanted, {false} false positives")

    bare = [max(sc) for t, w, sc in rows if w and not t.lower().startswith("hey")]
    print(f"\nbare 'Jarvis' peak across voices: {max(bare):.3f}")
    print("VERDICT:", "usable without training" if max(bare) > 0.5
          else "needs a custom model")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
