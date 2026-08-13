"""Full acceptance test.

Exercises every capability end to end and checks the things that actually
matter: does he call the tool rather than invent an answer, is he fast, is he
brief, and does he sound like JARVIS rather than a chatbot.

    python scripts/acceptance.py
    python scripts/acceptance.py --quick    # skip the slow web tests
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.ERROR)

from jarvis.brain import intents  # noqa: E402
from jarvis.brain.llm import Brain  # noqa: E402
from jarvis.brain.memory import Memory  # noqa: E402
from jarvis.config import CONFIG  # noqa: E402
from jarvis.tools import memory_tools, registry  # noqa: E402
from jarvis.voice.tts import Voice, speakable  # noqa: E402

# Things a spoken butler should never say or contain.
BANNED_OPENERS = ("certainly", "sure!", "of course i can", "i'd be happy",
                  "great question", "absolutely!")
MARKDOWN = re.compile(r"(\*\*|^\s*[-*]\s|^#{1,6}\s|```)", re.M)

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"
results: list[tuple[str, str, str]] = []


def record(name: str, status: str, detail: str = "") -> None:
    results.append((name, status, detail))
    tag = {"PASS": "  ok ", "FAIL": " FAIL", "WARN": " warn"}[status]
    print(f"{tag}  {name}" + (f"   {detail}" if detail else ""))


# ══════════════════════════════════════════════════════════════════
#  Unit-level checks (no model needed)
# ══════════════════════════════════════════════════════════════════
def test_intents() -> None:
    print("\n[1] intent shortcuts -- must never reach the model")
    cases = [
        ("pause the music", "pause_media"),
        ("Pause", "pause_media"),
        ("skip this track", "next_track"),
        ("next", "next_track"),
        ("resume", "resume_media"),
        ("previous track", "previous_track"),
        ("mute", "set_mute"),
        ("set volume to 40", "set_volume"),
        ("volume 75", "set_volume"),
        ("lock my screen", "lock_screen"),
        ("remember that I use Brave", "remember"),
        ("jarvis, pause the music", "pause_media"),
        # These must NOT shortcut -- they need the model.
        ("what is the weather", None),
        ("open spotify and play jazz", None),
        ("how much memory am I using", None),
    ]
    bad = []
    for text, expect in cases:
        got = intents.match(text)
        name = got[0] if got else None
        if name != expect:
            bad.append(f"{text!r}->{name} (want {expect})")
    record("intent matching", PASS if not bad else FAIL,
           "" if not bad else "; ".join(bad[:3]))


def test_sanitizer() -> None:
    print("\n[2] speech sanitiser")
    checks = [
        ("100% \u2014 done", "percent", True),
        ("see **bold** text", "**", False),
        ("go to https://example.com/x", "http", False),
        ("open C:\\Users\\nicho\\f.txt", "C:\\", False),
        ("- bullet one", "- bullet", False),
    ]
    bad = []
    for raw, needle, should_contain in checks:
        out = speakable(raw)
        if (needle in out) != should_contain:
            bad.append(f"{raw!r}->{out!r}")
    record("markdown/url/path stripped", PASS if not bad else FAIL,
           "; ".join(bad[:2]))


def test_tools_registered() -> None:
    print("\n[3] tool registry")
    n = registry.load_all()
    cats = {t.category for t in registry.REGISTRY.values()}
    record("tools registered", PASS if n >= 30 else FAIL, f"{n} tools")
    record("all categories present",
           PASS if cats >= {"system", "web", "files", "media", "memory"} else FAIL,
           ", ".join(sorted(cats)))
    destructive = [n for n, t in registry.REGISTRY.items() if t.destructive]
    record("destructive tools gated",
           PASS if {"shutdown_computer", "run_command"} <= set(destructive) else FAIL,
           ", ".join(sorted(destructive)))


async def test_direct_tools() -> None:
    print("\n[4] tools execute for real")
    for name, args, expect in [
        ("get_battery", {}, "percent"),
        ("get_system_stats", {}, "CPU"),
        ("get_time", {}, ":"),
        ("get_volume", {}, "percent"),
        ("now_playing", {}, ""),
        ("find_files", {"name": "config", "limit": 3}, ""),
    ]:
        out = await registry.execute(name, args)
        ok = out and not out.startswith("Error") and expect in out
        record(f"{name}", PASS if ok else WARN, out[:66].replace("\n", " "))


def test_voice() -> None:
    print("\n[5] voice")
    v = Voice(CONFIG)
    record("backend is kokoro", PASS if v.backend == "kokoro" else FAIL, v.backend)
    record("voice locked to bm_daniel",
           PASS if v.voice == "bm_daniel" else FAIL, v.voice)

    line = "Battery is at sixty-one percent, sir. Holding up admirably."
    t0 = time.perf_counter()
    audio, sr = v.say(line)
    dt = time.perf_counter() - t0
    dur = len(audio) / sr
    ratio = dur / dt if dt else 0
    record("synthesis faster than realtime",
           PASS if ratio > 1.5 else WARN, f"{ratio:.1f}x ({dt*1000:.0f}ms)")


# ══════════════════════════════════════════════════════════════════
#  Conversation
# ══════════════════════════════════════════════════════════════════
async def test_conversation(quick: bool) -> None:
    print("\n[6] conversation -- tool discipline, latency, character")
    memory = Memory(CONFIG)
    memory_tools.bind(memory)
    brain = Brain(CONFIG, memory)

    t0 = time.perf_counter()
    await brain.warm()
    record("warm-up", PASS, f"{time.perf_counter()-t0:.1f}s")

    turns = [
        ("What is my battery at?", "get_battery", True),
        ("How much memory am I using?", "get_system_stats", True),
        ("What time is it?", "get_time", True),
        ("Remember that my favourite film is Iron Man", "remember", True),
        ("What is my favourite film?", None, True),
        ("Pause the music", "pause_media", True),
    ]
    if not quick:
        turns += [
            ("What is the weather in Boston?", "get_weather", True),
            ("Search the web for AMD Ryzen AI news", "web_search", True),
        ]

    times, tool_hits, wanted = [], 0, 0
    style_problems = []

    for text, expect_tool, _ in turns:
        t0 = time.perf_counter()
        said, tools = [], []
        async for ev in brain.respond(text):
            if ev.type == "sentence":
                said.append(ev.text)
            elif ev.type == "tool_start":
                tools.append(ev.name)
        dt = time.perf_counter() - t0
        times.append(dt)

        spoken = speakable(" ".join(said))
        if expect_tool:
            wanted += 1
            if expect_tool in tools:
                tool_hits += 1

        # Character checks.
        low = spoken.lower()
        if any(low.startswith(b) for b in BANNED_OPENERS):
            style_problems.append(f"opener: {spoken[:40]}")
        if MARKDOWN.search(spoken):
            style_problems.append(f"markdown: {spoken[:40]}")
        if len(spoken) > 320:
            style_problems.append(f"too long ({len(spoken)}ch)")

        status = PASS if (not expect_tool or expect_tool in tools) else FAIL
        record(f"{text[:38]:40s}", status,
               f"{dt:5.1f}s  {tools or 'none'}  \"{spoken[:52]}\"")

    mean = sum(times) / len(times)
    steady = sum(times[1:]) / max(len(times) - 1, 1)
    record("tool discipline", PASS if tool_hits == wanted else FAIL,
           f"{tool_hits}/{wanted} correct")
    record("latency (steady state)",
           PASS if steady < 6 else WARN, f"mean {mean:.1f}s, steady {steady:.1f}s")
    record("spoken style", PASS if not style_problems else WARN,
           "; ".join(style_problems[:2]))

    # Memory must survive a restart.
    count = memory.count()
    memory.close()
    reopened = Memory(CONFIG)
    record("memory persists across restart",
           PASS if reopened.count() >= count and count > 0 else FAIL,
           f"{reopened.count()} facts")
    reopened.close()


async def test_offline() -> None:
    """The core promise: he works with the network unplugged."""
    print("\n[7] offline capability")
    import ollama
    try:
        client = ollama.AsyncClient(host=CONFIG.get("llm.host"))
        listing = await client.list()
        local = [m.get("model", "") for m in listing["models"]]
        record("LLM is local", PASS if local else FAIL, ", ".join(local[:3]))
    except Exception as e:
        record("LLM is local", FAIL, str(e))

    from jarvis.config import MODELS_DIR
    for label, path in [
        ("wake word", MODELS_DIR / "openwakeword" / "hey_jarvis_v0.1.onnx"),
        ("vad", MODELS_DIR / "silero_vad.onnx"),
        ("whisper", MODELS_DIR / "whisper"),
        ("kokoro", MODELS_DIR / "kokoro" / "kokoro-v1.0.onnx"),
    ]:
        record(f"{label} on disk", PASS if path.exists() else FAIL)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()

    print("=" * 72)
    print(" J.A.R.V.I.S. acceptance")
    print("=" * 72)

    test_intents()
    test_sanitizer()
    test_tools_registered()
    await test_direct_tools()
    test_voice()
    await test_conversation(args.quick)
    await test_offline()

    passed = sum(1 for _, s, _ in results if s == PASS)
    warned = sum(1 for _, s, _ in results if s == WARN)
    failed = sum(1 for _, s, _ in results if s == FAIL)

    print("\n" + "=" * 72)
    print(f" {passed} passed, {warned} warnings, {failed} failed")
    if failed:
        print("\n FAILURES:")
        for name, status, detail in results:
            if status == FAIL:
                print(f"   - {name}: {detail}")
    print("=" * 72)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
