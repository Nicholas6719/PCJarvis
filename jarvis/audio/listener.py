"""The ear: one coroutine owning wake -> capture -> transcribe.

One microphone stream for the whole session, never closed. The modes below are
software states over that single stream, which is what makes the next wake
instant rather than costing a device open.

    WAKE        resting. Only the wake model runs. Nothing else is processed.
    ARMED       inside the conversation window. Speech alone starts a turn --
                no wake word needed.
    CAPTURING   recording your sentence until you stop talking.
    SPEAKING    JARVIS has the floor. The wake model still runs, so saying his
                name cuts him off; ordinary speech does not, because the
                microphone hears the speakers.

The pre-wake buffer matters more than it looks. People say "Hey Jarvis what time
is it" in one breath, and the wake model needs most of a second to fire -- by
which point the command is already half spoken. Replaying the buffered audio
from before the trigger is the difference between a natural request and having
to pause awkwardly after his name.
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
    WAKE = "wake"
    ARMED = "armed"
    CAPTURING = "capturing"
    SPEAKING = "speaking"


class Listener:
    def __init__(self, cfg, mic: Microphone, stt: Transcriber):
        self.cfg = cfg
        self.mic = mic
        self.stt = stt
        self.wake = WakeWord(
            model_name=cfg.get("wake.model", "hey_jarvis"),
            threshold=cfg.get("wake.threshold", 0.45),
            refractory_seconds=cfg.get("wake.refractory_seconds", 2.0),
        ) if cfg.get("wake.enabled", True) else None
        self.vad = SileroVAD(threshold=cfg.get("vad.threshold", 0.5))

        self.min_speech_ms = cfg.get("vad.min_speech_ms", 250)
        self.silence_ms = cfg.get("vad.silence_ms", 700)
        self.silence_ms_long = cfg.get("vad.silence_ms_long", 750)
        self.max_utterance_s = cfg.get("vad.max_utterance_s", 15)
        self.patience_s = cfg.get("vad.patience_s", 6.0)
        self.conversation_s = cfg.get("conversation.window_s", 15)
        self.conversation_enabled = cfg.get("conversation.enabled", True)

        self.mode = Mode.WAKE
        self._force_capture = asyncio.Event()
        self._muted = False
        self._conversation_until = 0.0
        self._suspended = False
        self._speaking = False
        self._utterances: asyncio.Queue[str] = asyncio.Queue()

    # ── control surface ────────────────────────────────────────────
    def trigger(self) -> None:
        """Wake him without the wake word (hotkey, button, typed message)."""
        self._force_capture.set()

    def set_muted(self, muted: bool) -> None:
        """Hard pause: no capture, no wake detection, until unmuted."""
        self._muted = bool(muted)
        if muted:
            self.mode = Mode.WAKE
            self._conversation_until = 0.0
        else:
            self._reset_audio()
        log.info("microphone %s", "muted" if muted else "live")

    @property
    def muted(self) -> bool:
        return self._muted

    def begin_speaking(self) -> None:
        """JARVIS has the floor. Keep the wake model live for barge-in."""
        self._speaking = True
        self.mode = Mode.SPEAKING
        if self.wake:
            self.wake.reset()

    def end_speaking(self) -> None:
        """He has finished. Settle, flush, and reopen the conversation window."""
        self._speaking = False
        self._reset_audio()
        self.extend_conversation()
        self.mode = Mode.ARMED if self.in_conversation else Mode.WAKE

    def extend_conversation(self) -> None:
        """Reset the 15s window. Called per user turn and after he speaks."""
        if self.conversation_enabled:
            self._conversation_until = time.monotonic() + self.conversation_s

    def end_conversation(self) -> None:
        """Drop straight back to wake mode -- 'that's all', or a timeout."""
        self._conversation_until = 0.0
        self._suspended = False
        self.mode = Mode.WAKE
        self._reset_audio()

    def suspend_conversation(self) -> None:
        """Freeze the window for the duration of a turn.

        A reply that takes longer than the window used to expire it while he was
        still thinking, dropping to wake mode mid-answer -- so the follow-up he
        was about to ask needed the wake word again.
        """
        self._suspended = True

    def resume_conversation(self) -> None:
        self._suspended = False
        self.extend_conversation()

    @property
    def in_conversation(self) -> bool:
        return self._suspended or time.monotonic() < self._conversation_until

    def _reset_audio(self) -> None:
        self.mic.drain()
        self.vad.reset()
        if self.wake:
            self.wake.reset()

    async def utterances(self):
        while True:
            yield await self._utterances.get()

    # ── the loop ───────────────────────────────────────────────────
    async def run(self) -> None:
        frame_ms = (self.cfg.get("audio.block_size", 1280)
                    / self.cfg.get("audio.sample_rate", 16000) * 1000)

        captured: list[np.ndarray] = []
        speech_ms = silence_ms = 0.0
        started_at = 0.0

        async for frame in self.mic.frames():
            if self._muted:
                continue

            # ── he is speaking: only his name interrupts ───────────
            if self._speaking:
                if self.wake and self.wake.process(frame):
                    log.info("barge-in")
                    await BUS.emit("barge_in")
                continue

            # ── waiting, or armed inside the conversation window ───
            if self.mode in (Mode.WAKE, Mode.ARMED):
                armed = self.in_conversation
                if self.mode is Mode.ARMED and not armed:
                    self.mode = Mode.WAKE
                    self._reset_audio()
                    await BUS.emit("conversation.ended")
                elif self.mode is Mode.WAKE and armed:
                    self.mode = Mode.ARMED

                fired = False
                by_voice = False

                if self._force_capture.is_set():
                    self._force_capture.clear()
                    fired = True
                elif self.wake and self.wake.process(frame):
                    fired = True
                    by_voice = True
                    await BUS.emit("wake.detected")
                elif armed and self.vad.is_speech(frame):
                    # Inside the window, speech alone opens a turn.
                    fired = True

                if self.wake:
                    await BUS.emit("wake.score", score=self.wake.score)

                if not fired:
                    continue

                self.mode = Mode.CAPTURING
                self.vad.reset()
                # Replay everything from before the trigger. This is what makes
                # a single-breath "Hey Jarvis, what time is it" survive.
                captured = [self.mic.preroll_audio()]
                speech_ms = silence_ms = 0.0
                started_at = time.monotonic()
                if not by_voice:
                    await BUS.emit("listen.started")
                continue

            # ── capturing ─────────────────────────────────────────
            captured.append(frame)
            if self.vad.is_speech(frame):
                speech_ms += frame_ms
                silence_ms = 0.0
            else:
                silence_ms += frame_ms

            elapsed = time.monotonic() - started_at
            # A longer utterance earns a slightly longer tail, so a considered
            # sentence with a pause in it is not chopped in half.
            quiet_needed = (self.silence_ms_long if speech_ms > 2500
                            else self.silence_ms)
            done = (
                (speech_ms >= self.min_speech_ms and silence_ms >= quiet_needed)
                or elapsed >= self.max_utterance_s
                or (speech_ms < self.min_speech_ms and elapsed > self.patience_s)
            )
            if not done:
                continue

            audio = np.concatenate(captured) if captured else np.zeros(0, np.float32)
            captured = []
            self.mode = Mode.ARMED if self.in_conversation else Mode.WAKE

            if speech_ms < self.min_speech_ms:
                log.info("woke but heard no speech in %.1fs; standing down", elapsed)
                await BUS.emit("listen.empty")
                self._reset_audio()
                continue

            await BUS.emit("listen.transcribing")
            text = await asyncio.to_thread(self.stt.transcribe, audio)
            if self.wake:
                self.wake.reset()

            if not text:
                await BUS.emit("listen.empty")
                continue

            log.info("heard: %s", text)
            self.extend_conversation()
            await BUS.emit("listen.transcript", text=text,
                           duration=len(audio) / 16000)
            await self._utterances.put(text)
