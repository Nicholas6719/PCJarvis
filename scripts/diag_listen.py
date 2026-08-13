"""Why does he stop listening after the wake word?

Drives the real listener with real speech: synthesizes a sentence, plays it out
of the speakers, and watches whether the microphone path picks it up. Prints the
live VAD probability and microphone level every frame during capture, so the
failure point is visible rather than inferred.

    python scripts/diag_listen.py            # loopback through the speakers
    python scripts/diag_listen.py --live     # you speak instead
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(name)-18s %(message)s",
                    datefmt="%H:%M:%S")

import numpy as np  # noqa: E402

from jarvis.audio.mic import Microphone  # noqa: E402
from jarvis.audio.player import Player  # noqa: E402
from jarvis.audio.stt import Transcriber  # noqa: E402
from jarvis.audio.vad import SileroVAD  # noqa: E402
from jarvis.bus import BUS  # noqa: E402
from jarvis.config import CONFIG  # noqa: E402
from jarvis.voice.tts import Voice  # noqa: E402

SENTENCE = ("What is the weather in Boston today and how much battery "
            "do I have left")


async def raw_capture_probe(mic: Microphone, seconds: float) -> None:
    """Watch the microphone directly: level and VAD probability per frame."""
    vad = SileroVAD(threshold=CONFIG.get("vad.threshold", 0.5))
    t0 = time.monotonic()
    frames = 0
    speech_frames = 0
    peak_level = 0.0
    peak_prob = 0.0

    async for frame in mic.frames():
        p = vad.probability(frame)
        rms = float(np.sqrt(np.mean(frame ** 2)))
        peak_level = max(peak_level, rms)
        peak_prob = max(peak_prob, p)
        frames += 1
        if p >= vad.threshold:
            speech_frames += 1
        bar = "#" * int(min(rms * 220, 34))
        print(f"   t={time.monotonic()-t0:4.1f}s  rms={rms:.4f} "
              f"vad={p:.3f} {'SPEECH' if p >= vad.threshold else '      '} {bar}")
        if time.monotonic() - t0 > seconds:
            break

    print(f"\n   frames={frames}  speech={speech_frames}  "
          f"peak_rms={peak_level:.4f}  peak_vad={peak_prob:.3f}")
    if peak_level < 0.004:
        print("   >> MICROPHONE IS ESSENTIALLY SILENT. Wrong input device, or "
              "muted at the OS level.")
    elif peak_prob < 0.5:
        print("   >> Audio is arriving but silero never calls it speech. "
              "Level too low, or it is not speech (loopback too quiet).")
    else:
        print("   >> Microphone and VAD are both working.")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true",
                    help="you speak, instead of playing through the speakers")
    ap.add_argument("--seconds", type=float, default=8.0)
    args = ap.parse_args()

    print("=" * 70)
    print(" listener diagnostic")
    print("=" * 70)

    BUS.bind_loop(asyncio.get_running_loop())
    BUS.on("*", lambda ev: print(f"   [bus] {ev.get('event')} "
                                 f"{ {k: v for k, v in ev.items() if k != 'event'} }"))

    mic = Microphone(
        sample_rate=CONFIG.get("audio.sample_rate", 16000),
        block_size=CONFIG.get("audio.block_size", 1280),
        device=CONFIG.get("audio.input_device"),
        preroll_ms=CONFIG.get("vad.preroll_ms", 300),
    )
    mic.start()

    print("\n[A] raw microphone + VAD")
    if args.live:
        print("    SPEAK NOW for %.0f seconds..." % args.seconds)
        await raw_capture_probe(mic, args.seconds)
    else:
        voice = Voice(CONFIG)
        player = Player(sample_rate=voice.sample_rate,
                        device=CONFIG.get("audio.output_device"))
        player.start()
        audio, sr = voice.say(SENTENCE)
        print(f"    playing {len(audio)/sr:.1f}s of speech through the speakers")
        player.play(audio, sr)
        await raw_capture_probe(mic, min(args.seconds, len(audio) / sr + 1.5))
        player.stop()

    # ── B: the full listener state machine ────────────────────────
    print("\n[B] full listener (wake -> settle -> VAD -> whisper)")
    stt = Transcriber(
        model=CONFIG.get("stt.model", "small.en"),
        compute_type=CONFIG.get("stt.compute_type", "int8"),
        beam_size=CONFIG.get("stt.beam_size", 1),
        cpu_threads=CONFIG.get("stt.cpu_threads", 6),
    )
    from jarvis.audio.listener import Listener

    listener = Listener(CONFIG, mic, stt)
    task = asyncio.create_task(listener.run())

    # Surface a listener crash instead of letting the task die in silence --
    # this is exactly how a broken capture loop hides.
    def watch(t: asyncio.Task) -> None:
        if t.cancelled():
            return
        if t.exception():
            print("\n   >> LISTENER TASK DIED:")
            import traceback
            traceback.print_exception(t.exception())
    task.add_done_callback(watch)

    got: list[str] = []
    BUS.on("listen.transcript", lambda ev: got.append(ev["text"]))

    await asyncio.sleep(0.4)
    print("    triggering wake manually...")
    listener.trigger()

    if not args.live:
        voice = Voice(CONFIG)
        player = Player(sample_rate=voice.sample_rate,
                        device=CONFIG.get("audio.output_device"))
        player.start()
        audio, sr = voice.say(SENTENCE)
        await asyncio.sleep(CONFIG.get("wake.settle_ms", 450) / 1000 + 0.15)
        player.play(audio, sr)
    else:
        print("    SPEAK NOW...")

    for _ in range(180):          # up to 18s
        if got:
            break
        await asyncio.sleep(0.1)

    task.cancel()
    mic.stop()

    print()
    if got:
        print(f"   RESULT: transcribed {got[0]!r}")
        print("   >> The post-wake capture path works.")
    else:
        print("   RESULT: nothing transcribed.")
        print("   >> Capture after wake is broken; see the VAD trace above.")
    return 0 if got else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
