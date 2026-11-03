"""Registers gib decorations in Decorations.dat through the editor's own classes (32-bit PowerShell on Windows).

The editor exe is only loaded for reading; the only files written are the project's own .dat files, and only
after `apply.py` made a backup. When this is not possible, `manual_instructions` explains the same steps for the editor UI.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HOST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "efpse_host.ps1")


def powershell32():
    win = os.environ.get("WINDIR", r"C:\Windows")
    p = os.path.join(win, "SysWOW64", "WindowsPowerShell", "v1.0", "powershell.exe")
    return p if os.path.isfile(p) else None


def can_register(engine_root):
    """(ok, reason)."""
    if not sys.platform.startswith("win"):
        return False, "registering decorations needs Windows (32-bit PowerShell and the editor's own code)"
    if not engine_root:
        return False, "the editor folder was not found next to the project (use --engine)"
    exe = os.path.join(engine_root, "EasyFPSEditor_CE.exe")
    if not os.path.isfile(exe):
        return False, "EasyFPSEditor_CE.exe was not found in " + engine_root
    if not powershell32():
        return False, "32-bit Windows PowerShell was not found"
    return True, ""


def decoration_fields(name: str, scale: float) -> dict:
    return dict(name=name, scale=round(float(scale), 4), radius=1, hp=1, breakable=False, solid=False, dropType=-1, dropIndex=-1,
                lighted=True, disableCulling=True, removeAtDeath=False, align=0)


def run_host(engine_root: str, project_root: str, ops: list):
    ps = powershell32()
    exe = os.path.join(engine_root, "EasyFPSEditor_CE.exe")
    fd, tmp = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf8") as f:
            json.dump(ops, f)
        cmd = [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", HOST, "-Exe", exe, "-Proje", project_root, "-Is", "apply", "-Ops", tmp]
        r = subprocess.run(cmd, capture_output=True, creationflags=0x08000000 if sys.platform.startswith("win") else 0)
    finally:
        os.remove(tmp)
    out, err = r.stdout.decode("utf8", "replace"), r.stderr.decode("utf8", "replace")
    if r.returncode or "SAVED" not in out:
        raise RuntimeError("the editor code reported an error:\n" + (err or out)[-900:])


def manual_instructions(specs) -> list:
    """Step by step text for adding the decorations in the editor UI."""
    lines = [
        "Gib decorations could not be added automatically. Particles only mode is active: flying parts and blood work, lying parts do not.",
        "To get the lying parts too, add these decorations in the editor and run install again (it detects them):",
        "  1. Open the project, menu Entities Settings > Decorations Settings.",
        "  2. For each name below type the name and press Return, then import the sprite from the folder",
        "     bloodgore_manual/decorations/ (copy that folder out of the project first: the editor must not import from inside the project).",
        "  3. Settings for every one: Health 1, Radius 1, Breakable off, Solid off, Lighted on, Disable Culling on, Snap to ceiling off, Scale as listed.",
        "  4. Accept, save the project, close the editor, run install again.",
    ]
    for s in specs:
        lines.append("     %s   scale %g" % (s.name, s.scale))
    return lines
