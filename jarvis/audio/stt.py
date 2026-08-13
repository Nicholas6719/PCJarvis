"""Speech-to-text via faster-whisper (CTranslate2, int8, CPU)."""
from __future__ import annotations

import logging
import time

import numpy as np

from ..config import MODELS_DIR

log = logging.getLogger("jarvis.stt")

# Whisper hallucinates these into silence. Drop them rather than act on them.
_HALLUCINATIONS = {
    "thank you.", "thanks for watching!", "thank you for watching!",
    "you", "bye.", ".", "...", "[blank_audio]", "subs by www.zeoranger.co.uk",
    "please subscribe!", "okay.", "so.",
}


class Transcriber:
    def __init__(
        self,
        model: str = "small.en",
        compute_type: str = "int8",
        beam_size: int = 1,
        cpu_threads: int = 6,
    ):
        from faster_whisper import WhisperModel

        self.beam_size = beam_size
        t0 = time.perf_counter()
        self.model = WhisperModel(
            model,
            device="cpu",
            compute_type=compute_type,
            cpu_threads=cpu_threads,
            download_root=str(MODELS_DIR / "whisper"),
        )
        log.info("whisper %s (%s) loaded in %.1fs", model, compute_type,
                 time.perf_counter() - t0)

    def transcribe(self, audio: np.ndarray) -> str:
        """audio: float32 mono @16k. Returns cleaned text ('' if nothing useful)."""
        if audio.size < 1600:  # under 100ms of sound isn't a sentence
            return ""
        t0 = time.perf_counter()
        segments, _ = self.model.transcribe(
            audio.astype(np.float32),
            beam_size=self.beam_size,
            language="en",
            vad_filter=False,          # we already did our own VAD
            condition_on_previous_text=False,  # stops runaway repetition
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        log.debug("transcribed %.1fs of audio in %.2fs", len(audio) / 16000,
                  time.perf_counter() - t0)

        if text.lower().strip() in _HALLUCINATIONS:
            return ""
        return text

    def warm(self) -> None:
        """Run one inference at startup so the first real request isn't slow."""
        self.transcribe(np.zeros(16000, dtype=np.float32))
