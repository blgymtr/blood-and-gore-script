"""Command line: scan, install, uninstall, preview."""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

from . import __version__, analyze, apply as applymod, art, cutter, fsmgen, imaging as im, planner, registry
from .project import Project, ProjectError
from .safety import Guard, SafetyError, find_engine_root, is_inside

SAFETY_BANNER = "Blood and Gore only changes files inside the project folder. It never touches the editor, Game.exe or any dll."


def out(*a):
    print(*a)


def _project(args):
    try:
        return Project(args.project)
    except ProjectError as exc:
        raise SystemExit("error: %s" % exc)


def _engine(args, project):
    if getattr(args, "engine", None):
        return os.path.realpath(args.engine)
    return find_engine_root(project.root)


def _guard(args, project, lister=None):
    try:
        return Guard(project.root, _engine(args, project), lister)
    except SafetyError as exc:
        raise SystemExit("refused: %s" % exc)


def cmd_scan(args, lister=None):
    pr = _project(args)
    out("Project: %s" % pr.root)
    out("Project version: %s%s" % (pr.version or "unknown", "" if pr.version_ok() else "   (this tool is written for 1.10.x)"))
    eng = _engine(args, pr)
    out("Editor folder: %s" % (eng or "not found next to the project"))
    for p in pr.problems:
        out("warning: " + p)
    state = applymod.load_state(pr.root)
    out("Blood and Gore install: %s" % ("level %s" % state["level"] if state else "none"))
    out("Custom particles in the project: %d" % len(pr.custom_particle_indices()))
    out("Enemies: %d" % len(pr.enemies))
    models = pr.model_entities()
    for e in sorted(pr.enemies, key=lambda x: x.name):
        files = pr.frame_files(e.name)
        line = "  %-20s id %-3d hp %-5g scale %-5g radius %-4g frames %-3d states %s" % (e.name, e.id, e.hp, e.scale, e.radius, len(files), "yes" if pr.has_states(e.name) else "no (editor default)")
        out(line)
        if e.name in models:
            out("      3D model enemy, the tool leaves it alone")
            continue
        if not files:
            out("      no sprites found in Sprites/Enemies")
            continue
        read, _c = planner.original_bytes_reader(pr, state)
        idle = 0
        orig = read("States/%s.states" % e.name)
        if orig is not None:
            from .states import StatesDoc
            d = StatesDoc(orig.decode("utf-8", "replace"))
            st = d.states()
            if "IDLE" in st and st["IDLE"]["frames"]:
                try:
                    idle = int(d.frame_sprite(st["IDLE"]["frames"][0]))
                except ValueError:
                    idle = 0
        path = files.get(idle) or files[min(files)]
        try:
            img = im.read_png(path)
        except ValueError:
            out("      sprite is not a readable PNG: %s" % os.path.basename(path))
            continue
        b = analyze.analyze(img)
        out("      sprite %dx%d  %s" % (img.shape[1], img.shape[0], b.label()))
        if b.ok:
            out("      body %dx%d px: head %d, neck at %d, hip at %d" % (b.w, b.h, round(b.head_h), round(b.y_neck), round(b.y_hip)))
    out("Nothing was written.")
    return 0


def print_plan(plan, pr, prev):
    out("")
    out("Gore level %d. Enemies:" % plan.level)
    for r in plan.enemies:
        if r.status == "ok":
            out("  + %-20s %s%s" % (r.name, r.body.label(), "  [states file will be generated]" if r.generated_states else ""))
            out("      %d lines added to States/%s.states, %d decorations" % (r.lines, r.name, len(r.decorations)))
        else:
            out("  - %-20s skipped: %s" % (r.name, r.reason))
    out("")
    created = [r for r in sorted(plan.files) if not os.path.exists(os.path.join(pr.root, *r.split("/")))]
    changed = [r for r in sorted(plan.files) if r not in created]
    out("Files to create (%d):" % len(created))
    for r in created[:40]:
        out("    " + r)
    if len(created) > 40:
        out("    ... and %d more" % (len(created) - 40))
    out("Files to replace (%d, each is backed up first):" % len(changed))
    for r in changed:
        out("    " + r)
    if plan.register:
        out("Decorations to add through the editor's own code (%d):" % len(plan.register))
        out("    " + ", ".join(s.name for s in plan.register[:12]) + (" ..." if len(plan.register) > 12 else ""))
        out("    Decorations.dat (and the other .dat files the editor rewrites) are backed up first.")
    if plan.particle_ids:
        ids = [i for _k, i, _f in plan.particle_ids]
        out("Particle ids used: %d..%d (%d images, each stored in two Custom files)" % (min(ids), max(ids), len(ids)))
    for n in plan.notes:
        out("note: " + n)
    for w in plan.warnings:
        out("WARNING: " + w)
    for m in plan.manual:
        out(m)


def cmd_install(args, lister=None):
    pr = _project(args)
    if not pr.version_ok() and not args.allow_version:
        raise SystemExit("refused: the project is version %r; this tool is written for Easy FPS Editor CE 1.10.x. Use --allow-version to try anyway." % pr.version)
    guard = _guard(args, pr, lister)
    prev = applymod.load_state(pr.root)
    engine = _engine(args, pr)
    out(SAFETY_BANNER)
    if args.gore == 0:
        out("Gore level 0 means only the engine's own blood: the tool's install is removed.")
        return cmd_uninstall(args, lister)
    names = [n.strip() for n in args.enemies.split(",") if n.strip()] if args.enemies else None
    if names:
        known = {e.name for e in pr.enemies}
        bad = [n for n in names if n not in known]
        if bad:
            raise SystemExit("error: unknown enemies: %s (known: %s)" % (", ".join(bad), ", ".join(sorted(known))))
    plan = planner.build_plan(pr, args.gore, names, args.seed, args.gib_threshold, args.tear_threshold, not args.no_decorations, not args.no_sound, prev, engine)
    print_plan(plan, pr, prev)
    if not any(r.status == "ok" for r in plan.enemies):
        out("Nothing to install.")
        return 1
    if not args.apply:
        out("")
        out("DRY RUN: nothing was written. Add --apply to install. Close the editor and Game.exe first.")
        return 0
    if applymod.up_to_date(pr, plan, prev):
        out("")
        out("Already installed with these settings: nothing to do.")
        return 0
    try:
        applymod.install(pr, plan, guard, prev, engine)
    except SafetyError as exc:
        raise SystemExit("refused: %s" % exc)
    except (OSError, RuntimeError) as exc:
        raise SystemExit("failed, the project was restored from the backup: %s" % exc)
    out("")
    out("Installed. Backups are in %s." % os.path.join(pr.root, applymod.BACKUP_DIR))
    out("Test it: open the project in the editor, then Build > Test Game, and shoot an enemy.")
    out("Undo it any time with:  python bloodgore.py uninstall --project \"%s\" --apply" % pr.root)
    return 0


def cmd_uninstall(args, lister=None):
    pr = _project(args)
    guard = _guard(args, pr, lister)
    state = applymod.load_state(pr.root)
    if not state:
        out("Nothing to uninstall: no Blood and Gore install is recorded in this project.")
        return 0
    out("Install found (level %s). It will:" % state["level"])
    created = [r for r, i in state["files"].items() if i.get("created")]
    modified = [r for r, i in state["files"].items() if not i.get("created")]
    out("  delete %d files this tool created" % len(created))
    out("  restore %d files from the backup: %s" % (len(modified), ", ".join(sorted(modified))))
    if state.get("registered"):
        out("  remove %d decorations it registered" % len(state["registered"]))
    if not args.apply:
        out("DRY RUN: nothing was written. Add --apply to uninstall.")
        return 0
    try:
        msgs = applymod.uninstall(pr, guard, state, _engine(args, pr))
    except SafetyError as exc:
        raise SystemExit("refused: %s" % exc)
    for m in msgs:
        out("  " + m)
    out("Uninstalled. The folder %s still holds the backups; delete it when you do not need them." % applymod.BACKUP_DIR)
    return 0


def cmd_preview(args, lister=None):
    pr = _project(args)
    if is_inside(os.path.abspath(args.out), pr.root):
        raise SystemExit("refused: the preview image must be written outside the project folder")
    e = pr.enemy(args.enemy)
    if not e:
        raise SystemExit("error: unknown enemy %r" % args.enemy)
    files = pr.frame_files(e.name)
    if not files:
        raise SystemExit("error: no sprites for %s" % e.name)
    idle = 0
    if pr.has_states(e.name):
        from .states import StatesDoc
        d = StatesDoc(planner.read_text(pr.states_path(e.name)))
        st = d.states()
        if "IDLE" in st and st["IDLE"]["frames"]:
            try:
                idle = int(d.frame_sprite(st["IDLE"]["frames"][0]))
            except ValueError:
                pass
    frame = im.read_png(files.get(idle) or files[min(files)])
    body = analyze.analyze(frame)
    if not body.ok:
        raise SystemExit("error: " + body.label())
    lv = fsmgen.LEVELS[args.gore]
    rng = np.random.default_rng(fsmgen.seed_for(e.name, args.seed))
    parts, ctx = cutter.cut_parts(frame, body, rng, chunks=lv["chunks"])
    remains = cutter.make_remains(frame, body, ctx, rng)
    lib = fsmgen.Library(0)
    fsmgen.blood_library(lib, args.seed)
    cells = [frame] + [p.img for p in parts] + [remains]
    cells += [cutter.lying_decoration(p, rng) for p in parts if p.kind != "chunk"][:3]
    cells += [art.pool(int(max(body.w * 1.5, 16)), rng)]
    cells += [it.img for it in lib.items.values()]
    sheet = contact_sheet(cells, args.zoom)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "wb") as f:
        f.write(im.encode_png(sheet))
    out("Detected: %s" % body.label())
    out("Contact sheet written to %s (nothing in the project was written)" % args.out)
    return 0


def contact_sheet(images: list, zoom: int = 4, cols: int = 8) -> np.ndarray:
    bg = (46, 44, 42)
    cell_w = max(i.shape[1] for i in images) * zoom + 12
    cell_h = max(i.shape[0] for i in images) * zoom + 12
    cell_w = min(cell_w, 280)
    cell_h = min(cell_h, 360)
    rows = (len(images) + cols - 1) // cols
    sheet = np.empty((rows * cell_h, cols * cell_w, 3), np.uint8)
    sheet[:] = bg
    for n, img in enumerate(images):
        z = zoom
        h, w = img.shape[:2]
        while (h * z > cell_h - 8 or w * z > cell_w - 8) and z > 1:
            z -= 1
        big = im.cv2.resize(img, (w * z, h * z), interpolation=im.cv2.INTER_NEAREST)
        comp = im.composite_on(bg, big)
        r, c = divmod(n, cols)
        y = r * cell_h + (cell_h - comp.shape[0]) // 2
        x = c * cell_w + (cell_w - comp.shape[1]) // 2
        sheet[y:y + comp.shape[0], x:x + comp.shape[1]] = comp
    return sheet


def build_parser():
    ap = argparse.ArgumentParser(prog="bloodgore", description="Blood and gore for Easy FPS Editor CE projects. " + SAFETY_BANNER)
    ap.add_argument("--version", action="version", version="bloodgore " + __version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--project", required=True, help="path of the project folder (.../Projects/<Name>)")
        p.add_argument("--engine", help="folder with EasyFPSEditor_CE.exe (found automatically when the project is inside Projects)")

    s = sub.add_parser("scan", help="read only: list enemies and detected body structure")
    common(s)
    s.set_defaults(fn=cmd_scan)
    i = sub.add_parser("install", help="add gore (dry run unless --apply)")
    common(i)
    i.add_argument("--gore", type=int, default=2, choices=range(0, 5), help="0 only engine blood (removes the install), 1 low, 2 normal, 3 high, 4 extreme")
    i.add_argument("--enemies", help="comma separated enemy names (default: all)")
    i.add_argument("--apply", action="store_true", help="really write; without it only the plan is printed")
    i.add_argument("--gib-threshold", type=float, help="a hit of at least this share of the enemy's max health gibs it (default depends on the gore level)")
    i.add_argument("--tear-threshold", type=float, help="a hit of at least this share tears off a limb (default depends on the gore level)")
    i.add_argument("--no-decorations", action="store_true", help="particles only: no lying parts, no pools")
    i.add_argument("--no-sound", action="store_true", help="install silent sound files")
    i.add_argument("--seed", type=int, default=1, help="changes the random look of the parts")
    i.add_argument("--allow-version", action="store_true", help="try projects that are not version 1.10.x")
    i.set_defaults(fn=cmd_install)
    u = sub.add_parser("uninstall", help="restore the project (dry run unless --apply)")
    common(u)
    u.add_argument("--apply", action="store_true")
    u.set_defaults(fn=cmd_uninstall)
    pv = sub.add_parser("preview", help="render the parts into a contact sheet, writes nothing in the project")
    common(pv)
    pv.add_argument("--enemy", required=True)
    pv.add_argument("--out", required=True, help="output PNG (outside the project)")
    pv.add_argument("--gore", type=int, default=2, choices=range(1, 5))
    pv.add_argument("--seed", type=int, default=1)
    pv.add_argument("--zoom", type=int, default=4)
    pv.set_defaults(fn=cmd_preview)
    return ap


def main(argv=None, lister=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args, lister)
    except SafetyError as exc:
        raise SystemExit("refused: %s" % exc)
