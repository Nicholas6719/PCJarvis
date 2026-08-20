"""Why does he call nothing more than half the time?

bench_tool_ceiling found no ceiling worth the name -- accuracy wandered
between 25% and 45% with no trend against list size -- but it found something
worse on the way past: at every size the commonest outcome was no tool call at
all. Eleven to fifteen of twenty. "Put on some jazz", "what song is this",
"make a note that I prefer tea", all answered with talk and no action.

The deterministic intents have been hiding this. Everything they catch is
instant and correct, so the failure only shows on phrasings they miss -- which
is precisely where a model is supposed to earn its place.

Two suspects, both mine, and this measures them separately:

  compact_schema  cut every tool description to its first line to shrink the
                  prompt. The lines it cut are the "Use for current events,
                  facts you are unsure of" guidance -- the matching signal.
  temperature     0.6 is a reasonable setting for prose and a strange one for
                  choosing a function.

Run:  .venv/Scripts/python.exe scripts/bench_tool_signal.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ.setdefault("OLLAMA_IGPU_ENABLE", "1")

import httpx  # noqa: E402

from jarvis.app import start_ollama  # noqa: E402
from jarvis.brain import persona  # noqa: E402
from jarvis.config import CONFIG  # noqa: E402
from jarvis.tools import registry, router  # noqa: E402

from bench_tool_ceiling import CASES  # noqa: E402


def run(label: str, tools: list[dict], temperature: float,
        sysmsg: str) -> tuple[float, int, float]:
    body_opts = {"temperature": temperature,
                 "num_ctx": CONFIG.get("llm.num_ctx", 8192),
                 "num_predict": CONFIG.get("llm.num_predict", 120)}

    def ask(prompt: str) -> tuple[str, float]:
        body = {"model": CONFIG.get("llm.model"), "stream": False,
                "options": body_opts,
                "messages": [{"role": "system", "content": sysmsg},
                             {"role": "user", "content": prompt}],
                "tools": tools}
        t0 = time.perf_counter()
        r = httpx.post("http://127.0.0.1:11434/api/chat", json=body,
                       timeout=600).json()
        calls = (r.get("message") or {}).get("tool_calls") or []
        return (calls[0]["function"]["name"] if calls else ""), \
               time.perf_counter() - t0

    ask("hello")                      # warm this exact prefix
    hits = silent = 0
    total = 0.0
    misses = []
    for utterance, expected in CASES:
        got, dt = ask(utterance)
        total += dt
        if got == expected:
            hits += 1
        else:
            if not got:
                silent += 1
            misses.append(f"{utterance!r} -> {got or '(nothing)'}")
    acc = hits / len(CASES) * 100
    print(f"\n  {label:38} {hits:2d}/{len(CASES)} = {acc:3.0f}%   "
          f"silent {silent:2d}   avg {total/len(CASES):.2f}s")
    for m in misses[:4]:
        print(f"        {m}")
    return acc, silent, total / len(CASES)


def main() -> int:
    if not start_ollama():
        print("could not start Ollama")
        return 1
    registry.load_all()
    sysmsg = persona.build_system_prompt(CONFIG)

    names = router.offered_names()
    compact = [registry.REGISTRY[n].compact_schema for n in names]
    full = [registry.REGISTRY[n].schema for n in names]

    print("=" * 76)
    print(f"  TOOL SIGNAL   same {len(names)} tools, same {len(CASES)} utterances")
    print("=" * 76)

    table = {}
    table["compact, temp 0.6 (today)"] = run(
        "compact schema, temperature 0.6", compact, 0.6, sysmsg)
    table["full, temp 0.6"] = run(
        "FULL schema,    temperature 0.6", full, 0.6, sysmsg)
    table["compact, temp 0.0"] = run(
        "compact schema, temperature 0.0", compact, 0.0, sysmsg)
    table["full, temp 0.0"] = run(
        "FULL schema,    temperature 0.0", full, 0.0, sysmsg)

    print("\n" + "=" * 76)
    print("  variant                        accuracy   silent   avg turn")
    for label, (acc, silent, avg) in table.items():
        print(f"  {label:30} {acc:5.0f}%   {silent:5d}    {avg:5.2f}s")
    print("=" * 76)
    return 0


if __name__ == "__main__":
    sys.exit(main())
