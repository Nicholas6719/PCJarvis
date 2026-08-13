"""Instrument the real Brain and dump exactly what it sends to Ollama.

The isolated reproductions all call tools 4/4. The real Brain drops to 1/5, so
the difference is something Brain does that the reproduction does not. This
prints the actual message list per turn.

    python scripts/diag_brain.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.ERROR)

from jarvis.brain.llm import Brain  # noqa: E402
from jarvis.brain.memory import Memory  # noqa: E402
from jarvis.config import CONFIG  # noqa: E402
from jarvis.tools import memory_tools, registry  # noqa: E402

QUESTIONS = [
    "How much memory am I using?",
    "Remember that I use Brave as my main browser",
    "Pause the music",
]


def summarize(msgs: list[dict]) -> str:
    out = []
    for m in msgs:
        role = m.get("role")
        if role == "system":
            out.append(f"system({len(m.get('content',''))}ch)")
        elif role == "tool":
            out.append(f"TOOL[{m.get('name')}]={m.get('content','')[:28]!r}")
        elif role == "assistant":
            tc = m.get("tool_calls")
            names = [c.get("function", {}).get("name") for c in tc] if tc else []
            body = (m.get("content") or "")[:34]
            out.append(f"asst(calls={names},{body!r})")
        else:
            out.append(f"user({m.get('content','')[:34]!r})")
    return "\n     ".join(out)


async def main() -> int:
    registry.load_all()
    memory = Memory(CONFIG)
    memory_tools.bind(memory)
    brain = Brain(CONFIG, memory)
    await brain.warm()

    original = brain.client.chat
    captured: dict = {}

    async def spy(**kwargs):
        captured["messages"] = kwargs.get("messages", [])
        captured["tools"] = [t["function"]["name"]
                             for t in (kwargs.get("tools") or [])]
        return await original(**kwargs)

    brain.client.chat = spy

    for q in QUESTIONS:
        print(f"\n{'='*68}\nUSER: {q}\n{'='*68}")
        said, tools = [], []
        async for ev in brain.respond(q):
            if ev.type == "sentence":
                said.append(ev.text)
            elif ev.type == "tool_start":
                tools.append(ev.name)

        print(f"  sent {len(captured['tools'])} tools, "
              f"'remember' offered: {'remember' in captured['tools']}")
        print(f"  messages:\n     {summarize(captured['messages'])}")
        print(f"  --> tools called: {tools or 'NONE'}")
        print(f"  --> said: {' '.join(said)[:110]}")

    print(f"\nfacts stored: {memory.count()}")
    print(f"memory context_block: {memory.context_block()!r}")
    memory.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
