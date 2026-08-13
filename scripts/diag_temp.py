"""Does temperature govern tool-calling reliability? Three runs per setting."""
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
     ("What is my battery at?", "get_battery"),
     ("What is the weather in Boston?", "get_weather")]

async def main():
    registry.load_all()
    c = ollama.AsyncClient(host=CONFIG.get("llm.host"))
    system = persona.build_system_prompt(CONFIG, "")
    for temp in (0.0, 0.2, 0.6):
        correct = total = 0
        detail = []
        for run in range(3):
            for q, expect in Q:
                r = await c.chat(model=CONFIG.get("llm.model"),
                                 messages=[{"role": "system", "content": system},
                                           {"role": "user", "content": q}],
                                 tools=router.select(q), stream=False,
                                 options={"temperature": temp, "num_ctx": 8192},
                                 keep_alive="30m")
                tc = r["message"].get("tool_calls")
                got = [x["function"]["name"] for x in tc] if tc else []
                total += 1
                if expect in got:
                    correct += 1
                elif run == 0:
                    detail.append(f"{expect}->{got or 'NONE'}")
        print(f"temp {temp}: {correct}/{total} correct" +
              (f"   misses: {', '.join(detail)}" if detail else ""))

raise SystemExit(asyncio.run(main()))
