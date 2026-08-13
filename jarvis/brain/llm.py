"""The brain: a local Ollama model with a full tool-calling loop.

Responses stream, and are cut into sentences as they arrive so that speech can
begin while the model is still writing. On a CPU-only machine that overlap is
the single largest perceived-latency win available -- it turns "wait eight
seconds, then hear a reply" into "hear a reply after one".
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import AsyncIterator

import ollama

from ..tools import registry, router
from . import intents, persona

log = logging.getLogger("jarvis.llm")

# Sentence boundary: terminator + whitespace, but not after an abbreviation
# or a decimal point.
_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
_ABBREV = re.compile(r"\b(Mr|Mrs|Ms|Dr|Prof|St|vs|etc|e\.g|i\.e|approx)\.$", re.I)


# Two worked examples, prepended to every conversation.
#
# Without these, qwen2.5 acts on imperatives only 2 times in 10 -- it replies
# "Pausing the music" and calls nothing. Demonstrating the behaviour takes that
# to 8 in 10; *describing* it in the system prompt made things worse (1 in 10),
# which is why this is priming rather than another paragraph of instruction.
#
# It sits between the system prompt and the live history, so it is part of the
# stable cached prefix and costs nothing after the first turn.
PRIMING: list[dict] = [
    {"role": "user", "content": "Remember that I take my coffee black"},
    {"role": "assistant", "content": "",
     "tool_calls": [{"function": {
         "name": "remember",
         "arguments": {"fact": "Nicholas takes his coffee black",
                       "category": "preference"}}}]},
    {"role": "tool", "name": "remember", "content": "Noted."},
    {"role": "assistant", "content": "Noted, sir."},
    {"role": "user", "content": "Open Spotify"},
    {"role": "assistant", "content": "",
     "tool_calls": [{"function": {"name": "open_app",
                                  "arguments": {"name": "Spotify"}}}]},
    {"role": "tool", "name": "open_app", "content": "Opened Spotify."},
    {"role": "assistant", "content": "Spotify is open."},
]
# Both examples are deliberately *imperatives*, and neither tool result carries
# a figure. An earlier version demonstrated get_system_stats with real numbers
# in it, and the model started reciting those numbers back as though it had
# measured them -- a false answer produced by the very examples meant to make
# it call tools. Questions were never the weakness; commands were.


@dataclass
class Event:
    """Something the brain wants the rest of the system to know about."""
    type: str          # sentence | tool_start | tool_end | done | error | confirm
    text: str = ""
    name: str = ""
    data: dict | None = None


class Brain:
    def __init__(self, cfg, memory=None):
        self.cfg = cfg
        self.memory = memory
        self.client = ollama.AsyncClient(host=cfg.get("llm.host"))
        self.model = cfg.get("llm.model")
        self.history: list[dict] = []
        self.max_iterations = cfg.get("llm.max_tool_iterations", 6)
        self.history_turns = cfg.get("llm.history_turns", 12)
        self._pending_confirm: tuple[str, dict] | None = None

    # ── helpers ────────────────────────────────────────────────────
    def _options(self) -> dict:
        return {
            "temperature": self.cfg.get("llm.temperature", 0.6),
            "num_ctx": self.cfg.get("llm.num_ctx", 8192),
        }

    def _messages(self) -> list[dict]:
        memories = ""
        if self.memory:
            try:
                memories = self.memory.context_block()
            except Exception:
                log.exception("memory recall failed")
        system = persona.build_system_prompt(self.cfg, memories)
        return [{"role": "system", "content": system}, *PRIMING, *self.history]

    def _trim(self) -> None:
        """Keep the last N exchanges, never orphaning a tool result from its call."""
        limit = self.history_turns * 2
        if len(self.history) <= limit:
            return
        cut = len(self.history) - limit
        while cut < len(self.history) and self.history[cut].get("role") == "tool":
            cut += 1
        self.history = self.history[cut:]

    @staticmethod
    def _split_sentences(buffer: str) -> tuple[list[str], str]:
        """Pull complete sentences out of a streaming buffer."""
        out: list[str] = []
        while True:
            m = _BOUNDARY.search(buffer)
            if not m:
                break
            candidate = buffer[:m.start()].strip()
            if _ABBREV.search(candidate):  # "Dr." is not the end of a sentence
                nxt = _BOUNDARY.search(buffer, m.end())
                if not nxt:
                    break
                candidate = buffer[:nxt.start()].strip()
                buffer = buffer[nxt.end():]
            else:
                buffer = buffer[m.end():]
            if candidate:
                out.append(candidate)
        return out, buffer

    # ── confirmation gate ──────────────────────────────────────────
    def has_pending_confirmation(self) -> bool:
        return self._pending_confirm is not None

    async def resolve_confirmation(self, approved: bool) -> str:
        """Run (or drop) the destructive tool he was asked to confirm."""
        if not self._pending_confirm:
            return ""
        name, args = self._pending_confirm
        self._pending_confirm = None
        if not approved:
            self.history.append(
                {"role": "assistant", "content": "Understood. I've left it alone."}
            )
            return "Understood. I've left it alone."
        result = await registry.execute(name, args)
        self.history.append({"role": "assistant", "content": f"[executed {name}]"})
        return result

    # ── the main loop ──────────────────────────────────────────────
    async def respond(self, user_text: str) -> AsyncIterator[Event]:
        # Unambiguous commands are executed directly. This is both more reliable
        # than asking the model to recognise "skip" and considerably faster --
        # no round trip at all. See brain/intents.py for why.
        shortcut = intents.match(user_text)
        if shortcut:
            name, args, canned = shortcut
            spec = registry.get(name)
            if spec and not (spec.destructive
                             and self.cfg.get("tools.confirm_destructive", True)):
                yield Event("tool_start", name=name, data={"arguments": args})
                result = await registry.execute(name, args)
                yield Event("tool_end", name=name, text=result)

                reply = canned or result
                self.history.append({"role": "user", "content": user_text})
                self.history.append({
                    "role": "assistant", "content": "",
                    "tool_calls": [{"function": {"name": name,
                                                 "arguments": args}}]})
                self.history.append({"role": "tool", "name": name,
                                     "content": result})
                self.history.append({"role": "assistant", "content": reply})
                self._trim()
                log.info("intent shortcut answered in one step: %s", name)
                yield Event("sentence", text=reply)
                yield Event("done", text=reply)
                return

        self.history.append({"role": "user", "content": user_text})
        self._trim()

        # A stable core set (plus any extras this wording clearly asks for).
        # Stability is the point: an unchanged prefix replays from the KV cache
        # at ~3,000 tok/s instead of being re-evaluated at ~90. See tools/router.
        tools = router.select(user_text)
        started = time.perf_counter()

        for iteration in range(self.max_iterations):
            buffer = ""
            spoken = ""
            tool_calls: list[dict] = []

            try:
                stream = await self.client.chat(
                    model=self.model,
                    messages=self._messages(),
                    tools=tools,
                    stream=True,
                    options=self._options(),
                    keep_alive=self.cfg.get("llm.keep_alive", "30m"),
                )

                async for chunk in stream:
                    message = chunk.get("message") or {}

                    if message.get("tool_calls"):
                        for call in message["tool_calls"]:
                            fn = call.get("function", {})
                            tool_calls.append({
                                "name": fn.get("name", ""),
                                "arguments": fn.get("arguments", {}) or {},
                            })

                    piece = message.get("content") or ""
                    if piece:
                        buffer += piece
                        sentences, buffer = self._split_sentences(buffer)
                        for s in sentences:
                            spoken += s + " "
                            yield Event("sentence", text=s)

            except Exception as e:
                log.exception("ollama request failed")
                yield Event("error", text=str(e))
                return

            # Flush whatever is left in the buffer.
            tail = buffer.strip()
            if tail:
                spoken += tail
                yield Event("sentence", text=tail)

            # ── no tools requested: we are done ────────────────────
            if not tool_calls:
                final = spoken.strip()
                if final:
                    self.history.append({"role": "assistant", "content": final})
                log.info("responded in %.1fs (%d iteration%s)",
                         time.perf_counter() - started, iteration + 1,
                         "" if iteration == 0 else "s")
                yield Event("done", text=final)
                return

            # ── execute the requested tools ────────────────────────
            self.history.append({
                "role": "assistant",
                "content": spoken.strip(),
                "tool_calls": [
                    {"function": {"name": c["name"], "arguments": c["arguments"]}}
                    for c in tool_calls
                ],
            })

            for call in tool_calls:
                name, args = call["name"], call["arguments"]
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}

                spec = registry.get(name)

                # Destructive tools stop here and wait for a spoken yes.
                if (spec and spec.destructive
                        and self.cfg.get("tools.confirm_destructive", True)):
                    self._pending_confirm = (name, args)
                    question = persona.pick(persona.CONFIRM_PHRASES, self.cfg)
                    yield Event("confirm", text=question, name=name,
                                data={"arguments": args})
                    return

                yield Event("tool_start", name=name, data={"arguments": args})
                result = await registry.execute(name, args)
                log.info("tool %s(%s) -> %s", name, args, result[:120])
                yield Event("tool_end", name=name, text=result)

                self.history.append(
                    {"role": "tool", "name": name, "content": result}
                )

        yield Event("error", text="I got stuck in a loop there. Let's start again.")

    # ── lifecycle ──────────────────────────────────────────────────
    def reset(self) -> None:
        self.history.clear()
        self._pending_confirm = None

    async def available(self) -> tuple[bool, str]:
        """Is Ollama up and does it have our model?"""
        try:
            listing = await self.client.list()
            names = [m.get("model", m.get("name", "")) for m in listing["models"]]
            if not any(n.startswith(self.model.split(":")[0]) for n in names):
                return False, (f"Ollama is running but {self.model} is missing. "
                               f"Run: ollama pull {self.model}")
            return True, "ok"
        except Exception as e:
            return False, f"Ollama unreachable at {self.cfg.get('llm.host')}: {e}"

    async def warm(self) -> None:
        """Load the weights and pre-fill the KV cache.

        This sends the exact prefix real turns will use -- same system prompt,
        same core tool set -- so the ~11s first-turn prompt evaluation is paid
        during startup rather than by his first question.
        """
        try:
            t0 = time.perf_counter()
            # Unload first. Ollama caches the prompt prefix against the loaded
            # model, and if that prefix has changed since the model was loaded
            # -- an edited persona, a different tool set -- the stale cache
            # produces genuine garbage: raw "< tool_call>" markup and unrelated
            # words emitted as speech. Costs a few seconds at startup; prevents
            # a failure mode that looks like the model having a breakdown.
            try:
                await self.client.generate(model=self.model, keep_alive=0)
            except Exception:
                log.debug("pre-warm unload skipped")

            await self.client.chat(
                model=self.model,
                messages=[
                    {"role": "system",
                     "content": persona.build_system_prompt(self.cfg, "")},
                    {"role": "user", "content": router.warm_prefix_query()},
                ],
                tools=router.select(router.warm_prefix_query()),
                options={**self._options(), "num_predict": 1},
                keep_alive=self.cfg.get("llm.keep_alive", "30m"),
            )
            log.info("model %s warm, cache primed (%.1fs)",
                     self.model, time.perf_counter() - t0)
        except Exception:
            log.exception("warmup failed")

    async def quick(self, prompt: str, max_tokens: int = 60) -> str:
        """One-shot completion with no tools or history -- used for summaries."""
        try:
            r = await self.client.chat(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                options={**self._options(), "num_predict": max_tokens},
                keep_alive=self.cfg.get("llm.keep_alive", "30m"),
            )
            return (r["message"]["content"] or "").strip()
        except Exception:
            log.exception("quick completion failed")
            return ""
