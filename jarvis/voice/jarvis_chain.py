"""The JARVIS voice chain.

Kokoro gives us a clean, well-spoken British male read. That is not the same
thing as sounding like the films. What separates the two is post-treatment:
the JARVIS of the films sits slightly lower, is EQ'd for articulation, is
compressed to an unhurried and perfectly level delivery, is faintly doubled so
he reads as synthetic rather than human, and -- most importantly -- sits in a
small hard room rather than inside your head.

Signal flow:
    pitch/formant -> EQ -> compressor -> doubler -> room -> limiter

Everything is numpy/scipy and runs in a few milliseconds.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from scipy import signal

log = logging.getLogger("jarvis.voice.chain")

EPS = 1e-9


# ==================================================================
#  1. Pitch and formant
# ==================================================================
def _phase_vocoder_stretch(x: np.ndarray, rate: float, n_fft: int = 2048) -> np.ndarray:
    """Time-stretch by `rate` (>1 = longer) while preserving pitch."""
    hop = n_fft // 4
    window = np.hanning(n_fft).astype(np.float32)
    padded = np.pad(x, (n_fft, n_fft))

    stft = np.array(
        [
            np.fft.rfft(padded[i:i + n_fft] * window)
            for i in range(0, len(padded) - n_fft, hop)
        ]
    )
    if stft.shape[0] < 2:
        return x.copy()

    magnitude = np.abs(stft)
    phase = np.angle(stft)
    # Expected phase advance per hop, per bin.
    expected = 2.0 * np.pi * hop * np.arange(stft.shape[1]) / n_fft

    positions = np.arange(0, stft.shape[0] - 1, 1.0 / rate)
    out_phase = phase[0].copy()
    frames = []
    for pos in positions:
        i = int(pos)
        frac = pos - i
        mag = (1 - frac) * magnitude[i] + frac * magnitude[i + 1]
        delta = phase[i + 1] - phase[i] - expected
        delta = np.mod(delta + np.pi, 2 * np.pi) - np.pi  # wrap to [-pi, pi]
        frames.append(np.fft.irfft(mag * np.exp(1j * out_phase)) * window)
        out_phase = out_phase + expected + delta

    out = np.zeros(len(frames) * hop + n_fft, dtype=np.float32)
    norm = np.zeros_like(out)
    for i, frame in enumerate(frames):
        out[i * hop:i * hop + n_fft] += frame
        norm[i * hop:i * hop + n_fft] += window ** 2
    out /= np.maximum(norm, EPS)
    return out[n_fft:-n_fft] if len(out) > 2 * n_fft else out


def _warp_formants(x: np.ndarray, scale: float, n_fft: int = 1024) -> np.ndarray:
    """Shift the spectral envelope without touching pitch.

    Pitch and formants normally move together, and that coupling is exactly what
    makes a naive pitch shift sound like a cartoon. We separate the envelope
    from the harmonics by cepstral smoothing, stretch only the envelope, and put
    the harmonics back where they were.
    """
    if abs(scale - 1.0) < 1e-3:
        return x

    hop = n_fft // 4
    window = np.hanning(n_fft).astype(np.float32)
    padded = np.pad(x, (n_fft, n_fft))
    out = np.zeros_like(padded)
    norm = np.zeros_like(padded)
    bins = np.arange(n_fft // 2 + 1)

    for i in range(0, len(padded) - n_fft, hop):
        spectrum = np.fft.rfft(padded[i:i + n_fft] * window)
        mag = np.abs(spectrum)

        # Envelope = cepstrally smoothed log magnitude (low quefrencies only).
        log_mag = np.log(mag + EPS)
        cepstrum = np.fft.irfft(log_mag)
        cepstrum[40:-40] = 0
        envelope = np.exp(np.fft.rfft(cepstrum).real)

        warped = np.interp(bins / scale, bins, envelope,
                           left=envelope[0], right=envelope[-1])
        shaped = spectrum * (warped / (envelope + EPS))

        out[i:i + n_fft] += np.fft.irfft(shaped) * window
        norm[i:i + n_fft] += window ** 2

    out /= np.maximum(norm, EPS)
    return out[n_fft:-n_fft].astype(np.float32)


def pitch_shift(x: np.ndarray, semitones: float,
                formant_scale: float = 1.0) -> np.ndarray:
    """Shift pitch by `semitones`, then independently scale the formants."""
    if abs(semitones) > 1e-3:
        ratio = 2.0 ** (semitones / 12.0)
        stretched = _phase_vocoder_stretch(x, 1.0 / ratio)
        # Resampling back to the original length is what moves the pitch.
        x = signal.resample(stretched, len(x)).astype(np.float32)
    return _warp_formants(x, formant_scale)


# ==================================================================
#  2. EQ  (RBJ cookbook biquads)
# ==================================================================
def _peaking(freq: float, gain_db: float, q: float, sr: int) -> np.ndarray:
    A = 10 ** (gain_db / 40)
    w0 = 2 * np.pi * freq / sr
    alpha = np.sin(w0) / (2 * q)
    cos_w0 = np.cos(w0)
    b = [1 + alpha * A, -2 * cos_w0, 1 - alpha * A]
    a = [1 + alpha / A, -2 * cos_w0, 1 - alpha / A]
    return np.array([[*b, *a]]) / a[0]


def apply_eq(x: np.ndarray, cfg: dict, sr: int) -> np.ndarray:
    hp = cfg.get("highpass_hz")
    if hp:
        sos = signal.butter(2, hp / (sr / 2), btype="highpass", output="sos")
        x = signal.sosfilt(sos, x)
    for band in ("low_mid", "presence", "air"):
        b = cfg.get(band)
        if not b or abs(b.get("gain_db", 0)) < 1e-3:
            continue
        freq = min(b["freq"], sr / 2 * 0.95)
        x = signal.sosfilt(_peaking(freq, b["gain_db"], b.get("q", 0.7), sr), x)
    return x.astype(np.float32)


# ==================================================================
#  3. Compressor
# ==================================================================
def compress(x: np.ndarray, cfg: dict, sr: int, control_decim: int = 8) -> np.ndarray:
    """Feed-forward compressor with a dual-slope envelope follower.

    The detector runs at 1/8 rate and the resulting gain curve is interpolated
    back up. At these time constants that is inaudible, and eight times cheaper.
    """
    threshold = cfg.get("threshold_db", -20.0)
    ratio = cfg.get("ratio", 3.0)
    attack = np.exp(-1.0 / (max(cfg.get("attack_ms", 8.0), 0.1)
                            * 0.001 * sr / control_decim))
    release = np.exp(-1.0 / (max(cfg.get("release_ms", 120.0), 1.0)
                             * 0.001 * sr / control_decim))

    ctrl = np.abs(x[::control_decim])
    env = np.empty_like(ctrl)
    prev = 0.0
    for i, sample in enumerate(ctrl):
        coeff = attack if sample > prev else release
        prev = coeff * prev + (1 - coeff) * sample
        env[i] = prev

    level_db = 20 * np.log10(env + EPS)
    over = np.maximum(level_db - threshold, 0.0)
    gain_db = -over * (1 - 1 / ratio)

    gain = np.interp(np.arange(len(x)),
                     np.arange(len(ctrl)) * control_decim, gain_db)
    makeup = cfg.get("makeup_db", 0.0)
    return (x * 10 ** ((gain + makeup) / 20)).astype(np.float32)


# ==================================================================
#  4. Doubler -- the faint "not quite one person" quality
# ==================================================================
def double(x: np.ndarray, cfg: dict, sr: int) -> np.ndarray:
    if not cfg.get("enabled", True) or cfg.get("mix", 0) <= 0:
        return x
    delay = int(cfg.get("delay_ms", 15.0) * 0.001 * sr)
    detune = 2.0 ** (cfg.get("detune_cents", 7.0) / 1200.0)

    voice2 = signal.resample(x, max(1, int(len(x) / detune)))
    voice2 = np.pad(voice2, (delay, 0))[:len(x)]
    if len(voice2) < len(x):
        voice2 = np.pad(voice2, (0, len(x) - len(voice2)))

    mix = cfg.get("mix", 0.18)
    return ((1 - mix * 0.5) * x + mix * voice2).astype(np.float32)


# ==================================================================
#  5. Room -- where he actually is
# ==================================================================
def build_impulse_response(cfg: dict, sr: int) -> np.ndarray:
    """Synthesize a small, hard-surfaced room.

    Early reflections give the space its size; the decaying diffuse tail with
    progressive high-frequency damping gives it its material.
    """
    decay = cfg.get("decay_s", 0.40)
    damping = cfg.get("damping", 0.35)
    predelay = int(cfg.get("predelay_ms", 12.0) * 0.001 * sr)
    n = int(decay * sr) + predelay

    rng = np.random.default_rng(1970)  # deterministic: the same room every launch
    ir = rng.standard_normal(n).astype(np.float32)
    t = np.arange(n) / sr
    ir *= np.exp(-6.9 * t / decay)  # -60dB across the decay time

    # Discrete early reflections off nearby surfaces.
    for delay_ms, gain in ((7, 0.55), (11, 0.42), (17, 0.33), (23, 0.26), (31, 0.18)):
        idx = predelay + int(delay_ms * 0.001 * sr)
        if idx < n:
            ir[idx] += gain

    # Damping: high frequencies die first, as they do in a real room.
    sos = signal.butter(2, max(0.05, 1.0 - damping) * 0.9,
                        btype="lowpass", output="sos")
    ir = signal.sosfilt(sos, ir).astype(np.float32)

    ir[:predelay] = 0.0
    ir[0] = 1.0  # the dry impulse keeps the direct sound intact
    return ir / (np.sqrt(np.sum(ir ** 2)) + EPS)


_IR_CACHE: dict[tuple, np.ndarray] = {}


def room(x: np.ndarray, cfg: dict, sr: int, ir_path: Path | None = None) -> np.ndarray:
    if not cfg.get("enabled", True) or cfg.get("mix", 0) <= 0:
        return x

    key = (sr, cfg.get("decay_s"), cfg.get("damping"), cfg.get("predelay_ms"))
    if key not in _IR_CACHE:
        ir = None
        if ir_path and ir_path.exists():
            try:
                import soundfile as sf
                ir, ir_sr = sf.read(str(ir_path), dtype="float32", always_2d=False)
                if ir.ndim > 1:
                    ir = ir.mean(axis=1)
                if ir_sr != sr:
                    ir = signal.resample(ir, int(len(ir) * sr / ir_sr))
                ir = ir / (np.sqrt(np.sum(ir ** 2)) + EPS)
                log.info("using impulse response %s", ir_path.name)
            except Exception:
                log.warning("could not read %s; synthesizing a room instead", ir_path)
                ir = None
        if ir is None:
            ir = build_impulse_response(cfg, sr)
        _IR_CACHE[key] = ir

    wet = signal.fftconvolve(x, _IR_CACHE[key])[:len(x)]
    mix = cfg.get("mix", 0.14)
    return ((1 - mix) * x + mix * wet).astype(np.float32)


# ==================================================================
#  6. Limiter
# ==================================================================
def limit(x: np.ndarray, cfg: dict) -> np.ndarray:
    target = 10 ** (cfg.get("target_lufs_db", -16.0) / 20)
    rms = np.sqrt(np.mean(x ** 2)) + EPS
    x = x * min(target / rms, 8.0)  # cap the boost so near-silence cannot explode

    ceiling = 10 ** (cfg.get("ceiling_db", -1.0) / 20)
    peak = np.max(np.abs(x))
    if peak > ceiling:
        x = np.tanh(x / peak * 1.4) * ceiling / np.tanh(1.4)  # soft knee
    return x.astype(np.float32)


# ==================================================================
#  The chain
# ==================================================================
def apply_chain(audio: np.ndarray, sr: int, cfg: dict,
                ir_path: Path | None = None) -> np.ndarray:
    """Run raw TTS output through the full JARVIS treatment."""
    if not cfg.get("enabled", True) or audio.size == 0:
        return audio

    x = audio.astype(np.float32)
    p = cfg.get("pitch", {})
    x = pitch_shift(x, p.get("semitones", 0.0), p.get("formant_scale", 1.0))
    x = apply_eq(x, cfg.get("eq", {}), sr)
    x = compress(x, cfg.get("compressor", {}), sr)
    x = double(x, cfg.get("doubler", {}), sr)
    x = room(x, cfg.get("room", {}), sr, ir_path)
    x = limit(x, cfg.get("limiter", {}))
    return np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)



