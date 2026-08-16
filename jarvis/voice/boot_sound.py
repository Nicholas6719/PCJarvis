"""The sound of an arc reactor coming up to speed.

Synthesised from scratch rather than sampled. The films' actual sound design is
copyrighted, and lifting it would be both illegal and lazy -- but what makes
that sound read as *powering up* is not the recording, it is the shape:
something heavy spinning faster, electronics charging above it, and a clean
resolve when it settles. All of that is arithmetic.

Five layers, and each is doing a specific job:

    sub        a low swell that arrives before you consciously hear it, so
               the sound has weight rather than just brightness
    reactor    a harmonic stack whose fundamental sweeps upward -- the
               spinning-up-to-speed layer, and the one carrying the tension
    whine      a thin rising tone, the sound of something charging
    shimmer    filtered noise fading in, which keeps the sweep from sounding
               like a synthesiser test tone
    resolve    a perfect fifth landing at the end. The sweep is a question;
               this is the answer, and it is the moment that reads as "online"

Deliberately restrained. This plays every single launch, and a sound that is
thrilling the first time is intolerable by the twentieth -- so it sits under
the voice rather than announcing itself, and it is over in under three seconds.
"""
from __future__ import annotations

import logging

import numpy as np
from scipy import signal as _sig

log = logging.getLogger("jarvis.boot_sound")

# Halfway between the two he picked: the balanced one carried the weight,
# the bright one had the urgency. Blended by parameter rather than by summing
# the two renders -- two sweeps resolving 350ms apart smear into each other
# instead of combining, and you lose the single clean landing that makes the
# whole thing read as "online".
DURATION = 2.40         # between 2.6 and 2.2
RESOLVE_AT = 1.62       # between 1.80 and 1.45
WEIGHT = 0.85           # between 1.0 and 0.7 -- keeps some of the sub
AIR = 1.35              # between 1.0 and 1.7 -- keeps most of the shimmer
TOP = 385.0             # between 330 and 440 Hz
PEAK = 0.34             # never startling, never buried


def _fade(n: int, samples: int) -> np.ndarray:
    """A short raised-cosine fade, to keep edges from clicking."""
    if samples <= 0 or samples * 2 >= n:
        return np.ones(n, dtype=np.float32)
    ramp = 0.5 * (1 - np.cos(np.linspace(0, np.pi, samples)))
    env = np.ones(n, dtype=np.float32)
    env[:samples] = ramp
    env[-samples:] = ramp[::-1]
    return env.astype(np.float32)


def make_boot_sound(sample_rate: int = 24000, duration: float = DURATION,
                    resolve_at: float = RESOLVE_AT, weight: float = WEIGHT,
                    air: float = AIR, top: float = TOP) -> np.ndarray:
    """The power-up. Deterministic, so every launch sounds identical.

    Args:
        duration: Total length.
        resolve_at: When the fifth lands.
        weight: How much low end. Above 1 is heavier.
        air: How much shimmer and whine. Above 1 is brighter.
        top: Where the spin-up sweep ends, in Hz.
    """
    rng = np.random.default_rng(7)          # fixed: same sound every time
    n = int(duration * sample_rate)
    t = np.linspace(0, duration, n, endpoint=False, dtype=np.float64)
    DURATION_, RESOLVE_AT = duration, resolve_at
    out = np.zeros(n, dtype=np.float64)

    # ── sub ────────────────────────────────────────────────────────
    # Felt more than heard. Rises with the sweep and holds under the resolve.
    sub_f = 30 * (58 / 30) ** (t / DURATION_)
    sub_env = np.clip(t / 1.2, 0, 1) ** 1.5
    sub_env *= np.where(t > RESOLVE_AT, np.exp(-(t - RESOLVE_AT) * 1.6), 1.0)
    out += 0.42 * weight * np.sin(2 * np.pi * np.cumsum(sub_f) / sample_rate) * sub_env

    # ── reactor spin-up ────────────────────────────────────────────
    # Exponential so it accelerates the way a rotating thing does. The
    # partials thin out as it climbs, which stops the top end getting harsh.
    spin_f = 118 * (top / 118) ** (t / RESOLVE_AT).clip(0, 1)
    phase = 2 * np.pi * np.cumsum(spin_f) / sample_rate
    spin_env = np.clip(t / RESOLVE_AT, 0, 1) ** 1.8
    spin_env *= np.where(t > RESOLVE_AT, np.exp(-(t - RESOLVE_AT) * 5.0), 1.0)
    for harmonic, amp in ((1, 0.30), (2, 0.16), (3, 0.09), (4, 0.045)):
        out += amp * np.sin(harmonic * phase) * spin_env

    # A slow beat between two barely-detuned partials: the sound stops being
    # a plain tone and starts sounding like a machine with moving parts.
    out += 0.05 * np.sin(2.004 * phase) * spin_env

    # ── charging whine ─────────────────────────────────────────────
    whine_f = 900 * (3000 / 900) ** (t / RESOLVE_AT).clip(0, 1)
    whine_env = (np.clip((t - 0.25) / 1.3, 0, 1) ** 2) * 0.055 * air
    whine_env *= np.where(t > RESOLVE_AT, np.exp(-(t - RESOLVE_AT) * 9.0), 1.0)
    out += np.sin(2 * np.pi * np.cumsum(whine_f) / sample_rate) * whine_env

    # ── shimmer ────────────────────────────────────────────────────
    # Filtered noise. Without it the sweep sounds like a test tone; with it,
    # like air being moved.
    noise = rng.standard_normal(n)
    sos = _sig.butter(4, [2200 / (sample_rate / 2), 7000 / (sample_rate / 2)],
                      btype="bandpass", output="sos")
    shimmer = _sig.sosfilt(sos, noise)
    shimmer_env = (np.clip((t - 0.15) / 1.5, 0, 1) ** 2) * 0.05 * air
    shimmer_env *= (1 + 0.35 * np.sin(2 * np.pi * 7.0 * t))   # faint flutter
    shimmer_env *= np.where(t > RESOLVE_AT, np.exp(-(t - RESOLVE_AT) * 3.0), 1.0)
    out += shimmer * shimmer_env

    # ── the resolve ────────────────────────────────────────────────
    # A perfect fifth with the octave above it. The sweep poses a question;
    # this answers it, and it is the moment that means "online".
    after = np.maximum(t - RESOLVE_AT, 0)
    strike = (after > 0).astype(np.float64)
    decay = np.exp(-after * 2.2) * strike
    attack = np.clip(after / 0.006, 0, 1)
    for freq, amp in ((330.0, 0.20), (495.0, 0.15), (660.0, 0.10), (990.0, 0.04)):
        out += amp * np.sin(2 * np.pi * freq * after) * decay * attack

    # The tiny transient of something latching into place.
    click_env = np.exp(-after * 90.0) * strike
    out += 0.10 * _sig.sosfilt(
        _sig.butter(2, 1800 / (sample_rate / 2), btype="highpass", output="sos"),
        noise) * click_env

    # ── finish ─────────────────────────────────────────────────────
    # Roll the top off so it sits behind the voice instead of in front of it.
    out = _sig.sosfilt(
        _sig.butter(3, 9000 / (sample_rate / 2), btype="lowpass", output="sos"),
        out)
    out *= _fade(n, int(0.02 * sample_rate))

    # A little room, so it sounds like it is happening somewhere rather than
    # inside the speaker. Falls through untouched if the chain is unavailable.
    try:
        from . import jarvis_chain

        out = jarvis_chain.room(
            out.astype(np.float32),
            {"enabled": True, "mix": 0.18, "decay_s": 0.55,
             "predelay_ms": 14.0, "damping": 0.42},
            sample_rate, None)
    except Exception:
        log.debug("no reverb available for the boot sound", exc_info=True)

    out = np.nan_to_num(np.asarray(out, dtype=np.float32))
    peak = float(np.max(np.abs(out))) or 1.0
    return (out * (PEAK / peak)).astype(np.float32)
