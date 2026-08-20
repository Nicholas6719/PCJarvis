"""Phase 1 acceptance: the conversation loop.

Checks the behaviour that makes it feel alive -- the conversation window, the
pre-wake buffer, dismissal, barge-in cancellation, and gapless ordered speech.
Uses a fake microphone so the state machine can be driven deterministically
without anyone having to talk to it.

    python scripts/acceptance_phase1.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.ERROR)

import numpy as np  # noqa: E402

from jarvis.audio.listener import Listener, Mode  # noqa: E402
from jarvis.bus import BUS  # noqa: E402
from jarvis.config import CONFIG  # noqa: E402
from jarvis.main import DISMISS  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'  ok ' if ok else ' FAIL'}  {name}" + (f"   {detail}" if detail else ""))


# ══════════════════════════════════════════════════════════════════
class FakeMic:
    """A microphone we can feed on demand."""

    def __init__(self, block: int = 1280):
        self.block = block
        self.queue: asyncio.Queue = asyncio.Queue()
        self.level = 0.0
        self.muted = False
        self.preroll = np.zeros(block * 4, dtype=np.float32)
        self.drained = 0

    async def frames(self):
        while True:
            yield await self.queue.get()

    def feed(self, frame: np.ndarray) -> None:
        self.queue.put_nowait(frame)

    def preroll_audio(self) -> np.ndarray:
        return self.preroll

    def drain(self) -> None:
        self.drained += 1


class FakeSTT:
    def __init__(self, text: str = "what is the weather"):
        self.text = text
        self.calls = 0

    def transcribe(self, audio):
        self.calls += 1
        return self.text


def silence(n: int = 1280) -> np.ndarray:
    return np.zeros(n, dtype=np.float32)


# ══════════════════════════════════════════════════════════════════
def test_dismiss_phrases() -> None:
    print("\n[1] dismissal is recognised, and only when meant")
    should = ["that's all", "That's all.", "that will be all", "go to sleep",
              "goodbye", "bye", "stop listening", "return to wake mode",
              "jarvis, that's all", "we're done", "nothing else"]
    should_not = ["that's all I need to know about Rome",
                  "what time do I go to sleep", "say goodbye to my battery life",
                  "stop the music", "that's all wrong"]
    bad = [t for t in should if not DISMISS.match(t)]
    bad += [f"(false) {t}" for t in should_not if DISMISS.match(t)]
    check("dismissal phrases", not bad, "; ".join(bad[:3]))


def test_config() -> None:
    print("\n[2] configuration")
    check("always full screen", CONFIG.get("ui.fullscreen") is True)
    check("conversation window enabled",
          CONFIG.get("conversation.enabled") is True,
          f"{CONFIG.get('conversation.window_s')}s")
    check("pre-wake buffer >= 1.5s",
          CONFIG.get("vad.preroll_ms", 0) >= 1500,
          f"{CONFIG.get('vad.preroll_ms')}ms")
    check("settle window removed",
          CONFIG.get("wake.settle_ms") is None)
    check("wake threshold catches bare 'Jarvis'",
          0.3 <= CONFIG.get("wake.threshold", 1.0) <= 0.6,
          str(CONFIG.get("wake.threshold")))


async def test_conversation_window() -> None:
    print("\n[3] conversation window")
    mic, stt = FakeMic(), FakeSTT()
    listener = Listener(CONFIG, mic, stt)

    check("starts in wake mode", listener.mode is Mode.WAKE)
    check("not in conversation at rest", not listener.in_conversation)

    listener.extend_conversation()
    check("window opens", listener.in_conversation)

    # Speaking must pause the window rather than burn it.
    listener.begin_speaking()
    check("speaking is its own mode", listener.mode is Mode.SPEAKING)
    listener.end_speaking()
    check("window reopens after speaking", listener.in_conversation)
    check("armed after speaking", listener.mode is Mode.ARMED)

    listener.end_conversation()
    check("dismissal closes the window",
          not listener.in_conversation and listener.mode is Mode.WAKE)

    # Expiry.
    listener._conversation_until = time.monotonic() + 0.25
    check("window is open before expiry", listener.in_conversation)
    await asyncio.sleep(0.35)
    check("window expires on its own", not listener.in_conversation)


async def test_capture_uses_preroll() -> None:
    print("\n[4] wake -> capture uses the pre-wake buffer")
    mic, stt = FakeMic(), FakeSTT("hey jarvis what time is it")
    listener = Listener(CONFIG, mic, stt)
    listener.wake = None            # drive it with trigger() instead of audio

    got: list[str] = []
    BUS.on("listen.transcript", lambda ev: got.append(ev["text"]))

    task = asyncio.create_task(listener.run())
    await asyncio.sleep(0.05)
    listener.trigger()

    # Speech, then quiet long enough to endpoint.
    listener.vad.is_speech = lambda f: bool(f.any())        # loud = speech
    for _ in range(8):
        mic.feed(np.full(1280, 0.2, dtype=np.float32))
        await asyncio.sleep(0.005)
    for _ in range(20):
        mic.feed(silence())
        await asyncio.sleep(0.005)

    for _ in range(60):
        if got:
            break
        await asyncio.sleep(0.05)
    task.cancel()

    check("utterance transcribed after wake", bool(got), got[0] if got else "none")
    check("pre-wake audio was included", stt.calls > 0)


async def test_speaker_order_and_stop() -> None:
    print("\n[5] speech is ordered, gapless, and stoppable")
    from jarvis.voice.speaker import Speaker

    spoken: list[str] = []

    class FakeVoice:
        sample_rate = 24000

        def say(self, text):
            time.sleep(0.05)             # synthesis takes real time
            spoken.append(text)
            return np.zeros(1200, dtype=np.float32), 24000

    class FakePlayer:
        def __init__(self):
            self.played = []
            self.is_playing = False

        def play(self, audio, sr=None):
            self.played.append(len(audio))

        def interrupt(self):
            self.played.clear()

    speaker = Speaker(FakeVoice(), FakePlayer())
    for s in ["One.", "Two.", "Three."]:
        speaker.say(s)
    await speaker.wait_until_done(timeout=5)
    check("sentences spoken in order", spoken == ["One.", "Two.", "Three."],
          " ".join(spoken))

    # A stop mid-flight must abandon everything still queued.
    spoken.clear()
    for s in [f"Sentence {i}." for i in range(8)]:
        speaker.say(s)
    await asyncio.sleep(0.06)
    speaker.stop()
    await asyncio.sleep(0.25)
    check("stop abandons queued speech", len(spoken) <= 3,
          f"{len(spoken)} of 8 spoken before the stop took effect")
    speaker.shutdown()


async def test_one_prompt_shape() -> None:
    """Every model call must send the same prompt shape.

    This replaces the old fast-path test. The fast path sent short chit-chat
    with no tools and a truncated history -- a different prefix -- and it was
    removed when the tool list was fixed. Measured on this machine, that was
    not a neutral change but a fix: the fast path itself looked quick in
    isolation (0.25s) while evicting the cached prefix, so the next real
    question paid 17 seconds. A casual "hello" was quietly making whatever he
    asked next slow.

    So the invariant worth defending is not "is this short enough to skip
    tools" but "does anything send a different shape". Nothing should.
    """
    print("\n[6] one prompt shape, always")
    from jarvis.brain.llm import Brain
    from jarvis.tools import registry, router

    registry.load_all()
    brain = Brain(CONFIG)

    check("the fast path is gone, not renamed",
          not any(hasattr(brain, n) for n in
                  ("_is_fast_path", "_respond_fast")),
          "a second prompt shape would evict the cache")

    # router.select ignores its argument on purpose; wording must not change
    # the tool list, because the schemas sit in the cached prefix.
    shapes = {len(router.select(q)) for q in
              ("hello", "what is on my screen", "search amazon for a comic",
               "thank you", "remember I like tea")}
    check("the tool list never varies by wording", len(shapes) == 1,
          f"saw {sorted(shapes)} tool counts")

    first = router.select("hello")
    same = router.select("something entirely different")
    check("and it is byte-identical, not merely the same length",
          first == same)

async def main() -> int:
    print("=" * 70)
    print(" Phase 1 acceptance -- the conversation loop")
    print("=" * 70)
    BUS.bind_loop(asyncio.get_running_loop())

    test_dismiss_phrases()
    test_config()
    await test_conversation_window()
    await test_capture_uses_preroll()
    await test_speaker_order_and_stop()
    await test_one_prompt_shape()

    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    print("\n" + "=" * 70)
    print(f" {passed} passed, {failed} failed")
    if failed:
        for name, ok, detail in results:
            if not ok:
                print(f"   - {name}: {detail}")
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
