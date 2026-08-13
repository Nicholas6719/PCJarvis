"""Audio playback with barge-in.

Speech plays in small chunks off a queue rather than as one blocking write, so
that interrupting him is instant: we clear the queue and the current utterance
stops within one chunk (~20ms) instead of at the end of the sentence.
"""
from __future__ import annotations

import asyncio
import logging
import queue
import threading

import numpy as np
import sounddevice as sd

log = logging.getLogger("jarvis.player")

CHUNK = 512


class Player:
    def __init__(self, sample_rate: int = 24000, device: int | str | None = None):
        self.sample_rate = sample_rate
        self.device = device
        self._queue: queue.Queue[np.ndarray | None] = queue.Queue()
        self._stream: sd.OutputStream | None = None
        self._thread: threading.Thread | None = None
        self._interrupt = threading.Event()
        self._playing = threading.Event()
        self.envelope = 0.0  # live output level, 0..1 — drives the UI reactor

    def start(self) -> None:
        self._stream = sd.OutputStream(
            samplerate=self.sample_rate,
            device=self.device,
            channels=1,
            dtype="float32",
            blocksize=CHUNK,
        )
        self._stream.start()
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()
        log.info("output open @ %dHz", self.sample_rate)

    def stop(self) -> None:
        self._queue.put(None)
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    # ── playback thread ────────────────────────────────────────────
    def _pump(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            self._playing.set()
            try:
                for i in range(0, len(item), CHUNK):
                    if self._interrupt.is_set():
                        break
                    chunk = item[i:i + CHUNK]
                    if len(chunk) < CHUNK:
                        chunk = np.pad(chunk, (0, CHUNK - len(chunk)))
                    self.envelope = float(np.sqrt(np.mean(chunk ** 2)) * 3.0)
                    if self._stream:
                        self._stream.write(chunk)
            except Exception:
                log.exception("playback error")
            finally:
                self.envelope = 0.0
                if self._queue.empty():
                    self._playing.clear()

    # ── public API ─────────────────────────────────────────────────
    def play(self, audio: np.ndarray, sample_rate: int | None = None) -> None:
        """Queue audio for playback. Returns immediately."""
        if audio is None or audio.size == 0:
            return
        if sample_rate and sample_rate != self.sample_rate:
            from scipy import signal
            audio = signal.resample(
                audio, int(len(audio) * self.sample_rate / sample_rate)
            ).astype(np.float32)
        self._interrupt.clear()
        self._queue.put(np.asarray(audio, dtype=np.float32))

    async def play_and_wait(self, audio: np.ndarray,
                            sample_rate: int | None = None) -> bool:
        """Play to completion. Returns False if it was interrupted."""
        self.play(audio, sample_rate)
        await asyncio.sleep(0.02)
        while self._playing.is_set() and not self._interrupt.is_set():
            await asyncio.sleep(0.02)
        return not self._interrupt.is_set()

    async def wait_done(self) -> None:
        while self._playing.is_set():
            await asyncio.sleep(0.02)

    def interrupt(self) -> None:
        """Cut playback now and drop anything still queued."""
        if not self.is_playing:
            return
        self._interrupt.set()
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._playing.clear()
        log.debug("playback interrupted")

    @property
    def is_playing(self) -> bool:
        return self._playing.is_set()


def list_output_devices() -> list[dict]:
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] > 0:
            out.append({"index": i, "name": d["name"],
                        "default": i == sd.default.device[1]})
    return out
