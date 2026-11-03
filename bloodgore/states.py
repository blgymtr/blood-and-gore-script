"""Reading and editing .states files (the Easy FPS Editor finite state machine files).

The format has no comments and is whitespace sensitive, so lines are kept exactly as they are and new lines
are only inserted, never rewritten. Reverting removes exactly the lines this tool inserted.
"""
from __future__ import annotations

import re


class StatesDoc:
    def __init__(self, text: str):
        self.nl = "\r\n" if "\r\n" in text else "\n"
        self.final_nl = text.endswith("\n")
        body = text.replace("\r\n", "\n")
        if body.endswith("\n"):
            body = body[:-1]
        self.lines = body.split("\n") if body != "" else []

    def text(self) -> str:
        t = self.nl.join(self.lines)
        return t + (self.nl if self.final_nl else "")

    def kind(self, i: int) -> str:
        w = self.lines[i].split()
        return w[0] if w else ""

    def image_count(self) -> int:
        n = 0
        for i in range(len(self.lines)):
            w = self.lines[i].split()
            if w and w[0] == "image":
                if len(w) >= 4 and w[2].lstrip("-").isdigit() and w[3].lstrip("-").isdigit():
                    n += int(w[3]) - int(w[2]) + 1
                else:
                    n += 1
        return n

    def sound_count(self) -> int:
        return sum(1 for i in range(len(self.lines)) if self.kind(i) == "sound")

    def last_index(self, kind: str):
        idx = [i for i in range(len(self.lines)) if self.kind(i) == kind]
        return idx[-1] if idx else None

    def states(self) -> dict:
        """{name: dict(header=i, end=j, next=str, interp=str, frames=[line indices])}; end is exclusive."""
        heads = [(i, self.lines[i].split()) for i in range(len(self.lines)) if self.kind(i) == "state"]
        out = {}
        for n, (i, w) in enumerate(heads):
            end = heads[n + 1][0] if n + 1 < len(heads) else len(self.lines)
            frames = [k for k in range(i + 1, end) if self.kind(k) == "frame"]
            out[w[1] if len(w) > 1 else ""] = dict(header=i, end=end, next=w[2] if len(w) > 2 else "NONE", interp=w[3] if len(w) > 3 else "0", frames=frames)
        return out

    def frame_sprite(self, i: int) -> str:
        w = self.lines[i].split()
        return w[1] if len(w) > 1 else "0"

    def insert_after(self, index: int, new_lines: list):
        self.lines[index + 1:index + 1] = new_lines

    def append(self, new_lines: list):
        self.lines.extend(new_lines)


# ---- default state machine for enemies that have no .states file --------------------------------

def standard_layout(n: int) -> dict:
    """Frame groups of the manual's basic enemy (13 frames: idle, 4 walk, 2 attack, 2 hurt, 3 death, corpse).
    Other frame counts are split in the same proportions."""
    if n == 13:
        return dict(idle=[0], walk=[1, 2, 3, 4], attack=[5, 6], hurt=[7, 8], death=[9, 10, 11], dead=[12])
    if n <= 1:
        return dict(idle=[0], walk=[0], attack=[0], hurt=[0], death=[0], dead=[0])
    dead = [n - 1]
    nd = min(3, max(1, (n - 2) // 3))
    death = list(range(n - 1 - nd, n - 1))
    nh = min(2, max(1, (n - 2) // 5))
    hurt = list(range(death[0] - nh, death[0]))
    na = min(2, max(1, (n - 2) // 5))
    attack = list(range(hurt[0] - na, hurt[0]))
    walk = list(range(1, attack[0])) or [0]
    return dict(idle=[0], walk=walk, attack=attack, hurt=hurt, death=death, dead=dead)


def basic_enemy_states(name: str, n_frames: int, attack_delay: float, shooting: bool) -> str:
    """The manual's basic enemy state machine for `n_frames` sprites. The attack keeps the enemy's attack delay."""
    g = standard_layout(n_frames)
    L = ["image %s 0 %d" % (name, n_frames - 1), ""]

    def state(nm, nxt, frames):
        L.append("state %s %s 0" % (nm, nxt))
        for k, (spr, delay, act) in enumerate(frames):
            L.append("frame %d %s 0 0 0 %s" % (spr, delay, act))
        L.append("")

    i0 = g["idle"][0]
    state("IDLE", "NONE", [(i0, "0.125", "NONE"), (i0, "0.125", "READY")])
    state("SEE", "CHASE", [(i0, "0.125", "NONE"), (i0, "0.125", "READY")])
    walk = [(s, "0.1", "NONE") for s in g["walk"]]
    walk[-1] = (walk[-1][0], "0.1", "READY")
    walk.insert(0, walk[0]) if len(walk) == 1 else None
    if len(walk) >= 2 and walk[0][2] == "READY":
        walk[0] = (walk[0][0], "0.1", "NONE")
    state("CHASE", "NONE", walk)
    a = g["attack"]
    wait = max(0.05, round(attack_delay - 0.6, 3))
    fr = [(a[0], "0.25", "NONE"), (a[0], "0.25", "NONE"), (a[-1], "0.0625", "NONE"), (a[-1], "0.0625", "ATTACK"), (a[0], "0.25", "NONE"), (a[0], "%g" % wait, "NONE"), (a[0], "0.01", "READY")]
    state("ATTACK", "CHASE", fr)
    h = g["hurt"]
    state("HURT", "CHASE", [(h[0], "0.125", "NONE")] + [(s, "0.125", "NONE") for s in h[1:]] + ([(h[-1], "0.125", "NONE")] if len(h) == 1 else []))
    d = g["death"]
    state("DEATH", "DEAD", [(d[0], "0.166", "NONE")] + [(s, "0.166", "NONE") for s in d[1:]] + ([(d[-1], "0.166", "NONE")] if len(d) == 1 else []))
    c = g["dead"][0]
    state("DEAD", "NONE", [(c, "0.125", "NONE"), (c, "0.125", "NONE")])
    return "\n".join(L).rstrip("\n") + "\n"


MANDATORY = ("IDLE", "SEE", "CHASE", "ATTACK", "HURT", "DEATH", "DEAD")


def missing_states(doc: StatesDoc) -> list:
    st = doc.states()
    return [s for s in MANDATORY if s not in st]


FRAME_RE = re.compile(r"^frame \S+ \S+ \S+ \S+ \S+ \S+")
