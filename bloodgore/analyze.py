"""Reads the body structure of an enemy from its alpha silhouette: head, shoulders, arms, legs, facing.

The analysis only looks at the sprite's own pixels, so it fits any project. Sprites without a clear head and
legs are treated as creatures and get organic chunks instead of named limbs.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .imaging import cv2

ALPHA_SOLID = 96


@dataclass
class Body:
    ok: bool
    humanoid: bool = False
    reason: str = ""
    canvas: tuple = (0, 0)          # width, height of the whole sprite canvas
    bbox: tuple = (0, 0, 0, 0)      # x0, y0, x1, y1 inclusive, canvas pixels
    w: int = 0
    h: int = 0
    xc: float = 0.0                 # body centre line, relative to the bbox
    y_neck: float = 0.0
    y_hip: float = 0.0
    x_lcore: float = 0.0            # torso core edges, relative to the bbox
    x_rcore: float = 0.0
    head_h: float = 0.0
    legs_split: bool = False
    facing: str = "front"
    notes: list = field(default_factory=list)

    def label(self) -> str:
        if not self.ok:
            return "no usable silhouette (%s)" % self.reason
        if self.humanoid:
            return "humanoid: head %d px, torso, 2 arms, %s, facing %s" % (round(self.head_h), "2 legs" if self.legs_split else "legs joined", self.facing)
        return "creature (%s): organic chunks, facing %s" % (self.reason, self.facing)


def _runs(row: np.ndarray) -> int:
    d = np.diff(np.concatenate([[0], row.astype(np.int8), [0]]))
    return int((d == 1).sum())


def _box_smooth(v: np.ndarray, k: int) -> np.ndarray:
    k = max(1, k | 1)
    pad = k // 2
    vp = np.pad(v, (pad, pad), mode="edge")
    return np.convolve(vp, np.ones(k) / k, mode="valid")


def analyze(img: np.ndarray) -> Body:
    """img: BGRA frame (standing). Returns a Body."""
    ch, cw = img.shape[:2]
    mask = img[..., 3] >= ALPHA_SOLID
    ys, xs = np.nonzero(mask)
    if len(xs) < 12:
        return Body(ok=False, reason="almost empty frame", canvas=(cw, ch))
    x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
    m = mask[y0:y1 + 1, x0:x1 + 1]
    H, W = m.shape
    body = Body(ok=True, canvas=(cw, ch), bbox=(x0, y0, x1, y1), w=W, h=H)
    cx_all = float(np.nonzero(m)[1].mean())
    if H < 8 or W < 6:
        body.reason = "tiny sprite"
        body.xc = W / 2.0
        return body

    rw = m.sum(axis=1).astype(np.float64)
    sm = _box_smooth(rw, int(round(H * 0.05)))
    mid = m[int(H * 0.30):max(int(H * 0.30) + 1, int(H * 0.60))]
    xc = float(np.nonzero(mid)[1].mean()) if mid.any() else W / 2.0
    body.xc = xc

    # neck: a narrow row between a wider head and wider shoulders
    lo, hi = max(1, int(H * 0.10)), max(2, int(H * 0.38))
    seg = sm[lo:hi + 1]
    yn = int(np.argmin(seg)) + lo
    head_max = float(sm[:yn + 1].max())
    below_max = float(sm[yn:max(yn + 2, int(H * 0.62))].max())
    valley = sm[yn] <= 0.85 * head_max and below_max >= 1.2 * sm[yn]

    # legs: two separate runs in the lower rows, persisting to the feet
    cl, cr = int(W * 0.30), max(int(W * 0.30) + 2, int(W * 0.70))
    runs = np.array([_runs(m[y, cl:cr]) for y in range(H)])   # only the middle of the body: arms must not count as a gap
    crotch = None
    span = max(2, int(0.12 * H))
    for y in range(int(0.40 * H), int(0.88 * H)):
        if runs[y] >= 2 and (runs[y:y + span] >= 2).all():
            rest = runs[y:]
            if (rest >= 2).mean() >= 0.6:
                crotch = y
                break
    body.legs_split = crotch is not None

    aspect = H / float(W)
    humanoid = (valley and (body.legs_split or aspect >= 1.25)) or (body.legs_split and aspect >= 1.7)
    body.humanoid = bool(humanoid)
    body.y_neck = float(yn if valley else 0.2 * H)
    body.head_h = body.y_neck
    body.y_hip = float(crotch if crotch is not None else 0.55 * H)
    if body.y_hip < body.y_neck + 0.15 * H:
        body.y_hip = body.y_neck + 0.25 * H

    # torso core columns between the neck and the hip
    b0 = int(body.y_neck + 0.08 * H)
    b1 = max(b0 + 2, int(body.y_hip - 0.04 * H))
    band = m[b0:b1]
    occ = band.mean(axis=0) if band.size else np.zeros(W)
    core = occ >= 0.75
    if not core.any():
        core = occ >= 0.5
    xl, xr = 0.0, float(W)
    if core.any():
        idx = np.nonzero(core)[0]
        near = idx[np.argmin(np.abs(idx - xc))]
        l = r = near
        while l - 1 >= 0 and core[l - 1]:
            l -= 1
        while r + 1 < W and core[r + 1]:
            r += 1
        xl, xr = float(l), float(r + 1)
    if xr - xl >= 0.84 * W:      # arms hug the body: split by proportion
        xl, xr = xc - 0.27 * W, xc + 0.27 * W
    if xr - xl < 0.28 * W:
        grow = (0.28 * W - (xr - xl)) / 2.0
        xl, xr = xl - grow, xr + grow
    body.x_lcore, body.x_rcore = max(1.0, xl), min(W - 1.0, xr)

    # facing: bulk of the silhouette and the head position against the body line
    asym = (cx_all - W / 2.0) / W
    head_rows = m[:max(2, int(body.y_neck))]
    head_cx = float(np.nonzero(head_rows)[1].mean()) if head_rows.any() else xc
    head_off = (head_cx - xc) / W
    side = asym if abs(asym) >= abs(head_off) else head_off
    if abs(asym) > 0.07 or abs(head_off) > 0.10:
        body.facing = "right" if side > 0 else "left"
    body.reason = "head and legs found" if body.humanoid else ("no neck" if not valley else "no leg gap")
    if not body.humanoid and body.legs_split is False and valley:
        body.reason = "no leg gap"
    return body


def pick_standing_frame(frames: dict, idle_index: int | None) -> int | None:
    """frames: {index: BGRA}. The IDLE frame when there is one, otherwise the first frame with a silhouette."""
    if idle_index is not None and idle_index in frames and (frames[idle_index][..., 3] >= ALPHA_SOLID).sum() >= 12:
        return idle_index
    for i in sorted(frames):
        if (frames[i][..., 3] >= ALPHA_SOLID).sum() >= 12:
            return i
    return None
