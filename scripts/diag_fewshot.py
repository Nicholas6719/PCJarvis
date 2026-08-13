"""Can few-shot examples fix the imperative-command blind spot?"""
from __future__ import annotations
import asyncio, logging, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
logging.basicConfig(level=logging.ERROR)
import ollama  # noqa: E402
from jarvis.brain import persona  # noqa: E402
from jarvis.config import CONFIG  # noqa: E402
from jarvis.tools import registry, router  # noqa: E402

Q = [("Remember that I use Brave as my main browser", "remember"),
     ("Pause the music", "pause_media"),
     ("Remember my dog is called Rufus", "remember"),
     ("Skip this track", "next_track"),
     ("What is my battery at?", "get_battery")]

# Two turns showing that an imperative is executed, not acknowledged.
FEWSHOT = [
    {"role": "user", "content": "Remember that I take my coffee black"},
    {"role": "assistant", "content": "",
     "tool_calls": [{"function": {"name": "remember",
                                  "arguments": {"fact": "Nicholas takes his coffee black",
                                                "category": "preference"}}}]},
    {"role": "tool", "name": "remember", "content": "Noted."},
    {"role": "assistant", "content": "Noted, sir."},
    {"role": "user", "content": "Pause the music"},
    {"role": "assistant", "content": "",
     "tool_calls": [{"function": {"name": "pause_media", "arguments": {}}}]},
    {"role": "tool", "name": "pause_media", "content": "Paused."},
    {"role": "assistant", "content": "Paused."},
]

EXTRA = """

## Commands are actions, not acknowledgements
An instruction is a request to ACT. "Remember X" means call remember. "Pause"
means call pause_media. "Skip" means call next_track. Replying "Noted" or
"Pausing now" WITHOUT calling the tool means nothing happened, and you have
misled him. Call the tool first, then confirm."""

async def trial(c, label, system, prefix):
    correct = total = 0; misses = []
    for run in range(2):
        for q, expect in Q:
            msgs = [{"role": "system", "content": system}, *prefix,
                    {"role": "user", "content": q}]
            r = await c.chat(model=CONFIG.get("llm.model"), messages=msgs,
                             tools=router.select(q), stream=False,
                             options={"temperature": 0.6, "num_ctx": 8192},
                             keep_alive="30m")
            tc = r["message"].get("tool_calls")
            got = [x["function"]["name"] for x in tc] if tc else []
            total += 1
            if expect in got: correct += 1
            elif run == 0: misses.append(f"{expect}->{got or 'NONE'}")
    print(f"{label:34s} {correct:2d}/{total}" +
          (f"   misses: {', '.join(misses)}" if misses else "   PERFECT"))

async def main():
    registry.load_all()
    c = ollama.AsyncClient(host=CONFIG.get("llm.host"))
    base = persona.build_system_prompt(CONFIG, "")
    await trial(c, "baseline", base, [])
    await trial(c, "+ prompt rule", base + EXTRA, [])
    await trial(c, "+ few-shot turns", base, FEWSHOT)
    await trial(c, "+ rule AND few-shot", base + EXTRA, FEWSHOT)

raise SystemExit(asyncio.run(main()))
