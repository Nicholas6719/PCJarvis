"""Three short sounds, from the same instrument as the power-up.

Interfaces in the films are full of small noises, and they do real work: they
tell you something happened without anyone having to say so. A tone is faster
than a sentence and does not interrupt a thought the way speech does.

The rules that keep this from becoming irritating are all about restraint.
Every one of these is under a third of a second and peaks well below the voice,
so they sit underneath what he says rather than competing with it. They are
built from the same materials as the boot sound -- sine partials, perfect
intervals, a gentle roll-off -- so the whole machine sounds like one instrument
rather than a pile of downloaded beeps.

And they are used sparingly on purpose. A tone on every tool call is a machine
that clicks constantly; one before an unprompted remark is a machine that clears
its throat before speaking, which is a very different thing.

    attention   he is about to say something nobody asked for
    done        a routine finished
    error       something failed
"""
from __future__ import annotations

import logging

import numpy as np
from scipy import signal as _sig

log = logging.getLogger("jarvis.tones")


def _env(n: int, sample_rate: int, attack_ms: float, decay: float) -> np.ndarray:
    """A soft attack and an exponential tail. Sharp edges click."""
    t = np.arange(n) / sample_rate
    attack = np.clip(t / max(attack_ms / 1000.0, 1e-4), 0, 1)
    return (attack * np.exp(-t * decay)).astype(np.float64)


def _note(freq: float, seconds: float, sample_rate: int, amp: float,
          decay: float = 9.0, attack_ms: float = 6.0,
          partials: tuple = ((1, 1.0), (2, 0.28), (3, 0.09))) -> np.ndarray:
    n = int(seconds * sample_rate)
    t = np.arange(n) / sample_rate
    out = np.zeros(n, dtype=np.float64)
    for harmonic, weight in partials:
        out += weight * np.sin(2 * np.pi * freq * harmonic * t)
    return out * _env(n, sample_rate, attack_ms, decay) * amp


def _finish(out: np.ndarray, sample_rate: int, peak: float) -> np.ndarray:
    out = _sig.sosfilt(
        _sig.butter(3, 8000 / (sample_rate / 2), btype="lowpass", output="sos"),
        out)
    out = np.nan_to_num(np.asarray(out, dtype=np.float32))
    high = float(np.max(np.abs(out))) or 1.0
    return (out * (peak / high)).astype(np.float32)


def attention(sample_rate: int = 24000) -> np.ndarray:
    """He is about to speak unprompted. A rising fifth, quiet and brief.

    Rising because it is a question being opened rather than an answer being
    closed -- it precedes speech instead of concluding it.
    """
    gap = np.zeros(int(0.028 * sample_rate), dtype=np.float64)
    out = np.concatenate([
        _note(440.0, 0.10, sample_rate, 0.5, decay=13.0),
        gap,
        _note(660.0, 0.16, sample_rate, 0.42, decay=9.0),
    ])
    return _finish(out, sample_rate, 0.16)


def done(sample_rate: int = 24000) -> np.ndarray:
    """Something finished. A falling fourth: settled, not triumphant."""
    gap = np.zeros(int(0.022 * sample_rate), dtype=np.float64)
    out = np.concatenate([
        _note(880.0, 0.08, sample_rate, 0.42, decay=16.0),
        gap,
        _note(660.0, 0.15, sample_rate, 0.38, decay=11.0),
    ])
    return _finish(out, sample_rate, 0.13)


def error(sample_rate: int = 24000) -> np.ndarray:
    """Something failed. Low, flat, and slightly wrong.

    A minor second beating against itself. Unresolved on purpose -- it should
    sound like a problem without sounding like an alarm, because most failures
    here are a network hiccup rather than an emergency.
    """
    n = int(0.30 * sample_rate)
    t = np.arange(n) / sample_rate
    out = (np.sin(2 * np.pi * 196.0 * t)
           + 0.75 * np.sin(2 * np.pi * 208.0 * t)
           + 0.2 * np.sin(2 * np.pi * 392.0 * t))
    out *= _env(n, sample_rate, 10.0, 7.0)
    return _finish(out, sample_rate, 0.15)


PALETTE = {"attention": attention, "done": done, "error": error}


def make(name: str, sample_rate: int = 24000) -> np.ndarray | None:
    fn = PALETTE.get(name)
    if fn is None:
        return None
    try:
        return fn(sample_rate)
    except Exception:
        log.debug("could not synthesise the %s tone", name, exc_info=True)
        return None
