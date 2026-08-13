"""End-to-end check of the brain: latency, tool discipline, brevity, memory.

    python scripts/smoke_brain.py
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

from jarvis.brain.llm import Brain  # noqa: E402
from jarvis.brain.memory import Memory  # noqa: E402
from jarvis.config import CONFIG  # noqa: E402
from jarvis.tools import memory_tools, registry  # noqa: E402
from jarvis.voice.tts import speakable  # noqa: E402

QUESTIONS = [
    "How much memory am I using?",
    "Remember that I use Brave as my main browser",
    "What is my battery at?",
    "What do you know about my browser?",
    "Pause the music",
]


def check_sanitizer() -> None:
    messy = (
        "C drive has 932 GB\u2014plenty. See **docs** at https://x.com/a "
        "and C:\\Users\\nicho\\file.txt \u2014 100% \u201cdone\u201d."
    )
    print("sanitizer in :", messy)
    print("sanitizer out:", speakable(messy))
    print()


async def main() -> int:
    check_sanitizer()

    registry.load_all()
    memory = Memory(CONFIG)
    memory_tools.bind(memory)

    brain = Brain(CONFIG, memory)
    t0 = time.perf_counter()
    await brain.warm()
    print(f"warmup {time.perf_counter() - t0:.1f}s\n")

    times = []
    for q in QUESTIONS:
        t0 = time.perf_counter()
        said, tools = [], []
        async for ev in brain.respond(q):
            if ev.type == "sentence":
                said.append(ev.text)
            elif ev.type == "tool_start":
                tools.append(ev.name)
            elif ev.type == "error":
                said.append(f"[ERROR {ev.text}]")
        dt = time.perf_counter() - t0
        times.append(dt)

        spoken = speakable(" ".join(said))
        sentences = len([s for s in spoken.split(".") if s.strip()])
        print(f"[{dt:4.1f}s] {q}")
        print(f"   tools : {tools or 'NONE'}")
        print(f"   says  : {spoken}")
        print(f"   length: {sentences} sentence(s), {len(spoken)} chars")

    print(f"\nMEAN {sum(times)/len(times):.1f}s | "
          f"facts stored: {memory.count()}")
    memory.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
