"""最適化の面（harness/surface.py）：つまみの当て方・鎖の段の走りにだけ効くこと・差分の検査（面の外・面の設定・漏れ）。
一時ディレクトリの偽のリポジトリと偽の鎖の置き場だけで確かめる。

    python -m unittest tests.test_surface -v
"""
import argparse
import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import bench_chain  # noqa: E402
import bench_run  # noqa: E402
import surface  # noqa: E402
from bench_start import BenchError  # noqa: E402

CFG = {"implementer": {"cli": "claude", "budget": {"max_turns": 15, "resume_max_turns": 8}}, "max_attempts": 2, "other": 1}
GOOD = {"_comment": "x", "implementer_max_turns": 20, "implementer_resume_max_turns": 10, "max_attempts": 3}


def git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", check=True)
    return r.stdout.strip()


def write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def setting(self, doc, name="surface.json"):
        path = self.tmp / name
        write(path, doc if isinstance(doc, str) else json.dumps(doc))
        return path


class Apply(Temp):
    def test_values_are_put_on_a_copy(self):
        before = json.loads(json.dumps(CFG))
        got = surface.apply(CFG, self.setting(GOOD))
        self.assertEqual((got["implementer"]["budget"], got["max_attempts"], got["other"]),
                         ({"max_turns": 20, "resume_max_turns": 10}, 3, 1))
        self.assertEqual(CFG, before)
        self.assertIsNot(got["implementer"], CFG["implementer"])

    def test_the_shipped_setting_keeps_the_current_values(self):
        cfg = json.loads((ROOT / "config" / "hline.json").read_text(encoding="utf-8"))
        self.assertEqual(surface.apply(cfg, ROOT / surface.SETTING), cfg)

    def test_comment_keys_are_skipped(self):
        got = surface.apply(CFG, self.setting({"_comment": "a", "_note": [1], "max_attempts": 1}))
        self.assertEqual((got["max_attempts"], got["implementer"]["budget"]["max_turns"]), (1, 15))

    def test_missing_broken_unknown_and_wrong_type_stop(self):
        cases = {"missing": self.tmp / "none.json", "broken": self.setting("{not json", "b.json"),
                 "list": self.setting([1], "l.json"), "unknown": self.setting({"planner_on": 1}, "u.json"),
                 "bool": self.setting({"max_attempts": True}, "t.json"), "float": self.setting({"max_attempts": 2.0}, "f.json"),
                 "string": self.setting({"implementer_max_turns": "15"}, "s.json")}
        for why, path in cases.items():
            with self.subTest(why), self.assertRaises(BenchError):
                surface.apply(CFG, path)

    def test_range_ends_pass_and_outside_stops(self):
        for name, (_, _, low, high) in surface.KNOBS.items():
            for value in (low, high):
                with self.subTest(name=name, value=value):
                    surface.apply(CFG, self.setting({name: value}))
            for value in (low - 1, high + 1):
                with self.subTest(name=name, value=value), self.assertRaises(BenchError):
                    surface.apply(CFG, self.setting({name: value}))

    def test_the_frame_is_two_paths_and_excludes_this_module(self):
        self.assertEqual(set(surface.FRAME), {"harness/hline_prompt.py", "config/surface.json"})
        self.assertEqual(set(surface.KNOBS), {"implementer_max_turns", "implementer_resume_max_turns", "max_attempts"})


class OnlyTheChainStage(Temp):
    def ns(self):
        return argparse.Namespace(task="B01", run_id="t-s1", repo=str(self.tmp), base="HEAD", out=str(self.tmp / "out"),
                                  sessions=str(self.tmp / "sessions"), plan=str(self.tmp / "plan"), implementer=None)

    def stage(self):
        return bench_chain.ChainStage(self.ns(), "0" * 40, self.tmp / "spec.md")

    def test_effective_puts_the_setting_and_records_values_and_sha256(self):
        path = self.setting(GOOD, surface.SETTING)
        f = self.stage()
        got = f.effective(CFG)
        self.assertEqual((got["implementer"]["budget"]["max_turns"], got["max_attempts"]), (20, 3))
        self.assertEqual(f.run["surface"]["values"], {k: v for k, v in GOOD.items() if not k.startswith("_")})
        self.assertEqual(f.run["surface"]["sha256"], bench_run.sha256_file(path))

    def test_the_implementer_override_also_gets_the_setting(self):
        self.setting(GOOD, surface.SETTING)
        f = self.stage()
        f.impl = {"cli": "agy", "budget": {"max_turns": 9}}
        got = f.effective(CFG)
        self.assertEqual((got["implementer"]["cli"], got["implementer"]["budget"]), ("agy", {"max_turns": 20, "resume_max_turns": 10}))

    def test_a_plain_factory_run_ignores_the_setting(self):
        self.setting(GOOD, surface.SETTING)
        f = bench_run.Factory(self.ns())
        self.assertEqual(f.effective(CFG), CFG)
        self.assertNotIn("surface", f.run)

    def test_a_bad_setting_aborts_the_stage_before_it_starts(self):
        self.setting({"max_attempts": 9}, surface.SETTING)
        f = self.stage()
        err = io.StringIO()
        with patch.object(f, "check_head"), patch.object(f, "check_plan"), patch.object(f, "cfg", return_value=CFG), \
                patch.object(f, "freeze_version", return_value=None), patch.object(f, "plan") as plan, \
                patch.object(bench_run.proc, "run", return_value=(0, "", "")), contextlib.redirect_stderr(err):
            self.assertEqual(bench_chain.run_stage(f), 2)
        self.assertIn("ABORT", err.getvalue())
        plan.assert_not_called()


class Check(Temp):
    def setUp(self):
        super().setUp()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Tester")
        git(self.repo, "config", "user.email", "tester@example.com")
        write(self.repo / "harness" / "hline_prompt.py", "".join(f"line_{i} = {i}\n" for i in range(20)) + "OLD = 'test_secret_alpha'\n")
        write(self.repo / "harness" / "surface.py", "FRAME = ()\n")
        write(self.repo / "notes" / "other.py", "".join(f"other_{i} = {i}\n" for i in range(20)))
        write(self.repo / surface.SETTING, json.dumps(GOOD))
        self.old = self.commit("base")

    def commit(self, message):
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", message)
        return git(self.repo, "rev-parse", "HEAD")

    def check(self, *extra, old=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = surface.main(["check", "--repo", str(self.repo), "--old", old or self.old, "--new", "HEAD", *extra])
        return rc, out.getvalue()

    def append(self, rel, text):
        path = self.repo / rel
        write(path, path.read_text(encoding="utf-8") + text)

    # ---- 面の外
    def test_changes_inside_the_frame_pass(self):
        self.append("harness/hline_prompt.py", "NEW = 1\n")
        write(self.repo / surface.SETTING, json.dumps({"max_attempts": 4}))
        self.commit("frame")
        self.assertEqual(self.check()[0], 0)

    def test_a_file_outside_the_frame_is_a_hit(self):
        self.append("notes/other.py", "x = 1\n")
        self.commit("outside")
        rc, out = self.check()
        self.assertEqual(rc, 1)
        self.assertIn("notes/other.py", out)

    def test_renames_across_the_frame_are_hits_both_ways(self):
        git(self.repo, "mv", "harness/hline_prompt.py", "notes/prompt.py")
        self.commit("frame to outside")
        rc, out = self.check()
        self.assertEqual(rc, 1)
        self.assertIn("harness/hline_prompt.py -> notes/prompt.py", out)
        git(self.repo, "mv", "notes/prompt.py", "harness/hline_prompt.py")
        self.commit("back")
        git(self.repo, "rm", "-q", "harness/hline_prompt.py")
        gone = self.commit("no prompt")
        git(self.repo, "mv", "notes/other.py", "harness/hline_prompt.py")
        self.commit("outside to frame")
        rc, out = self.check(old=gone)
        self.assertEqual(rc, 1)
        self.assertIn("notes/other.py -> harness/hline_prompt.py", out)

    def test_deleting_the_setting_is_a_hit(self):
        git(self.repo, "rm", "-q", surface.SETTING)
        self.commit("delete")
        rc, out = self.check()
        self.assertEqual(rc, 1)
        self.assertIn("面の設定を消しています", out)

    def test_changing_the_frame_module_itself_is_a_hit(self):
        self.append("harness/surface.py", "FRAME = ('x',)\n")
        self.commit("widen")
        rc, out = self.check()
        self.assertEqual(rc, 1)
        self.assertIn("harness/surface.py", out)

    # ---- 面の設定
    def test_a_value_outside_the_range_is_a_hit(self):
        write(self.repo / surface.SETTING, json.dumps({"implementer_max_turns": 41}))
        self.commit("too many turns")
        rc, out = self.check()
        self.assertEqual(rc, 1)
        self.assertIn("面の設定", out)

    # ---- 漏れ
    def chains(self):
        c = self.tmp / "chains"
        write(c / "B07" / "s0" / "tree" / "calcpkg" / "__init__.py", "")
        write(c / "B07" / "s0" / "tree" / "tests" / "test_calcpkg_base.py", "")
        write(c / "B07" / "s0" / "hidden" / "test_hidden_B07.py",
              "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_secret_alpha(self):\n        pass\n\n\n"
              "class Short:\n    pass\n\n\nclass HiddenSuite(unittest.TestCase):\n    def helper(self):\n        pass\n")
        write(c / "B07" / "s1" / "hidden" / "test_chain_B07_s1.py", "def test_secret_beta():\n    pass\n")
        return ["--chains", str(c), "--bases", "B07"]

    def leak(self, line):
        self.append("harness/hline_prompt.py", line + "\n")
        self.commit("leak?")
        return self.check(*self.chains())

    def test_a_hidden_test_name_in_an_added_line_is_a_hit_without_showing_it(self):
        rc, out = self.leak("# keep test_secret_beta working")
        self.assertEqual(rc, 1)
        self.assertIn("漏れ: harness/hline_prompt.py:22", out)
        self.assertNotIn("test_secret_beta", out)

    def test_long_class_package_and_base_names_are_hits(self):
        for line in ("x = 'HiddenSuite'", "import calcpkg", "# for B07の段"):
            with self.subTest(line):
                self.assertEqual(self.leak(line)[0], 1)
                git(self.repo, "reset", "-q", "--hard", self.old)

    def test_short_class_names_are_not_collected(self):
        self.assertEqual(self.leak("T = Short = 1")[0], 0)

    def test_a_name_only_in_removed_lines_is_not_a_hit(self):
        path = self.repo / "harness" / "hline_prompt.py"
        write(path, path.read_text(encoding="utf-8").replace("OLD = 'test_secret_alpha'\n", ""))
        self.commit("remove")
        self.assertEqual(self.check(*self.chains())[0], 0)

    def test_a_base_name_inside_another_word_is_not_a_hit(self):
        self.assertEqual(self.leak("# see B070 and xB07 and B07x")[0], 0)

    def test_without_chains_the_leak_check_is_skipped_in_one_line(self):
        self.append("harness/hline_prompt.py", "# test_secret_beta\n")
        self.commit("no chains")
        rc, out = self.check()
        self.assertEqual(rc, 0)
        self.assertEqual(sum("漏れの検めを飛ばします" in line for line in out.splitlines()), 1)

    # ---- 誤り
    def test_bad_arguments_and_revisions_are_2(self):
        self.assertEqual(self.check(old="no-such-rev")[0], 2)
        self.assertEqual(self.check("--chains", str(self.tmp))[0], 2)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(surface.main(["check", "--repo", str(self.repo)]), 2)
        self.assertEqual(self.check("--chains", str(self.tmp / "none"), "--bases", "B99")[0], 2)


if __name__ == "__main__":
    unittest.main()
