"""End-to-end tests against the real assembled JARVIS.

Every previous suite tested components. Components kept passing while the
assembled system failed -- the timer tool worked perfectly in isolation and
never once fired in the application. So this drives the actual Jarvis object,
through the actual turn handler, and asserts on the bus events and the side
effects a user would observe.

Audio devices are stubbed (there is nobody here to talk to it) but everything
above them is real: the intent layer, the brain, the tools, the speaker
pipeline, memory, and the conversation state machine.

    python scripts/e2e.py              # everything
    python scripts/e2e.py --fast       # skip anything needing the model
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)-20s %(message)s",
    datefmt="%H:%M:%S",
)
for noisy in ("faster_whisper", "comtypes", "httpx", "phonemizer", "numba"):
    logging.getLogger(noisy).setLevel(logging.ERROR)

import numpy as np  # noqa: E402

from jarvis.bus import BUS  # noqa: E402
from jarvis.config import CONFIG  # noqa: E402

results: list[tuple[str, bool, str]] = []
TIMINGS: list[tuple[str, float]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'  ok ' if ok else ' FAIL'}  {name}" + (f"   {detail}" if detail else ""))


# ══════════════════════════════════════════════════════════════════
#  Silent audio, so the real pipeline can run with nobody present
# ══════════════════════════════════════════════════════════════════
class SilentPlayer:
    def __init__(self, *a, **k):
        self.sample_rate = 24000
        self.envelope = 0.0
        self.is_playing = False
        self.played: list[int] = []

    def start(self):
        pass

    def stop(self):
        pass

    def play(self, audio, sample_rate=None):
        self.played.append(len(audio) if audio is not None else 0)

    def interrupt(self):
        pass

    async def play_and_wait(self, audio, sample_rate=None):
        self.play(audio, sample_rate)
        return True

    async def wait_done(self):
        return


class SilentMic:
    def __init__(self, *a, **k):
        self.level = 0.0
        self.muted = False
        self.dropped_frames = 0
        self._q: asyncio.Queue = asyncio.Queue()

    def start(self):
        pass

    def stop(self):
        pass

    def drain(self):
        pass

    def preroll_audio(self):
        return np.zeros(0, dtype=np.float32)

    async def frames(self):
        while True:
            yield await self._q.get()


class FastVoice:
    """Real text handling, no synthesis -- keeps the suite quick."""

    sample_rate = 24000

    def __init__(self, *a, **k):
        self.voice = "bm_daniel"
        self.backend = "kokoro"
        self.spoken: list[str] = []

    def say(self, text):
        from jarvis.voice.tts import speakable

        self.spoken.append(speakable(text))
        return np.zeros(240, dtype=np.float32), 24000

    def warm(self):
        pass


async def build_app(fast_voice: bool = True):
    """A real Jarvis with silent audio hardware."""
    import jarvis.main as jmain

    jmain.Microphone = SilentMic
    jmain.Player = SilentPlayer
    if fast_voice:
        jmain.Voice = FastVoice

    # Start Ollama the way the app does. This suite ends by calling
    # app.shutdown(), which now stops Ollama on purpose -- he asked for
    # the model to be released when JARVIS closes -- so a second run in
    # the same session used to fail at boot with the dependency the first
    # run had correctly torn down.
    import os

    from jarvis.app import start_ollama

    os.environ.setdefault("OLLAMA_IGPU_ENABLE", "1")
    if not start_ollama():
        raise RuntimeError("could not start Ollama")

    app = jmain.Jarvis(CONFIG)
    ok = await app.boot()
    if not ok:
        raise RuntimeError("boot failed even with Ollama running")

    # The proactive path is wired inside run(); this suite never calls run(),
    # so wire the same handlers here. Getting this wrong is exactly how the
    # timer looked fine in tests and did nothing in the app.
    BUS.on("proactive", lambda ev: asyncio.create_task(
        app._say_proactively(ev.get("text", ""))))
    return app


class Recorder:
    """Captures everything the interface would see."""

    def __init__(self):
        self.events: list[dict] = []
        BUS.on("*", self._on)

    def _on(self, ev):
        self.events.append(dict(ev))

    def clear(self):
        self.events.clear()

    def kinds(self):
        return [e.get("event") for e in self.events]

    def spoken(self):
        return " ".join(e.get("text", "") for e in self.events
                        if e.get("event") == "speaking")

    def tools(self):
        return [e.get("name") for e in self.events if e.get("event") == "tool"]


async def turn(app, rec: Recorder, text: str) -> tuple[str, list[str], float]:
    rec.clear()
    t0 = time.perf_counter()
    await app.handle(text)
    dt = time.perf_counter() - t0
    TIMINGS.append((text[:38], dt))
    return rec.spoken(), rec.tools(), dt


# ══════════════════════════════════════════════════════════════════
#  Tests
# ══════════════════════════════════════════════════════════════════
async def test_follow_up_still_calls_a_tool(app, rec) -> None:
    """The follow-up he answered most confidently, and most wrongly.

    Asked "what about tomorrow" after a weather report, he relabelled today
    as tomorrow: mostly sunny and mild, when the real forecast was heavy rain
    ten degrees cooler. No tool call, no hedging, entirely invented. This is
    the worst failure available to him, and it is silent -- nothing looks
    wrong unless you go and check the sky.
    """
    print("\n[follow-up] a second question needs a second look")

    said, tools, _ = await turn(app, rec, "what is the weather in Boston")
    check("first question calls the tool", "get_weather" in tools, str(tools))

    said, tools, _ = await turn(app, rec, "what about tomorrow")
    check("FOLLOW-UP CALLS IT AGAIN rather than inventing",
          "get_weather" in tools, f"{tools} | {said[:50]}")
    check("and the answer is about tomorrow",
          "tomorrow" in said.lower(), said[:60])


async def test_ambient_watch(app, rec) -> None:
    """Does an unprompted observation actually reach his voice?

    The unit tests cover restraint. This covers the wiring: an observation
    raised by the watcher has to travel the bus, survive the governing rules
    and come out of his mouth. That path is exactly where the timer failed
    three times, so it is worth asserting end to end rather than assuming.
    """
    print("\n[watch] an unprompted remark, end to end")

    from types import SimpleNamespace

    import psutil

    from jarvis.watch import Watcher

    spoken: list[str] = []
    BUS.on("proactive.spoken", lambda ev: spoken.append(ev.get("text", "")))

    watcher = Watcher(app.cfg, state_getter=lambda: app.state)

    real_battery = psutil.sensors_battery
    psutil.sensors_battery = lambda: SimpleNamespace(
        percent=9, power_plugged=False, secsleft=600)
    try:
        found = watcher._check_power()
        check("noticed the battery", bool(found),
              found[0].text if found else "nothing")
        check("treated it as urgent", bool(found) and found[0].critical)

        for obs in found:
            await BUS.emit("proactive", text=obs.text, source="watch")

        for _ in range(80):
            await asyncio.sleep(0.1)
            if spoken:
                break
        check("HE SAID IT UNPROMPTED", bool(spoken),
              spoken[0][:60] if spoken else "silence")
    finally:
        psutil.sensors_battery = real_battery


async def test_timer_end_to_end(app, rec) -> None:
    """The one that has failed three times. Does it actually go off?"""
    print("\n[timers] end to end, in the real application")

    said, tools, dt = await turn(app, rec, "set a timer for 3 seconds")
    check("timer accepted instantly", dt < 1.5, f"{dt:.2f}s")
    check("timer went through the intent layer", "set_timer" in tools, str(tools))
    check("confirmed the timer", "timer" in said.lower(), said[:60])

    fired: list[str] = []
    BUS.on("proactive.spoken", lambda ev: fired.append(ev.get("text", "")))

    print("      waiting 6s for it to elapse...")
    for _ in range(60):
        await asyncio.sleep(0.1)
        if fired:
            break

    check("TIMER ACTUALLY FIRED", bool(fired),
          fired[0] if fired else "nothing was spoken -- the timer never went off")

    said, tools, dt = await turn(app, rec, "set a timer for 20 minutes")
    said2, _, _ = await turn(app, rec, "how much time is left")
    check("running timer is reported", "minute" in said2.lower(), said2[:60])
    await turn(app, rec, "cancel the timer")


async def test_deterministic_speed(app, rec) -> None:
    print("\n[speed] deterministic commands must not touch the model")
    cases = [
        ("what is my battery", "get_battery"),
        ("what is my cpu", "get_system_stats"),
        ("how much memory am I using", "get_system_stats"),
        ("what time is it", "get_time"),
        ("volume 45", "set_volume"),
        ("pause the music", "pause_media"),
        ("10 second timer", "set_timer"),
    ]
    slow = []
    for text, expect in cases:
        said, tools, dt = await turn(app, rec, text)
        ok = expect in tools and dt < 2.0
        if not ok:
            slow.append(f"{text} {dt:.1f}s {tools}")
        print(f"      {dt:5.2f}s  {text:34s} -> {tools} | {said[:44]}")
    check("all deterministic commands under 2s", not slow, "; ".join(slow[:2]))
    await turn(app, rec, "cancel the timer")


async def test_accuracy(app, rec) -> None:
    print("\n[accuracy] readings must not be reworded into falsehoods")
    import psutil

    battery = psutil.sensors_battery()
    said, _, _ = await turn(app, rec, "what is my battery")
    if battery and battery.power_plugged:
        ok = "plugged in" in said.lower() or "charging" in said.lower()
        check("charging state is correct", ok, said[:70])
        check("does not claim it is NOT charging",
              "not currently" not in said.lower()
              and "not charging" not in said.lower(), said[:70])

    said, _, _ = await turn(app, rec, "what is my cpu")
    check("CPU question answers only about the CPU",
          "cpu" in said.lower() and "drive" not in said.lower(), said[:70])


async def test_conversation_state(app, rec) -> None:
    print("\n[state] the conversation window")
    listener = app.listener
    listener.end_conversation()
    check("starts closed", not listener.in_conversation)

    await turn(app, rec, "what is my battery")
    check("a turn opens the window", listener.in_conversation)

    listener.suspend_conversation()
    listener._conversation_until = time.monotonic() - 1
    check("suspended window survives its deadline", listener.in_conversation)
    listener.resume_conversation()
    check("resuming refreshes it", listener.in_conversation)

    rec.clear()
    await app.handle("that's all")
    check("dismissal closes it", not listener.in_conversation)
    check("dismissal minimises", "window.minimize" in rec.kinds(),
          str(rec.kinds()))
    check("dismissal is acknowledged", bool(rec.spoken()), rec.spoken()[:50])

    rec.clear()
    await app.handle("no, go to sleep")
    check("'no, go to sleep' dismisses rather than suspending the laptop",
          "window.minimize" in rec.kinds() and "sleep_computer" not in rec.tools(),
          f"tools={rec.tools()}")


async def test_dismiss_and_shutdown(app, rec) -> None:
    """Standing down, leaving, and suspending the laptop are three different
    things, and the HUD has to show which one happened."""
    print("\n[dismiss/shutdown] three outcomes, three HUD states")
    from jarvis.state import State

    for phrase in ["thank you, go to sleep",
                   "that's all, go to sleep",
                   "good work, go to sleep",
                   "return to wake mode",
                   "thank you, return to wake mode"]:
        app.listener.extend_conversation()
        app.state = State.IDLE
        rec.clear()
        await app.handle(phrase)
        kinds = rec.kinds()
        ok = ("window.minimize" in kinds
              and not app.listener.in_conversation
              and app.state is State.SLEEPING
              and "app.quit" not in kinds)
        check(f"dismiss: {phrase[:34]}", ok,
              f"state={app.state.value} minimise={'window.minimize' in kinds}")

    # The HUD must be told, not merely have it happen.
    states = [e.get("state") for e in rec.events if e.get("event") == "state"]
    check("HUD is told he is asleep", "sleeping" in states, str(states))

    # Shutting JARVIS down.
    app.listener.extend_conversation()
    app.state = State.IDLE
    rec.clear()
    await app.handle("jarvis, shut down")
    kinds = rec.kinds()
    states = [e.get("state") for e in rec.events if e.get("event") == "state"]
    check("shutdown asks the app to quit", "app.quit" in kinds, str(kinds[-4:]))
    check("HUD is told he is stopping", "stopping" in states, str(states))
    check("shutdown spoke a farewell", bool(rec.spoken()), rec.spoken()[:50])
    app.running = True   # the harness continues

    # And the laptop is a different matter entirely.
    app.listener.extend_conversation()
    app.state = State.IDLE
    rec.clear()
    await app.handle("shut down my computer")
    kinds = rec.kinds()
    check("'shut down my computer' does NOT quit JARVIS",
          "app.quit" not in kinds, str(kinds[-4:]))
    check("...and does not silently shut the machine down",
          "shutdown_computer" not in rec.tools()
          or "confirm" in kinds,
          f"tools={rec.tools()} kinds={kinds[-3:]}")
    app.running = True


async def test_documents(app, rec) -> None:
    print("\n[documents] the file must exist where he asked")
    from jarvis.tools.documents import OUTPUT_DIR

    # Where Windows itself says the Desktop is, read straight from the
    # registry. Deliberately NOT jarvis.folders: a test that resolves the
    # path with the same helper the code uses would pass no matter what
    # that helper returned. This assertion used to be Path.home() /
    # "Desktop", which is a folder that exists, accepts writes, and never
    # appears on screen -- so the test passed while every file vanished.
    import winreg

    _key = "\\".join(["Software", "Microsoft", "Windows",
                                   "CurrentVersion", "Explorer",
                                   "Shell Folders"])
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _key) as _k:
        desktop = Path(winreg.QueryValueEx(_k, "Desktop")[0])

    for stale in list(desktop.glob("e2e_*.pdf")) + list(OUTPUT_DIR.glob("e2e_*.pdf")):
        stale.unlink(missing_ok=True)

    from jarvis.tools import registry
    r = await registry.execute("export_conversation",
                               {"filename": "e2e_desktop", "location": "desktop"})
    made = desktop / "e2e_desktop.pdf"
    check("PDF lands on the visible Desktop when asked", made.exists(),
          f"{r[:60]} | expected in {desktop}")
    if made.exists():
        check("and it is a real PDF", made.read_bytes()[:5] == b"%PDF-")
        made.unlink(missing_ok=True)


async def test_model_path(app, rec) -> None:
    print("\n[model] questions that genuinely need it")
    for text in ["what is the weather in Boston",
                 "remember that I prefer tea over coffee",
                 "what do I prefer to drink"]:
        said, tools, dt = await turn(app, rec, text)
        print(f"      {dt:5.1f}s  {text:38s} -> {tools} | {said[:50]}")
        check(f"answered: {text[:30]}", bool(said.strip()), f"{dt:.1f}s")


async def test_resilience(app, rec) -> None:
    print("\n[resilience] hostile input must not take him down")
    for text in ["", "   ", "?", "a" * 400,
                 "set a timer for bananas",
                 "open a website called ???",
                 "!!!...???"]:
        try:
            await asyncio.wait_for(app.handle(text), timeout=45)
            check(f"survived {text[:22]!r}", True)
        except Exception as e:
            check(f"survived {text[:22]!r}", False, f"{type(e).__name__}: {e}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true")
    args = ap.parse_args()

    print("=" * 74)
    print(" END-TO-END -- the real Jarvis, silent audio")
    print("=" * 74)

    t0 = time.perf_counter()
    app = await build_app()
    boot = time.perf_counter() - t0
    check("boot", boot < 15, f"{boot:.1f}s")

    rec = Recorder()
    try:
        await test_timer_end_to_end(app, rec)
        await test_deterministic_speed(app, rec)
        await test_accuracy(app, rec)
        await test_conversation_state(app, rec)
        await test_follow_up_still_calls_a_tool(app, rec)
        await test_ambient_watch(app, rec)
        await test_dismiss_and_shutdown(app, rec)
        await test_documents(app, rec)
        if not args.fast:
            await test_model_path(app, rec)
        await test_resilience(app, rec)
    finally:
        await app.shutdown()

    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)

    print("\n" + "=" * 74)
    if TIMINGS:
        slowest = sorted(TIMINGS, key=lambda x: -x[1])[:5]
        print(" slowest turns:")
        for label, dt in slowest:
            print(f"   {dt:6.2f}s  {label}")
    print(f"\n {passed} passed, {failed} failed")
    for name, ok, detail in results:
        if not ok:
            print(f"   - {name}: {detail}")
    print("=" * 74)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
