"""How many tools can he be offered before he stops using them?

router.py withholds 58 of his 97 tools from the model, citing "a measured
ceiling before the model stops calling tools at all". That number decides what
he can do when a phrasing falls outside the deterministic intents, so it is
worth knowing whether the ceiling is real and where it actually sits, rather
than inherited from a claim in a comment.

The measurement is deliberately unkind to the hypothesis. Each utterance is
sent cold-ish against a fixed system prompt, the tool list grows between runs,
and the same utterances are used every time so the only variable is list
size. Accuracy is scored on the tool the model actually calls, not on whether
it produced pleasant text.

Run:  .venv/Scripts/python.exe scripts/bench_tool_ceiling.py
"""
from __future__ import annotations

import json
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

# One utterance per capability, phrased the way he would actually say it, and
# deliberately NOT the phrasing any deterministic intent already catches --
# these are exactly the cases that fall through to the model.
CASES: list[tuple[str, str]] = [
    ("what is the weather looking like", "get_weather"),
    ("look up who invented the arc reactor", "web_search"),
    ("find me a spider-man comic on amazon", "search_site"),
    ("grab a picture of the tesseract", "show_images"),
    ("what does this error on my screen say", "read_screen"),
    ("stick a note in a pdf about the meeting", "create_pdf"),
    ("dig up whatever I wrote about the router", "search_documents"),
    ("put on some jazz", "play_music"),
    ("what song is this", "now_playing"),
    ("make a note that I prefer tea", "remember"),
    ("what do you know about my browser", "recall"),
    ("give me five minutes", "set_timer"),
    ("keep an eye on handbrake for me", "watch_for_process"),
    ("tidy this sentence up for me", "proofread_clipboard"),
    ("put that into spanish", "translate_clipboard"),
    ("how are you holding up", "about_yourself"),
    ("shut the machine down", "shutdown_computer"),
    ("how full is the disk", "get_system_stats"),
    ("take me to the bbc website", "open_website"),
    ("how do I get to Boston from here", "get_directions"),
]


def system_prompt() -> str:
    return persona.build_system_prompt(CONFIG)


def tools_at(n: int) -> list[dict]:
    """The first n tools, offered list first so the core stays constant."""
    names = router.offered_names()
    rest = [x for x in registry.REGISTRY if x not in set(names)]
    chosen = (names + rest)[:n]
    return [registry.REGISTRY[c].compact_schema for c in chosen
            if c in registry.REGISTRY]


def ask(prompt: str, tools: list[dict], sysmsg: str) -> tuple[str, float]:
    body = {
        "model": CONFIG.get("llm.model"),
        "stream": False,
        "options": {"temperature": CONFIG.get("llm.temperature", 0.6),
                    "num_ctx": CONFIG.get("llm.num_ctx", 8192),
                    "num_predict": CONFIG.get("llm.num_predict", 120)},
        "messages": [{"role": "system", "content": sysmsg},
                     {"role": "user", "content": prompt}],
        "tools": tools,
    }
    t0 = time.perf_counter()
    r = httpx.post("http://127.0.0.1:11434/api/chat", json=body,
                   timeout=600).json()
    dt = time.perf_counter() - t0
    calls = (r.get("message") or {}).get("tool_calls") or []
    return (calls[0]["function"]["name"] if calls else ""), dt


def main() -> int:
    if not start_ollama():
        print("could not start Ollama")
        return 1
    registry.load_all()
    sysmsg = system_prompt()

    sizes = [39, 60, len(router.offered_names())]
    sizes = sorted({s for s in sizes if s <= len(registry.REGISTRY)})

    print("=" * 74)
    print(f"  TOOL CEILING   {len(CASES)} utterances, none of them intent-shaped")
    print("=" * 74)

    results = {}
    for n in sizes:
        tools = tools_at(n)
        prompt_chars = len(json.dumps(tools))
        hits = 0
        called_nothing = 0
        wrong = []
        total = 0.0
        # One warm-up so the prefix for THIS list size is cached, matching how
        # the real thing behaves after boot.
        ask("hello", tools, sysmsg)
        for utterance, expected in CASES:
            got, dt = ask(utterance, tools, sysmsg)
            total += dt
            if got == expected:
                hits += 1
            elif not got:
                called_nothing += 1
                wrong.append(f"{utterance!r} -> (nothing)")
            else:
                wrong.append(f"{utterance!r} -> {got}")
        acc = hits / len(CASES) * 100
        results[n] = (acc, called_nothing, total / len(CASES), prompt_chars)
        print(f"\n  {n:3d} tools  ({prompt_chars:6d} chars)   "
              f"{hits}/{len(CASES)} correct = {acc:.0f}%   "
              f"silent {called_nothing}   avg {total/len(CASES):.2f}s")
        for w in wrong[:6]:
            print(f"        {w}")

    print("\n" + "=" * 74)
    print("  n    accuracy   called nothing   avg turn   schema size")
    for n, (acc, silent, avg, chars) in sorted(results.items()):
        print(f"  {n:3d}    {acc:5.0f}%    {silent:6d}          "
              f"{avg:5.2f}s     {chars:6d}")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
