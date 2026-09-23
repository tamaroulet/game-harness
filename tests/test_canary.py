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


class RepoFixture(unittest.TestCase):
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


class CanaryTests(RepoFixture):
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


class MigrateTests(RepoFixture):
    """main への正式な移行（ADR-003 §3.1、B3.1-1）。prepare と違い impl_files を消さない。"""

    def fake_v2_decompose(self, drop=None):
        def runner(args, **kw):
            wt = Path(args[args.index("--repo-dir") + 1])
            (wt / "tools" / "issue_12.json").write_text(json.dumps({"schema": 2, "id": "issue_12"}), encoding="utf-8")
            if drop:
                (wt / drop).unlink()
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return runner

    def fake_gh(self, sha=None, conclusion="success"):
        def gh(args):
            head = sha or git("rev-parse", "origin/main", cwd=self.repo).strip()
            runs = [{"headSha": head, "status": "completed", "conclusion": conclusion}]
            return SimpleNamespace(returncode=0, stdout=json.dumps(runs), stderr="")
        return gh

    def land(self):
        """移行の枝を main に merge して push する（人間が PR をマージした状態）。"""
        branch, _, _ = canary.migrate(self.proj, self.unit, self.wt, runner=self.fake_v2_decompose())
        git("merge", "-q", "--no-ff", "-m", "merge", branch, cwd=self.repo)
        git("push", "-q", "origin", "main", cwd=self.repo)

    def test_migrate_keeps_impl_files_and_retires_v1_tests(self):
        branch, _, retired = canary.migrate(self.proj, self.unit, self.wt, runner=self.fake_v2_decompose())
        self.assertEqual(branch, "migrate/issue-12-v2")
        self.assertEqual(retired, ["tests/Core.Tests/OldTests" + ".cs"])
        changed = git("diff", "--name-status", "origin/main", "HEAD", cwd=self.wt).split()
        self.assertEqual(changed, ["D", "tests/Core.Tests/OldTests" + ".cs", "M", "tools/issue_12.json"])
        self.assertEqual(git("status", "--porcelain", cwd=self.wt), "")

    def test_migrate_stops_when_an_impl_file_disappears(self):
        with self.assertRaises(canary.CanaryError):
            canary.migrate(self.proj, self.unit, self.wt, runner=self.fake_v2_decompose(drop="impl/A.txt"))

    def test_check_migrated_passes_after_landing_with_green_ci(self):
        self.proj["repo_slug"] = "o/r"
        self.land()
        self.assertEqual(canary.check_migrated(self.proj, "issue_12", gh=self.fake_gh()), [])

    def test_check_migrated_survives_a_later_edit_of_the_unit(self):
        self.proj["repo_slug"] = "o/r"
        self.land()
        (self.repo / "tools" / "issue_12.json").write_text(json.dumps({"schema": 2, "id": "issue_12", "x": 1}),
                                                           encoding="utf-8")
        git("commit", "-q", "-am", "later edit", cwd=self.repo)
        git("push", "-q", "origin", "main", cwd=self.repo)
        self.assertEqual(canary.check_migrated(self.proj, "issue_12", gh=self.fake_gh()), [])

    def test_check_migrated_fails_before_landing(self):
        self.proj["repo_slug"] = "o/r"
        self.assertEqual(len(canary.check_migrated(self.proj, "issue_12", gh=self.fake_gh())), 1)

    def test_check_migrated_fails_on_red_or_stale_ci(self):
        self.proj["repo_slug"] = "o/r"
        self.land()
        self.assertEqual(len(canary.check_migrated(self.proj, "issue_12", gh=self.fake_gh(conclusion="failure"))), 1)
        self.assertEqual(len(canary.check_migrated(self.proj, "issue_12", gh=self.fake_gh(sha="0" * 40))), 1)

    def test_check_migrated_fails_when_a_v1_test_survives(self):
        self.proj["repo_slug"] = "o/r"
        self.land()
        (self.repo / "tests" / "Core.Tests" / ("OldTests" + ".cs")).write_text("x", encoding="utf-8")
        git("add", "-A", cwd=self.repo)
        git("commit", "-q", "-m", "revive", cwd=self.repo)
        git("push", "-q", "origin", "main", cwd=self.repo)
        problems = canary.check_migrated(self.proj, "issue_12", gh=self.fake_gh())
        self.assertEqual(len(problems), 1)
        self.assertIn("OldTests", problems[0])


if __name__ == "__main__":
    unittest.main()
