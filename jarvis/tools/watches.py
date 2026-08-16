"""Asking him to keep an eye on something and report back.

These are the only tools that do not finish when the sentence does. Everything
else answers now; these answer later, possibly after a restart, possibly hours
later. That is the whole point of them.

The tools are split by kind rather than taking a "condition" string, because a
7B asked to fill in a free-form condition field invents plausible nonsense --
"when the build finishes" as a literal string it expects something to
understand. A tool per kind means the only thing it has to get right is which
one to call.
"""
from __future__ import annotations

import logging

from .. import standing
from .registry import tool

log = logging.getLogger("jarvis.tools.watches")


@tool(category="watches")
def watch_for_process(name: str) -> str:
    """Tell him when a running program finishes.

    Use for "tell me when the build is done", "let me know when the render
    finishes", "tell me when Handbrake is finished".

    Args:
        name: The program, as you would see it in Task Manager -- "chrome",
            "handbrake", "python". Partial names are fine.
    """
    clean = (name or "").strip().strip(".!?")
    if not clean:
        return "Which program should I watch?"
    standing.add("process", target=clean,
                 description=f"when {clean} finishes")
    return f"I'll tell you when {clean} finishes."


@tool(category="watches")
def watch_for_battery(percent: int, direction: str = "at") -> str:
    """Tell him when the battery reaches a level.

    Use for "tell me when it's charged", "let me know at 80%", "tell me when
    the battery drops below 20".

    Args:
        percent: The level to watch for.
        direction: "at" for charging up to it, "below" for falling to it.
    """
    level = max(1, min(100, int(percent)))
    below = str(direction).lower().startswith("b")
    standing.add("battery", level=level,
                 direction="below" if below else "at",
                 description=(f"when the battery drops below {level}%" if below
                              else f"when the battery reaches {level}%"))
    if below:
        return f"I'll tell you when the battery drops below {level}%."
    return f"I'll tell you when the battery reaches {level}%."


@tool(category="watches")
def watch_for_download(name: str = "") -> str:
    """Tell him when a download finishes.

    Use for "tell me when that download is done".

    Args:
        name: Part of the filename, if you know it. Leave empty for whichever
            download finishes next.
    """
    clean = (name or "").strip()
    standing.add("download", target=clean,
                 description=(f"when {clean} finishes downloading" if clean
                              else "when the next download finishes"))
    if clean:
        return f"I'll tell you when {clean} finishes downloading."
    return "I'll tell you when the download finishes."


@tool(category="watches")
def list_watches() -> str:
    """List the things he is currently keeping an eye on."""
    watches = standing.all_watches()
    if not watches:
        return "I am not watching for anything at the moment."
    parts = [w.get("description") or w.get("kind", "something")
             for w in watches]
    if len(parts) == 1:
        return f"I am watching for {parts[0]}."
    return "I am watching for " + ", and ".join(parts) + "."


@tool(category="watches")
def cancel_watch(which: str = "") -> str:
    """Stop watching for something.

    Args:
        which: Part of what you asked him to watch. Leave empty to stop
            watching for everything.
    """
    dropped = standing.cancel(which)
    if not dropped:
        return "I was not watching for that."
    if len(dropped) == 1:
        return f"No longer watching {dropped[0].get('description', 'that')}."
    return f"Stopped watching {len(dropped)} things."
