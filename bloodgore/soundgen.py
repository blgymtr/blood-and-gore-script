"""Gore sounds synthesized with numpy. No recordings are used."""
from __future__ import annotations

import io
import wave

import numpy as np

RATE = 22050


def _wav(x: np.ndarray) -> bytes:
    x = np.clip(x, -1, 1)
    pcm = (x * 32000).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def _lowpass(x: np.ndarray, k: int) -> np.ndarray:
    return np.convolve(x, np.ones(k) / k, mode="same")


def gib_burst(seed: int = 5) -> bytes:
    """A wet burst: a low thump, filtered noise and a few squelchy clicks."""
    rng = np.random.default_rng(seed)
    n = int(RATE * 0.75)
    t = np.arange(n) / RATE
    thump = np.sin(2 * np.pi * (95 - 55 * t / 0.3).clip(35, 95) * t) * np.exp(-t * 11)
    noise = _lowpass(rng.normal(0, 1, n), 9) * np.exp(-t * 6.5)
    wet = _lowpass(rng.normal(0, 1, n), 3) * np.exp(-((t - 0.12) ** 2) / 0.004) * 0.5
    clicks = np.zeros(n)
    for _ in range(9):
        i = int(rng.uniform(0.05, 0.6) * RATE)
        clicks[i:i + 40] += rng.normal(0, 1, 40) * np.exp(-np.arange(40) / 9.0) * 0.6
    x = thump * 0.9 + noise * 0.8 + wet + _lowpass(clicks, 3)
    return _wav(x / max(1e-6, np.abs(x).max()) * 0.9)


def limb_tear(seed: int = 9) -> bytes:
    """A short crack with a wet tear after it."""
    rng = np.random.default_rng(seed)
    n = int(RATE * 0.5)
    t = np.arange(n) / RATE
    crack = rng.normal(0, 1, n) * np.exp(-t * 90) * 1.0
    snap = np.sin(2 * np.pi * 420 * t) * np.exp(-t * 60) * 0.5
    tear = _lowpass(rng.normal(0, 1, n), 5) * np.exp(-((t - 0.14) ** 2) / 0.006) * 0.7
    thud = np.sin(2 * np.pi * 70 * t) * np.exp(-t * 14) * 0.6
    x = crack + snap + tear + thud
    return _wav(x / max(1e-6, np.abs(x).max()) * 0.9)


def silence() -> bytes:
    """A very short silent file, used when sounds are switched off so the FSM sound lines still load."""
    return _wav(np.zeros(int(RATE * 0.05)))
