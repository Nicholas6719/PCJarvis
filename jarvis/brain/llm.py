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

# A model whose cached prefix has gone stale does not raise an error -- it emits
# the tool-call template as literal text, mixed with unrelated words. Cheap to
# detect, and worth detecting, because otherwise JARVIS reads the markup aloud.
_CORRUPT = re.compile(
    r"(<\s*/?\s*tool_call\s*>"          # < tool_call> leaking into prose
    r"|\{\s*name\s*:\s*\w"              # {name: get_battery, ...}
    r"|\{\s*\"name\"\s*:\s*\""          # {"name": "get_battery"}
    r"|\barguments\s*:\s*\{)",          # arguments: {}
    re.I,
)


def _is_corrupt(text: str) -> bool:
    """True if the model is emitting tool-call machinery as speech."""
    return bool(text) and bool(_CORRUPT.search(text))


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
    # Memory lives in a tool, not in the prompt, so anything personal has to be
    # looked up. Without this example he answers "I don't have that stored"
    # while the fact is sitting in the database.
    {"role": "user", "content": "What sort of desk do I use?"},
    {"role": "assistant", "content": "",
     "tool_calls": [{"function": {"name": "recall",
                                  "arguments": {"query": "desk"}}}]},
    {"role": "tool", "name": "recall", "content": "- He uses a standing desk."},
    {"role": "assistant", "content": "A standing desk, sir."},
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
        """system + priming + history. Nothing else, ever.

        Memories are deliberately NOT injected here. Two earlier attempts both
        failed, and instructively:

        1. Appended to the system prompt: the block grows every time he tells
           JARVIS something, so the cached prefix changed mid-session and
           Ollama's KV cache went stale -- after which the model emits raw
           "< tool_call>" markup as speech instead of calling anything.
        2. Inserted as a system message before the newest user turn: qwen's chat
           template mishandles a system role that is not first, and the model
           began writing tool calls as plain text for the same reason.

        Memory is a *tool*, not context. `recall` is in the core tool set and he
        calls it when a question needs it, which keeps this prefix byte-identical
        for the life of the install -- the entire basis of the ~2s reply.
        """
        return [
            {"role": "system", "content": persona.build_system_prompt(self.cfg)},
            *PRIMING,
            *self.history,
        ]

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

    async def _unload(self) -> None:
        """Drop the model from memory, discarding its cached prefix."""
        try:
            await self.client.generate(model=self.model, keep_alive=0)
            await asyncio.sleep(1.0)  # let Ollama actually release it
        except Exception:
            log.debug("unload skipped")

    async def _probe(self) -> str:
        """One tiny generation through the real prefix, to inspect the output."""
        try:
            r = await self.client.chat(
                model=self.model,
                messages=[
                    {"role": "system",
                     "content": persona.build_system_prompt(self.cfg)},
                    *PRIMING,
                    {"role": "user", "content": "Say the single word: online."},
                ],
                tools=router.select(router.warm_prefix_query()),
                options={**self._options(), "num_predict": 12},
                keep_alive=self.cfg.get("llm.keep_alive", "30m"),
            )
            return (r["message"].get("content") or "").strip()
        except Exception:
            log.exception("probe failed")
            return ""

    def _prefix_changed(self) -> bool:
        """Has the cached prompt prefix changed since the last run?

        The prefix is the system prompt, the priming turns and the core tool
        schemas -- everything Ollama caches ahead of the live conversation.
        """
        import hashlib
        import json as _json

        from ..config import DATA_DIR

        material = _json.dumps([
            persona.build_system_prompt(self.cfg, ""),
            PRIMING,
            router.select(router.warm_prefix_query()),
            self.model,
        ], sort_keys=True, default=str)
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]

        marker = DATA_DIR / "prefix.hash"
        try:
            previous = marker.read_text(encoding="utf-8").strip()
        except OSError:
            previous = ""
        if previous == digest:
            return False
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(digest, encoding="utf-8")
        except OSError:
            log.debug("could not record the prefix hash")
        return True

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
            # Ollama caches the prompt prefix against the loaded model. If that
            # prefix changed since the model was loaded -- an edited persona, a
            # different tool set -- the stale cache produces genuine garbage:
            # raw "< tool_call>" markup and unrelated words emitted as speech.
            #
            # Unloading fixes it but costs ~30s of reload, so we only do it when
            # the prefix has actually changed, tracked by a hash on disk. Normal
            # launches skip it entirely.
            if self._prefix_changed():
                log.info("prompt prefix changed; flushing the model cache")
                await self._unload()

            # Probe, then verify. A corrupted cache does not raise -- it returns
            # cheerful nonsense with tool-call markup in it -- so the only way
            # to know is to look at what came back and flush if it is garbage.
            if _is_corrupt(await self._probe()):
                log.warning("model cache is corrupt; flushing and reloading")
                await self._unload()
                if _is_corrupt(await self._probe()):
                    log.error("model still producing malformed output after a flush")

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
