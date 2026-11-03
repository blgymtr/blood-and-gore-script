"""Tests on a synthetic Easy FPS Editor project built by code (tests/fixture.py)."""
import contextlib
import io
import os
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import numpy as np   # noqa: E402

import fixture   # noqa: E402
from bloodgore import analyze, apply as applymod, cli, cutter, fsmgen, imaging as im, planner, registry   # noqa: E402
from bloodgore.project import Project   # noqa: E402
from bloodgore.safety import Guard, SafetyError   # noqa: E402
from bloodgore.states import StatesDoc   # noqa: E402


def run(argv, lister=None):
    """Runs the CLI, returns (exit code, output text)."""
    buf = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(buf):
        try:
            code = cli.main(argv, lister=lister or (lambda: []))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            if isinstance(exc.code, str):
                buf.write(exc.code)
    return code, buf.getvalue()


def no_process():
    return []


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bgtest_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.proj = fixture.build_project(self.tmp)
        self.engine = self.tmp

    def args(self, *a):
        return list(a) + ["--project", self.proj]

    def tree(self):
        return fixture.tree_hashes(self.proj)

    def install(self, *extra, apply=True):
        a = ["install", "--project", self.proj, "--no-decorations"] + list(extra) + (["--apply"] if apply else [])
        return run(a)


class ScanAndDryRun(Base):
    def test_scan_reads_only(self):
        before = self.tree()
        code, text = run(["scan", "--project", self.proj])
        self.assertEqual(code, 0)
        self.assertIn("Humanoid", text)
        self.assertIn("Blobby", text)
        self.assertIn("humanoid: head", text)
        self.assertIn("creature", text)
        self.assertIn("scale 1.5", text)
        self.assertEqual(before, self.tree())

    def test_dry_run_writes_nothing(self):
        before = self.tree()
        code, text = self.install(apply=False)
        self.assertEqual(code, 0)
        self.assertIn("DRY RUN", text)
        self.assertEqual(before, self.tree(), "a dry run must not change a single byte or add a file")
        self.assertFalse(os.path.exists(os.path.join(self.proj, "bloodgore_backup")))

    def test_dry_run_default_without_apply_flag(self):
        before = self.tree()
        run(["install", "--project", self.proj])
        self.assertEqual(before, self.tree())


class InstallUninstall(Base):
    def test_apply_creates_expected_files(self):
        code, text = self.install("--gore", "2")
        self.assertEqual(code, 0, text)
        self.assertTrue(os.path.isfile(os.path.join(self.proj, "bloodgore_backup", "state.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.proj, "Sprites", "Enemies", "HumanoidBgRemains0.png")))
        self.assertTrue(os.path.isfile(os.path.join(self.proj, "States", "Blobby.states")), "an enemy without states gets a generated file")
        self.assertTrue(os.path.isfile(os.path.join(self.proj, "Sounds", "BgGib.wav")))
        text = open(os.path.join(self.proj, "States", "Humanoid.states"), encoding="utf8").read()
        self.assertIn("state BGGIB", text)
        self.assertIn("SETVAR bgdmg LASTDAMAGE", text)

    def test_untouched_enemy_frames_and_timings(self):
        before = open(os.path.join(self.proj, "States", "Humanoid.states"), encoding="utf8").read().split("\n")
        self.install()
        after = open(os.path.join(self.proj, "States", "Humanoid.states"), encoding="utf8").read().split("\n")
        # every original line is still there, in the same order
        it = iter(after)
        for ln in before:
            self.assertTrue(any(ln == x for x in it), "original line lost or reordered: " + ln)

    def test_idempotent_reapply(self):
        self.install("--gore", "3")
        first = self.tree()
        code, text = self.install("--gore", "3")
        self.assertEqual(code, 0)
        self.assertIn("nothing to do", text)
        self.assertEqual(first, self.tree())

    def test_level_change_then_uninstall_is_byte_identical(self):
        original = self.tree()
        self.install("--gore", "1")
        self.install("--gore", "4")
        self.install("--gore", "2")
        code, text = run(["uninstall", "--project", self.proj, "--apply"])
        self.assertEqual(code, 0, text)
        now = {k: v for k, v in self.tree().items() if not k.startswith("bloodgore_backup/")}
        self.assertEqual(original, now, "uninstall must restore every original file byte for byte and remove everything it created")

    def test_gore_zero_removes_install(self):
        original = self.tree()
        self.install("--gore", "2")
        code, _ = self.install("--gore", "0")
        self.assertEqual(code, 0)
        now = {k: v for k, v in self.tree().items() if not k.startswith("bloodgore_backup/")}
        self.assertEqual(original, now)

    def test_enemy_filter(self):
        self.install("--enemies", "Humanoid")
        self.assertFalse(os.path.exists(os.path.join(self.proj, "States", "Blobby.states")))
        self.assertTrue(os.path.exists(os.path.join(self.proj, "Sprites", "Enemies", "HumanoidBgRemains0.png")))

    def test_crlf_states_file_stays_crlf(self):
        shutil.rmtree(self.tmp)
        os.makedirs(self.tmp)
        self.proj = fixture.build_project(self.tmp, crlf_states=True)
        orig = open(os.path.join(self.proj, "States", "Humanoid.states"), "rb").read()
        self.install()
        new = open(os.path.join(self.proj, "States", "Humanoid.states"), "rb").read()
        self.assertEqual(new.count(b"\n"), new.count(b"\r\n"), "every line ending must stay CRLF")
        run(["uninstall", "--project", self.proj, "--apply"])
        self.assertEqual(open(os.path.join(self.proj, "States", "Humanoid.states"), "rb").read(), orig)

    def test_uninstall_after_user_edit_removes_only_our_lines(self):
        self.install()
        p = os.path.join(self.proj, "States", "Humanoid.states")
        text = open(p, encoding="utf8").read()
        orig = open(os.path.join(self.proj, "bloodgore_backup", [d for d in sorted(os.listdir(os.path.join(self.proj, "bloodgore_backup"))) if os.path.isdir(os.path.join(self.proj, "bloodgore_backup", d))][0], "files", "States", "Humanoid.states"), encoding="utf8").read()
        edited = text.replace("state DEAD NONE 0", "state DEAD NONE 0\nframe 12 0.5 0 0 0 NONE", 1)  # user adds a line (breaks the sha)
        edited = edited.replace("state IDLE NONE 0\n", "state IDLE NONE 0\n", 1)
        open(p, "w", encoding="utf8", newline="").write(edited)
        run(["uninstall", "--project", self.proj, "--apply"])
        after = open(p, encoding="utf8").read()
        self.assertNotIn("bg", after.replace("Bg", "").lower().replace("humanoid", "").replace("bgremains", ""))
        self.assertNotIn("BGGIB", after)
        self.assertIn("frame 12 0.5 0 0 0 NONE", after)
        self.assertEqual(after.replace("frame 12 0.5 0 0 0 NONE\n", "", 1), orig)

    def test_decorations_mocked_registration(self):
        """With a (mocked) editor host the decorations are registered and uninstall asks to remove them."""
        calls = []

        def fake_host(engine, project, ops):
            calls.append(ops)
            # emulate the editor: append records to Decorations.dat
            path = os.path.join(project, "Decorations.dat")
            data = open(path, "rb").read().decode("utf8")
            n = 10
            for op in ops:
                if op["op"] == "add":
                    n += 1
                    data += fixture.decoration_record(op["fields"]["name"], n)
            open(path, "wb").write(data.encode("utf8"))

        original = self.tree()
        with mock.patch.object(registry, "run_host", fake_host), mock.patch.object(registry, "can_register", lambda e: (True, "")):
            code, text = run(["install", "--project", self.proj, "--apply"])
            self.assertEqual(code, 0, text)
            self.assertEqual(len(calls), 1)
            names = [o["fields"]["name"] for o in calls[0]]
            self.assertIn("BgHumanoidHead", names)
            self.assertIn("SPAWN BgHumanoidPool", open(os.path.join(self.proj, "States", "Humanoid.states")).read())
            code, text = run(["uninstall", "--project", self.proj, "--apply"])
            self.assertEqual(code, 0, text)
        now = {k: v for k, v in self.tree().items() if not k.startswith("bloodgore_backup/")}
        self.assertEqual(original, now)


class GeneratedStates(Base):
    def check_states(self, path, project_root):
        doc = StatesDoc(open(path, encoding="utf8").read())
        st = doc.states()
        images = doc.image_count()
        sounds = doc.sound_count()
        for s in ("IDLE", "SEE", "CHASE", "ATTACK", "HURT", "DEATH", "DEAD"):
            self.assertIn(s, st)
        varnames = set()
        for name, info in st.items():
            self.assertTrue(info["frames"], "state %s has no frames" % name)
            self.assertTrue(info["next"] == "NONE" or info["next"] in st, "state %s jumps to unknown %s" % (name, info["next"]))
            first = True
            for i in info["frames"]:
                w = doc.lines[i].split()
                self.assertEqual(len(w) >= 7, True, doc.lines[i])
                self.assertLess(int(w[1]), images, "sprite index out of range: " + doc.lines[i])
                if first:
                    self.assertEqual(w[6], "NONE", "first line action is ignored by the engine, must be NONE in %s" % name)
                    first = False
                act = w[6:]
                if act[0].startswith("JUMPIF"):
                    self.assertIn(act[-1], st, doc.lines[i])
                if act[0] == "SOUND":
                    self.assertLess(int(act[1]), sounds)
                if act[0] in ("SETVAR", "MULTIPLY", "INCREMENT", "DECREMENT") and act[1].startswith("bg"):
                    varnames.add(act[1])
                if not act[0].isupper():
                    self.fail("action not upper case: " + doc.lines[i])
        for a in varnames:
            for b in varnames:
                if a != b:
                    self.assertFalse(b.startswith(a), "variable %s starts with variable %s (engine quirk)" % (b, a))
        for ln in doc.lines:
            self.assertEqual(ln, ln.lstrip(), "no leading whitespace allowed")
        # every CUSTOMPARTICLE id has its two Custom files
        for ln in doc.lines:
            w = ln.split()
            if len(w) > 7 and w[6] == "CUSTOMPARTICLE":
                pid = int(w[7])
                for k in (pid, pid - 1):
                    self.assertTrue(os.path.isfile(os.path.join(project_root, "Sprites", "Particles", "Custom%d.png" % k)), "missing Custom%d.png" % k)
        return doc

    def test_states_are_valid_for_every_level(self):
        for level in (1, 2, 3, 4):
            self.install("--gore", str(level))
            for name in ("Humanoid", "Blobby"):
                self.check_states(os.path.join(self.proj, "States", name + ".states"), self.proj)

    def test_uses_only_documented_actions(self):
        self.install("--gore", "4")
        documented = set("""NONE READY SOUND SOUNDANDATTACK PLAYERSPEED PARTICLES CUSTOMPARTICLE SETVAR MODELTEXTURE SPAWN HUDIMG CAMSPEED INCREMENT DECREMENT MULTIPLY
        DIVIDE MODULO CLAMP JUMPIFEQUALS JUMPIFNEQUALS JUMPIFGEQUALS JUMPIFLEQUALS JUMPIFGREATER JUMPIFLESS JUMPIFHPLESS ATTACK EXPLOSION PROJECTILE""".split())
        for name in ("Humanoid", "Blobby"):
            for ln in open(os.path.join(self.proj, "States", name + ".states"), encoding="utf8"):
                w = ln.split()
                if w[:1] == ["frame"]:
                    self.assertIn(w[6], documented, ln)

    def test_particle_ids_do_not_collide_with_existing(self):
        self.install()
        state = applymod.load_state(self.proj)
        self.assertTrue(all(i >= 2 for i in state["particle_files"]), "Custom0 and Custom1 already existed and must stay untouched")
        self.assertEqual(min(state["particle_files"]), 2)

    def test_particle_pairs_identical(self):
        self.install()
        state = applymod.load_state(self.proj)
        files = state["particle_files"]
        for a in range(files[0], files[-1], 2):
            d1 = open(os.path.join(self.proj, "Sprites", "Particles", "Custom%d.png" % a), "rb").read()
            d2 = open(os.path.join(self.proj, "Sprites", "Particles", "Custom%d.png" % (a + 1)), "rb").read()
            self.assertEqual(d1, d2)

    def test_default_states_for_enemy_without_file_keep_attack_delay(self):
        self.install()
        text = open(os.path.join(self.proj, "States", "Blobby.states"), encoding="utf8").read()
        self.assertIn("image Blobby 0 12", text)
        self.assertIn("frame 5 0.6 0 0 0 NONE", text)    # attack delay 1.2 s: 0.6 s wait plus the animation


class NeverSquare(Base):
    def all_images(self):
        pr = Project(self.proj)
        plan = planner.build_plan(pr, 4, None, 1, None, None, True, True, None, None)
        imgs = []
        for rel, data in plan.files.items():
            if rel.endswith(".png"):
                imgs.append((rel, im.decode_png(data)))
        return pr, plan, imgs

    def test_every_generated_image_is_organic(self):
        pr, plan, imgs = self.all_images()
        self.assertGreater(len(imgs), 40)
        bad = []
        for rel, img in imgs:
            r = im.organic_report(img)
            if r["empty"] or r["corner_opaque"] != 0 or r["fill"] >= im.TEST_MAX_FILL:
                bad.append((rel, r["corner_opaque"], round(r["fill"], 3)))
        self.assertEqual(bad, [], "generated images must have clear corners and a bounding box fill below %.2f" % im.TEST_MAX_FILL)

    def test_particle_images_are_square(self):
        _pr, _plan, imgs = self.all_images()
        for rel, img in imgs:
            if "/Particles/" in rel:
                self.assertEqual(img.shape[0], img.shape[1], rel)

    def test_cut_parts_are_organic_for_both_figures_and_several_seeds(self):
        for fn in (fixture.draw_humanoid, fixture.draw_blob):
            frame = fn(0)
            body = analyze.analyze(frame)
            for seed in range(1, 6):
                rng = np.random.default_rng(seed)
                parts, ctx = cutter.cut_parts(frame, body, rng, chunks=6)
                self.assertGreaterEqual(len(parts), 5)
                for p in parts:
                    r = im.organic_report(p.img)
                    self.assertEqual(r["corner_opaque"], 0, p.name)
                    self.assertLess(r["fill"], im.TEST_MAX_FILL, p.name)
                rem = cutter.make_remains(frame, body, ctx, rng)
                r = im.organic_report(rem)
                self.assertEqual(r["corner_opaque"], 0)
                self.assertLess(r["fill"], im.TEST_MAX_FILL)
                self.assertEqual(rem.shape[0], frame.shape[0], "remains keep the enemy's canvas height")

    def test_cut_lines_are_not_straight(self):
        """A cut leaves a bloody stump: red pixels along the cut, and the cut edge is not a straight axis aligned line."""
        frame = fixture.draw_humanoid(0)
        body = analyze.analyze(frame)
        parts, _ = cutter.cut_parts(frame, body, np.random.default_rng(3), chunks=3)
        by = {p.name: p for p in parts}
        for name in ("armA", "armB", "head", "legA"):
            img = by[name].img
            a = img[..., 3] > 128
            red = (img[..., 2].astype(int) > img[..., 1].astype(int) + 40) & (img[..., 2].astype(int) > img[..., 0].astype(int) + 40) & (img[..., 2] > 90) & a
            self.assertGreater(int(red.sum()), 5, "%s has no bloody stump" % name)
            rows = [np.nonzero(a[y])[0] for y in range(a.shape[0]) if a[y].any()]
            edges = [r.min() for r in rows]
            self.assertGreater(len(set(edges)), 2, "%s: the left edge is one straight vertical line" % name)
            cols = [np.nonzero(a[:, x])[0] for x in range(a.shape[1]) if a[:, x].any()]
            self.assertGreater(len(set(c.min() for c in cols)), 2, "%s: the top edge is one straight horizontal line" % name)
            fully = (a.sum(axis=1) == a.shape[1]).sum()
            self.assertEqual(int(fully), 0, "%s has a full width row, meaning a square edge" % name)

    def test_organic_rule_detects_squares(self):
        sq = np.zeros((20, 20, 4), np.uint8)
        sq[2:18, 2:18] = (0, 0, 200, 255)
        self.assertFalse(im.is_organic(sq))
        self.assertTrue(im.is_organic(im.enforce_organic(sq)))


class Detection(Base):
    def test_humanoid_proportions(self):
        b = analyze.analyze(fixture.draw_humanoid(0))
        self.assertTrue(b.humanoid)
        self.assertTrue(b.legs_split)
        self.assertGreater(b.y_hip, b.y_neck + 10)
        self.assertLess(b.y_neck, 0.3 * b.h)

    def test_blob_is_creature(self):
        b = analyze.analyze(fixture.draw_blob(0))
        self.assertFalse(b.humanoid)
        self.assertTrue(b.ok)

    def test_empty_frame(self):
        self.assertFalse(analyze.analyze(np.zeros((32, 32, 4), np.uint8)).ok)

    def test_scale_changes_particle_scale(self):
        pr = Project(self.proj)
        plan = planner.build_plan(pr, 2, ["Humanoid"], 1, None, None, False, True, None, None)
        text = plan.files["States/Humanoid.states"].decode()
        scales = {float(m.group(1)) for m in re.finditer(r"CUSTOMPARTICLE \d+ \S+ \S+ \S+ ([0-9.]+) ", text)}
        self.assertIn(0.6, scales)      # enemy scale 1.5 x 0.4 world units per pixel


class SafetyRules(Base):
    def test_write_outside_project_refused(self):
        g = Guard(self.proj, self.engine, lister=no_process)
        for bad in (os.path.join(self.proj, "..", "x.txt"), os.path.join(self.tmp, "EasyFPSEditor_CE.exe"), os.path.join(self.tmp, "Projects", "Game.exe"),
                    os.path.join(self.proj, "evil.dll"), os.path.join(self.tmp, "Projects", "OtherProject", "Enemies.dat"), self.proj):
            with self.assertRaises(SafetyError, msg=bad):
                g.check_write(bad)
        self.assertTrue(g.check_write(os.path.join(self.proj, "Sprites", "Particles", "Custom9.png")))

    def test_symlink_escape_refused(self):
        outside = tempfile.mkdtemp(prefix="bgout_")
        self.addCleanup(shutil.rmtree, outside, True)
        link = os.path.join(self.proj, "Linked")
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not available")
        g = Guard(self.proj, self.engine, lister=no_process)
        with self.assertRaises(SafetyError):
            g.check_write(os.path.join(link, "x.png"))

    def test_editor_folder_is_not_a_project(self):
        with self.assertRaises(SafetyError):
            Guard(self.tmp, self.tmp, lister=no_process)

    def test_malicious_enemy_name_is_skipped(self):
        with open(os.path.join(self.proj, "Enemies.dat"), "a", newline="") as f:
            f.write(fixture.enemy_record("..", 3))
        before = self.tree()
        code, text = self.install(apply=False)
        self.assertIn("skipped", text)
        self.assertEqual(before, self.tree())

    def test_running_editor_blocks_apply(self):
        before = self.tree()
        code, text = run(["install", "--project", self.proj, "--no-decorations", "--apply"], lister=lambda: [("EasyFPSEditor_CE.exe", os.path.join(self.tmp, "EasyFPSEditor_CE.exe"))])
        self.assertNotEqual(code, 0)
        self.assertIn("running", text)
        self.assertEqual(before, self.tree(), "nothing may be written while the editor is open")

    def test_running_game_blocks_apply_and_uninstall(self):
        self.install()
        before = self.tree()
        lister = lambda: [("Game.exe", os.path.join(self.tmp, "Projects", "Game.exe"))]
        code, text = run(["uninstall", "--project", self.proj, "--apply"], lister=lister)
        self.assertNotEqual(code, 0)
        self.assertEqual(before, self.tree())

    def test_editor_of_another_engine_folder_does_not_block(self):
        code, text = run(["install", "--project", self.proj, "--no-decorations", "--apply"], lister=lambda: [("EasyFPSEditor_CE.exe", r"Z:\Other\engine\EasyFPSEditor_CE.exe")])
        self.assertEqual(code, 0, text)

    def test_locked_file_is_reported_and_rolled_back(self):
        before = self.tree()
        real_open = open

        def fake_probe(path):
            if path.endswith("Humanoid.states"):
                raise SafetyError("cannot write %s (locked or read only)" % path)

        with mock.patch.object(applymod, "probe_writable", fake_probe):
            code, text = self.install()
        self.assertNotEqual(code, 0)
        self.assertEqual(before, self.tree())

    def test_failure_during_write_restores_everything(self):
        before = self.tree()
        calls = {"n": 0}
        real = applymod.registry.run_host

        def boom(*a, **k):
            raise RuntimeError("editor code failed")

        with mock.patch.object(registry, "run_host", boom), mock.patch.object(registry, "can_register", lambda e: (True, "")):
            code, text = run(["install", "--project", self.proj, "--apply"])
        self.assertNotEqual(code, 0)
        now = {k: v for k, v in self.tree().items() if not k.startswith("bloodgore_backup/")}
        self.assertEqual(before, now, "a failed install must leave the project exactly as it was")

    def test_editor_and_engine_files_never_modified(self):
        exe = os.path.join(self.tmp, "EasyFPSEditor_CE.exe")
        game = os.path.join(self.tmp, "Projects", "Game.exe")
        h = fixture.tree_hashes(self.tmp)
        engine_before = {k: v for k, v in h.items() if not k.startswith("Projects/TestProject/")}
        self.install("--gore", "3")
        self.install("--gore", "1")
        run(["uninstall", "--project", self.proj, "--apply"])
        h2 = fixture.tree_hashes(self.tmp)
        self.assertEqual(engine_before, {k: v for k, v in h2.items() if not k.startswith("Projects/TestProject/")})
        self.assertTrue(os.path.isfile(exe) and os.path.isfile(game))

    def test_preview_never_writes_into_project(self):
        before = self.tree()
        code, text = run(["preview", "--project", self.proj, "--enemy", "Humanoid", "--out", os.path.join(self.proj, "sheet.png")])
        self.assertNotEqual(code, 0)
        self.assertEqual(before, self.tree())
        out = os.path.join(self.tmp, "sheet.png")
        code, text = run(["preview", "--project", self.proj, "--enemy", "Humanoid", "--out", out])
        self.assertEqual(code, 0, text)
        self.assertTrue(os.path.getsize(out) > 1000)
        self.assertEqual(before, self.tree())


if __name__ == "__main__":
    unittest.main()
