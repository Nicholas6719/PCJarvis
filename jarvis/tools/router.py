"""Tool selection.

This exists because of one measurement. On this machine Ollama re-evaluates the
whole prompt whenever its prefix changes, at ~90-150 tokens/second, but replays
an *unchanged* prefix from the KV cache at ~3,000 tokens/second. The tool
schemas sit in that prefix. So the question is not "how few tools can we send"
but "can we send the same ones every time".

Measured across a five-turn conversation on qwen2.5:7b, GPU-resident:

    all 37 tools, stable      1.9s/turn   but 0/5 tool calls -- too many
                                          options and it stops choosing at all
    10 tools, re-routed       8.6s/turn   5/5 tool calls, cache missed every
                                          turn because the set kept changing
    22 tools, stable          2.3s/turn   5/5 tool calls

So: a fixed core set, always sent, always in the same order. Rare tools are
admitted only on an unambiguous keyword, which costs one cache miss on the turn
that needs them -- a fair price for a tool used once a month.

The ordering is deliberately deterministic. A set that reorders between turns
is a different prefix, and a different prefix is a cache miss.
"""
from __future__ import annotations

import logging
import re

from .registry import REGISTRY

log = logging.getLogger("jarvis.tools.router")

# Everything the model is ever offered. One list, every turn, in this order,
# forever.
#
# The old router chose tools by wording: a fixed core plus extras admitted on
# a keyword. It was built to keep the prompt prefix stable and it did the
# opposite, because the schemas sit in that prefix ahead of the message.
# Measured on this machine: asking a different question costs 0.30s, and
# adding a single tool to the list costs 5.70s. Every turn whose wording
# pulled in a different extra paid seconds before he said a word.
#
# So the list never changes. The prefix is evaluated once during the
# background warm at boot and cached from then on, which makes the size of
# this list nearly free per turn -- it buys capability rather than latency.
# What it must never do is vary.
#
# Left out deliberately: things no one asks for by voice mid-conversation
# (create_protocol, delete_protocol, run_command), and things already handled
# instantly by a deterministic intent where the model adds nothing.
OFFERED: tuple[str, ...] = (
    # the machine
    "get_time", "get_battery", "get_system_stats", "set_volume", "open_app",
    "close_app", "take_screenshot", "lock_screen", "shutdown_computer",
    "about_yourself",
    # the web
    "web_search", "search_site", "get_weather", "get_news", "read_webpage",
    "show_images",
    # the browser in front of him
    "open_website", "search_in_browser", "get_directions", "current_page",
    # his files
    "find_files", "read_file", "open_file", "search_documents", "create_pdf",
    "export_conversation",
    # what is playing
    "play_music", "play_pause", "next_track", "now_playing",
    # copied text, and the screen
    "proofread_clipboard", "translate_clipboard", "summarise_clipboard",
    "read_screen",
    # memory, time, and what he asks it to hold
    "remember", "recall", "set_timer", "watch_for_process", "run_protocol",
)


def select(query: str = "", limit: int | None = None) -> list[dict]:
    """The tools to offer. Always the same ones, in the same order.

    query is ignored, and kept only so the callers and the diagnostics do not
    all have to change. Varying this by wording is precisely the bug.
    """
    schemas = [REGISTRY[n].compact_schema for n in OFFERED if n in REGISTRY]
    if limit and len(schemas) > limit:
        schemas = schemas[:limit]
    return schemas

def warm_prefix_query() -> str:
    """A query that selects exactly the core set, for warming the KV cache at
    startup so the first real question is not the one that pays for it."""
    return "hello"
