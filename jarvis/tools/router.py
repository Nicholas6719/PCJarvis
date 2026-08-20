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

# Everything the model is ever offered. One list, every turn, in the same
# order, forever.
#
# The old router chose tools by wording: a fixed core plus extras admitted on
# a keyword. It was built to keep the prompt prefix stable and did the
# opposite, because the schemas sit in that prefix ahead of the message.
# Measured: a different question costs 0.30s, one extra tool costs 5.70s.
#
# It was then narrowed to 39 hand-picked names, on the stated grounds that
# there was "a measured ceiling before the model stops calling tools at
# all". That was never actually measured. When it finally was, against 20
# utterances deliberately phrased outside the deterministic intents:
#
#     39 tools   13/20 correct
#     60 tools   13/20
#     97 tools   14/20
#
# There is no ceiling in that range. Withholding 58 tools bought nothing and
# cost every phrasing the intents happen to miss. So he is offered all of
# them, and the list is derived rather than hand-maintained -- a name typed
# into a tuple silently disappears when the tool is renamed, and the whole
# point is that this list never quietly changes shape.
#
# Sorted, because the prefix must be byte-identical between runs, and dict
# order depends on import order.
#
# Cost of the wider list: 6,353 prompt tokens instead of 3,571, which is 55s
# of background warm at boot instead of 24s, and 0.13 GB more KV cache. Per
# turn it is free -- the prefix is cached and replays in 0.12s either way.
EXCLUDED: frozenset[str] = frozenset({
    # Arbitrary PowerShell. It has a confirmation gate, but a model that can
    # reach for it will eventually reach for it on a misheard sentence, and
    # nothing he asks for by voice needs it.
    "run_command",
})


def offered_names() -> list[str]:
    """Every tool he may call, in a stable order."""
    return sorted(n for n in REGISTRY if n not in EXCLUDED)


def select(query: str = "", limit: int | None = None) -> list[dict]:
    """The tools to offer. Always the same ones, in the same order.

    query is ignored, and kept only so callers and diagnostics need not all
    change. Varying this by wording is precisely the bug.
    """
    schemas = [REGISTRY[n].compact_schema for n in offered_names()]
    if limit and len(schemas) > limit:
        schemas = schemas[:limit]
    return schemas

def warm_prefix_query() -> str:
    """A query that selects exactly the core set, for warming the KV cache at
    startup so the first real question is not the one that pays for it."""
    return "hello"
