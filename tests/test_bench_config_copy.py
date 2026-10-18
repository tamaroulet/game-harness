"""工廠の設定の控え（rundir/factory/config.json）は、非公開オラクルの場所を伏せる（W-107）。
本物の工廠・CLI・オラクルは使わず、一時のディレクトリに作った設定と git だけで確かめる。"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import bench_guard  # noqa: E402
import bench_run  # noqa: E402

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
MODEL = "claude-sonnet-5-5"


def sh(repo, *args):
    r = subprocess.run([*GIT, "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", check=True)
    return r.stdout.strip()


class ConfigCopyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.oracles = self.root / "secret-oracles"
        (self.oracles / "bench" / "t1").mkdir(parents=True)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        sh(self.repo, "init", "-q")
        (self.repo / "a.txt").write_text("a", encoding="utf-8")
        sh(self.repo, "add", "-A")
        sh(self.repo, "commit", "-q", "-m", "x")
        self.start = sh(self.repo, "rev-parse", "HEAD")
        self.work = self.root / "work"
        (self.work / "inbox").mkdir(parents=True)
        self.rundir = self.root / "run"
        self.impl = {"cli": "claude", "model": MODEL, "auxiliary_models": ["claude-haiku-"]}
        self.cfg = {
            "oracles": str(self.oracles),
            "inbox": str(self.root / "inbox0"),
            "out": str(self.root / "out0"),
            "planner": {"cli": "claude", "model": MODEL},
            "reviewer": {"cli": "claude", "model": MODEL},
            "implementer": self.impl,
        }

    def make_factory(self):
        f = object.__new__(bench_run.Factory)
        f.run = {"start": self.start}
        f.work, f.repo, f.rundir = self.work, self.repo, self.rundir
        f.branch, f.impl, f.frozen = "integration", self.impl, None
        return f

    def test_copy_hides_oracles_and_guard_finds_no_leak(self):
        f = self.make_factory()
        f.setup_clone(self.work / "none", self.cfg)
        copy = self.rundir / "factory" / "config.json"
        text = copy.read_text(encoding="utf-8")
        self.assertNotIn(str(self.oracles), text)
        self.assertNotIn(str(self.oracles).replace("\\", "/"), text)
        self.assertNotIn("secret-oracles", text)
        self.assertEqual(bench_guard.leaks(self.cfg, [self.rundir]), [])

    def test_line_config_keeps_oracles_and_model_problem_unchanged(self):
        f = self.make_factory()
        f.setup_clone(self.work / "none", self.cfg)
        line = json.loads((self.work / "hline.json").read_text(encoding="utf-8"))
        self.assertEqual(line["oracles"], str(self.oracles))
        for name, model in (("1-planner", MODEL), ("2-reviewer", "other-model"), ("3-implementer", "claude-haiku-9"),
                            ("4-implementer", "bad-model")):
            d = self.rundir / "calls" / name
            d.mkdir(parents=True)
            (d / "result.json").write_text(json.dumps({"modelUsage": {model: {}}}), encoding="utf-8")
        after = bench_run.model_problem(self.rundir, self.cfg)
        (self.rundir / "factory" / "config.json").write_text(json.dumps(f.line_cfg), encoding="utf-8")
        before = bench_run.model_problem(self.rundir, self.cfg)
        self.assertEqual(after, before)
        self.assertEqual(after, ["bad-model", "other-model"])


if __name__ == "__main__":
    unittest.main()
