"""Voice tuning bench.

Renders sample lines through the chain so it can be judged by ear, which is the
only way this can be judged. Writes wav files to logs/voice/ and can play them.

    python scripts/voice_lab.py                 # render the standard lines
    python scripts/voice_lab.py --ab            # raw vs treated, for comparison
    python scripts/voice_lab.py --play          # play them as they render
    python scripts/voice_lab.py --say "text"    # one custom line
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

from jarvis.config import CONFIG  # noqa: E402
from jarvis.voice import jarvis_chain  # noqa: E402
from jarvis.voice.tts import IR_PATH, Voice  # noqa: E402

OUT = ROOT / "logs" / "voice"

# Lines chosen to exercise the range: greeting, report, dry wit, refusal,
# and a long one to expose artifacts in the pitch shifter.
LINES = {
    "greeting": "Good evening, Nicholas. All systems are online.",
    "report": "CPU is at twelve percent, memory at forty-one. "
              "Everything is well within tolerance.",
    "wit": "Of course. I'll have the diagnostics ready for afterwards.",
    "dry": "The battery remains at sixty-one percent, sir. "
           "It is holding up admirably under the scrutiny.",
    "concern": "You have been at this for nine hours. "
               "I feel obliged to mention it.",
    "long": "I have searched the web and found three relevant results. "
            "The first suggests the driver update was released on Tuesday, "
            "which would explain the behaviour you described earlier.",
}


def play(audio: np.ndarray, sr: int) -> None:
    import sounddevice as sd
    sd.play(audio, sr)
    sd.wait()


def render(voice: Voice, name: str, text: str, do_play: bool) -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    raw, sr = voice.say_raw(text)
    synth = time.perf_counter() - t0

    t0 = time.perf_counter()
    treated = jarvis_chain.apply_chain(
        raw, sr, CONFIG.section("voice_chain"), IR_PATH
    )
    chain = time.perf_counter() - t0

    sf.write(OUT / f"{name}.wav", treated, sr)
    duration = len(treated) / sr
    print(f"  {name:10s} {duration:5.2f}s audio | synth {synth*1000:5.0f}ms "
          f"| chain {chain*1000:4.0f}ms | {duration/(synth+chain):4.1f}x realtime")
    if do_play:
        play(treated, sr)


def render_ab(voice: Voice, name: str, text: str, do_play: bool) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    raw, sr = voice.say_raw(text)
    treated = jarvis_chain.apply_chain(raw, sr, CONFIG.section("voice_chain"), IR_PATH)

    # Level-match the raw take so the comparison is about character, not volume.
    raw_matched = jarvis_chain.limit(raw.copy(), CONFIG.get("voice_chain.limiter", {}))
    gap = np.zeros(int(0.5 * sr), dtype=np.float32)

    sf.write(OUT / f"ab_{name}.wav",
             np.concatenate([raw_matched, gap, treated]), sr)
    print(f"  ab_{name:10s} raw ... 0.5s gap ... treated")
    if do_play:
        play(np.concatenate([raw_matched, gap, treated]), sr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ab", action="store_true", help="raw vs treated")
    ap.add_argument("--play", action="store_true", help="play as rendered")
    ap.add_argument("--say", type=str, help="render one custom line")
    args = ap.parse_args()

    print("=" * 66)
    print(" JARVIS voice bench")
    print("=" * 66)

    voice = Voice(CONFIG)
    if voice.backend != "kokoro":
        print(f" !! backend is '{voice.backend}', not kokoro. "
              f"Run scripts/download_models.py")
    print(f" backend: {voice.backend} | voice: {voice.voice} | "
          f"speed: {voice.speed}")
    chain_cfg = CONFIG.section("voice_chain")
    print(f" chain: pitch {chain_cfg['pitch']['semitones']:+.1f}st, "
          f"formant {chain_cfg['pitch']['formant_scale']}, "
          f"room {chain_cfg['room']['mix']:.2f}, "
          f"double {chain_cfg['doubler']['mix']:.2f}")
    print()

    if args.say:
        render(voice, "custom", args.say, args.play)
    elif False:
        for v in ("bm_george", "bm_daniel", "bm_lewis", "bm_fable"):
            voice.reload_voice(voice=v)
            print(f" -- {v} --")
            render(voice, f"voice_{v}", LINES["greeting"], args.play)
            render(voice, f"voice_{v}_dry", LINES["dry"], args.play)
    elif args.ab:
        for name, text in LINES.items():
            render_ab(voice, name, text, args.play)
    else:
        for name, text in LINES.items():
            render(voice, name, text, args.play)

    print(f"\n wav files in {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
