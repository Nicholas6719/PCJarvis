"""Deterministic commands: everything that should never wait on a model.

Two separate reasons for this layer, and both were learned the hard way.

**Speed.** A tool call through the LLM costs two round trips and five to fifteen
seconds. "Set a timer for ten seconds" answered in fifteen is not a timer, it is
an insult. Matched here, it is instant.

**Truth.** Measured on qwen2.5, the model acts on imperatives only 2 times in 10
-- it says "Pausing the music" and calls nothing. And even when it does call a
tool it paraphrases the result, which is how "Battery: 100%. Plugged in and
charging" reached the user as "fully charged, but not currently being charged".
Returning the tool's own words removes both failure modes.

The rule for adding an intent: it must be unambiguous in plain English, and a
wrong match must be harmless. Anything requiring judgement belongs to the model.
A `None` return means exactly that -- fall through, no harm done.
"""
from __future__ import annotations

import logging
import re
from typing import Callable

log = logging.getLogger("jarvis.intents")


class Intent:
    __slots__ = ("pattern", "tool", "args", "reply", "guard")

    def __init__(self, pattern: str, tool: str,
                 args: Callable | None = None, reply: str = "",
                 guard: Callable | None = None):
        self.pattern = re.compile(pattern, re.I)
        self.tool = tool
        self.args = args or (lambda m: {})
        self.reply = reply          # empty means: speak the tool's own output
        self.guard = guard          # optional veto after the pattern matches


# ── argument builders ─────────────────────────────────────────────
def _volume_args(m: re.Match) -> dict:
    return {"level": max(0, min(100, int(m.group("level"))))}


def _remember_args(m: re.Match) -> dict:
    fact = m.group("fact").strip().rstrip(".")
    return {"fact": fact[0].upper() + fact[1:] if fact else fact,
            "category": "preference"}


def _timer_args(m: re.Match) -> dict:
    from ..tools.timers import parse_duration

    duration = m.group("dur").strip(" ,.")
    label = ""
    if "label" in m.groupdict() and m.group("label"):
        label = m.group("label").strip(" ,.")
    return {"duration": duration, "label": label} if parse_duration(duration) else {}


def _timer_guard(m: re.Match) -> bool:
    """Only claim the turn if the duration is actually parseable."""
    from ..tools.timers import parse_duration

    return bool(parse_duration(m.group("dur").strip(" ,.")))


def _component(name: str) -> Callable:
    return lambda m: {"component": name}


# ── the table. First match wins, so specific precedes general. ────
INTENTS: list[Intent] = [
    # ══ timers ══ instant, and the reason this layer exists
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:set|start|create|make|put on)\s+"
           r"(?:an?\s+)?(?:timer|countdown)\s+(?:for|of)?\s*(?P<dur>.+?)"
           r"(?:\s+for\s+(?:the\s+)?(?P<label>[\w\s]+?))?\s*[.!]?$",
           "set_timer", _timer_args, guard=_timer_guard),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:set|start|make)?\s*(?:an?\s+)?"
           r"(?P<dur>[\w\s.-]+?)\s+(?:timer|countdown)\s*[.!]?$",
           "set_timer", _timer_args, guard=_timer_guard),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:remind|wake|nudge)\s+me\s+in\s+"
           r"(?P<dur>.+?)\s*[.!]?$",
           "set_timer", _timer_args, guard=_timer_guard),
    Intent(r"^(?:jarvis[,\s]+)?(?:cancel|stop|clear)\s+(?:the\s+|my\s+)?"
           r"(?:timer|countdown)s?\s*[.!]?$", "cancel_timer"),
    Intent(r"^(?:jarvis[,\s]+)?(?:how\s+(?:long|much\s+time)\s+(?:is\s+)?"
           r"(?:left|remaining)|what\s+timers).*$", "list_timers"),

    # ══ system readings ══ answered from the tool, never paraphrased
    Intent(r"^(?:jarvis[,\s]+)?(?:what(?:'s| is)\s+)?(?:my\s+|the\s+)?"
           r"(?:cpu|processor)(?:\s+(?:usage|load|at|doing))?\s*[.?!]?$",
           "get_system_stats", _component("cpu")),
    Intent(r"^(?:jarvis[,\s]+)?(?:how\s+much\s+|what(?:'s| is)\s+(?:my\s+)?)"
           r"(?:ram|memory)(?:\s+(?:am\s+i\s+)?(?:usage|using|used|at|free))?"
           r"\s*[.?!]?$", "get_system_stats", _component("memory")),
    Intent(r"^(?:jarvis[,\s]+)?(?:how\s+much\s+|what(?:'s| is)\s+my\s+)?"
           r"(?:disk|storage|drive|hard\s+drive)(?:\s+space)?"
           r"(?:\s+do\s+i\s+have)?(?:\s+is)?(?:\s+(?:left|free|available))?"
           r"\s*[.?!]?$", "get_system_stats", _component("disk")),
    Intent(r"^(?:jarvis[,\s]+)?(?:what(?:'s| is)\s+)?(?:my\s+|the\s+)?"
           r"battery(?:\s+(?:level|percentage|at|life|status))?\s*[.?!]?$",
           "get_battery"),
    Intent(r"^(?:jarvis[,\s]+)?(?:am\s+i\s+)?(?:charging|plugged\s+in)"
           r"\s*[.?!]?$", "get_battery"),
    Intent(r"^(?:jarvis[,\s]+)?(?:what(?:'s| is)\s+)?(?:my\s+)?system\s+"
           r"(?:status|stats|info)\s*[.?!]?$",
           "get_system_stats", _component("all")),
    Intent(r"^(?:jarvis[,\s]+)?what\s+time\s+is\s+it\s*[.?!]?$", "get_time"),
    Intent(r"^(?:jarvis[,\s]+)?what(?:'s| is)\s+(?:today(?:'s)?\s+)?the\s+date"
           r"\s*[.?!]?$", "get_time"),

    # ══ media transport ══
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?pause(?:\s+(?:the\s+)?"
           r"(?:music|song|track|audio|video|playback|it))?\s*[.!]?$",
           "pause_media", reply="Paused."),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:resume|unpause|continue)"
           r"(?:\s+(?:the\s+)?(?:music|song|track|playback|it))?\s*[.!]?$",
           "resume_media", reply="Playing."),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:skip|next)"
           r"(?:\s+(?:this|the)?\s*(?:track|song|one))?\s*[.!]?$",
           "next_track", reply="Skipped."),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:previous|go back|last)"
           r"(?:\s+(?:track|song|one))?\s*[.!]?$",
           "previous_track", reply="Going back."),
    Intent(r"^(?:jarvis[,\s]+)?what(?:'s| is)\s+(?:currently\s+)?playing"
           r"\s*[.?!]?$", "now_playing"),

    # ══ audio ══
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:set |turn )?(?:the )?volume"
           r"(?:\s+(?:to|at))?\s+(?P<level>\d{1,3})\s*(?:percent)?\s*[.!]?$",
           "set_volume", _volume_args),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?mute\s*(?:the\s+)?"
           r"(?:audio|sound|volume)?\s*[.!]?$",
           "set_mute", lambda m: {"muted": True}, reply="Muted."),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?unmute\s*(?:the\s+)?"
           r"(?:audio|sound|volume)?\s*[.!]?$",
           "set_mute", lambda m: {"muted": False}, reply="Unmuted."),

    # ══ opening things ══
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:open|show|go to)\s+"
           r"(?:my\s+|the\s+)?(?P<folder>downloads|documents|desktop|pictures|"
           r"music|videos|screenshots)(?:\s+folder)?\s*[.!]?$",
           "open_folder", lambda m: {"name": m.group("folder")}),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?search\s+(?:on\s+)?youtube\s+"
           r"(?:for\s+)?(?P<q>.+?)\s*[.!?]?$",
           "open_youtube_search", lambda m: {"query": m.group("q").strip()}),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:get|give me)?\s*directions\s+"
           r"to\s+(?P<dest>.+?)\s*[.!?]?$",
           "get_directions", lambda m: {"destination": m.group("dest").strip()}),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:open|go to|pull up|take me to)"
           r"\s+(?P<site>youtube|github|gmail|google|reddit|twitter|amazon|"
           r"netflix|spotify\.com|wikipedia|linkedin|discord|twitch|ebay|espn|"
           r"chatgpt|claude|notion|figma|outlook)\s*[.!]?$",
           "open_website", lambda m: {"site": m.group("site")}),

    # ══ machine ══
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?lock\s+(?:the\s+|my\s+)?"
           r"(?:screen|computer|pc|laptop|workstation)\s*[.!]?$",
           "lock_screen", reply="Locking now."),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?take\s+a\s+screenshot\s*[.!]?$",
           "take_screenshot"),

    # ══ memory ══
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?remember(?:\s+that)?\s+"
           r"(?P<fact>.+)$", "remember", _remember_args),
]


def match(text: str) -> tuple[str, dict, str] | None:
    """Return (tool, arguments, canned_reply) for an unambiguous command.

    An empty canned_reply means the tool's own output is the answer, which is
    what keeps readings accurate -- the model never gets a chance to reword a
    measurement into something untrue.
    """
    stripped = text.strip()
    for intent in INTENTS:
        m = intent.pattern.match(stripped)
        if not m:
            continue
        if intent.guard and not intent.guard(m):
            continue        # matched the shape but not the substance
        try:
            args = intent.args(m)
        except Exception:
            log.exception("intent arguments failed for %r", stripped)
            return None
        log.info("intent: %r -> %s(%s)", stripped, intent.tool, args)
        return intent.tool, args, intent.reply
    return None
