"""Reads an Easy FPS Editor project. Read only: nothing in this module writes."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,40}$")
SUPPORTED_VERSION = "1.10"


@dataclass
class EnemyDef:
    name: str
    id: int
    hp: float = 50.0
    damage: float = 0.0
    speed: float = 100.0
    attack_delay: float = 1.0
    shooting: bool = False
    scale: float = 1.0
    radius: float = 15.0
    remove_at_death: bool = False


@dataclass
class DecoDef:
    name: str
    id: int


def _f(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def parse_enemies(text: str):
    """13 lines per enemy: name id, hp, damage, speed, attack delay, shooting, projectiles, scale, drop type, drop index,
    remove at death, radius, 'explosion projectile-speed fullbright spread'. Returns (enemies, problems)."""
    lines = text.replace("\r", "").split("\n")
    out, problems, i = [], [], 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        rec = lines[i:i + 13]
        m = re.match(r"^(\S+) (\d+)$", rec[0]) if len(rec) == 13 else None
        if not m:
            problems.append("Enemies.dat: unexpected record near line %d" % (i + 1))
            break
        out.append(EnemyDef(m.group(1), int(m.group(2)), _f(rec[1], 50), _f(rec[2], 0), _f(rec[3], 100), _f(rec[4], 1), rec[5].strip() == "1",
                            _f(rec[7], 1.0) or 1.0, _f(rec[11], 15), rec[10].strip() == "1"))
        i += 13
    return out, problems


def parse_decorations(text: str):
    """8 lines per decoration: name id, hp, breakable, solid, scale, drop type, drop index, 'radius lighted align culling remove'."""
    lines = text.replace("\r", "").split("\n")
    out, problems, i = [], [], 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        rec = lines[i:i + 8]
        m = re.match(r"^(\S+) (\d+)$", rec[0]) if len(rec) == 8 else None
        if not m:
            problems.append("Decorations.dat: unexpected record near line %d" % (i + 1))
            break
        out.append(DecoDef(m.group(1), int(m.group(2))))
        i += 8
    return out, problems


def read_text(path: str) -> str:
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


class ProjectError(Exception):
    pass


class Project:
    def __init__(self, root: str):
        self.root = os.path.realpath(os.path.abspath(root))
        if not os.path.isdir(self.root):
            raise ProjectError("not a folder: " + root)
        if not (os.path.isfile(self.p("Game.dat")) and os.path.isfile(self.p("Enemies.dat"))):
            raise ProjectError("this does not look like an Easy FPS Editor project (Game.dat or Enemies.dat is missing): " + self.root)
        self.problems = []
        gl = read_text(self.p("Game.dat")).replace("\r", "").split("\n")
        self.version = gl[0].strip() if gl else ""
        self.enemies, pr = parse_enemies(read_text(self.p("Enemies.dat")))
        self.problems += pr
        self.decorations = []
        if os.path.isfile(self.p("Decorations.dat")):
            self.decorations, pr = parse_decorations(read_text(self.p("Decorations.dat")))
            self.problems += pr

    def p(self, *a) -> str:
        return os.path.join(self.root, *a)

    def version_ok(self) -> bool:
        return self.version.startswith(SUPPORTED_VERSION)

    def enemy(self, name: str):
        for e in self.enemies:
            if e.name == name:
                return e
        return None

    def frame_files(self, name: str) -> dict:
        """{index: path} of Sprites/Enemies/<name><N>.png."""
        d = self.p("Sprites", "Enemies")
        out = {}
        if not os.path.isdir(d):
            return out
        rx = re.compile("^" + re.escape(name) + r"(\d+)\.png$")
        for fn in os.listdir(d):
            m = rx.match(fn)
            if m:
                out[int(m.group(1))] = os.path.join(d, fn)
        return out

    def states_path(self, name: str) -> str:
        return self.p("States", name + ".states")

    def has_states(self, name: str) -> bool:
        return os.path.isfile(self.states_path(name))

    def custom_particle_indices(self) -> set:
        d = self.p("Sprites", "Particles")
        out = set()
        if os.path.isdir(d):
            for fn in os.listdir(d):
                m = re.match(r"^Custom(\d+)\.png$", fn)
                if m:
                    out.add(int(m.group(1)))
        return out

    def model_entities(self) -> set:
        """Names that appear in Models.dat (3D model enemies are left alone)."""
        p = self.p("Models.dat")
        if not os.path.isfile(p):
            return set()
        return set(re.findall(r"[A-Za-z0-9]+", read_text(p)))

    def taken_names(self) -> set:
        return {e.name.lower() for e in self.enemies} | {d.name.lower() for d in self.decorations}
