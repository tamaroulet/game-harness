"""固定カナリアの準備と後片付け（harness/canary.py、docs/design/canary_issue12.md）。

    python -m unittest discover -s tests -v

**なぜ要るか**: 標本は「impl_files を機械的に削除し、v2 の単位定義と生成テストを書き出したもの」で
なければならない。総監督が手で作ると deny 設定に触れ、標本も毎回同じにならない。ハーネスの決まった操作で
作れること、失敗したら止まること、後片付けで main に何も残らないことを、使い捨ての git リポジトリで確かめる。
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import canary  # noqa: E402


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


class CanaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        origin, repo = self.tmp / "origin.git", self.tmp / "repo"
        git("init", "-q", "--bare", "-b", "main", str(origin), cwd=self.tmp)
        git("clone", "-q", str(origin), str(repo), cwd=self.tmp)
        git("config", "user.email", "t@example.com", cwd=repo)
        git("config", "user.name", "t", cwd=repo)
        (repo / "impl").mkdir()
        for name in ("A.txt", "B.txt", "Keep.txt"):
            (repo / "impl" / name).write_text(name, encoding="utf-8")
        # v1 の単位定義と、それが挙げる手書きの受入テスト（中身は使わない）
        (repo / "tests" / "Core.Tests").mkdir(parents=True)
        for cls in ("OldTests", "OtherTests"):
            (repo / "tests" / "Core.Tests" / (cls + ".cs")).write_text("x", encoding="utf-8")
        (repo / "tools").mkdir()
        (repo / "tools" / "issue_12.json").write_text(
            json.dumps({"id": "issue_12", "acceptance": {"required_tests": ["OldTests"]}}), encoding="utf-8")
        git("add", "-A", cwd=repo)
        git("commit", "-q", "-m", "init", cwd=repo)
        git("push", "-q", "origin", "main", cwd=repo)
        self.repo = repo
        self.proj = {"id": "demo", "repo_dir": str(repo), "base_branch": "main",
                     "test_dir": "tests/Core.Tests", "units_dir": "tools"}
        self.unit = self.tmp / "unit.json"
        self.unit.write_text(json.dumps({"schema": 2, "id": "issue_12",
                                         "impl_files": ["impl/A.txt", "impl/B.txt"]}), encoding="utf-8")
        self.wt = self.tmp / "wt"

    def fake_decompose(self, rc=0):
        def runner(args, **kw):
            wt = Path(args[args.index("--repo-dir") + 1])
            if rc == 0:
                (wt / "tools").mkdir(exist_ok=True)
                (wt / "tools" / "issue_12.json").write_text("{}", encoding="utf-8")
            return SimpleNamespace(returncode=rc, stdout="", stderr="")
        return runner

    def test_prepare_deletes_impl_files_and_commits_generated_output(self):
        branch, _ = canary.prepare(self.proj, self.unit, self.wt, runner=self.fake_decompose())
        self.assertEqual(branch, "test/canary-issue-12")
        self.assertFalse((self.wt / "impl" / "A.txt").exists())
        self.assertFalse((self.wt / "impl" / "B.txt").exists())
        self.assertTrue((self.wt / "impl" / "Keep.txt").exists())
        self.assertEqual(git("status", "--porcelain", cwd=self.wt), "")
        changed = git("diff", "--name-status", "origin/main", "HEAD", cwd=self.wt).split()
        self.assertEqual(changed, ["D", "impl/A.txt", "D", "impl/B.txt", "M", "tools/issue_12.json"])

    def test_retire_v1_tests_removes_only_the_listed_classes(self):
        canary.prepare(self.proj, self.unit, self.wt, runner=self.fake_decompose(), retire_v1=True)
        self.assertFalse((self.wt / "tests" / "Core.Tests" / ("OldTests" + ".cs")).exists())
        self.assertTrue((self.wt / "tests" / "Core.Tests" / ("OtherTests" + ".cs")).exists())

    def test_retire_stops_when_a_listed_class_is_missing(self):
        (self.repo / "tools" / "issue_12.json").write_text(
            json.dumps({"id": "issue_12", "acceptance": {"required_tests": ["GoneTests"]}}), encoding="utf-8")
        git("commit", "-q", "-am", "v1 を変える", cwd=self.repo)
        git("push", "-q", "origin", "main", cwd=self.repo)
        with self.assertRaises(canary.CanaryError):
            canary.prepare(self.proj, self.unit, self.wt, runner=self.fake_decompose(), retire_v1=True)

    def test_prepare_stops_when_decompose_fails(self):
        with self.assertRaises(canary.CanaryError):
            canary.prepare(self.proj, self.unit, self.wt, runner=self.fake_decompose(rc=1))

    def test_cleanup_leaves_nothing_behind(self):
        canary.prepare(self.proj, self.unit, self.wt, runner=self.fake_decompose())
        self.assertEqual(canary.cleanup(self.proj, self.wt), "test/canary-issue-12")
        self.assertFalse(self.wt.exists())
        self.assertNotIn("canary", git("branch", "--list", cwd=self.repo))
        self.assertEqual(git("status", "--porcelain", cwd=self.repo), "")

    def test_refuses_v1_units(self):
        self.unit.write_text(json.dumps({"id": "issue_12", "impl_files": ["impl/A.txt"]}), encoding="utf-8")
        with self.assertRaises(canary.CanaryError):
            canary.prepare(self.proj, self.unit, self.wt, runner=self.fake_decompose())


if __name__ == "__main__":
    unittest.main()
