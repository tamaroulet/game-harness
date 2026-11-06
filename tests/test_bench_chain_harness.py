"""鎖の段の走り（harness/bench_chain.py の ChainStage）の H ラインの木の検査。H ラインは clone の先端の harness/ で走るので、
その木が走らせ役の木と違えば、poll の前に無効にして止める（11-06、L6 の対の実行の 1 本目は、旧版・新版とも基準の版の
H ラインで走った）。一時ディレクトリの偽のリポジトリだけで書き、H ラインは走らせない。

    python -m unittest tests.test_bench_chain_harness -v
"""
import argparse
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import bench_chain  # noqa: E402


def git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", check=True)
    return r.stdout.strip()


class HarnessTree(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = self.root / "repo"
        (self.repo / "harness").mkdir(parents=True)
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Tester")
        git(self.repo, "config", "user.email", "tester@example.com")
        self.revs = {}
        for name in ("runner", "other"):
            (self.repo / "harness" / "m.py").write_text(f"A = {name!r}\n", encoding="utf-8")
            git(self.repo, "add", "-A")
            git(self.repo, "commit", "-q", "-m", name)
            self.revs[name] = git(self.repo, "rev-parse", "HEAD")
        self.mine = git(self.repo, "rev-parse", f"{self.revs['runner']}:harness")

    def stage(self, name):
        """先端が self.revs[name] の段の走り。走らせ役の木は runner の harness/。"""
        ns = argparse.Namespace(task="B07", run_id="t-s1", repo=str(self.repo), base="HEAD", out=str(self.root / "out"),
                                sessions=str(self.root / "sessions"), plan=str(self.root / "plan"), implementer=None)
        f = bench_chain.ChainStage(ns, self.revs[name], self.root / "spec.md")
        f.clone = self.repo
        return f, (patch.object(bench_chain, "runner_harness", return_value=self.mine),
                   patch.object(f, "tip", return_value=self.revs[name]), patch.object(f, "tokens", return_value={"total": 0}))

    def test_the_runner_tree_runs(self):
        f, (p1, p2, p3) = self.stage("runner")
        with p1, p2, p3:
            self.assertIsNone(f.stop_reason())
        self.assertIsNone(f.run["invalid"])

    def test_another_tree_is_invalid_and_stops(self):
        f, (p1, p2, p3) = self.stage("other")
        with p1, p2, p3:
            self.assertEqual(f.stop_reason(), "harness")
        self.assertEqual(f.run["invalid"], "harness")
        self.assertIn(self.mine[:12], f.run["reason"])
        self.assertIn(self.revs["other"][:12], f.run["reason"])

    def test_the_loop_stops_before_the_first_poll(self):
        f, (p1, p2, p3) = self.stage("other")
        with p1, p2, p3, patch.object(f, "one_poll") as poll:
            f.loop()
        poll.assert_not_called()
        self.assertEqual((f.run["stop"], f.run["invalid"]), ("harness", "harness"))

    def test_the_runner_tree_is_the_harness_of_the_runner_clone_head(self):
        self.assertEqual(bench_chain.runner_harness(), git(ROOT, "rev-parse", "HEAD:harness"))


if __name__ == "__main__":
    unittest.main()
