"""Builds the gore FSM lines for one enemy and patches them into its .states file.

Engine facts this module relies on (measured in Easy FPS Editor CE 1.10.5, see README "How it works"):
* CUSTOMPARTICLE: the image of id N is Custom(N-1).png (ids 0 and 1 both give Custom0), so every particle image
  is stored in two neighbouring files and the id of the second one is used. Works with either numbering.
* particle size in world units = image pixels x scale; sprites and decorations = pixels x scale x 0.4.
* in the FSM, positions are (north, up, east) and velocities (east, up, north) in units per second; the frame is
  not turned toward the player, so a spray cannot be aimed away from the shooter and is sprayed all around.
* gravity: acceleration = 233.5 x the gravity argument (units per second squared); particles do not stop at
  the floor, so their lifetime is set to the time they need to land.
* particle images must be square.
"""
from __future__ import annotations

import math
import re
import zlib
from dataclasses import dataclass, field

import numpy as np

from . import art
from .imaging import pad_to_square
from .states import StatesDoc

GRAVITY_UNIT = 233.5
UNITS_PER_PIXEL = 0.4
FRAME_DELAY = "0.01"
FIRST_DELAY = "0.01"   # a zero delay on the first frame of a looping state hangs the engine (measured), so never use 0 there

LEVELS = {
    1: dict(hurt=4, mist=0, death=6, gibf=1.00, tearf=None, chunks=3, burst=16, variants=1, head_tear=False),
    2: dict(hurt=8, mist=0, death=10, gibf=0.80, tearf=0.40, chunks=4, burst=26, variants=2, head_tear=False),
    3: dict(hurt=14, mist=2, death=16, gibf=0.60, tearf=0.25, chunks=5, burst=36, variants=3, head_tear=True),
    4: dict(hurt=20, mist=4, death=22, gibf=0.40, tearf=0.15, chunks=6, burst=46, variants=3, head_tear=True),
}

BLOOD_SPECS = (   # key, art kind, art size px, world size in units for a 48 unit tall enemy
    ("dropS", "round", 5, 2.0), ("dropM", "tear", 8, 3.2), ("dropL", "tear", 11, 4.6),
    ("streak", "streak", 12, 5.0), ("clot", "clot", 10, 5.0), ("mist", "mist", 22, 16.0), ("splat", "splat", 14, 8.0),
)
OWN_STATES = ("BGGIB", "BGREMAINS", "BGTEAR", "BGDEATHCOPY")
SOUND_NAMES = ("BgGib", "BgTear")


def seed_for(name: str, seed: int) -> int:
    return (zlib.crc32(name.encode("utf8")) ^ (seed * 2654435761)) & 0xFFFFFFFF


def fmt(v: float) -> str:
    s = "%.1f" % v
    if s in ("-0.0", "0.0"):
        return "0"
    return s[:-2] if s.endswith(".0") else s


def land_time(y0: float, vy: float, a: float, floor: float) -> float:
    return (vy + math.sqrt(vy * vy + 2.0 * a * max(y0 - floor, 0.0))) / a


@dataclass
class Item:
    key: str
    id: int
    img: np.ndarray
    files: list


class Library:
    """Particle images of one install. Each image takes two neighbouring Custom files (see the module docstring)."""

    def __init__(self, first_free: int):
        self.next = first_free
        self.items = {}

    def add(self, key: str, img: np.ndarray) -> Item:
        a = self.next
        self.next += 2
        img = pad_to_square(img)      # the engine needs square particle images
        it = Item(key, a + 1, img, [a, a + 1])
        self.items[key] = it
        return it


def blood_library(lib: Library, seed: int):
    rng = np.random.default_rng(seed)
    for key, kind, size, _w in BLOOD_SPECS:
        if kind == "mist":
            img = art.mist(size, rng)
        elif kind == "splat":
            img = art.splat_air(size, rng)
        else:
            img = art.drop(size, kind, rng)
        lib.add(key, img)


@dataclass
class DecoSpec:
    name: str
    img: np.ndarray
    scale: float


class EnemyGore:
    """All gore for one enemy: particle ids, decoration names, level settings, and the FSM text builders."""

    def __init__(self, enemy, body, parts, remains, lib: Library, decos: dict, level: dict, seed: int, gib_f=None, tear_f=None):
        self.enemy, self.body, self.parts = enemy, body, parts
        self.remains, self.lib, self.decos, self.lv = remains, lib, decos, level
        self.rng = np.random.default_rng(seed)
        self.gib_f = level["gibf"] if gib_f is None else gib_f
        self.tear_f = level["tearf"] if tear_f is None else tear_f
        self.H = max(body.h * enemy.scale * UNITS_PER_PIXEL, 6.0)       # body height in world units
        self.W = max(body.w * enemy.scale * UNITS_PER_PIXEL, 4.0)
        self.k = float(np.clip(self.H / 48.0, 0.45, 2.4))      # blood size factor
        self.f = float(np.clip(math.sqrt(self.H / 50.0), 0.7, 1.8))   # speed factor
        self.a = 780.0
        self.g = self.a / GRAVITY_UNIT
        self.sound_base = 0
        self.remains_index = 0
        if self.tear_f is not None and not self.tear_options():
            self.tear_f = None

    # ---- particle actions ----
    def _world_size(self, key: str) -> float:
        for k, _kind, _s, w in BLOOD_SPECS:
            if k == key:
                return w * self.k
        return 4.0

    def drop(self, y: float, vu: float, speed: float, key=None, ang=None, ve=None, vn=None, origin=0.0):
        rng = self.rng
        key = key or rng.choice(["dropS", "dropM", "dropL", "dropM", "streak", "clot"])
        item = self.lib.items[key]
        size = self._world_size(key) * rng.uniform(0.8, 1.2)
        scale = size / item.img.shape[0]
        if ve is None:
            ang = rng.uniform(0, 2 * math.pi) if ang is None else ang
            sp = speed * rng.uniform(0.35, 1.0)
            ve, vn = sp * math.cos(ang), sp * math.sin(ang)
        life = float(np.clip(land_time(y, vu, self.a, size / 2) + 0.04, 0.25, 2.4))
        n0, e0 = rng.uniform(-origin, origin), rng.uniform(-origin, origin)
        return ("CUSTOMPARTICLE %d %.2f %s,%s,%s %s,%s,%s %.3f %.2f" % (item.id, life, fmt(n0), fmt(y), fmt(e0), fmt(ve), fmt(vu), fmt(vn), scale, self.g),
                life, (ve, vn))

    def mist(self, y: float):
        item = self.lib.items["mist"]
        size = self._world_size("mist") * self.rng.uniform(0.8, 1.2)
        ang = self.rng.uniform(0, 2 * math.pi)
        sp = 40 * self.f
        return "CUSTOMPARTICLE %d %.2f %s,%s,%s %s,%s,%s %.3f %.2f" % (
            item.id, 0.55, "0", fmt(y), "0", fmt(sp * math.cos(ang)), fmt(40), fmt(sp * math.sin(ang)), size / item.img.shape[0], 0.6)

    def part_particle(self, name: str, vu_lo=150.0, vu_hi=300.0, speed=(70.0, 150.0)):
        """Action throwing one body part, and (landing time, displacement n, e)."""
        rng = self.rng
        p = next(x for x in self.parts if x.name == name)
        item = self.lib.items["%s:%s" % (self.enemy.name, name)]
        sc = self.enemy.scale * UNITS_PER_PIXEL
        y = (self.body.canvas[1] - p.cy) * sc
        y = max(y, p.img.shape[0] * sc / 2 + 1)
        ang = rng.uniform(0, 2 * math.pi)
        sp = rng.uniform(*speed) * self.f
        vu = rng.uniform(vu_lo, vu_hi) * self.f
        ve, vn = sp * math.cos(ang), sp * math.sin(ang)
        floor = p.img.shape[0] * sc / 2
        t = land_time(y, vu, self.a, floor)
        n0, e0 = rng.uniform(-0.1, 0.1) * self.W, rng.uniform(-0.1, 0.1) * self.W
        act = "CUSTOMPARTICLE %d %.2f %s,%s,%s %s,%s,%s %.3f %.2f" % (item.id, t + 0.04, fmt(n0), fmt(y), fmt(e0), fmt(ve), fmt(vu), fmt(vn), sc, self.g)
        return act, t, (vn * t, ve * t), y

    def lying(self, part_name: str, t: float, disp):
        """SPAWN action for a part lying where it landed (only when its decoration exists)."""
        name = self.decos.get(part_name)
        if not name:
            return None
        dn, de = disp
        d = math.hypot(dn, de)
        cap = 85.0
        if d > cap:
            dn, de = dn * cap / d, de * cap / d
        return "SPAWN %s %s,0,%s 0,0" % (name, fmt(dn), fmt(de))

    def pool_spawn(self):
        name = self.decos.get("pool")
        return "SPAWN %s 0,0,0 0,0" % name if name else None

    # ---- hurt ----
    def _vars(self):
        lo, hi = int(30 * self.f), int(150 * self.f)
        return ["SETVAR bgva RANDOM(%d,%d)" % (lo, hi), "SETVAR bgvb RANDOM(%d,%d)" % (lo, hi), "SETVAR bgvn $bgva", "MULTIPLY bgvn -1",
                "SETVAR bgvm $bgvb", "MULTIPLY bgvm -1"]

    def _var_drop(self, i: int, y: float, vu: float):
        combos = (("$bgva", "$bgvb"), ("$bgvn", "$bgvb"), ("$bgva", "$bgvm"), ("$bgvn", "$bgvm"))
        a, b = combos[i % 4]
        key = self.rng.choice(["dropS", "dropM", "dropL", "dropM", "clot"])
        item = self.lib.items[key]
        size = self._world_size(key) * self.rng.uniform(0.8, 1.2)
        life = float(np.clip(land_time(y, vu, self.a, size / 2) + 0.04, 0.25, 2.4))
        return "CUSTOMPARTICLE %d %.2f 0,%s,0 %s,%s,%s %.3f %.2f" % (item.id, life, fmt(y), a, fmt(vu), b, size / item.img.shape[0], self.g)

    def spray(self, count: int, ylo: float, yhi: float, speed: float, vu_lo: float, vu_hi: float, with_vars=True):
        out = []
        if with_vars:
            out += self._vars()
        for i in range(count):
            y = self.rng.uniform(ylo, yhi) * self.H
            vu = self.rng.uniform(vu_lo, vu_hi) * self.f
            if with_vars and i % 2 == 0:
                out.append(self._var_drop(i // 2, y, vu))
            else:
                out.append(self.drop(y, vu, speed * self.f)[0])
        return out

    def hurt_actions(self) -> list:
        n = self.lv["hurt"]
        out = self.spray(n, 0.45, 0.85, 150, 100, 300)
        for _ in range(self.lv["mist"]):
            out.append(self.mist(self.rng.uniform(0.5, 0.8) * self.H))
        return [(FRAME_DELAY, a) for a in out]

    # ---- death ----
    def death_inline(self) -> list:
        """Frames inserted into DEATH after its first frame: damage check, jumps, small-hit blood."""
        a = []
        a.append("SETVAR bgdmg LASTDAMAGE")
        a.append("SETVAR bgmax MAXHP")
        a.append("MULTIPLY bgmax %g" % self.gib_f)
        a.append("JUMPIFGEQUALS bgdmg $bgmax BGGIB")
        if self.tear_f is not None:
            a.append("SETVAR bgmax MAXHP")
            a.append("MULTIPLY bgmax %g" % self.tear_f)
            a.append("SETVAR bgroll RANDOM(0,%d)" % self.tear_count())
            a.append("JUMPIFGEQUALS bgdmg $bgmax BGTEAR")
        a += self.spray(self.lv["death"], 0.25, 0.8, 130, 80, 280)
        for _ in range(self.lv["mist"] // 2):
            a.append(self.mist(self.rng.uniform(0.4, 0.7) * self.H))
        pool = self.pool_spawn()
        if pool:
            a.append(pool)
        return [(FRAME_DELAY, x) for x in a]

    def tear_options(self) -> list:
        if not self.body.humanoid:
            return [p.name for p in self.parts if p.kind == "chunk"][:1]
        names = [n for n in ("armA", "armB", "legA", "legB") if any(p.name == n for p in self.parts)]
        if self.lv["head_tear"] and any(p.name == "head" for p in self.parts):
            names.append("head")
        return names

    def tear_count(self) -> int:
        return max(1, len(self.tear_options()))

    def tear_states(self, death_sprite: str, death_next: str, death_interp: str) -> list:
        """[(state name, next, interp, [(sprite, delay, action)])]"""
        opts = self.tear_options()
        if not opts:
            return []
        states = []
        names = []
        for i, part in enumerate(opts):
            nm = "BGTEAR%d" % i if len(opts) > 1 else "BGTEAR"
            names.append(nm)
            fr = [(death_sprite, FIRST_DELAY, "NONE")]
            fr.append((death_sprite, FRAME_DELAY, "SOUND %d" % (self.sound_base + 1)))
            act, t, disp, y = self.part_particle(part, 110, 210, (80, 150))
            fr.append((death_sprite, FRAME_DELAY, act))
            for _ in range(6 + 2 * (self.lv["death"] // 6)):
                d = self.drop(y + self.rng.uniform(-0.05, 0.05) * self.H, self.rng.uniform(120, 320) * self.f, 90 * self.f)[0]
                fr.append((death_sprite, FRAME_DELAY, d))
            pool = self.pool_spawn()
            if pool:
                fr.append((death_sprite, FRAME_DELAY, pool))
            lay = self.lying(part, t, disp)
            if lay:
                fr.append((death_sprite, "%.2f" % max(0.02, t), lay))
            states.append((nm, "BGDEATHCOPY", death_interp, fr))
        if len(opts) > 1:
            fr = [(death_sprite, FIRST_DELAY, "NONE")]
            for i, nm in enumerate(names[:-1]):
                fr.append((death_sprite, FRAME_DELAY, "JUMPIFLESS bgroll %d %s" % (i + 1, nm)))
            fr.append((death_sprite, FRAME_DELAY, "JUMPIFGEQUALS bgroll 0 %s" % names[-1]))
            states.insert(0, ("BGTEAR", "BGDEATHCOPY", death_interp, fr))
        return states

    def gib_variant(self, r: str) -> list:
        """One full gib burst, frames use the remains sprite r."""
        rng = self.rng
        fr = [(r, FIRST_DELAY, "NONE"), (r, FRAME_DELAY, "SOUND %d" % self.sound_base)]
        events = []     # (landing time, action)
        thrown = [p for p in self.parts]
        n = max(len(thrown), 1)
        base_ang = rng.uniform(0, 2 * math.pi)
        for i, p in enumerate(thrown):
            act, t, disp, _y = self.part_particle(p.name, 110, 230, (60, 150))
            fr.append((r, FRAME_DELAY, act))
            lay = self.lying(p.name, t, disp)
            if lay and p.kind != "chunk" or (lay and rng.uniform() < 0.5):
                events.append((t, lay))
        fr += [(r, FRAME_DELAY, a) for a in self.spray(self.lv["burst"], 0.1, 0.9, 190, 90, 280)]
        for _ in range(self.lv["mist"] + 1):
            fr.append((r, FRAME_DELAY, self.mist(rng.uniform(0.3, 0.7) * self.H)))
        pool = self.pool_spawn()
        if pool:
            events.append((0.18, pool))
        events.sort(key=lambda e: e[0])
        last = 0.0
        for t, act in events:
            fr.append((r, "%.2f" % max(0.02, t - last), act))
            last = max(last, t)
        fr.append((r, "0.2", "NONE"))
        return fr

    def gib_states(self, remains_sprite: str, nxt: str) -> list:
        v = self.lv["variants"]
        out = []
        if v == 1:
            return [("BGGIB", nxt, "0", self.gib_variant(remains_sprite))]
        for i in range(v):
            out.append(("BGGIB%d" % i, nxt, "0", self.gib_variant(remains_sprite)))
        fr = [(remains_sprite, FIRST_DELAY, "NONE"), (remains_sprite, FRAME_DELAY, "SETVAR bgroll RANDOM(0,%d)" % v)]
        for i in range(v - 1):
            fr.append((remains_sprite, FRAME_DELAY, "JUMPIFLESS bgroll %d BGGIB%d" % (i + 1, i)))
        fr.append((remains_sprite, FRAME_DELAY, "JUMPIFGEQUALS bgroll 0 BGGIB%d" % (v - 1)))
        out.insert(0, ("BGGIB", nxt, "0", fr))
        return out

    # ---- patching ----
    def apply(self, doc: StatesDoc, name: str) -> dict:
        """Patches the document in place. Returns a report dict; raises ValueError when the file cannot be patched."""
        st = doc.states()
        if "HURT" not in st or "DEATH" not in st or not st["HURT"]["frames"] or not st["DEATH"]["frames"]:
            raise ValueError("no HURT or DEATH state with frames")
        if any(s in st for s in OWN_STATES) or any(s.startswith("BG") for s in st):
            raise ValueError("the file already contains BG states; run uninstall first")
        img_last = doc.last_index("image")
        if img_last is None:
            raise ValueError("no image line")
        self.remains_index = doc.image_count()
        self.sound_base = doc.sound_count()
        remains = str(self.remains_index)
        hurt, death = st["HURT"], st["DEATH"]
        hs0 = doc.frame_sprite(hurt["frames"][0])
        ds0 = doc.frame_sprite(death["frames"][0])
        edits = []
        edits.append((hurt["frames"][0], ["frame %s %s 0 0 0 %s" % (hs0, d, a) for d, a in self.hurt_actions()]))
        edits.append((death["frames"][0], ["frame %s %s 0 0 0 %s" % (ds0, d, a) for d, a in self.death_inline()]))
        snd_last = doc.last_index("sound")
        sound_lines = ["sound %s" % s for s in SOUND_NAMES]
        edits.append((snd_last if snd_last is not None else img_last, sound_lines))
        edits.append((img_last, ["image %sBgRemains 0 0" % name]))
        if snd_last is not None and snd_last > img_last:
            pass
        # death copy of the original frames (for the limb tear path)
        orig_death = [doc.lines[i] for i in death["frames"]]
        d_next, d_interp = death["next"], death["interp"]
        new_states = []
        gib_next = "DEAD" if self.enemy.remove_at_death else "BGREMAINS"
        new_states += self.gib_states(remains, gib_next)
        if self.tear_f is not None:
            new_states += self.tear_states(ds0, "DEAD", d_interp)
        new_lines = []
        for nm, nxt, interp, frames in new_states:
            new_lines.append("state %s %s %s" % (nm, nxt, interp))
            for spr, delay, act in frames:
                new_lines.append("frame %s %s 0 0 0 %s" % (spr, delay, act))
        if not self.enemy.remove_at_death:
            new_lines.append("state BGREMAINS NONE 0")
            new_lines += ["frame %s 0.1 0 0 0 NONE" % remains, "frame %s 0.25 0 0 0 NONE" % remains, "frame %s 0.25 0 0 0 NONE" % remains]
        if self.tear_f is not None and self.tear_options():
            new_lines.append("state BGDEATHCOPY %s %s" % (d_next, d_interp))
            for k, ln in enumerate(orig_death):
                w = ln.split()
                if k == 0 and len(w) > 2:
                    w[2] = FRAME_DELAY
                    ln = " ".join(w)
                new_lines.append(ln)
        # the tear states return to BGDEATHCOPY, which continues with the enemy's own death frames
        edits.sort(key=lambda e: e[0], reverse=True)
        inserted = []
        for idx, lines in edits:
            doc.insert_after(idx, lines)
            inserted += lines
        doc.append(new_lines)
        inserted += new_lines
        return dict(inserted=inserted, sound_base=self.sound_base, remains_index=self.remains_index, lines=len(inserted))


# ---- reverting ----

def revert_states_text(text: str, particle_ids: set, sound_indices: set) -> str:
    """Removes everything this tool inserted: BG* states, signature frames, our image and sound lines."""
    doc = StatesDoc(text)
    out = []
    skipping = False
    act_rx = re.compile(r"^frame \S+ \S+ \S+ \S+ \S+ (.*)$")
    for line in doc.lines:
        w = line.split()
        if w and w[0] == "state":
            skipping = len(w) > 1 and w[1].startswith("BG")
            if skipping:
                continue
        elif skipping:
            if w and w[0] in ("image", "sound"):
                skipping = False
            else:
                continue
        if w and w[0] == "image" and len(w) > 1 and w[1].endswith("BgRemains"):
            continue
        if w and w[0] == "sound" and len(w) > 1 and w[1] in SOUND_NAMES:
            continue
        m = act_rx.match(line)
        if m:
            act = m.group(1).split()
            if act:
                a0 = act[0]
                if a0 in ("SETVAR", "MULTIPLY") and len(act) > 1 and act[1].startswith("bg"):
                    continue
                if a0.startswith("JUMPIF") and len(act) > 1 and act[1].startswith("bg"):
                    continue
                if a0 == "SPAWN" and len(act) > 1 and act[1].startswith("Bg"):
                    continue
                if a0 == "CUSTOMPARTICLE" and len(act) > 1 and act[1].isdigit() and int(act[1]) in particle_ids:
                    continue
                if a0 == "SOUND" and len(act) > 1 and act[1].isdigit() and int(act[1]) in sound_indices:
                    continue
        out.append(line)
    doc.lines = out
    return doc.text()
