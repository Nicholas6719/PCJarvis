"""Why does tool calling stop after the first turn?

Compares a fresh conversation per question against one accumulating history, to
establish whether the history format is poisoning later turns.

    python scripts/diag_tools.py
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

import ollama  # noqa: E402

from jarvis.brain import persona  # noqa: E402
from jarvis.config import CONFIG  # noqa: E402
from jarvis.tools import registry, router  # noqa: E402

QUESTIONS = [
    "How much memory am I using?",
    "Remember that I use Brave as my main browser",
    "What is my battery at?",
    "Pause the music",
]


async def run(client, label: str, keep_history: bool, tool_style: str) -> None:
    print(f"--- {label} ---")
    system = persona.build_system_prompt(CONFIG, "")
    history: list[dict] = []
    hits = 0

    for q in QUESTIONS:
        msgs = [{"role": "system", "content": system}]
        if keep_history:
            msgs += history
        msgs.append({"role": "user", "content": q})

        r = await client.chat(
            model=CONFIG.get("llm.model"), messages=msgs,
            tools=router.select(q), stream=False,
            options={"temperature": 0.6, "num_ctx": 8192}, keep_alive="30m",
        )
        m = r["message"]
        tc = m.get("tool_calls")
        names = [c["function"]["name"] for c in tc] if tc else None
        if tc:
            hits += 1
        print(f"  {'OK ' if tc else 'MISS'} {q[:40]:42s} {names}")

        if keep_history:
            history.append({"role": "user", "content": q})
            if tool_style == "full":
                # What the code does today: assistant message carrying the
                # tool_calls, then a tool-role result.
                history.append({"role": "assistant",
                                "content": m.get("content") or "",
                                "tool_calls": tc or []})
                if tc:
                    for c in tc:
                        history.append({"role": "tool",
                                        "name": c["function"]["name"],
                                        "content": "(result)"})
                    history.append({"role": "assistant",
                                    "content": "Answered from the tool."})
            else:
                # Flattened: only the final spoken answer is retained, with no
                # record of the mechanics of how it was obtained.
                history.append({"role": "assistant",
                                "content": m.get("content")
                                or "Answered from the tool."})

    print(f"  => {hits}/{len(QUESTIONS)} tool calls\n")


async def main() -> int:
    registry.load_all()
    client = ollama.AsyncClient(host=CONFIG.get("llm.host"))

    print(f"model: {CONFIG.get('llm.model')}")
    print(f"tools offered: {len(router.select('hello'))}")
    print(f"schema chars: {len(json.dumps(router.select('hello')))}\n")

    await run(client, "no history at all", False, "none")
    await run(client, "history WITH tool_calls (current code)", True, "full")
    await run(client, "history FLATTENED to answers only", True, "flat")
    return 0


if __name__ == "__main__" and "--stream" not in sys.argv:
    raise SystemExit(asyncio.run(main()))


async def stream_vs_not() -> None:
    """Does streaming suppress tool calls? Same history format either way."""
    registry.load_all()
    client = ollama.AsyncClient(host=CONFIG.get("llm.host"))
    system = persona.build_system_prompt(CONFIG, "")

    for streaming in (False, True):
        print(f"--- stream={streaming} ---")
        history: list[dict] = []
        hits = 0
        for q in QUESTIONS:
            msgs = [{"role": "system", "content": system}, *history,
                    {"role": "user", "content": q}]
            tc, content = None, ""
            if streaming:
                calls = []
                stream = await client.chat(
                    model=CONFIG.get("llm.model"), messages=msgs,
                    tools=router.select(q), stream=True,
                    options={"temperature": 0.6, "num_ctx": 8192},
                    keep_alive="30m")
                async for chunk in stream:
                    m = chunk.get("message") or {}
                    if m.get("tool_calls"):
                        calls.extend(m["tool_calls"])
                    content += m.get("content") or ""
                tc = calls or None
            else:
                r = await client.chat(
                    model=CONFIG.get("llm.model"), messages=msgs,
                    tools=router.select(q), stream=False,
                    options={"temperature": 0.6, "num_ctx": 8192},
                    keep_alive="30m")
                tc = r["message"].get("tool_calls")
                content = r["message"].get("content") or ""

            names = [c["function"]["name"] for c in tc] if tc else None
            if tc:
                hits += 1
            print(f"  {'OK ' if tc else 'MISS'} {q[:38]:40s} {names}")

            history.append({"role": "user", "content": q})
            history.append({"role": "assistant", "content": content,
                            "tool_calls": tc or []})
            if tc:
                for c in tc:
                    history.append({"role": "tool",
                                    "name": c["function"]["name"],
                                    "content": "(result)"})
                history.append({"role": "assistant",
                                "content": "Answered from the tool."})
        print(f"  => {hits}/{len(QUESTIONS)}\n")


if __name__ == "__main__" and "--stream" in sys.argv:
    raise SystemExit(asyncio.run(stream_vs_not()))
