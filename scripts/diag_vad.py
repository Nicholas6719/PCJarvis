"""Find the input shape silero actually wants.

The VAD returns ~0.001 for everything, including loud speech, which means it is
being fed in a shape it does not understand. Silero v5 keeps a 64-sample context
window that must be prepended to each chunk; without it the graph runs happily
and returns constant nonsense rather than raising.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import onnxruntime as ort  # noqa: E402
from scipy import signal  # noqa: E402

from jarvis.config import CONFIG, MODELS_DIR  # noqa: E402
from jarvis.voice.tts import Voice  # noqa: E402


def probs(sess, sig, win, ctx):
    state = np.zeros((2, 1, 128), np.float32)
    context = np.zeros(ctx, np.float32)
    out_probs = []
    for i in range(0, len(sig) - win, win):
        chunk = sig[i:i + win]
        inp = np.concatenate([context, chunk]) if ctx else chunk
        try:
            o = sess.run(None, {"input": inp.reshape(1, -1).astype(np.float32),
                                "state": state,
                                "sr": np.array(16000, dtype=np.int64)})
        except Exception as e:
            return f"ERROR {str(e)[:80]}"
        out_probs.append(float(o[0].squeeze()))
        state = o[1]
        if ctx:
            context = chunk[-ctx:]
    return out_probs


def main() -> int:
    voice = Voice(CONFIG)
    raw, sr = voice.say_raw(
        "The quick brown fox jumps over the lazy dog, and keeps on running.")
    speech = signal.resample(raw, int(len(raw) * 16000 / sr)).astype(np.float32)
    speech = speech / (np.max(np.abs(speech)) + 1e-9) * 0.3
    silence = (np.random.default_rng(0).standard_normal(len(speech))
               * 0.0005).astype(np.float32)

    print(f"speech: {len(speech)/16000:.1f}s  "
          f"rms={np.sqrt(np.mean(speech**2)):.4f}")

    sess = ort.InferenceSession(str(MODELS_DIR / "silero_vad.onnx"),
                                providers=["CPUExecutionProvider"])
    print("inputs:", [i.name for i in sess.get_inputs()])
    print()

    for win, ctx in [(512, 0), (512, 64), (1536, 0), (1024, 64), (256, 64)]:
        sp = probs(sess, speech, win, ctx)
        if isinstance(sp, str):
            print(f"  win={win:5d} ctx={ctx:3d}  {sp}")
            continue
        si = probs(sess, silence, win, ctx)
        verdict = "WORKS" if max(sp) > 0.6 and max(si) < 0.5 else ""
        print(f"  win={win:5d} ctx={ctx:3d}  speech max={max(sp):.3f} "
              f"mean={np.mean(sp):.3f} | silence max={max(si):.3f}  {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
