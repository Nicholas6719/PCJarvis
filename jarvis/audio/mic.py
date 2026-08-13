"""Microphone capture. One stream, 80ms frames, delivered to an asyncio queue.

sounddevice's callback runs on a PortAudio thread, so everything crossing into
async land goes through call_soon_threadsafe.
"""
from __future__ import annotations

import asyncio
import collections
import logging
import time

import numpy as np
import sounddevice as sd

log = logging.getLogger("jarvis.mic")


class Microphone:
    def __init__(
        self,
        sample_rate: int = 16000,
        block_size: int = 1280,
        device: int | str | None = None,
        preroll_ms: int = 300,
    ):
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.device = device
        # Generous: 256 frames is ~20s of audio. The queue only backs up if the
        # event loop stalls, and when it does we would rather hold the audio
        # than lose the middle of a sentence.
        self._queue: asyncio.Queue[np.ndarray] = asyncio.Queue(maxsize=256)
        self.dropped_frames = 0
        self._last_drop_warning = 0.0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stream: sd.InputStream | None = None
        self.muted = False
        # Rolling history so we can recover the moment *before* speech was detected.
        preroll_frames = max(1, int((preroll_ms / 1000) * sample_rate / block_size))
        self._preroll: collections.deque[np.ndarray] = collections.deque(
            maxlen=preroll_frames
        )
        self.level = 0.0  # live RMS, 0..1 — drives the UI reactor

    # ── lifecycle ──────────────────────────────────────────────────
    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            blocksize=self.block_size,
            device=self.device,
            channels=1,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()
        dev = sd.query_devices(self._stream.device, "input")["name"]
        log.info("microphone open: %s @ %dHz", dev, self.sample_rate)

    def stop(self) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    # ── capture callback (PortAudio thread) ────────────────────────
    def _callback(self, indata, frames, time_info, status) -> None:
        if status:
            log.debug("input status: %s", status)
        mono = indata[:, 0].copy()
        self.level = float(np.sqrt(np.mean(mono**2)) * 4.0)
        if self.muted:
            return
        self._preroll.append(mono)
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._push, mono)

    def _push(self, frame: np.ndarray) -> None:
        try:
            self._queue.put_nowait(frame)
            return
        except asyncio.QueueFull:
            pass

        # The queue is full, which means the event loop is not draining it --
        # something is blocking. Dropping frames here is invisible to every
        # layer above: Whisper simply receives a sentence with holes in it and
        # transcribes the fragments, and it looks like poor recognition rather
        # than lost audio. So it is counted and reported.
        self.dropped_frames += 1
        now = time.monotonic()
        if now - self._last_drop_warning > 5.0:
            self._last_drop_warning = now
            log.warning(
                "microphone queue full -- dropped %d frames. The event loop is "
                "blocked; audio is being lost and recognition will suffer.",
                self.dropped_frames)
        try:
            self._queue.get_nowait()
            self._queue.put_nowait(frame)
        except (asyncio.QueueEmpty, asyncio.QueueFull):
            pass

    # ── consumption ────────────────────────────────────────────────
    async def frames(self):
        """Async iterator of float32 mono frames."""
        while True:
            yield await self._queue.get()

    def preroll_audio(self) -> np.ndarray:
        """Everything still sitting in the pre-speech buffer."""
        if not self._preroll:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(list(self._preroll))

    def drain(self) -> None:
        """Throw away queued audio — used after TTS so he doesn't hear himself."""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._preroll.clear()


def list_input_devices() -> list[dict]:
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            out.append({"index": i, "name": d["name"],
                        "default": i == sd.default.device[0]})
    return out
