"""Safety rules. The tool reads Easy FPS Editor files but must never change the editor or the engine.

* every write goes through `Guard.check_write`, which resolves the real path and refuses anything outside the
  selected project folder, anything inside the editor folder but outside the project, and program files;
* writing is refused while the editor or Game.exe of the same engine folder is running;
* files are checked for locks before the first byte is written.
"""
from __future__ import annotations

import os
import subprocess
import sys


class SafetyError(Exception):
    """A rule was violated; nothing was written."""


FORBIDDEN_SUFFIXES = (".exe", ".dll", ".so", ".dylib", ".bat", ".cmd", ".ps1", ".com", ".msi", ".sys", ".lnk")
FORBIDDEN_NAMES = ("easyfpseditor_ce.exe", "game.exe", "openal32.dll")
PROGRAM_NAMES = ("easyfpseditor_ce.exe", "game.exe")


def real(path: str) -> str:
    """Absolute path with links resolved, case folded on Windows for comparisons."""
    p = os.path.realpath(os.path.abspath(path))
    return p


def _norm(p: str) -> str:
    return os.path.normcase(p)


def is_inside(child: str, parent: str) -> bool:
    c, p = _norm(real(child)), _norm(real(parent))
    try:
        return os.path.commonpath([c, p]) == p
    except ValueError:      # different drives
        return False


def find_engine_root(project: str):
    """The folder that holds EasyFPSEditor_CE.exe when the project sits in <engine>/Projects/<Name>, else None."""
    proj = real(project)
    parent = os.path.dirname(proj)
    if os.path.basename(parent).lower() != "projects":
        return None
    eng = os.path.dirname(parent)
    try:
        names = {n.lower() for n in os.listdir(eng)}
    except OSError:
        return None
    return eng if "easyfpseditor_ce.exe" in names else None


def running_programs():
    """[(name, exe_path or '')] for the editor and Game.exe processes that are running. Tests replace this function."""
    found = []
    try:
        if sys.platform.startswith("win"):
            cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                   "Get-CimInstance Win32_Process | Where-Object { $_.Name -in 'EasyFPSEditor_CE.exe','Game.exe' } | ForEach-Object { $_.Name + '|' + $_.ExecutablePath }"]
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=30, creationflags=0x08000000).stdout
            for line in out.splitlines():
                if "|" in line:
                    n, _, p = line.partition("|")
                    found.append((n.strip(), p.strip()))
        else:       # Wine and friends
            out = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True, timeout=30).stdout
            for line in out.splitlines():
                low = line.lower()
                for n in PROGRAM_NAMES:
                    if n in low:
                        found.append((n, line.strip()))
    except (OSError, subprocess.SubprocessError):
        pass
    return found


class Guard:
    def __init__(self, project: str, engine: str | None = None, lister=None):
        self.project = real(project)
        self.engine = real(engine) if engine else find_engine_root(project)
        self.lister = lister or running_programs
        if self.engine and _norm(self.project) == _norm(self.engine):
            raise SafetyError("the project folder is the editor folder; select a folder inside Projects")
        for n in os.listdir(self.project) if os.path.isdir(self.project) else []:
            if n.lower() in FORBIDDEN_NAMES:
                raise SafetyError("%s contains %s: this is the editor folder, not a project" % (self.project, n))

    def check_write(self, path: str) -> str:
        """Returns the resolved path when writing it is allowed, raises SafetyError otherwise."""
        p = real(path)
        if not is_inside(p, self.project):
            raise SafetyError("refusing to write outside the project folder: " + path)
        if _norm(p) == _norm(self.project):
            raise SafetyError("refusing to replace the project folder itself")
        low = os.path.basename(p).lower()
        if low in FORBIDDEN_NAMES or low.endswith(FORBIDDEN_SUFFIXES):
            raise SafetyError("refusing to write a program file: " + path)
        if self.engine and is_inside(p, self.engine) and not is_inside(p, self.project):
            raise SafetyError("refusing to write inside the editor folder: " + path)
        return p

    def blockers(self):
        """Editor / Game.exe processes that may have this project open."""
        out = []
        for name, exe in self.lister():
            if not self.engine:
                out.append((name, exe))
            elif not exe or is_inside(os.path.dirname(exe) if exe.lower().endswith(".exe") else exe, self.engine):
                out.append((name, exe))
        return out

    def require_closed(self):
        b = self.blockers()
        if b:
            names = ", ".join(sorted({n for n, _ in b}))
            raise SafetyError("%s is running. Save your work and close it first: the editor would overwrite or lose these changes." % names)


def probe_writable(path: str):
    """Raises SafetyError when an existing file is locked (open elsewhere) or read only."""
    if not os.path.exists(path):
        return
    try:
        with open(path, "r+b"):
            pass
    except OSError as exc:
        raise SafetyError("cannot write %s (locked or read only): %s" % (path, exc)) from exc
