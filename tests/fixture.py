"""Builds a synthetic Easy FPS Editor project for the tests.

Everything here is drawn by code: an original humanoid figure and an original blob creature.
No third-party art is used. The project only contains what Blood and Gore reads or writes.
"""
from __future__ import annotations

import os
import struct
import wave

import cv2
import numpy as np

HUMANOID_SIZE = (64, 96)   # width, height
BLOB_SIZE = (64, 64)
FRAMES = 13                # idle, 4 walk, 2 attack, 2 hurt, 3 death, 1 corpse (the manual's basic enemy)

SKIN = (130, 172, 224, 255)      # BGR + alpha
HAIR = (30, 50, 90, 255)
JACKET = (160, 90, 50, 255)
JACKET_LIGHT = (200, 130, 80, 255)
PANTS = (70, 60, 60, 255)
BOOTS = (30, 30, 34, 255)
BELT = (40, 140, 190, 255)


def _poly(img, pts, col):
    cv2.fillPoly(img, [np.array(pts, np.int32)], col, lineType=cv2.LINE_8)


def draw_humanoid(frame: int = 0, size=HUMANOID_SIZE) -> np.ndarray:
    """One frame of the test humanoid, BGRA, standing feet at the bottom edge."""
    w, h = size
    big = np.zeros((h * 2, w * 2, 4), np.uint8)
    img = np.zeros((h, w, 4), np.uint8)
    walk = {1: 3, 2: 0, 3: -3, 4: 0}.get(frame, 0)
    swing = walk // 2
    lean = 3 if frame in (7, 8) else 0
    cx = w // 2
    # legs, a 2 px gap in the middle
    _poly(img, [(cx - 9, 56), (cx - 1, 56), (cx - 1 - walk, 92), (cx - 9 - walk, 92)], PANTS)
    _poly(img, [(cx + 1, 56), (cx + 9, 56), (cx + 9 + walk, 92), (cx + 1 + walk, 92)], PANTS)
    _poly(img, [(cx - 10 - walk, 90), (cx - 1 - walk, 90), (cx - 1 - walk, 95), (cx - 11 - walk, 95)], BOOTS)
    _poly(img, [(cx + 1 + walk, 90), (cx + 10 + walk, 90), (cx + 11 + walk, 95), (cx + 1 + walk, 95)], BOOTS)
    # torso
    _poly(img, [(cx - 12 + lean, 27), (cx + 12 + lean, 27), (cx + 9, 58), (cx - 9, 58)], JACKET)
    _poly(img, [(cx - 12 + lean, 27), (cx - 2 + lean, 27), (cx - 3, 58), (cx - 9, 58)], JACKET_LIGHT)
    _poly(img, [(cx - 9, 53), (cx + 9, 53), (cx + 9, 57), (cx - 9, 57)], BELT)
    # arms: they leave the body a little, so the sides show a thin gap near the hands
    la = (cx - 12 + lean, 28), (cx - 17 + lean, 30), (cx - 21 + swing, 55), (cx - 16 + swing, 56)
    _poly(img, list(la), JACKET)
    cv2.circle(img, (cx - 19 + swing, 58), 3, SKIN, -1, lineType=cv2.LINE_8)
    if frame in (5, 6):   # attack: right arm raised and forward
        _poly(img, [(cx + 12 + lean, 28), (cx + 18, 22), (cx + 24, 20), (cx + 23, 26), (cx + 15, 33)], JACKET)
        cv2.circle(img, (cx + 25, 22), 3, SKIN, -1, lineType=cv2.LINE_8)
    else:
        ra = (cx + 12 + lean, 28), (cx + 17 + lean, 30), (cx + 21 - swing, 55), (cx + 16 - swing, 56)
        _poly(img, list(ra), JACKET)
        cv2.circle(img, (cx + 19 - swing, 58), 3, SKIN, -1, lineType=cv2.LINE_8)
    # neck and head
    _poly(img, [(cx - 3 + lean, 22), (cx + 3 + lean, 22), (cx + 3 + lean, 28), (cx - 3 + lean, 28)], SKIN)
    cv2.circle(img, (cx + lean + (2 if frame in (7, 8) else 0), 14), 9, SKIN, -1, lineType=cv2.LINE_8)
    cv2.ellipse(img, (cx + lean, 11), (9, 7), 0, 180, 360, HAIR, -1, lineType=cv2.LINE_8)
    cv2.circle(img, (cx + lean - 3, 15), 1, (20, 20, 20, 255), -1)
    cv2.circle(img, (cx + lean + 3, 15), 1, (20, 20, 20, 255), -1)
    if frame >= 9:
        # falling: rotate the standing figure around the feet, the last frame is lying down
        base = draw_humanoid(0, size)
        ang = {9: -25, 10: -60, 11: -80, 12: -90}[frame]
        big[:] = 0
        big[h:, w // 2:w // 2 + w] = base
        m = cv2.getRotationMatrix2D((w, h * 2 - 4), ang, 1.0)
        rot = cv2.warpAffine(big, m, (w * 2, h * 2), flags=cv2.INTER_NEAREST)
        ys, xs = np.nonzero(rot[..., 3])
        crop = rot[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        img = np.zeros((h, w, 4), np.uint8)
        ch, cw = crop.shape[:2]
        ox = max(0, (w - cw) // 2)
        crop = crop[:h, :w]
        img[h - crop.shape[0]:h, ox:ox + crop.shape[1]] = crop
    return img


def draw_blob(frame: int = 0, size=BLOB_SIZE) -> np.ndarray:
    """An original blob creature with a few stubby feet and two eyes."""
    w, h = size
    img = np.zeros((h, w, 4), np.uint8)
    wob = {1: 2, 2: 0, 3: -2, 4: 0}.get(frame, 0)
    sq = 5 if frame in (7, 8) else 0
    body = (150, 60, 120, 255)
    light = (200, 120, 170, 255)
    cv2.ellipse(img, (w // 2, h - 24 + sq // 2), (24, 20 - sq), 0, 0, 360, body, -1, lineType=cv2.LINE_8)
    cv2.ellipse(img, (w // 2 + 10, h - 38 + sq), (12, 11 - sq // 2), 0, 0, 360, body, -1, lineType=cv2.LINE_8)
    cv2.ellipse(img, (w // 2 - 14, h - 34 + sq), (9, 8), 0, 0, 360, body, -1, lineType=cv2.LINE_8)
    cv2.ellipse(img, (w // 2 - 6, h - 32 + sq), (12, 9), 0, 180, 270, light, -1, lineType=cv2.LINE_8)
    for k, dx in enumerate((-17, -6, 6, 17)):
        cv2.ellipse(img, (w // 2 + dx + (wob if k % 2 else -wob), h - 5), (5, 5), 0, 0, 360, body, -1, lineType=cv2.LINE_8)
    if frame in (5, 6):
        cv2.ellipse(img, (w // 2 + 26, h - 30), (10, 5), 0, 0, 360, body, -1, lineType=cv2.LINE_8)
    cv2.circle(img, (w // 2 + 4, h - 30 + sq), 4, (255, 255, 255, 255), -1)
    cv2.circle(img, (w // 2 + 16, h - 30 + sq), 4, (255, 255, 255, 255), -1)
    cv2.circle(img, (w // 2 + 5, h - 29 + sq), 2, (20, 20, 20, 255), -1)
    cv2.circle(img, (w // 2 + 17, h - 29 + sq), 2, (20, 20, 20, 255), -1)
    if frame >= 9:
        k = {9: 0.85, 10: 0.65, 11: 0.5, 12: 0.4}[frame]
        small = cv2.resize(img, (w, max(8, int(h * k))), interpolation=cv2.INTER_NEAREST)
        img = np.zeros((h, w, 4), np.uint8)
        img[h - small.shape[0]:] = small
    return img


def _write_png(path, img):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    with open(path, "wb") as f:
        f.write(buf.tobytes())


def _tiny_wav(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(11025)
        w.writeframes(struct.pack("<%dh" % 200, *([0] * 200)))


ENEMY_FIELDS = dict(hp=60, damage=9, speed=125, attack_delay=1.2, shooting=1, projectiles=0, scale=1.5, drop_type=-1, drop_index=-1,
                    remove_at_death=0, radius=14, explosion=0, proj_speed=0, fullbright=1, spread=0)


def enemy_record(name, idx, **kw):
    f = dict(ENEMY_FIELDS)
    f.update(kw)
    return ("%s %d\n%d\n%d\n%g\n%g\n%d\n%d\n%g\n%d\n%d\n%d\n%g\n%d %g %d %g\r\n" % (
        name, idx, f["hp"], f["damage"], f["speed"], f["attack_delay"], f["shooting"], f["projectiles"], f["scale"], f["drop_type"],
        f["drop_index"], f["remove_at_death"], f["radius"], f["explosion"], f["proj_speed"], f["fullbright"], f["spread"]))


def decoration_record(name, idx, hp=40, breakable=1, solid=1, scale=1, radius=14, lighted=1, align=0, cull=1, remove=1):
    return "%s %d\n%d\n%d\n%d\n%g\n-1\n-1\n%g %d %d %d %d\r\n" % (name, idx, hp, breakable, solid, scale, radius, lighted, align, cull, remove)


BASIC_STATES = """image {name} 0 12
sound {name}Shot

state IDLE NONE 0
frame 0 0.125 0 0 0 NONE
frame 0 0.125 0 0 0 READY

state SEE CHASE 0
frame 0 0.125 0 0 0 NONE
frame 0 0.125 0 0 0 READY

state CHASE NONE 0
frame 1 0.1 0 0 0 NONE
frame 2 0.1 0 0 0 NONE
frame 3 0.1 0 0 0 NONE
frame 4 0.1 0 0 0 READY

state ATTACK CHASE 0
frame 5 0.25 0 0 0 NONE
frame 5 0.25 0 0 0 NONE
frame 6 0.0625 0 0 0 NONE
frame 6 0.0625 0 0 0 ATTACK
frame 5 0.25 0 0 0 NONE
frame 5 0.25 0 0 0 READY

state HURT CHASE 0
frame 7 0.125 0 0 0 NONE
frame 8 0.125 0 0 0 NONE

state DEATH DEAD 0
frame 9 0.166 0 0 0 NONE
frame 10 0.166 0 0 0 NONE
frame 11 0.166 0 0 0 NONE

state DEAD NONE 0
frame 12 0.125 0 0 0 NONE
frame 12 0.125 0 0 0 NONE
"""


def build_project(root: str, name: str = "TestProject", with_engine: bool = True, crlf_states: bool = False) -> str:
    """Creates root/Projects/<name> and (optionally) a placeholder editor exe next to Projects. Returns the project path."""
    engine = root
    proj = os.path.join(engine, "Projects", name)
    os.makedirs(proj, exist_ok=True)
    if with_engine:
        with open(os.path.join(engine, "EasyFPSEditor_CE.exe"), "wb") as f:
            f.write(b"MZ placeholder, never executed by the tests\n")
        with open(os.path.join(engine, "Projects", "Game.exe"), "wb") as f:
            f.write(b"MZ placeholder, never executed by the tests\n")
    with open(os.path.join(proj, "Game.dat"), "w", newline="") as f:
        f.write("1.10.5\nTest Game\n1 1 1 0 1 0 0 0\n1\n255 255 255 128\n255 0 0 128\n1\n\r\n")
    with open(os.path.join(proj, "Enemies.dat"), "w", newline="") as f:
        f.write(enemy_record("Humanoid", 1))
        f.write(enemy_record("Blobby", 2, hp=40, scale=1.25, radius=18, remove_at_death=0))
    with open(os.path.join(proj, "Decorations.dat"), "w", newline="") as f:
        f.write(decoration_record("Crate", 1))
    for fn in ("Weapons.dat", "Maps.dat", "Models.dat", "Textures.dat", "Tiles.dat", "Player.dat", "Hp.dat", "Armour.dat", "Animations.dat", "HUD.dat"):
        with open(os.path.join(proj, fn), "w", newline="") as f:
            f.write("\r\n")
    for i in range(FRAMES):
        _write_png(os.path.join(proj, "Sprites", "Enemies", "Humanoid%d.png" % i), draw_humanoid(i))
        _write_png(os.path.join(proj, "Sprites", "Enemies", "Blobby%d.png" % i), draw_blob(i))
    # the editor's own default particles and two custom ones that are already in use
    for fn, px in (("Blood.png", 8), ("BloodFloor.png", 32), ("Spark.png", 8), ("Custom0.png", 12), ("Custom1.png", 12)):
        im = np.zeros((px, px, 4), np.uint8)
        cv2.circle(im, (px // 2, px // 2), max(2, px // 3), (30, 30, 200, 255), -1)
        _write_png(os.path.join(proj, "Sprites", "Particles", fn), im)
    _tiny_wav(os.path.join(proj, "Sounds", "HumanoidShot.wav"))
    os.makedirs(os.path.join(proj, "States"), exist_ok=True)
    text = BASIC_STATES.format(name="Humanoid")
    if crlf_states:
        text = text.replace("\n", "\r\n")
    with open(os.path.join(proj, "States", "Humanoid.states"), "w", newline="") as f:
        f.write(text)
    for sub in ("Sprites/Decorations", "Maps", "Scripts"):
        os.makedirs(os.path.join(proj, sub), exist_ok=True)
    _write_png(os.path.join(proj, "Sprites", "Decorations", "Crate0.png"), draw_blob(0, (24, 24)))
    return proj


def tree_hashes(root: str) -> dict:
    """sha256 of every file under root (relative path -> digest). Used to prove that a dry run writes nothing."""
    import hashlib
    out = {}
    for base, _dirs, files in os.walk(root):
        for fn in files:
            p = os.path.join(base, fn)
            with open(p, "rb") as f:
                out[os.path.relpath(p, root).replace("\\", "/")] = hashlib.sha256(f.read()).hexdigest()
    return out


if __name__ == "__main__":   # quick look: python tests/fixture.py out.png
    import sys
    sheet = np.zeros((100, 64 * 13 + 40, 4), np.uint8)
    for i in range(13):
        im = draw_humanoid(i)
        sheet[:96, i * 66:i * 66 + 64] = im
    cv2.imwrite(sys.argv[1] if len(sys.argv) > 1 else "fixture_sheet.png", sheet)
