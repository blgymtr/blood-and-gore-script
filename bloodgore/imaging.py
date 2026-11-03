"""Small image helpers: PNG input/output, noise, supersampling and the organic-silhouette rules.

All generated sprites are drawn at 4x and averaged down so their edges are anti-aliased.
"""
from __future__ import annotations

import numpy as np

try:
    import cv2
except ImportError as exc:   # pragma: no cover
    raise SystemExit("opencv-python is required: pip install -r requirements.txt") from exc

SS = 4                 # supersampling factor
ALPHA_ON = 16          # alpha above this counts as an opaque pixel in the organic checks
MAX_FILL = 0.80        # the generator keeps the opaque fill of the bounding box below this
TEST_MAX_FILL = 0.85   # the automated test accepts anything below this


def read_png(path: str) -> np.ndarray:
    """BGRA uint8 image from a PNG path (works with non-ASCII paths)."""
    data = np.fromfile(path, np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError("not a readable image: " + path)
    if img.dtype == np.uint16:
        img = (img // 257).astype(np.uint8)
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
    elif img.shape[2] == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    return img


def encode_png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img, [cv2.IMWRITE_PNG_COMPRESSION, 9])
    if not ok:
        raise ValueError("png encoding failed")
    return buf.tobytes()


def decode_png(data: bytes) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError("not a png")
    return img


def value_noise(h: int, w: int, cell: float, rng: np.random.Generator) -> np.ndarray:
    """Smooth noise in [-1, 1], features about `cell` pixels wide."""
    gh, gw = max(2, int(h / cell) + 2), max(2, int(w / cell) + 2)
    g = rng.uniform(-1, 1, (gh, gw)).astype(np.float32)
    n = cv2.resize(g, (w, h), interpolation=cv2.INTER_CUBIC)
    return np.clip(n, -1, 1)


def fractal_noise(h: int, w: int, cell: float, rng: np.random.Generator, octaves: int = 3) -> np.ndarray:
    out = np.zeros((h, w), np.float32)
    amp, tot = 1.0, 0.0
    for o in range(octaves):
        out += amp * value_noise(h, w, max(1.5, cell / (2 ** o)), rng)
        tot += amp
        amp *= 0.5
    return out / tot


def downsample_rgba(big: np.ndarray, f: int = SS) -> np.ndarray:
    """Averages f x f blocks of a BGRA image, premultiplied so colours do not bleed from transparent pixels."""
    h, w = big.shape[:2]
    h2, w2 = h // f, w // f
    big = big[:h2 * f, :w2 * f].astype(np.float32)
    a = big[..., 3:4] / 255.0
    pm = np.concatenate([big[..., :3] * a, big[..., 3:4]], axis=2)
    small = cv2.resize(pm, (w2, h2), interpolation=cv2.INTER_AREA)
    alpha = small[..., 3:4]
    col = np.where(alpha > 0.5, small[..., :3] / np.maximum(alpha / 255.0, 1e-3), 0)
    out = np.concatenate([np.clip(col, 0, 255), np.clip(alpha, 0, 255)], axis=2)
    return np.round(out).astype(np.uint8)


def bleed_colors(img: np.ndarray) -> np.ndarray:
    """Gives transparent and half transparent pixels the colour of the nearest solid pixel (no dark halos)."""
    a = img[..., 3]
    solid = a >= 200
    if solid.all() or not solid.any():
        return img
    inv = (~solid).astype(np.uint8)
    _dist, labels = cv2.distanceTransformWithLabels(inv, cv2.DIST_L2, 3, labelType=cv2.DIST_LABEL_PIXEL)
    ys, xs = np.nonzero(solid)
    lab_of_solid = labels[ys, xs]
    lut = np.zeros((labels.max() + 1, 3), np.uint8)
    lut[lab_of_solid] = img[ys, xs, :3]
    out = img.copy()
    low = ~solid
    out[low, :3] = lut[labels[low]]
    return out


def alpha_bbox(img: np.ndarray, thr: int = ALPHA_ON):
    ys, xs = np.nonzero(img[..., 3] > thr)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def trim(img: np.ndarray, pad: int = 1) -> np.ndarray:
    """Crops to the opaque area plus `pad` transparent pixels on every side."""
    bb = alpha_bbox(img)
    if bb is None:
        return img
    x0, y0, x1, y1 = bb
    crop = img[y0:y1 + 1, x0:x1 + 1]
    return cv2.copyMakeBorder(crop, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(0, 0, 0, 0))


def pad_to_square(img: np.ndarray) -> np.ndarray:
    """Centres the image on a square transparent canvas (the engine needs square particle images)."""
    h, w = img.shape[:2]
    s = max(h, w)
    top, left = (s - h) // 2, (s - w) // 2
    return cv2.copyMakeBorder(img, top, s - h - top, left, s - w - left, cv2.BORDER_CONSTANT, value=(0, 0, 0, 0))


def corner_size(h: int, w: int) -> int:
    return max(2, int(np.ceil(min(h, w) * 0.15)))


def organic_report(img: np.ndarray) -> dict:
    """Facts the organic-silhouette rules are about: opaque pixels in the corner squares and the fill of the bounding box."""
    a = img[..., 3]
    h, w = a.shape
    c = corner_size(h, w)
    corners = [a[:c, :c], a[:c, w - c:], a[h - c:, :c], a[h - c:, w - c:]]
    corner_opaque = int(sum(int((q > ALPHA_ON).sum()) for q in corners))
    bb = alpha_bbox(img)
    if bb is None:
        return dict(corner_opaque=corner_opaque, fill=0.0, bbox=None, empty=True)
    x0, y0, x1, y1 = bb
    box = a[y0:y1 + 1, x0:x1 + 1] > ALPHA_ON
    return dict(corner_opaque=corner_opaque, fill=float(box.mean()), bbox=bb, empty=False)


def is_organic(img: np.ndarray, max_fill: float = MAX_FILL) -> bool:
    r = organic_report(img)
    return (not r["empty"]) and r["corner_opaque"] == 0 and r["fill"] < max_fill


def _superellipse_alpha(h: int, w: int, bb, n: float, k: float, soft: float) -> np.ndarray:
    x0, y0, x1, y1 = bb
    cx, cy = (x0 + x1 + 1) / 2.0, (y0 + y1 + 1) / 2.0
    ax, ay = max((x1 - x0 + 1) / 2.0, 1.0), max((y1 - y0 + 1) / 2.0, 1.0)
    xx = (np.arange(w * SS, dtype=np.float32) + 0.5) / SS
    yy = (np.arange(h * SS, dtype=np.float32) + 0.5) / SS
    u = np.abs((xx - cx) / ax)[None, :]
    v = np.abs((yy - cy) / ay)[:, None]
    d = (u ** n + v ** n) ** (1.0 / n)
    m = np.clip((k - d) / soft, 0, 1).astype(np.float32)
    return cv2.resize(m, (w, h), interpolation=cv2.INTER_AREA)


def enforce_organic(img: np.ndarray, max_fill: float = MAX_FILL) -> np.ndarray:
    """Rounds the silhouette until the corner squares are empty and the bounding box fill is below max_fill.

    The mask is a soft superellipse around the opaque area, so the result stays organic and anti-aliased.
    """
    if organic_report(img)["empty"]:
        return img
    if is_organic(img, max_fill):
        return img
    pad = max(2, int(0.12 * max(img.shape[:2])))
    img = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(0, 0, 0, 0))
    h, w = img.shape[:2]
    out = img
    for k, n in ((1.30, 2.6), (1.22, 2.6), (1.15, 2.6), (1.08, 2.6), (1.02, 2.6), (0.97, 2.6), (0.93, 2.6), (0.89, 2.6), (0.85, 2.6), (0.80, 2.6), (0.76, 2.3), (0.72, 2.2), (0.68, 2.0), (0.64, 2.0)):
        bb = alpha_bbox(img)
        out = img.copy()
        m = _superellipse_alpha(h, w, bb, n, k, max(0.12, 1.5 / max(bb[2] - bb[0] + 1, 4)))
        out[..., 3] = np.round(out[..., 3].astype(np.float32) * m).astype(np.uint8)
        out = trim(out, 1)
        for _ in range(4):   # grow the canvas a little until the corner squares are clear
            if organic_report(out)["corner_opaque"] == 0:
                break
            hh, ww = out.shape[:2]
            g = max(1, int(0.08 * max(hh, ww)))
            out = cv2.copyMakeBorder(out, g, g, g, g, cv2.BORDER_CONSTANT, value=(0, 0, 0, 0))
        if is_organic(out, max_fill):
            return out
    return clear_corners(out)


def clear_corners(img: np.ndarray) -> np.ndarray:
    """Last resort: makes the four corner squares fully transparent."""
    out = img.copy()
    h, w = out.shape[:2]
    c = corner_size(h, w)
    out[:c, :c, 3] = 0
    out[:c, w - c:, 3] = 0
    out[h - c:, :c, 3] = 0
    out[h - c:, w - c:, 3] = 0
    return out


def finish_sprite(big: np.ndarray, square: bool = False, max_fill: float = MAX_FILL, f: int = SS) -> np.ndarray:
    """f-times supersampled BGRA drawing -> final sprite: downsample, remove colour halos, enforce the organic rules."""
    small = downsample_rgba(big, f)
    small = bleed_colors(small)
    small[..., 3][small[..., 3] < 6] = 0
    small = enforce_organic(trim(small, 1), max_fill)
    small = bleed_colors(small)
    if square:
        small = pad_to_square(small)
        if organic_report(small)["corner_opaque"]:
            small = clear_corners(small)
    return small


def smooth_mask(mask: np.ndarray, sigma: float, level: float = 0.5) -> np.ndarray:
    """Rounds a boolean mask: blur and threshold."""
    f = cv2.GaussianBlur(mask.astype(np.float32), (0, 0), sigma)
    return f > level


def composite_on(bg_bgr: tuple, img: np.ndarray) -> np.ndarray:
    """BGRA over a flat colour -> BGR (for contact sheets)."""
    a = img[..., 3:4].astype(np.float32) / 255.0
    base = np.empty(img.shape[:2] + (3,), np.float32)
    base[:] = bg_bgr
    return np.clip(img[..., :3].astype(np.float32) * a + base * (1 - a), 0, 255).astype(np.uint8)


def round_in_place(img: np.ndarray, max_fill: float = MAX_FILL) -> np.ndarray:
    """Like enforce_organic but keeps the canvas size (used for sprites that must stay aligned with the enemy's frames)."""
    if organic_report(img)["empty"] or is_organic(img, max_fill):
        return img
    h, w = img.shape[:2]
    for k in (1.30, 1.20, 1.10, 1.02, 0.96, 0.92, 0.88, 0.84):
        bb = alpha_bbox(img)
        out = img.copy()
        m = _superellipse_alpha(h, w, bb, 2.6, k, max(0.12, 1.5 / max(bb[2] - bb[0] + 1, 4)))
        out[..., 3] = np.round(out[..., 3].astype(np.float32) * m).astype(np.uint8)
        out = clear_corners(out)
        if is_organic(out, max_fill):
            return out
    return out
