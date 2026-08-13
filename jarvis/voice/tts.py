"""Text-to-speech: Kokoro-82M (ONNX, CPU) into the JARVIS voice chain.

Kokoro is Apache-2.0, 82M parameters, and runs comfortably faster than real
time on this CPU -- which matters, because every millisecond here is latency
the user hears. Its British male voices are the starting point; jarvis_chain
does the rest.

A Windows SAPI fallback exists so the pipeline still speaks if the Kokoro
weights are missing. It does not sound like JARVIS. It is a diagnostic, not
a feature.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path

import numpy as np
from scipy import signal as _sig

from ..config import BUNDLE, MODELS_DIR
from . import jarvis_chain

log = logging.getLogger("jarvis.tts")

KOKORO_MODEL = MODELS_DIR / "kokoro" / "kokoro-v1.0.onnx"
KOKORO_VOICES = MODELS_DIR / "kokoro" / "voices-v1.0.bin"
IR_PATH = BUNDLE / "jarvis" / "voice" / "ir" / "workshop.wav"

# JARVIS speaks with exactly one voice. This was chosen by ear against every
# other British male voice Kokoro ships, against blends of them, and against
# six post-processing treatments -- all of which lost to the untreated read.
# Anything else is not JARVIS, so the choice is pinned here rather than left
# to configuration, and config is validated against it at load.
LOCKED_VOICE = "bm_daniel"
LOCKED_SPEED = 1.0

# Split on sentence boundaries so we can start speaking before the LLM has
# finished writing. Keeps the delimiter attached to the sentence.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Characters a language model emits happily and a speech synthesizer cannot
# pronounce. Em dashes in particular come out as a swallowed glitch.
_REPLACEMENTS = {
    "—": ", ", "–": ", ",           # em / en dash -> a spoken pause
    "‘": "'", "’": "'",             # smart quotes
    "“": "", "”": "", '"': "",
    "…": "...", " ": " ",
    "&": " and ", "%": " percent", "@": " at ",
    "#": " number ", "+": " plus ", "=": " equals ",
    "°": " degrees ", "→": " to ", "/": " ",
}
_MARKDOWN = re.compile(r"(\*\*|__|\*|`{1,3}|^#{1,6}\s*|^\s*[-*+]\s+)", re.M)
_URL = re.compile(r"https?://\S+")
_PATH = re.compile(r"[A-Za-z]:\\[^\s]+")
_MULTISPACE = re.compile(r"\s{2,}")


def speakable(text: str) -> str:
    """Reduce model output to something a synthesizer can actually say."""
    text = _MARKDOWN.sub("", text)
    text = _URL.sub("a link", text)
    text = _PATH.sub("that path", text)
    for src, dst in _REPLACEMENTS.items():
        text = text.replace(src, dst)
    # Anything still outside the speakable range would be guesswork.
    text = "".join(c for c in text if c.isascii() or c.isalpha())
    return _MULTISPACE.sub(" ", text).strip()


class Voice:
    def __init__(self, cfg):
        self.cfg = cfg
        self.sample_rate = cfg.get("tts.sample_rate", 24000)

        # Config may not override the locked voice -- it can only agree with it.
        configured = cfg.get("tts.voice", LOCKED_VOICE)
        if configured != LOCKED_VOICE:
            log.warning("config asked for voice %r; using the locked voice %r",
                        configured, LOCKED_VOICE)
        self.voice = LOCKED_VOICE
        self.speed = cfg.get("tts.speed", LOCKED_SPEED)

        self._kokoro = None
        self._backend = "none"
        self._load()

    def _load(self) -> None:
        if KOKORO_MODEL.exists() and KOKORO_VOICES.exists():
            try:
                from kokoro_onnx import Kokoro

                t0 = time.perf_counter()
                self._kokoro = Kokoro(str(KOKORO_MODEL), str(KOKORO_VOICES))
                self._backend = "kokoro"
                log.info("kokoro loaded in %.1fs (voice %s)",
                         time.perf_counter() - t0, self.voice)
                return
            except Exception:
                log.exception("kokoro failed to load; falling back to SAPI")
        else:
            log.warning("kokoro weights missing -- run scripts/download_models.py")
        self._backend = "sapi"

    @property
    def backend(self) -> str:
        return self._backend

    # ── synthesis ──────────────────────────────────────────────────
    def _synthesize_raw(self, text: str) -> tuple[np.ndarray, int]:
        if self._backend == "kokoro":
            samples, sr = self._kokoro.create(
                text, voice=self.voice, speed=self.speed, lang="en-gb"
            )
            return np.asarray(samples, dtype=np.float32), int(sr)
        return self._sapi(text)

    def _sapi(self, text: str) -> tuple[np.ndarray, int]:
        """Windows built-in TTS, rendered to memory. Diagnostic fallback only."""
        import tempfile

        import comtypes.client
        import soundfile as sf

        engine = comtypes.client.CreateObject("SAPI.SpVoice")
        stream = comtypes.client.CreateObject("SAPI.SpFileStream")
        # Prefer a British male voice if the system has one installed.
        for v in engine.GetVoices():
            desc = v.GetDescription()
            if "George" in desc or "Hazel" in desc or "UK" in desc:
                engine.Voice = v
                break
        path = Path(tempfile.gettempdir()) / "jarvis_sapi.wav"
        stream.Open(str(path), 3)  # 3 = SSFMCreateForWrite
        engine.AudioOutputStream = stream
        engine.Speak(text)
        stream.Close()
        audio, sr = sf.read(str(path), dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        return audio, sr

    def say(self, text: str) -> tuple[np.ndarray, int]:
        """Synthesize `text` and run it through the JARVIS chain."""
        text = speakable(text)
        if not text:
            return np.zeros(0, dtype=np.float32), self.sample_rate

        t0 = time.perf_counter()
        audio, sr = self._synthesize_raw(text)
        synth_ms = (time.perf_counter() - t0) * 1000

        t1 = time.perf_counter()
        audio = jarvis_chain.apply_chain(
            audio, sr, self.cfg.section("voice_chain"), IR_PATH
        )
        log.debug("tts %d chars: synth %.0fms + chain %.0fms",
                  len(text), synth_ms, (time.perf_counter() - t1) * 1000)
        return audio, sr

    def say_raw(self, text: str) -> tuple[np.ndarray, int]:
        """Bypass the chain -- used by the tuning tool for A/B comparison."""
        return self._synthesize_raw(text)

    # ── streaming ──────────────────────────────────────────────────
    @staticmethod
    def split_sentences(text: str) -> list[str]:
        return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]

    def set_speed(self, speed: float) -> None:
        """Adjust cadence. The voice itself is locked and cannot be changed."""
        self.speed = max(0.5, min(1.5, float(speed)))

    def warm(self) -> None:
        """Force the graph to compile now rather than on the first real reply."""
        try:
            self.say("Systems online.")
        except Exception:
            log.exception("tts warmup failed")


def make_chime(sample_rate: int = 24000) -> np.ndarray:
    """A short two-tone rise for the moment he wakes. Deliberately understated."""
    def tone(freq: float, dur: float, amp: float) -> np.ndarray:
        t = np.linspace(0, dur, int(dur * sample_rate), endpoint=False)
        env = np.exp(-t * 9.0)
        wave = np.sin(2 * np.pi * freq * t) + 0.35 * np.sin(4 * np.pi * freq * t)
        return (wave * env * amp).astype(np.float32)

    gap = np.zeros(int(0.012 * sample_rate), dtype=np.float32)
    out = np.concatenate([tone(660, 0.09, 0.16), gap, tone(990, 0.13, 0.13)])
    sos = _sig.butter(2, 6000 / (sample_rate / 2), btype="lowpass", output="sos")
    return _sig.sosfilt(sos, out).astype(np.float32)
