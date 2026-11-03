"""Blood, flesh and bone sprites drawn by code. No outside art is used.

Every function returns a finished BGRA sprite (see imaging.finish_sprite): drawn at 4x, averaged down,
silhouette rounded and torn, never a rectangle.
"""
from __future__ import annotations

import math

import numpy as np

from . import imaging as im
from .imaging import SS, cv2

# BGR colours
BLOOD_DARK = np.array((12, 6, 72), np.float32)
BLOOD = np.array((22, 12, 150), np.float32)
BLOOD_LIGHT = np.array((46, 40, 214), np.float32)
BLOOD_SPEC = np.array((150, 140, 255), np.float32)
FLESH_DARK = np.array((26, 20, 112), np.float32)
FLESH = np.array((66, 56, 184), np.float32)
FLESH_LIGHT = np.array((118, 108, 226), np.float32)
FAT = np.array((168, 198, 236), np.float32)
BONE = np.array((196, 224, 236), np.float32)
BONE_SHADE = np.array((120, 156, 184), np.float32)


def blob_mask(h: int, w: int, rng: np.random.Generator, lumps: int = 6, fill: float = 0.62, rough: float = 0.5) -> np.ndarray:
    """Irregular lumpy mask, bool (h, w)."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    f = np.zeros((h, w), np.float32)
    for _ in range(lumps):
        cx, cy = rng.uniform(0.3, 0.7) * w, rng.uniform(0.3, 0.7) * h
        rx, ry = rng.uniform(0.16, 0.30) * w, rng.uniform(0.16, 0.30) * h
        f = np.maximum(f, 1 - np.sqrt(((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2))
    f += im.fractal_noise(h, w, min(h, w) / 3.0, rng, 3) * rough * 0.35
    return im.smooth_mask(f > (1 - fill) - 0.4, SS * 0.7)


def shade(mask: np.ndarray, rng: np.random.Generator, base=BLOOD, dark=BLOOD_DARK, light=BLOOD_LIGHT, gloss: float = 1.0,
          speckle: float = 0.10) -> np.ndarray:
    """BGRA 4x image: rim dark, middle base, inner light, one glossy highlight, a little mottling."""
    h, w = mask.shape
    dist = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 3)
    k = np.clip(dist / max(float(dist.max()), 1.0), 0, 1)[..., None]
    col = dark + (base - dark) * np.clip(k * 2.4, 0, 1) + (light - base) * np.clip(k * 2 - 0.9, 0, 1)
    if speckle:
        col = col * (1 + im.fractal_noise(h, w, SS * 2.0, rng, 2)[..., None] * speckle)
    if gloss > 0 and mask.any():
        ys, xs = np.nonzero(mask)
        gx = xs.min() + (xs.max() - xs.min()) * 0.34
        gy = ys.min() + (ys.max() - ys.min()) * 0.30
        gr = max(2.0, min(xs.max() - xs.min(), ys.max() - ys.min()) * 0.13)
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        g = np.clip(1 - np.hypot((xx - gx) / (gr * 1.25), (yy - gy) / gr), 0, 1)[..., None] * gloss
        col = col * (1 - g) + BLOOD_SPEC * g
    out = np.zeros((h, w, 4), np.uint8)
    out[..., :3] = np.clip(col, 0, 255)
    out[..., 3] = mask.astype(np.uint8) * 255
    return out


def _canvas(px: int) -> int:
    return int(px) * SS


def drop(size: int, kind: str, rng: np.random.Generator) -> np.ndarray:
    """A blood drop sprite about `size` px tall. kind: round, tear, streak, clot."""
    pad = 3
    s = (size + 2 * pad)
    h = w = _canvas(s)
    mask = np.zeros((h, w), np.uint8)
    cx, cy = w // 2, h // 2
    r = size * SS * 0.5
    if kind == "round":
        cv2.circle(mask, (cx, cy), int(r * rng.uniform(0.8, 0.95)), 1, -1)
    elif kind == "tear":
        rr = r * 0.62
        hy = cy + int(r * 0.32)
        cv2.circle(mask, (cx, hy), int(rr), 1, -1)
        tip = (cx + int(rng.uniform(-0.1, 0.1) * rr), cy - int(r * 0.95))
        cv2.fillPoly(mask, [np.array([tip, (cx - int(rr * 0.92), hy - int(rr * 0.25)), (cx + int(rr * 0.92), hy - int(rr * 0.25))], np.int32)], 1)
    elif kind == "streak":
        cv2.ellipse(mask, (cx, cy), (max(2, int(r * 0.32)), int(r * 0.95)), rng.uniform(-8, 8), 0, 360, 1, -1)
        cv2.circle(mask, (cx, cy + int(r * 0.45)), max(2, int(r * 0.36)), 1, -1)
    else:  # clot
        m = blob_mask(h, w, rng, lumps=5, fill=0.52, rough=0.9)
        mask = m.astype(np.uint8)
        mask = cv2.erode(mask, np.ones((3, 3), np.uint8), iterations=int(SS * 0.3))
    m = im.smooth_mask(mask.astype(bool), SS * 0.9)
    big = shade(m, rng, gloss=0.9 if size >= 6 else 0.5)
    return im.finish_sprite(big, square=True)


def mist(size: int, rng: np.random.Generator) -> np.ndarray:
    """A thin red spray: many tiny dots inside a soft round envelope (dithered, no half transparent pixels)."""
    s = size + 6
    h = w = _canvas(s)
    big = np.zeros((h, w, 4), np.uint8)
    cx = cy = h / 2.0
    R = size * SS / 2.0
    n = int(size * size * 1.3)
    for _ in range(n):
        ang = rng.uniform(0, 2 * math.pi)
        rad = R * (rng.uniform(0, 1) ** 0.65) * rng.uniform(0.6, 1.0)
        x, y = cx + math.cos(ang) * rad, cy + math.sin(ang) * rad
        rr = int(SS * rng.choice([0.5, 0.5, 0.75, 1.0]))
        col = BLOOD_LIGHT if rng.uniform() < 0.35 else BLOOD
        cv2.circle(big, (int(x), int(y)), max(1, rr), tuple(int(c) for c in col) + (255,), -1)
    return im.finish_sprite(big, square=True)


def splat_air(size: int, rng: np.random.Generator) -> np.ndarray:
    """A small star-shaped splat with streaks and satellite dots."""
    s = size + 6
    h = w = _canvas(s)
    mask = np.zeros((h, w), np.uint8)
    c = h // 2
    R = size * SS / 2.0
    cv2.circle(mask, (c, c), int(R * 0.45), 1, -1)
    for _ in range(int(rng.integers(6, 10))):
        a = rng.uniform(0, 2 * math.pi)
        ln = R * rng.uniform(0.6, 0.98)
        wd = R * rng.uniform(0.07, 0.16)
        p = (c + math.cos(a) * ln, c + math.sin(a) * ln)
        q1 = (c + math.cos(a + 1.57) * wd * 2.2, c + math.sin(a + 1.57) * wd * 2.2)
        q2 = (c + math.cos(a - 1.57) * wd * 2.2, c + math.sin(a - 1.57) * wd * 2.2)
        cv2.fillPoly(mask, [np.array([p, q1, q2], np.int32)], 1)
        cv2.circle(mask, (int(p[0]), int(p[1])), max(1, int(wd * 1.1)), 1, -1)
    m = im.smooth_mask(mask.astype(bool), SS * 0.8)
    return im.finish_sprite(shade(m, rng, gloss=0.6), square=True)


def pool(width: int, rng: np.random.Generator, spread: float = 1.0) -> np.ndarray:
    """A wide flat puddle with lobes and satellite drops, seen from a low angle (the engine draws decorations as upright sprites)."""
    w = _canvas(max(width, 12))
    h = int(w * 0.42)
    pad = int(0.1 * w)
    W, H = w + 2 * pad, h + 2 * pad
    mask = np.zeros((H, W), np.uint8)
    cx, cy = W // 2, H // 2 + int(h * 0.1)
    cv2.ellipse(mask, (cx, cy), (int(w * 0.36 * spread), int(h * 0.30)), 0, 0, 360, 1, -1)
    for _ in range(int(rng.integers(4, 8))):
        a = rng.uniform(0, 2 * math.pi)
        d = rng.uniform(0.25, 0.42) * w * spread
        px = cx + math.cos(a) * d
        py = cy + math.sin(a) * d * 0.38
        cv2.ellipse(mask, (int(px), int(py)), (int(rng.uniform(0.06, 0.14) * w), int(rng.uniform(0.05, 0.11) * h)), rng.uniform(-30, 30), 0, 360, 1, -1)
    for _ in range(int(rng.integers(4, 9))):
        a = rng.uniform(0, 2 * math.pi)
        d = rng.uniform(0.38, 0.5) * w
        cv2.circle(mask, (int(cx + math.cos(a) * d), int(cy + math.sin(a) * d * 0.36)), max(1, int(rng.uniform(0.012, 0.03) * w)), 1, -1)
    n = im.fractal_noise(H, W, w / 7.0, rng, 3)
    m = im.smooth_mask(cv2.GaussianBlur(mask.astype(np.float32), (0, 0), SS * 1.2) + n * 0.12 > 0.46, SS * 0.8)
    big = shade(m, rng, gloss=0.0, speckle=0.08)
    # a glossy streak across the middle makes it read as liquid
    ys, xs = np.nonzero(m)
    if len(xs):
        gx0, gx1 = xs.min() + (xs.max() - xs.min()) * 0.22, xs.min() + (xs.max() - xs.min()) * 0.5
        gy = ys.min() + (ys.max() - ys.min()) * 0.36
        gl = np.zeros_like(m, np.uint8)
        cv2.ellipse(gl, (int((gx0 + gx1) / 2), int(gy)), (int((gx1 - gx0) / 2), max(1, int(h * 0.035))), -6, 0, 360, 1, -1)
        sel = (gl > 0) & m
        big[sel, :3] = (big[sel, :3] * 0.4 + BLOOD_SPEC * 0.6).astype(np.uint8)
    return im.finish_sprite(big)


def flesh_chunk(size: int, rng: np.random.Generator, with_fat: bool = True) -> np.ndarray:
    """A torn lump of meat with a dark rim, a glossy top and a little pale fat."""
    s = size + 6
    h = w = _canvas(s)
    m = blob_mask(h, w, rng, lumps=int(rng.integers(4, 7)), fill=0.55, rough=1.0)
    big = shade(m, rng, base=FLESH, dark=FLESH_DARK, light=FLESH_LIGHT, gloss=0.55, speckle=0.16)
    if with_fat and m.any():
        n = im.fractal_noise(h, w, SS * 3.0, rng, 2)
        fatm = m & (n > 0.55) & (cv2.distanceTransform(m.astype(np.uint8), cv2.DIST_L2, 3) > SS)
        big[fatm, :3] = FAT
    return im.finish_sprite(big, square=True)


def bone_shard(size: int, rng: np.random.Generator) -> np.ndarray:
    """A splintered piece of bone with a pink marrow end."""
    s = size + 6
    h = w = _canvas(s)
    mask = np.zeros((h, w), np.uint8)
    c = h // 2
    L = size * SS * 0.48
    ang = rng.uniform(-0.6, 0.6)
    dx, dy = math.cos(ang), math.sin(ang)
    nx, ny = -dy, dx
    wd = size * SS * rng.uniform(0.09, 0.15)
    pts = []
    for t, f in ((-1.0, 0.6), (-0.6, 1.0), (0.0, 0.85), (0.55, 1.1), (1.0, 0.35)):
        pts.append((c + dx * L * t + nx * wd * f, c + dy * L * t + ny * wd * f))
    for t, f in ((0.9, 0.7), (0.4, 1.0), (-0.1, 0.95), (-0.65, 1.1), (-1.0, 0.5)):
        pts.append((c + dx * L * t - nx * wd * f, c + dy * L * t - ny * wd * f))
    cv2.fillPoly(mask, [np.array(pts, np.int32)], 1)
    cv2.circle(mask, (int(c + dx * L), int(c + dy * L)), int(wd * 1.1), 1, -1)
    m = im.smooth_mask(mask.astype(bool), SS * 0.7)
    big = shade(m, rng, base=BONE, dark=BONE_SHADE, light=np.array((230, 244, 250), np.float32), gloss=0.0, speckle=0.05)
    cv2.circle(big, (int(c + dx * L), int(c + dy * L)), int(wd * 0.7), tuple(int(v) for v in FLESH_LIGHT) + (255,), -1)
    big[..., 3] = np.where(m, 255, 0)
    return im.finish_sprite(big, square=True)


def flesh_palette_at(t: np.ndarray, rng_noise: np.ndarray) -> np.ndarray:
    """Colour of a torn flesh surface for a depth value t in 0 (outer rim) .. 1 (middle), shape (..., 3) BGR."""
    t = np.clip(t, 0, 1)[..., None]
    col = FLESH_DARK + (FLESH - FLESH_DARK) * np.clip(t * 2.2, 0, 1) + (FLESH_LIGHT - FLESH) * np.clip(t * 2 - 1.0, 0, 1)
    return col * (1 + rng_noise[..., None] * 0.14)
