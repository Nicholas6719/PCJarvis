"""Deterministic intent shortcuts.

Measured on qwen2.5:7b: the model answers *questions* by calling tools quite
reliably, but treats *imperatives* as things to acknowledge rather than do. Told
to pause the music it says "Pausing the music" and calls nothing, which is the
worst possible failure -- it looks like it worked.

Few-shot priming takes that from 2/10 to 8/10 (see brain/llm.py), but 8/10 is
not good enough for "lock my screen". So the handful of commands that are
unambiguous in plain English are matched here and executed directly.

This is not a workaround grafted on; it is faster and more correct than asking
a language model to recognise "skip" -- no round trip, no sampling, no chance of
a polite fiction. Anything not matched here falls through to the model as usual.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("jarvis.intents")


class Intent:
    __slots__ = ("pattern", "tool", "args", "reply")

    def __init__(self, pattern: str, tool: str, args=None, reply: str = ""):
        self.pattern = re.compile(pattern, re.I)
        self.tool = tool
        self.args = args or (lambda m: {})
        self.reply = reply


def _volume_args(m: re.Match) -> dict:
    return {"level": max(0, min(100, int(m.group("level"))))}


def _remember_args(m: re.Match) -> dict:
    fact = m.group("fact").strip().rstrip(".")
    # "I use Brave" -> "Nicholas uses Brave" reads better on recall, but getting
    # the conjugation right is not worth the risk of mangling it. Store as told,
    # marked as his statement.
    return {"fact": fact[0].upper() + fact[1:] if fact else fact,
            "category": "preference"}


# Ordered: the first match wins, so put the specific before the general.
INTENTS: list[Intent] = [
    # ── media transport ───────────────────────────────────────────
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
    Intent(r"^(?:jarvis[,\s]+)?what(?:'s| is) (?:currently )?playing\s*[.?!]?$",
           "now_playing"),

    # ── audio ─────────────────────────────────────────────────────
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:set |turn )?(?:the )?volume"
           r"(?:\s+(?:to|at))?\s+(?P<level>\d{1,3})\s*(?:percent)?\s*[.!]?$",
           "set_volume", _volume_args),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?mute\s*(?:the\s+)?"
           r"(?:audio|sound|volume)?\s*[.!]?$",
           "set_mute", lambda m: {"muted": True}, reply="Muted."),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?unmute\s*(?:the\s+)?"
           r"(?:audio|sound|volume)?\s*[.!]?$",
           "set_mute", lambda m: {"muted": False}, reply="Unmuted."),

    # ── memory ────────────────────────────────────────────────────
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?remember(?:\s+that)?\s+(?P<fact>.+)$",
           "remember", _remember_args),

    # ── machine ───────────────────────────────────────────────────
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?lock\s+(?:the\s+|my\s+)?"
           r"(?:screen|computer|pc|laptop|workstation)\s*[.!]?$",
           "lock_screen", reply="Locking now."),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?take\s+a\s+screenshot\s*[.!]?$",
           "take_screenshot"),
]


def match(text: str) -> tuple[str, dict, str] | None:
    """Return (tool_name, arguments, canned_reply) if this is an unambiguous
    command, otherwise None to let the model handle it."""
    stripped = text.strip()
    for intent in INTENTS:
        m = intent.pattern.match(stripped)
        if m:
            try:
                args = intent.args(m)
            except Exception:
                log.exception("intent arg extraction failed for %r", stripped)
                return None
            log.info("intent shortcut: %r -> %s(%s)", stripped, intent.tool, args)
            return intent.tool, args, intent.reply
    return None
