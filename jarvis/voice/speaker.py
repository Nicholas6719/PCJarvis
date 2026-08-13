"""Speech output: synthesis pipelined ahead of playback.

The naive arrangement -- synthesize a sentence, play it, synthesize the next --
leaves a hole between every sentence the length of a synthesis (300ms to 1.5s
here). The stream underruns, and a reply comes out in audible chunks.

So synthesis runs one sentence ahead of playback on its own thread: while
sentence N is being spoken, sentence N+1 is already being made. The player then
pulls from a queue that is rarely empty, and the reply lands as one continuous
utterance.

Everything supports a hard stop, because barge-in has to cut him off within a
block rather than at the end of the sentence.
"""
from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time

import numpy as np

from .pronounce import split_for_synthesis

log = logging.getLogger("jarvis.speaker")

_SENTINEL = object()


class Speaker:
    def __init__(self, voice, player):
        self.voice = voice
        self.player = player

        self._text: queue.Queue = queue.Queue()
        self._worker = threading.Thread(target=self._synthesize_loop, daemon=True)
        self._stop = threading.Event()
        self._busy = threading.Event()      # text pending OR audio playing
        self._synthesizing = threading.Event()
        self._generation = 0                # bumped on stop, to drop stale audio
        self._lock = threading.Lock()
        self.last_spoken = ""
        self._worker.start()

    # ── synthesis thread ───────────────────────────────────────────
    def _synthesize_loop(self) -> None:
        while True:
            item = self._text.get()
            if item is _SENTINEL:
                return

            generation, text = item
            # Dropped mid-flight by a stop() that happened while we queued.
            if generation != self._generation or self._stop.is_set():
                self._settle()
                continue

            self._synthesizing.set()
            try:
                audio, sr = self.voice.say(text)
                # Check again: synthesis takes time, and he may have been
                # interrupted during it. Playing this now would be him talking
                # over an interruption.
                if generation == self._generation and not self._stop.is_set():
                    self.player.play(audio, sr)
                    self.last_spoken = text
            except Exception:
                log.exception("synthesis failed for %r", text[:60])
            finally:
                self._synthesizing.clear()
                self._settle()

    def _settle(self) -> None:
        if self._text.empty() and not self._synthesizing.is_set() \
                and not self.player.is_playing:
            self._busy.clear()

    # ── public API ─────────────────────────────────────────────────
    def say(self, text: str) -> None:
        """Queue a sentence. Returns immediately; speech follows in order.

        Long sentences are split before synthesis: past ~180 characters Kokoro
        starts losing coherence and repeating syllables. The pieces play back to
        back through the same stream, so the split is inaudible.
        """
        text = (text or "").strip()
        if not text:
            return
        self._stop.clear()
        self._busy.set()
        with self._lock:
            for chunk in split_for_synthesis(text):
                self._text.put((self._generation, chunk))

    async def wait_until_done(self, timeout: float = 120.0) -> bool:
        """Block until everything queued has been spoken. False if interrupted."""
        deadline = time.monotonic() + timeout
        # Give the worker a moment to pick up the first item.
        await asyncio.sleep(0.05)
        while time.monotonic() < deadline:
            if self._stop.is_set():
                return False
            if (self._text.empty() and not self._synthesizing.is_set()
                    and not self.player.is_playing):
                return True
            await asyncio.sleep(0.03)
        return True

    def stop(self) -> None:
        """Hard stop: kill playback, drop queued audio, abandon pending synthesis."""
        with self._lock:
            self._generation += 1        # anything in flight is now stale
            self._stop.set()
            while not self._text.empty():
                try:
                    self._text.get_nowait()
                except queue.Empty:
                    break
        self.player.interrupt()
        self._busy.clear()
        log.debug("speech stopped")

    @property
    def is_speaking(self) -> bool:
        return (self._busy.is_set() or self.player.is_playing
                or self._synthesizing.is_set())

    def play_audio(self, audio: np.ndarray, sample_rate: int) -> None:
        """Play raw audio (the wake chime) without going through synthesis."""
        self.player.play(audio, sample_rate)

    def shutdown(self) -> None:
        self.stop()
        self._text.put(_SENTINEL)
