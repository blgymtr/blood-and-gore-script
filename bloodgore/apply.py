"""Applying a plan and undoing it: backups, manifest, rollback, uninstall.

Every file that will be touched is first copied to <project>/bloodgore_backup/<timestamp>/files/. The tool
remembers what it did in <project>/bloodgore_backup/state.json, so uninstall (and re-install) can restore the
original bytes and delete only the files the tool created.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil

from . import __version__, fsmgen, registry
from .safety import Guard, SafetyError, probe_writable
from .states import StatesDoc

BACKUP_DIR = "bloodgore_backup"
STATE_FILE = "state.json"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_file(path: str):
    if os.path.isfile(path):
        with open(path, "rb") as f:
            return f.read()
    return None


def load_state(project_root: str):
    p = os.path.join(project_root, BACKUP_DIR, STATE_FILE)
    if os.path.isfile(p):
        with open(p, encoding="utf8") as f:
            return json.load(f)
    return None


def _abs(project_root: str, rel: str) -> str:
    return os.path.join(project_root, *rel.split("/"))


def _root_dats(project_root: str) -> list:
    return sorted(fn for fn in os.listdir(project_root) if fn.lower().endswith(".dat") and os.path.isfile(os.path.join(project_root, fn)))


def stamp() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def _unique_dir(base: str, ts: str) -> str:
    d = os.path.join(base, ts)
    n = 1
    while os.path.exists(d):
        n += 1
        d = os.path.join(base, "%s_%d" % (ts, n))
    return d


def up_to_date(project, plan, prev) -> bool:
    """True when applying the plan again would change nothing."""
    if not prev or prev.get("params") != plan.params:
        return False
    for rel, info in prev["files"].items():
        if rel not in plan.files and not rel.endswith(".dat"):
            return False
    for rel, data in plan.files.items():
        if read_file(_abs(project.root, rel)) != data:
            return False
    if plan.deco_mode == "register":
        existing = {d.name for d in project.decorations}
        if {s.name for s in plan.register} - existing:
            return False
        wanted = {n for r in plan.enemies for n in r.decorations}
        if set(prev.get("registered", [])) - wanted:
            return False
    return True


def install(project, plan, guard: Guard, prev, engine_root, log=print) -> dict:
    guard.require_closed()
    root = project.root
    ts = stamp()
    bdir = _unique_dir(os.path.join(root, BACKUP_DIR), ts)
    bname = os.path.basename(bdir)
    prev_files = prev["files"] if prev else {}
    wanted_decos = {n for r in plan.enemies for n in r.decorations}
    prev_reg = set(prev.get("registered", [])) if prev else set()
    adds = [s for s in plan.register]
    dels = sorted(prev_reg - wanted_decos) if plan.deco_mode == "register" else []
    touching_dat = bool(adds or dels)

    stale_created = [r for r, i in prev_files.items() if i.get("created") and r not in plan.files]
    stale_modified = [r for r, i in prev_files.items() if not i.get("created") and r not in plan.files and not r.endswith(".dat")]
    dat_rels = [d for d in _root_dats(root)] if touching_dat else []
    touched = sorted(set(plan.files) | set(stale_created) | set(stale_modified) | set(dat_rels))
    for rel in touched:
        p = guard.check_write(_abs(root, rel))
        probe_writable(p)
    guard.check_write(os.path.join(root, BACKUP_DIR, STATE_FILE))

    pre = {rel: read_file(_abs(root, rel)) for rel in touched}
    os.makedirs(os.path.join(bdir, "files"), exist_ok=True)
    for rel, data in pre.items():
        if data is not None:
            dst = os.path.join(bdir, "files", *rel.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(data)
    manifest = dict(tool="bloodgore", version=__version__, time=ts, operation="install", level=plan.level, touched=touched,
                    backed_up=sorted(r for r, d in pre.items() if d is not None), will_create=sorted(r for r, d in pre.items() if d is None))
    with open(os.path.join(bdir, "manifest.json"), "w", encoding="utf8") as f:
        json.dump(manifest, f, indent=1)

    def rollback():
        for rel, data in pre.items():
            p = _abs(root, rel)
            try:
                if data is None:
                    if os.path.isfile(p):
                        os.remove(p)
                else:
                    with open(p, "wb") as f:
                        f.write(data)
            except OSError:
                pass

    try:
        for rel, data in plan.files.items():
            p = guard.check_write(_abs(root, rel))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(data)
        for rel in stale_created:
            p = _abs(root, rel)
            if os.path.isfile(p):
                os.remove(guard.check_write(p))
        for rel in stale_modified:
            info = prev_files[rel]
            src = os.path.join(root, BACKUP_DIR, *info["origin_backup"].split("/"))
            data = read_file(src)
            if data is not None:
                with open(guard.check_write(_abs(root, rel)), "wb") as f:
                    f.write(data)
        if touching_dat:
            ops = [dict(op="add", fields=registry.decoration_fields(s.name, s.scale)) for s in adds] + [dict(op="del", name=n) for n in dels]
            registry.run_host(engine_root, root, ops)
    except BaseException:
        rollback()
        raise

    files = {}
    for rel, data in plan.files.items():
        old = prev_files.get(rel)
        info = dict(old) if old else ({"created": True} if pre.get(rel) is None else dict(created=False, origin_backup="%s/files/%s" % (bname, rel), origin_sha=sha(pre[rel])))
        info["written_sha"] = sha(data)
        files[rel] = info
    for rel in dat_rels:
        now = read_file(_abs(root, rel))
        if now is not None and now != pre.get(rel):
            old = prev_files.get(rel)
            info = dict(old) if old else dict(created=False, origin_backup="%s/files/%s" % (bname, rel), origin_sha=sha(pre[rel]) if pre.get(rel) is not None else None)
            info["written_sha"] = sha(now)
            files[rel] = info
        elif rel in prev_files:
            info = dict(prev_files[rel])
            info["written_sha"] = sha(now) if now is not None else info.get("written_sha")
            files[rel] = info
    for rel, info in prev_files.items():       # dat files modified by an earlier run stay tracked
        if rel.endswith(".dat") and rel not in files and rel not in stale_created:
            files[rel] = info
    registered = sorted((prev_reg | {s.name for s in adds}) - set(dels)) if plan.deco_mode == "register" else sorted(prev_reg)
    state = dict(tool="bloodgore", version=__version__, level=plan.level, params=plan.params, time=ts, files=files,
                 particle_files=sorted({i for _k, _id, fl in plan.particle_ids for i in fl}), particle_ids=sorted({i for _k, i, _f in plan.particle_ids}),
                 sound_indices=sorted(plan.sound_indices), states_info=plan.states_info, registered=registered, engine=engine_root)
    with open(os.path.join(root, BACKUP_DIR, STATE_FILE), "w", encoding="utf8") as f:
        json.dump(state, f, indent=1)
    manifest["state"] = state
    with open(os.path.join(bdir, "manifest.json"), "w", encoding="utf8") as f:
        json.dump(manifest, f, indent=1)
    return state


def uninstall(project, guard: Guard, state: dict, engine_root, log=print) -> list:
    """Restores the originals and removes created files. Returns a list of messages."""
    guard.require_closed()
    root = project.root
    ts = stamp()
    bdir = _unique_dir(os.path.join(root, BACKUP_DIR), ts)
    msgs = []
    files = state["files"]
    pids = set(state.get("particle_ids", []))
    sidx = set(state.get("sound_indices", []))
    for rel in files:
        guard.check_write(_abs(root, rel))
        probe_writable(_abs(root, rel))
    os.makedirs(os.path.join(bdir, "files"), exist_ok=True)
    for rel in files:       # keep a copy of what is there now before changing it
        data = read_file(_abs(root, rel))
        if data is not None:
            dst = os.path.join(bdir, "files", *rel.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(data)
    need_host_del = False
    for rel, info in sorted(files.items()):
        p = _abs(root, rel)
        cur = read_file(p)
        if info.get("created"):
            if cur is None:
                continue
            if sha(cur) == info.get("written_sha"):
                os.remove(guard.check_write(p))
                msgs.append("removed %s" % rel)
            else:
                msgs.append("left %s: it was changed after the install" % rel)
            continue
        orig = read_file(os.path.join(root, BACKUP_DIR, *info["origin_backup"].split("/")))
        if cur is not None and sha(cur) == info.get("written_sha") and orig is not None:
            with open(guard.check_write(p), "wb") as f:
                f.write(orig)
            msgs.append("restored %s" % rel)
        elif rel.endswith(".states") and cur is not None:
            text = fsmgen.revert_states_text(cur.decode("utf-8", "replace"), pids, sidx)
            with open(guard.check_write(p), "wb") as f:
                f.write(text.encode("utf-8"))
            msgs.append("cleaned %s (changed after the install, removed only this tool's lines)" % rel)
        elif rel == "Decorations.dat":
            need_host_del = True
        else:
            msgs.append("left %s: it was changed after the install" % rel)
    reg = state.get("registered", [])
    if reg and (need_host_del or not any(r.endswith("Decorations.dat") for r in files)):
        ok, why = registry.can_register(engine_root)
        if ok:
            registry.run_host(engine_root, root, [dict(op="del", name=n) for n in reg])
            msgs.append("removed %d decorations through the editor's own code" % len(reg))
        else:
            msgs.append("decorations %s are still in Decorations.dat (%s); remove them in the editor under Decorations Settings" % (", ".join(reg), why))
    os.remove(guard.check_write(os.path.join(root, BACKUP_DIR, STATE_FILE)))
    with open(os.path.join(bdir, "manifest.json"), "w", encoding="utf8") as f:
        json.dump(dict(tool="bloodgore", version=__version__, time=ts, operation="uninstall", messages=msgs), f, indent=1)
    for sub in ("bloodgore_manual",):
        d = os.path.join(root, sub)
        if os.path.isdir(d):
            for base, dirs, fs in os.walk(d, topdown=False):
                if not os.listdir(base):
                    os.rmdir(base)
    return msgs
