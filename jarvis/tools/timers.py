"""Timers and short reminders.

These are the first thing that makes JARVIS speak without being spoken to, so
they own the proactive-speech path: a timer publishes to the bus and the main
loop decides when it is polite to say it. The tool never speaks directly, which
is what keeps announcements from landing on top of a reply in progress.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid

from ..bus import BUS
from .registry import tool

log = logging.getLogger("jarvis.tools.timers")

_timers: dict[str, dict] = {}

# A strong reference to every running timer task. See set_timer for why this
# is not optional.
_tasks: set[asyncio.Task] = set()

WORD_NUMBERS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
    "forty-five": 45, "fortyfive": 45, "sixty": 60, "ninety": 90,
    "half": 0.5, "couple": 2, "few": 3,
}
UNITS = {"second": 1, "seconds": 1, "sec": 1, "secs": 1,
         "minute": 60, "minutes": 60, "min": 60, "mins": 60,
         "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600}

# Words that can sit between a quantity and its unit while meaning nothing:
# "a couple OF minutes", "half AN hour".
_FILLER = {"of", "and"}
_ARTICLES = {"a", "an"}
_TOKEN = re.compile(r"[a-z]+(?:-[a-z]+)?|\d+(?:\.\d+)?", re.I)


def parse_duration(text: str) -> float | None:
    """Total seconds from spoken phrasing.

    Walks tokens rather than matching the whole phrase with a single pattern.
    One regex covering "5 minutes", "half an hour" and "an hour and 30 minutes"
    becomes unreadable and -- as the first attempt proved -- silently wrong in
    ways that are miserable to debug. Stepping back from each unit to find its
    quantity handles every case and can actually be followed.
    """
    tokens = [t.lower() for t in _TOKEN.findall(text)]
    total = 0.0

    for i, token in enumerate(tokens):
        if token not in UNITS:
            continue

        # Step back over filler to whatever quantifies this unit.
        j = i - 1
        while j >= 0 and tokens[j] in _FILLER:
            j -= 1

        value = 0.0
        if j >= 0:
            quantity = tokens[j]
            if quantity.replace(".", "").isdigit():
                value = float(quantity)
            elif quantity in WORD_NUMBERS:
                value = float(WORD_NUMBERS[quantity])

                # "half an hour": the article carries no quantity of its own,
                # so look one further back for the real multiplier.
                if quantity in _ARTICLES and j - 1 >= 0:
                    prior = tokens[j - 1]
                    if prior in WORD_NUMBERS and prior not in _ARTICLES:
                        value = float(WORD_NUMBERS[prior])

        if value:
            total += value * UNITS[token]

    return total or None


def _spoken(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)} seconds"
    if seconds < 3600:
        minutes = seconds / 60
        return (f"{int(minutes)} minute{'s' if int(minutes) != 1 else ''}"
                if minutes == int(minutes) else f"{minutes:.1f} minutes")
    hours = seconds / 3600
    return (f"{int(hours)} hour{'s' if int(hours) != 1 else ''}"
            if hours == int(hours) else f"{hours:.1f} hours")


async def _run_timer(timer_id: str, seconds: float, label: str) -> None:
    try:
        await asyncio.sleep(seconds)
    except asyncio.CancelledError:
        return
    if timer_id not in _timers:
        return
    _timers.pop(timer_id, None)
    message = (f"Your {label} timer is up." if label
               else f"That's {_spoken(seconds)}.")
    log.info("timer elapsed: %s", message)
    # The main loop decides when to say this -- never mid-reply, never muted.
    try:
        await BUS.emit("proactive", text=message, source="timer")
    except Exception:
        log.exception("timer fired but could not be announced")


@tool(category="timers")
async def set_timer(duration: str, label: str = "") -> str:
    """Set a timer. JARVIS will speak up when it elapses.

    Args:
        duration: How long, in plain words: "5 minutes", "two minutes",
            "half an hour", "30 seconds".
        label: What it is for, e.g. "pasta". Optional.
    """
    seconds = parse_duration(duration)
    if not seconds:
        return f"I couldn't work out how long {duration} is."
    if seconds > 24 * 3600:
        return "That's longer than a day. Use a reminder instead."

    timer_id = uuid.uuid4().hex[:8]
    _timers[timer_id] = {
        "id": timer_id, "label": label.strip(),
        "ends_at": time.time() + seconds, "seconds": seconds,
    }
    # This tool is async precisely so it runs on the main event loop. Sync tools
    # execute on a worker thread via asyncio.to_thread, where there is no running
    # loop, and the timer could never be scheduled.
    #
    # The reference MUST be kept. asyncio holds only a weak reference to a
    # running task, so a task nobody else refers to can be garbage collected
    # mid-await and simply vanish -- no error, no warning, the timer just
    # never goes off. In a short test run the collector never gets around to
    # it and everything looks fine; in a long-lived session under memory
    # pressure it disappears. This is the whole bug.
    task = asyncio.get_running_loop().create_task(
        _run_timer(timer_id, seconds, label.strip()))
    _timers[timer_id]["task"] = task
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)

    return (f"Timer set for {_spoken(seconds)}"
            + (f", for the {label.strip()}." if label.strip() else "."))


@tool(category="timers")
def list_timers() -> str:
    """List the timers currently running."""
    if not _timers:
        return "No timers running."
    now = time.time()
    parts = []
    for t in sorted(_timers.values(), key=lambda x: x["ends_at"]):
        left = max(0, t["ends_at"] - now)
        parts.append(f"{t['label'] or 'a timer'} with {_spoken(left)} left")
    return "You have " + ", and ".join(parts) + "."


def pending_descriptions() -> list[str]:
    """How much is left on each running timer, spoken.

    Used by jarvis.cautions, which mentions them before he shuts the machine
    down. It reads the same store list_timers does, but returns the pieces
    rather than a finished sentence, because a caution is worded differently
    from an answer.
    """
    now = time.time()
    return [_spoken(max(0, t["ends_at"] - now))
            for t in sorted(_timers.values(), key=lambda x: x["ends_at"])]


@tool(category="timers")
def cancel_timer(label: str = "") -> str:
    """Cancel a running timer.

    Args:
        label: Which one. Leave empty to cancel the only one, or all of them.
    """
    if not _timers:
        return "There are no timers to cancel."
    if label.strip():
        match = [t for t in _timers.values()
                 if label.lower().strip() in t["label"].lower()]
        if not match:
            return f"I don't have a timer for {label}."
        for t in match:
            entry = _timers.pop(t["id"], None)
            if entry and entry.get("task"):
                entry["task"].cancel()
        return f"Cancelled the {label} timer."
    count = len(_timers)
    for entry in _timers.values():
        if entry.get("task"):
            entry["task"].cancel()
    _timers.clear()
    return "Timer cancelled." if count == 1 else f"Cancelled all {count} timers."
