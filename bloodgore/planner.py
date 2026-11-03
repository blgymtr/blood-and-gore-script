"""Builds the full install plan in memory: files to create or replace, decorations to register. Writes nothing."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import numpy as np

from . import analyze, art, cutter, fsmgen, imaging as im, registry, soundgen
from .project import NAME_RE, Project, read_text
from .states import StatesDoc, basic_enemy_states, missing_states


@dataclass
class EnemyReport:
    name: str
    status: str = "skipped"       # ok / skipped
    reason: str = ""
    frames: int = 0
    size: tuple = (0, 0)
    scale: float = 1.0
    radius: float = 15.0
    has_states: bool = False
    body: object = None
    generated_states: bool = False
    particles: int = 0
    decorations: list = field(default_factory=list)
    lines: int = 0


@dataclass
class Plan:
    level: int
    files: dict = field(default_factory=dict)        # rel path -> bytes (create or replace)
    states_new: set = field(default_factory=set)     # rel paths of .states files that did not exist
    enemies: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    register: list = field(default_factory=list)     # DecoSpec to add to Decorations.dat
    deco_mode: str = "none"                          # register / existing / none
    particle_ids: list = field(default_factory=list)
    sound_files: list = field(default_factory=list)
    manual: list = field(default_factory=list)
    params: dict = field(default_factory=dict)
    states_info: dict = field(default_factory=dict)
    sound_indices: list = field(default_factory=list)


def _rel(*a) -> str:
    return "/".join(a)


def original_bytes_reader(project: Project, prev):
    """read(rel) -> bytes or None, as the project was before any install of this tool."""
    created = set()
    backups = {}
    if prev:
        for rel, info in prev["files"].items():
            if info.get("created"):
                created.add(rel)
            elif info.get("origin_backup"):
                backups[rel] = info["origin_backup"]

    def read(rel: str):
        if rel in created:
            return None
        if rel in backups:
            p = os.path.join(project.root, "bloodgore_backup", *backups[rel].split("/"))
            if os.path.isfile(p):
                with open(p, "rb") as f:
                    return f.read()
        p = os.path.join(project.root, *rel.split("/"))
        if os.path.isfile(p):
            with open(p, "rb") as f:
                return f.read()
        return None
    return read, created


def loaded_images(doc: StatesDoc, enemy_name: str) -> list:
    """File paths (relative to Sprites/Enemies) for each loaded image index, in load order."""
    out = []
    for ln in doc.lines:
        w = ln.split()
        if not w or w[0] != "image" or len(w) < 2:
            continue
        if len(w) >= 4 and w[2].lstrip("-").isdigit() and w[3].lstrip("-").isdigit():
            for i in range(int(w[2]), int(w[3]) + 1):
                out.append("%s%d.png" % (w[1], i))
        else:
            out.append(w[1] + ".png")
    return out


def build_plan(project: Project, level: int, enemies=None, seed: int = 1, gib_threshold=None, tear_threshold=None,
               decorations: bool = True, sound: bool = True, prev=None, engine_root=None) -> Plan:
    lv = fsmgen.LEVELS[level]
    plan = Plan(level=level)
    plan.params = dict(level=level, seed=seed, gib=gib_threshold, tear=tear_threshold, decorations=decorations, sound=sound)
    read, created_before = original_bytes_reader(project, prev)
    # particle numbering: after the highest file that was not created by an earlier install of this tool
    custom = set(project.custom_particle_indices())
    if prev:
        custom -= {int(i) for i in prev.get("particle_files", [])}
    if custom and (max(custom) + 1 != len(custom)):
        missing = sorted(set(range(max(custom) + 1)) - custom)
        plan.warnings.append("Sprites/Particles has gaps in the Custom numbering (missing Custom%d...). The engine only loads Custom files without gaps, "
                             "so fix this first or the new particles will not load." % missing[0])
    lib = fsmgen.Library((max(custom) + 1) if custom else 0)
    fsmgen.blood_library(lib, seed)

    ok_reg, why = registry.can_register(engine_root)
    existing_decos = {d.name for d in project.decorations}
    if not decorations:
        plan.deco_mode = "none"
    elif ok_reg:
        plan.deco_mode = "register"
    else:
        plan.deco_mode = "existing"
        plan.notes.append("Decorations are not added automatically: " + why)

    names = [e.name for e in project.enemies]
    wanted = enemies if enemies else names
    models = project.model_entities()
    taken = project.taken_names()
    per_enemy = []
    for e in sorted(project.enemies, key=lambda x: x.name):
        rep = EnemyReport(e.name, scale=e.scale, radius=e.radius)
        plan.enemies.append(rep)
        if e.name not in wanted:
            rep.reason = "not selected"
            continue
        if not NAME_RE.match(e.name):
            rep.reason = "name has characters other than letters, digits and underscore"
            continue
        if e.name in models:
            rep.reason = "3D model enemy (the tool works on sprite enemies)"
            continue
        files = project.frame_files(e.name)
        rep.frames = len(files)
        if not files:
            rep.reason = "no sprites found in Sprites/Enemies"
            continue
        sp = project.states_path(e.name)
        rel_states = _rel("States", e.name + ".states")
        orig = read(rel_states)
        rep.has_states = orig is not None
        if orig is not None:
            text = orig.decode("utf-8-sig", "replace") if not orig.startswith(b"\xef\xbb\xbf") else orig.decode("utf-8-sig")
            if orig.startswith(b"\xef\xbb\xbf"):
                plan.warnings.append("%s: States file starts with a byte order mark; skipped to keep it intact" % e.name)
                rep.reason = "states file has a BOM"
                continue
        else:
            n = max(files) + 1
            if sorted(files) != list(range(n)):
                rep.reason = "sprite numbering has gaps, cannot build default states"
                continue
            text = basic_enemy_states(e.name, n, e.attack_delay, e.shooting)
            rep.generated_states = True
            if n != 13:
                plan.warnings.append("%s has no .states file and %d sprites (the standard layout has 13): the generated state machine splits them in the same proportions, "
                                     "check the animation in the editor or skip this enemy with --enemies" % (e.name, n))
            else:
                plan.notes.append("%s has no .states file: generated from the manual's basic enemy template with its 13 sprites and attack delay %g" % (e.name, e.attack_delay))
        doc = StatesDoc(text)
        miss = missing_states(doc)
        if miss and not rep.generated_states:
            rep.reason = "states file lacks mandatory states: " + ", ".join(miss)
            continue
        imgs = loaded_images(doc, e.name)
        st = doc.states()
        idle_sprite = 0
        if "IDLE" in st and st["IDLE"]["frames"]:
            try:
                idle_sprite = int(doc.frame_sprite(st["IDLE"]["frames"][0]))
            except ValueError:
                idle_sprite = 0
        frames = {}
        for k in (idle_sprite, 0):
            if 0 <= k < len(imgs):
                p = os.path.join(project.root, "Sprites", "Enemies", imgs[k])
                if os.path.isfile(p):
                    try:
                        frames[k] = im.read_png(p)
                    except ValueError:
                        pass
        pick = analyze.pick_standing_frame(frames, idle_sprite)
        if pick is None:
            rep.reason = "no usable standing frame"
            continue
        frame = frames[pick]
        rep.size = (frame.shape[1], frame.shape[0])
        body = analyze.analyze(frame)
        rep.body = body
        if not body.ok:
            rep.reason = body.label()
            continue
        rng = np.random.default_rng(fsmgen.seed_for(e.name, seed))
        parts, ctx = cutter.cut_parts(frame, body, rng, chunks=lv["chunks"])
        if not parts:
            rep.reason = "could not cut any part from the sprite"
            continue
        remains = cutter.make_remains(frame, body, ctx, rng)
        for p in parts:
            lib.add("%s:%s" % (e.name, p.name), p.img)
        rep.status = "ok"
        per_enemy.append((e, rep, doc, parts, remains, body, rng, rel_states, text))

    # decorations per enemy
    for e, rep, doc, parts, remains, body, rng, rel_states, text in per_enemy:
        decos = {}
        specs = []
        if plan.deco_mode != "none":
            pool_img = art.pool(int(max(body.w * 1.5, 16)), rng, spread=1.0)
            specs.append(fsmgen.DecoSpec("Bg%sPool" % e.name, pool_img, e.scale))
            decos["pool"] = "Bg%sPool" % e.name
            lying_parts = [p for p in parts if p.kind != "chunk"] + [p for p in parts if p.kind == "chunk"][:2]
            for p in lying_parts:
                nm = "Bg%s%s" % (e.name, p.name[0].upper() + p.name[1:])
                specs.append(fsmgen.DecoSpec(nm, cutter.lying_decoration(p, rng), e.scale))
                decos[p.name] = nm
            clash = [s.name for s in specs if s.name.lower() in taken and s.name not in existing_decos]
            if clash:
                plan.warnings.append("%s: decoration name %s is already used by something else; lying parts skipped" % (e.name, clash[0]))
                decos, specs = {}, []
            elif plan.deco_mode == "existing":
                if not all(s.name in existing_decos for s in specs):
                    plan.manual += registry.manual_instructions(specs) if not plan.manual else [""] + ["  (%s) %s" % (e.name, ", ".join(s.name for s in specs))]
                    for s in specs:
                        plan.files[_rel("bloodgore_manual", "decorations", s.name + ".png")] = im.encode_png(s.img)
                    decos, specs = {}, []
        g = fsmgen.EnemyGore(e, body, parts, remains, lib, decos, lv, fsmgen.seed_for(e.name + "#fsm", seed), gib_threshold, tear_threshold)
        d2 = StatesDoc(text)
        try:
            info = g.apply(d2, e.name)
        except ValueError as exc:
            rep.status, rep.reason = "skipped", "cannot patch the states file: %s" % exc
            continue
        rep.lines = info["lines"]
        plan.states_info[rel_states] = info["sound_base"]
        plan.sound_indices += [info["sound_base"], info["sound_base"] + 1]
        rep.decorations = [s.name for s in specs]
        plan.files[rel_states] = d2.text().encode("utf-8")
        if rep.generated_states:
            plan.states_new.add(rel_states)
        plan.files[_rel("Sprites", "Enemies", "%sBgRemains0.png" % e.name)] = im.encode_png(remains)
        for s in specs:
            plan.files[_rel("Sprites", "Decorations", s.name + "0.png")] = im.encode_png(s.img)
            if s.name not in existing_decos:
                plan.register.append(s)
        if plan.deco_mode == "existing":
            plan.register = []

    ok_names = {r.name for r in plan.enemies if r.status == "ok"}
    for key, it in lib.items.items():
        if ":" not in key or key.split(":")[0] in ok_names:
            for idx in it.files:
                plan.files[_rel("Sprites", "Particles", "Custom%d.png" % idx)] = im.encode_png(it.img)
            plan.particle_ids.append((key, it.id, it.files))
    per_enemy = [p for p in per_enemy if p[1].status == "ok"]
    if per_enemy and sound:
        plan.files[_rel("Sounds", "BgGib.wav")] = soundgen.gib_burst()
        plan.files[_rel("Sounds", "BgTear.wav")] = soundgen.limb_tear()
        plan.sound_files = ["Sounds/BgGib.wav", "Sounds/BgTear.wav"]
    elif per_enemy:
        # without sounds the sound lines would point to missing files: still write silent files so the FSM loads
        silent = soundgen.silence()
        plan.files[_rel("Sounds", "BgGib.wav")] = silent
        plan.files[_rel("Sounds", "BgTear.wav")] = silent
        plan.sound_files = ["Sounds/BgGib.wav", "Sounds/BgTear.wav"]
    if not per_enemy:
        plan.files = {}
        plan.particle_ids = []
        plan.register = []
    return plan
