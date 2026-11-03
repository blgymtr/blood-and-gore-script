"""The repository must not contain any game or engine name (only Easy FPS Editor may be named).

The deny list is stored as hashes, so the list itself contains no names either.
"""
import hashlib
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SKIP_DIRS = {".git", "__pycache__", "bloodgore_backup", ".pytest_cache"}
SKIP_FILES = {"denylist.sha256"}


def load_hashes():
    with open(os.path.join(HERE, "denylist.sha256"), encoding="utf8") as f:
        return {ln.strip() for ln in f if ln.strip() and not ln.startswith("#")}


def tokens(text: str):
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def hits(text: str, hashes: set):
    toks = tokens(text)
    found = set()
    for n in (1, 2, 3, 4):
        for i in range(len(toks) - n + 1):
            joined = "".join(toks[i:i + n])
            if hashlib.sha256(joined.encode()).hexdigest() in hashes:
                found.add(hashlib.sha256(joined.encode()).hexdigest()[:10])
    return found


def repo_files():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            if fn in SKIP_FILES:
                continue
            yield os.path.join(base, fn)


class NoGameNames(unittest.TestCase):
    def test_files_and_names(self):
        hashes = load_hashes()
        self.assertGreater(len(hashes), 20)
        bad = []
        for path in repo_files():
            rel = os.path.relpath(path, ROOT)
            found = hits(rel.replace("\\", "/"), hashes)
            with open(path, "rb") as f:
                data = f.read()
            found |= hits(data.decode("latin-1"), hashes)     # also PNG metadata and other binary files
            if found:
                bad.append((rel, sorted(found)))
        self.assertEqual(bad, [], "files mention a name on the deny list (hash prefixes shown)")


if __name__ == "__main__":
    unittest.main()
