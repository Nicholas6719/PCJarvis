"""The ear: one coroutine owning the wake -> capture -> transcribe cycle.

Keeping this as a single state machine rather than scattering it across
callbacks makes the whole listening behaviour readable in one place, which
matters because timing bugs here are miserable to debug.
"""
from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum

import numpy as np

from ..bus import BUS
from .mic import Microphone
from .stt import Transcriber
from .vad import SileroVAD
from .wake import WakeWord

log = logging.getLogger("jarvis.listener")


class Mode(Enum):
    WAITING = "waiting"    # listening for the wake word
    CAPTURING = "capturing"  # he is awake; recording your sentence


class Listener:
    def __init__(self, cfg, mic: Microphone, stt: Transcriber):
        self.cfg = cfg
        self.mic = mic
        self.stt = stt
        self.wake = WakeWord(
            model_name=cfg.get("wake.model", "hey_jarvis"),
            threshold=cfg.get("wake.threshold", 0.5),
            refractory_seconds=cfg.get("wake.refractory_seconds", 2.0),
        ) if cfg.get("wake.enabled", True) else None
        self.vad = SileroVAD(threshold=cfg.get("vad.threshold", 0.5))

        self.min_speech_ms = cfg.get("vad.min_speech_ms", 250)
        self.silence_ms = cfg.get("vad.silence_ms", 800)
        self.max_utterance_s = cfg.get("vad.max_utterance_s", 30)

        self.mode = Mode.WAITING
        self._force_capture = asyncio.Event()  # push-to-talk / hotkey
        self._paused = False
        self._utterances: asyncio.Queue[str] = asyncio.Queue()

    # ── external triggers ──────────────────────────────────────────
    def trigger(self) -> None:
        """Wake him without the wake word (hotkey or the UI button)."""
        self._force_capture.set()

    def pause(self) -> None:
        """Stop consuming audio -- used while he is speaking, to avoid self-hearing."""
        self._paused = True

    def resume(self) -> None:
        self.mic.drain()
        self.vad.reset()
        if self.wake:
            self.wake.reset()
        self._paused = False

    async def utterances(self):
        """Async iterator of transcribed user speech."""
        while True:
            yield await self._utterances.get()

    # ── the loop ───────────────────────────────────────────────────
    async def run(self) -> None:
        frame_ms = self.cfg.get("audio.block_size", 1280) / \
            self.cfg.get("audio.sample_rate", 16000) * 1000

        captured: list[np.ndarray] = []
        speech_ms = 0.0
        silence_ms = 0.0
        started_at = 0.0

        async for frame in self.mic.frames():
            if self._paused:
                continue

            # ── waiting for attention ──────────────────────────────
            if self.mode is Mode.WAITING:
                fired = self._force_capture.is_set()
                if fired:
                    self._force_capture.clear()
                elif self.wake:
                    fired = self.wake.process(frame)
                    await BUS.emit("wake.score", score=self.wake.score)

                if fired:
                    self.mode = Mode.CAPTURING
                    self.vad.reset()
                    captured = [self.mic.preroll_audio()]
                    speech_ms = silence_ms = 0.0
                    started_at = time.monotonic()
                    await BUS.emit("wake.detected")
                continue

            # ── capturing your sentence ────────────────────────────
            captured.append(frame)
            is_speech = self.vad.is_speech(frame)

            if is_speech:
                speech_ms += frame_ms
                silence_ms = 0.0
            else:
                silence_ms += frame_ms

            elapsed = time.monotonic() - started_at
            endpoint = (
                (speech_ms >= self.min_speech_ms and silence_ms >= self.silence_ms)
                or elapsed >= self.max_utterance_s
                # Woken by accident: nothing said in the first 2.5s.
                or (speech_ms < self.min_speech_ms and elapsed > 2.5)
            )
            if not endpoint:
                continue

            self.mode = Mode.WAITING
            audio = np.concatenate(captured)
            captured = []

            if speech_ms < self.min_speech_ms:
                log.debug("woke but heard nothing; standing down")
                await BUS.emit("listen.empty")
                if self.wake:
                    self.wake.reset()
                continue

            await BUS.emit("listen.transcribing")
            text = await asyncio.to_thread(self.stt.transcribe, audio)
            if self.wake:
                self.wake.reset()

            if not text:
                await BUS.emit("listen.empty")
                continue

            log.info("heard: %s", text)
            await BUS.emit("listen.transcript", text=text,
                           duration=len(audio) / 16000)
            await self._utterances.put(text)
