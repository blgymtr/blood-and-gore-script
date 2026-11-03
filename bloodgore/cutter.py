"""Cuts an enemy sprite into body parts, chunks and a remains pile, all from the sprite's own pixels.

Cut lines are warped with smooth noise, so they are curved and jagged, never straight. Every cut leaves a
rounded, bloody stump (dark red rim, lighter flesh, a pale bone hint on limbs). Nothing is a rectangle.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from . import art
from . import imaging as im
from .analyze import ALPHA_SOLID, Body
from .imaging import cv2


@dataclass
class Part:
    name: str
    img: np.ndarray          # BGRA, final sprite
    cx: float                # centre of the part in the source sprite canvas (pixels from the left)
    cy: float                # centre of the part in the source sprite canvas (pixels from the top)
    kind: str                # head, torso, arm, leg, chunk


def cut_scale(canvas_w: int, canvas_h: int) -> int:
    m = max(canvas_w, canvas_h)
    return 4 if m <= 256 else (3 if m <= 400 else 2)


class _Ctx:
    """Shared arrays for one enemy: the sprite at S x, its mask and the warped coordinate fields."""

    def __init__(self, frame: np.ndarray, body: Body, rng: np.random.Generator):
        self.rng = rng
        self.body = body
        ch, cw = frame.shape[:2]
        self.S = S = cut_scale(cw, ch)
        self.big = cv2.resize(frame, (cw * S, ch * S), interpolation=cv2.INTER_NEAREST)
        self.M = self.big[..., 3] >= ALPHA_SOLID
        self.h, self.w = self.M.shape
        x0, y0, _x1, _y1 = body.bbox
        jj = (np.arange(self.w, dtype=np.float32) + 0.5) / S - x0
        ii = (np.arange(self.h, dtype=np.float32) + 0.5) / S - y0
        self.X = np.broadcast_to(jj[None, :], (self.h, self.w))
        self.Y = np.broadcast_to(ii[:, None], (self.h, self.w))
        W, H = max(body.w, 8), max(body.h, 8)
        a_low, a_high = max(0.9, 0.05 * W), max(0.35, 0.013 * W)
        lowx = im.fractal_noise(self.h, self.w, S * max(5.0, 0.22 * W), rng, 2)
        lowy = im.fractal_noise(self.h, self.w, S * max(5.0, 0.22 * H), rng, 2)
        hix = im.value_noise(self.h, self.w, S * max(1.6, 0.05 * W), rng)
        hiy = im.value_noise(self.h, self.w, S * max(1.6, 0.05 * H), rng)
        self.Xw = self.X + a_low * lowx + a_high * hix
        self.Yw = self.Y + a_low * 0.8 * lowy + a_high * hiy
        self.noise01 = (im.fractal_noise(self.h, self.w, S * 2.5, rng, 2) + 1) / 2
        self.legs_region = None

    def center_px(self, mask: np.ndarray):
        ys, xs = np.nonzero(mask)
        return (xs.min() + xs.max() + 1) / 2.0 / self.S, (ys.min() + ys.max() + 1) / 2.0 / self.S


def _finish_piece(ctx: _Ctx, region: np.ndarray, kind: str, whole_cut: bool = False) -> np.ndarray | None:
    """Turns a region mask into a part sprite with a rounded, bloody stump wherever it was cut."""
    S, M, rng = ctx.S, ctx.M, ctx.rng
    body = ctx.body
    if region.sum() < 6 * S * S:
        return None
    reg = im.smooth_mask(region, S * 0.8) & M
    if reg.sum() < 4 * S * S:
        return None
    rest = M & ~region
    if whole_cut:
        # a chunk is cut loose all around
        dist_rest = cv2.distanceTransform(reg.astype(np.uint8), cv2.DIST_L2, 3)
        dist_rest = np.where(reg, dist_rest, 0.0)
        edge_dist = dist_rest
    else:
        inv = (~rest).astype(np.uint8)
        edge_dist = cv2.distanceTransform(inv, cv2.DIST_L2, 3) if rest.any() else np.full(reg.shape, 1e3, np.float32)
    thick_px = S * max(1.5, 0.05 * body.h)
    thick = thick_px * (0.7 + 0.6 * ctx.noise01)
    if whole_cut:
        zone = reg & (edge_dist <= thick)
        cap = np.zeros_like(reg)
    else:
        zone = reg & (edge_dist <= thick)
        dist_reg = cv2.distanceTransform((~reg).astype(np.uint8), cv2.DIST_L2, 3)
        bulge = S * (0.9 + 1.0 * ctx.noise01)
        cap = rest & (dist_reg <= bulge) & (edge_dist <= S * 1.2)
    final = reg | cap
    final = im.smooth_mask(final, S * 0.55)
    zone &= final
    cap &= final
    out = np.zeros((ctx.h, ctx.w, 4), np.uint8)
    col = ctx.big[..., :3].astype(np.float32)
    # flesh colour grows from a dark rim to lighter meat toward the middle of the zone
    depth = np.clip(edge_dist / np.maximum(thick, 1e-3), 0, 1.4)
    flesh = art.flesh_palette_at(np.clip(depth * 0.85, 0, 1), ctx.noise01 * 2 - 1)
    wgt = np.clip(1.5 * (1.0 - depth), 0, 1)[..., None]
    col = np.where(zone[..., None], col * (1 - wgt) + flesh * wgt, col)
    col = np.where(cap[..., None], art.flesh_palette_at(np.clip(ctx.noise01 * 0.5, 0, 1), ctx.noise01 * 2 - 1), col)
    # pale spots of fat in the flesh
    fat = zone & (depth > 0.55) & (ctx.noise01 > 0.80)
    col = np.where(fat[..., None], art.FAT, col)
    out[..., :3] = np.clip(col, 0, 255).astype(np.uint8)
    out[..., 3] = np.where(final, 255, 0)
    if kind in ("arm", "leg", "head") and zone.any():
        ys, xs = np.nonzero(zone)
        cx, cy = float(xs.mean()), float(ys.mean())
        r = max(S * 0.9, min(S * 3.2, math.sqrt(float(zone.sum())) / 5.5))
        if kind == "head":
            r *= 0.6
        rr = int(round(r))
        cv2.circle(out, (int(cx), int(cy)), rr, tuple(int(v) for v in art.BONE_SHADE) + (255,), -1, lineType=cv2.LINE_8)
        cv2.circle(out, (int(cx), int(cy)), max(1, int(rr * 0.65)), tuple(int(v) for v in art.BONE) + (255,), -1, lineType=cv2.LINE_8)
        cv2.circle(out, (int(cx) + 1, int(cy)), max(1, int(rr * 0.25)), tuple(int(v) for v in art.FLESH_LIGHT) + (255,), -1, lineType=cv2.LINE_8)
        out[..., 3] = np.where(final, 255, 0)
    return out


def _humanoid_labels(ctx: _Ctx) -> dict:
    b = ctx.body
    Xw, Yw = ctx.Xw, ctx.Yw
    head = Yw < b.y_neck
    arm_bottom = b.y_hip + 0.10 * b.h
    arm_l = ~head & (Xw < b.x_lcore) & (Yw < arm_bottom)
    arm_r = ~head & (Xw > b.x_rcore) & (Yw < arm_bottom)
    legs = ~head & ~arm_l & ~arm_r & (Yw >= b.y_hip)
    leg_l = legs & (Xw < b.xc)
    leg_r = legs & (Xw >= b.xc)
    torso = ~head & ~arm_l & ~arm_r & ~legs
    return dict(head=head, armA=arm_l, armB=arm_r, legA=leg_l, legB=leg_r, torso=torso, _legs=legs)


def _chunk_mask(ctx: _Ctx, cx: float, cy: float, radius: float) -> np.ndarray:
    """Irregular blob around (cx, cy) in 4x pixels."""
    rng, S = ctx.rng, ctx.S
    m = np.zeros((ctx.h, ctx.w), np.uint8)
    for _ in range(int(rng.integers(3, 6))):
        ox, oy = rng.normal(0, radius * 0.45), rng.normal(0, radius * 0.45)
        cv2.ellipse(m, (int(cx + ox), int(cy + oy)), (max(2, int(radius * rng.uniform(0.45, 0.85))), max(2, int(radius * rng.uniform(0.4, 0.75)))),
                    rng.uniform(0, 180), 0, 360, 1, -1)
    return im.smooth_mask(m.astype(bool), S * 0.9)


def _chunk_part(ctx: _Ctx, name: str, size_px: float, sprite_tint: bool, area_mask: np.ndarray) -> Part | None:
    """A torn lump: sprite pixels with a bloody rim, or pure meat when the area is not suitable."""
    S, rng = ctx.S, ctx.rng
    ys, xs = np.nonzero(area_mask)
    if len(xs) == 0:
        ys, xs = np.nonzero(ctx.M)
    k = int(rng.integers(0, len(xs)))
    cx, cy = float(xs[k]), float(ys[k])
    radius = size_px * S * rng.uniform(0.8, 1.15) / 2.0
    blob = _chunk_mask(ctx, cx, cy, radius)
    if blob.sum() < 10 * S * S:
        blob = _chunk_mask(ctx, cx, cy, radius * 1.4)
    if sprite_tint:
        sub = blob & ctx.M
        if sub.sum() < 0.4 * blob.sum():
            sprite_tint = False
    if not sprite_tint:
        h, w = ctx.h, ctx.w
        bounds = blob
        piece = art.shade(bounds, rng, base=art.FLESH, dark=art.FLESH_DARK, light=art.FLESH_LIGHT, gloss=0.5, speckle=0.18)
        n = ctx.noise01
        fat = bounds & (n > 0.8) & (cv2.distanceTransform(bounds.astype(np.uint8), cv2.DIST_L2, 3) > S)
        piece[fat, :3] = art.FAT
        yb, xb = np.nonzero(bounds)
        crop = piece[yb.min():yb.max() + 1, xb.min():xb.max() + 1]
        spr = im.finish_sprite(crop, square=True, f=S)
        return Part(name, spr, (xb.min() + xb.max() + 1) / 2.0 / S, (yb.min() + yb.max() + 1) / 2.0 / S, "chunk")
    # sprite pixels inside the blob; the rim is torn flesh
    saved_M = ctx.M
    ctx.M = blob | saved_M
    piece = _finish_piece(ctx, blob, "chunk", whole_cut=True)
    ctx.M = saved_M
    if piece is None:
        return None
    alpha = piece[..., 3] > 0
    yb, xb = np.nonzero(alpha)
    crop = piece[yb.min():yb.max() + 1, xb.min():xb.max() + 1]
    # pixels of the blob that were transparent in the sprite become meat
    spr = im.finish_sprite(crop, square=True, f=S)
    return Part(name, spr, (xb.min() + xb.max() + 1) / 2.0 / S, (yb.min() + yb.max() + 1) / 2.0 / S, "chunk")


def _voronoi_labels(ctx: _Ctx, count: int) -> list:
    rng, S = ctx.rng, ctx.S
    ys, xs = np.nonzero(ctx.M[::S, ::S])
    pts = np.stack([xs, ys], axis=1).astype(np.float32)
    if len(pts) == 0:
        return []
    seeds = []
    mind = max(2.0, math.sqrt(len(pts) / max(count, 1)) * 0.75)
    tries = 0
    while len(seeds) < count and tries < 400:
        p = pts[int(rng.integers(0, len(pts)))]
        if all(np.hypot(*(p - q)) >= mind for q in seeds):
            seeds.append(p)
        tries += 1
    if len(seeds) < 2:
        seeds = [pts[0], pts[-1]]
    x0, y0, _x1, _y1 = ctx.body.bbox
    best = np.full((ctx.h, ctx.w), 1e18, np.float32)
    lab = np.zeros((ctx.h, ctx.w), np.int32)
    for k, p in enumerate(seeds):
        d = (ctx.Xw - (p[0] - x0 + 0.5)) ** 2 + (ctx.Yw - (p[1] - y0 + 0.5)) ** 2
        better = d < best
        best = np.where(better, d, best)
        lab = np.where(better, k, lab)
    return [(lab == k) & ctx.M for k in range(len(seeds))]


def cut_parts(frame: np.ndarray, body: Body, rng: np.random.Generator, chunks: int = 4):
    """Returns (parts, ctx). Humanoids give head, torso, armA, armB, legA, legB and `chunks` lumps;
    creatures give Voronoi-like pieces (chunk1..) and a few extra lumps."""
    ctx = _Ctx(frame, body, rng)
    S = ctx.S
    parts = []
    if body.humanoid:
        labels = _humanoid_labels(ctx)
        for name, kind in (("head", "head"), ("torso", "torso"), ("armA", "arm"), ("armB", "arm"), ("legA", "leg"), ("legB", "leg")):
            region = labels[name] & ctx.M
            piece = _finish_piece(ctx, region, kind)
            if piece is None:
                continue
            yb, xb = np.nonzero(piece[..., 3] > 0)
            crop = piece[yb.min():yb.max() + 1, xb.min():xb.max() + 1]
            spr = im.finish_sprite(crop, f=S)
            cx, cy = ctx.center_px(piece[..., 3] > 0)
            parts.append(Part(name, spr, cx, cy, kind))
        torso_area = labels["torso"] & ctx.M
        for i in range(chunks):
            size = max(4.0, body.h * rng.uniform(0.09, 0.15))
            p = _chunk_part(ctx, "chunk%d" % (i + 1), size, i % 2 == 0, torso_area if i % 3 else ctx.M)
            if p is not None:
                parts.append(p)
        legs_region = labels["_legs"] & ctx.M
        ctx.legs_region = legs_region
    else:
        regions = _voronoi_labels(ctx, int(np.clip(round(math.sqrt(max(ctx.M.sum() / (S * S), 1) / 28.0)), 4, 7)))
        n = 0
        for region in regions:
            piece = _finish_piece(ctx, region, "chunk")
            if piece is None:
                continue
            yb, xb = np.nonzero(piece[..., 3] > 0)
            crop = piece[yb.min():yb.max() + 1, xb.min():xb.max() + 1]
            spr = im.finish_sprite(crop, f=S)
            cx, cy = ctx.center_px(piece[..., 3] > 0)
            n += 1
            parts.append(Part("chunk%d" % n, spr, cx, cy, "chunk"))
        for i in range(max(0, chunks - 2)):
            size = max(4.0, body.h * rng.uniform(0.12, 0.2))
            p = _chunk_part(ctx, "chunk%d" % (n + i + 1), size, i % 2 == 0, ctx.M)
            if p is not None:
                parts.append(p)
        ctx.legs_region = None
    return parts, ctx


def make_remains(frame: np.ndarray, body: Body, ctx: _Ctx, rng: np.random.Generator) -> np.ndarray:
    """Remains sprite for the gibbed body: the lower body torn off at the hip (or the lower part of a creature)
    standing in a pool, with a few lumps. Same canvas height as the enemy's frames, centred like them."""
    S = ctx.S
    cw, ch = body.canvas
    W = body.w
    extra = max(0, int(math.ceil((1.7 * W - cw) / 2.0)))
    Wc = cw + 2 * extra
    big_w, big_h = Wc * S, ch * S
    canvas = np.zeros((big_h, big_w, 4), np.uint8)
    # pool first (behind everything), a low wide ellipse at the feet
    pool = art.pool(int(max(W * 1.35, 14)), rng, spread=1.0)
    ph, pw = pool.shape[:2]
    pool_big = cv2.resize(pool, (pw * S, ph * S), interpolation=cv2.INTER_NEAREST)
    py = big_h - pool_big.shape[0]
    px = (big_w - pool_big.shape[1]) // 2
    canvas[py:py + pool_big.shape[0], px:px + pool_big.shape[1]] = pool_big
    # the lower body
    if ctx.legs_region is not None and ctx.legs_region.any():
        region = ctx.legs_region
    else:
        region = ctx.M & (ctx.Yw >= 0.62 * body.h)
    piece = _finish_piece(ctx, region, "torso")
    if piece is not None:
        a = piece[..., 3:4].astype(np.float32) / 255.0
        ox = extra * S
        view = canvas[:, ox:ox + ctx.w]
        view[...] = (view.astype(np.float32) * (1 - a) + piece.astype(np.float32) * a).astype(np.uint8)
        view[..., 3] = np.maximum(view[..., 3], piece[..., 3])
    # a couple of lumps lying in the pool
    for _ in range(3):
        c = art.flesh_chunk(int(max(3, body.h * rng.uniform(0.07, 0.11))), rng)
        c = cv2.resize(c, (c.shape[1] * S, c.shape[0] * S), interpolation=cv2.INTER_NEAREST)
        cx = int(big_w / 2 + rng.uniform(-0.55, 0.55) * W * S)
        cy = int(big_h - c.shape[0] // 2 - rng.uniform(0.0, 0.04) * big_h)
        y0, x0 = cy - c.shape[0] // 2, cx - c.shape[1] // 2
        if y0 < 0 or x0 < 0 or y0 + c.shape[0] > big_h or x0 + c.shape[1] > big_w:
            continue
        a = c[..., 3:4].astype(np.float32) / 255.0
        v = canvas[y0:y0 + c.shape[0], x0:x0 + c.shape[1]]
        v[...] = (v.astype(np.float32) * (1 - a) + c.astype(np.float32) * a).astype(np.uint8)
        v[..., 3] = np.maximum(v[..., 3], c[..., 3])
    small = im.downsample_rgba(canvas, S)
    small = im.bleed_colors(small)
    small[..., 3][small[..., 3] < 6] = 0
    return im.round_in_place(small, 0.80)


def lying_decoration(part: Part, rng: np.random.Generator) -> np.ndarray:
    """A part lying on the floor: rotated a little, with a smear of blood under it. Seen from the side, so it stays low."""
    img = part.img
    ang = float(rng.choice([-1, 1])) * rng.uniform(60, 100)
    h, w = img.shape[:2]
    side = int(math.ceil(math.hypot(h, w))) + 4
    canvas = np.zeros((side, side, 4), np.uint8)
    y0, x0 = (side - h) // 2, (side - w) // 2
    canvas[y0:y0 + h, x0:x0 + w] = img
    m = cv2.getRotationMatrix2D((side / 2.0, side / 2.0), ang, 1.0)
    rot = cv2.warpAffine(canvas, m, (side, side), flags=cv2.INTER_NEAREST, borderValue=(0, 0, 0, 0))
    bb = im.alpha_bbox(rot)
    if bb is None:
        return img
    rot = rot[bb[1]:bb[3] + 1, bb[0]:bb[2] + 1]
    rh, rw = rot.shape[:2]
    smear = art.pool(int(max(rw * 1.5, 10)), rng, spread=0.9)
    sh, sw = smear.shape[:2]
    W2, H2 = max(sw, rw + 4), max(sh, rh + 2)
    out = np.zeros((H2, W2, 4), np.uint8)
    sx, sy = (W2 - sw) // 2, H2 - sh
    out[sy:sy + sh, sx:sx + sw] = smear
    px, py = (W2 - rw) // 2, H2 - rh - max(0, sh // 5)
    py = max(0, py)
    a = rot[..., 3:4].astype(np.float32) / 255.0
    v = out[py:py + rh, px:px + rw]
    v[...] = (v.astype(np.float32) * (1 - a) + rot.astype(np.float32) * a).astype(np.uint8)
    v[..., 3] = np.maximum(v[..., 3], rot[..., 3])
    out = im.trim(out, 1)
    return im.enforce_organic(out) if not im.is_organic(out) else out
