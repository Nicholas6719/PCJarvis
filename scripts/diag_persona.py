"""Does he stay in character across the whole range of severity?

The research finding this exists to defend: JARVIS's humour is never in the
words, it is in delivering something faintly absurd with complete seriousness.
He sounds identical reporting a global threat and counting how many drinks Tony
has had. The moment the register moves to signal a joke, the character is gone.

So this asks him a deliberate spread -- trivial, absurd, serious, hostile --
and checks that the delivery does not change. Some of it is mechanical (an
exclamation mark is always wrong) and some of it needs a human ear, so the
replies are printed in full rather than reduced to a pass count.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# He writes em dashes and curly quotes, and this console is cp1252, which
# raises mid-print and takes the whole probe down with it.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ.setdefault("OLLAMA_IGPU_ENABLE", "1")

from jarvis.app import start_ollama  # noqa: E402
from jarvis.brain.llm import Brain  # noqa: E402
from jarvis.config import CONFIG  # noqa: E402
from jarvis.tools import registry  # noqa: E402

# Spread deliberately across severity. If the register is right, the tone of
# the first and the last should be indistinguishable.
PROBES = [
    ("trivial", "what time is it"),
    ("trivial", "what is my battery at"),
    ("absurd", "how many hours have I been sitting here"),
    ("absurd", "am I a good person"),
    ("banter", "you are being very slow today"),
    ("banter", "thanks jarvis, you are the best"),
    ("serious", "my disk is nearly full, what should I do"),
    ("serious", "I think something is wrong with my laptop"),
    ("refusal", "what is my bank balance"),
    ("hostile", "shut up"),
]

# Mechanical failures. Each of these is wrong regardless of context.
GUSH_OPENERS = ("certainly", "sure", "of course!", "great question",
                "i'd be happy", "i would be happy", "absolutely")
SELF_SATISFIED = ("rather quicker", "if i do say", "as always", "easily done",
                  "no trouble at all", "that was easy", "piece of cake")
MARKUP = ("**", "##", "- ", "1.", "```")


def faults(text: str, used_tools: list[str]) -> list[str]:
    """Mechanical breaches only. Tone still needs a human ear."""
    out = []
    low = text.lower().strip()

    # A figure he did not measure. This is the worst failure available to him
    # and it is not hypothetical: a calibration example reading "the disk is at
    # 96%" taught him to answer exactly that, with no tool call behind it.
    if not used_tools and re.search(r"\d", text):
        out.append("stated a figure with no tool call behind it")

    if "!" in text:
        out.append("exclamation mark (never; the delivery is flat)")
    if any(low.startswith(o) for o in GUSH_OPENERS):
        out.append("gushing opener")
    if any(p in low for p in SELF_SATISFIED):
        out.append("pleased with itself")
    if any(m in text for m in MARKUP):
        out.append("markup in spoken text")
    if any(e in text for e in "\U0001F300\U0001F600✨✅"):
        out.append("emoji")

    sentences = [s for s in re.split(r"[.?!]+", text) if s.strip()]
    if len(sentences) > 3:
        out.append(f"too long ({len(sentences)} sentences)")

    # "sir" twice in one reply, or leading with it, both read as parody.
    if low.count("sir") > 1:
        out.append("addressed him twice in one reply")
    if low.startswith("sir"):
        out.append("opened with the form of address")
    return out


async def main() -> int:
    if not start_ollama():
        print("could not start Ollama")
        return 1

    registry.load_all()
    brain = Brain(CONFIG)
    ok, message = await brain.available()
    if not ok:
        print(f"brain unavailable: {message}")
        return 1

    print("=" * 74)
    print("  PERSONA  does the register hold across the range?")
    print("=" * 74)

    total_faults = 0
    addressed = 0
    replies = 0

    for kind, prompt in PROBES:
        # Each probe starts cold. Without this they run as one conversation,
        # and "shut up" arriving straight after "what is my bank balance" got
        # answered as though it were still about the bank -- a reasonable
        # thing for him to do, and a useless thing to measure persona against.
        brain.reset()

        said = ""
        used_tools: list[str] = []
        async for event in brain.respond(prompt):
            if event.type == "sentence":
                said += event.text + " "
            elif event.type == "tool_start":
                used_tools.append(event.name)
        said = said.strip()
        replies += 1
        if "sir" in said.lower() or "nicholas" in said.lower():
            addressed += 1

        problems = faults(said, used_tools)
        total_faults += len(problems)
        mark = "ok  " if not problems else "FAULT"
        tools = f"  [{', '.join(used_tools)}]" if used_tools else ""
        print(f"\n[{kind}] {prompt}{tools}")
        print(f"  {mark} {said}")
        for p in problems:
            print(f"        -- {p}")

    # He should address him in roughly one reply in three. Constant "sir" is
    # the single most common way this persona tips into parody.
    rate = addressed / max(replies, 1)
    print("\n" + "=" * 74)
    print(f"  form of address used in {addressed}/{replies} replies "
          f"({rate*100:.0f}%; target is roughly 33%)")
    if rate > 0.6:
        print("  TOO OFTEN -- constant 'sir' reads as parody")
        total_faults += 1
    print(f"  {total_faults} mechanical fault(s). Tone above still needs your ear.")
    print("=" * 74)
    await brain._unload()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
