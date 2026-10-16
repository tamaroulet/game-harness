"""ベンチの開始のコミット（W-44）の試験。一時のディレクトリに git のリポジトリを作って確かめる。"""
import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import bench_start  # noqa: E402
import progress  # noqa: E402

SPEC = "# B06\n\nリポジトリの根の `launcher` パッケージを作る。\n"
FILES = {
    "launcher/__init__.py": "x = 1\n",
    "launcher/sub/core.py": "y = 2\n",
    "tests/launcher_judge.py": "judge\n",
    "tests/test_launcher_entry.py": "entry\n",
    "tests/test_launcher.py": "body\n",
    "tests/test_other.py": "other\n",
    "tests/launcher_helper.py": "helper\n",
    "tests/sub/test_launcher_x.py": "nested\n",
    "harness/tool.py": "tool\n",
    "docs/prompts/auditor.md": "audit\n",
    "docs/bench/B06/spec.md": SPEC,
    "docs/bench/B07/spec.md": "# B07\n\n仕様にパッケージの記述が無い。\n",
    "docs/progress.yaml": "project_goal: g\nnodes: []\ntasks: []\nconstraints: []\n",
}
GOOD = [{"id": "P-01", "title": "一つ目", "verification": {"command": "python -m unittest tests.test_launcher", "expected_exit_code": 0}},
        {"id": "P-02", "title": "二つ目", "depends_on": ["P-01"]}]


def git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", check=True)
    return r.stdout.strip()


class BenchStartTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = self.root / "clone"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "t")
        git(self.repo, "config", "user.email", "t@example.com")
        for rel, text in FILES.items():
            p = self.repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(text.encode("utf-8"))
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")

    def run_main(self, task, ledger=None):
        argv = ["--repo", str(self.repo), "--base", self.base, "--task", task]
        if ledger is not None:
            argv += ["--ledger", str(ledger)]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = bench_start.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def ledger(self, tasks, name="ledger.yaml"):
        p = self.root / name
        p.write_text(yaml.safe_dump(tasks, allow_unicode=True), encoding="utf-8")
        return p

    def files_of(self, rev):
        return set(git(self.repo, "ls-tree", "-r", "--name-only", rev).splitlines())

    def test_commit_without_package_and_judge(self):
        rc, out, _ = self.run_main("B06")
        self.assertEqual(rc, 0)
        sha = out.strip().splitlines()[-1]
        self.assertRegex(sha, r"^[0-9a-f]{40}$")
        self.assertEqual(git(self.repo, "rev-list", "--parents", "-n", "1", sha), sha)
        self.assertEqual(git(self.repo, "rev-list", "--count", sha), "1")
        removed = {"launcher/__init__.py", "launcher/sub/core.py", "tests/launcher_judge.py",
                   "tests/test_launcher_entry.py", "tests/test_launcher.py"}
        self.assertEqual(self.files_of(sha), self.files_of(self.base) - removed)
        for kept in ("tests/test_other.py", "tests/launcher_helper.py", "tests/sub/test_launcher_x.py", "harness/tool.py"):
            self.assertIn(kept, self.files_of(sha))
        for path in self.files_of(sha):
            self.assertEqual(git(self.repo, "rev-parse", f"{sha}:{path}"), git(self.repo, "rev-parse", f"{self.base}:{path}"))

    def test_clone_state_unchanged(self):
        before = (git(self.repo, "rev-parse", "HEAD"), git(self.repo, "status", "--porcelain"),
                  git(self.repo, "branch", "--show-current"), git(self.repo, "ls-files", "-s"))
        rc, _, _ = self.run_main("B06", self.ledger(GOOD))
        self.assertEqual(rc, 0)
        after = (git(self.repo, "rev-parse", "HEAD"), git(self.repo, "status", "--porcelain"),
                 git(self.repo, "branch", "--show-current"), git(self.repo, "ls-files", "-s"))
        self.assertEqual(before, after)
        self.assertEqual(before[1], "")
        self.assertTrue((self.repo / "launcher" / "__init__.py").exists())

    def test_ledger_replaces_progress(self):
        rc, out, _ = self.run_main("B06", self.ledger(GOOD))
        self.assertEqual(rc, 0)
        sha = out.strip().splitlines()[-1]
        state = yaml.safe_load(git(self.repo, "show", f"{sha}:docs/progress.yaml"))
        self.assertEqual(progress.validate(state), [])
        self.assertEqual([t["id"] for t in state["tasks"]], ["P-01", "P-02"])
        self.assertTrue(all(t["group"] == "bench" and t["target_repo"] == "launcher" and t["status"] == "pending"
                            for t in state["tasks"]))
        self.assertEqual(state["nodes"], [{"id": "bench", "title": "bench", "parent": None}])
        self.assertEqual(state["constraints"], [])
        self.assertNotIn("active_task_id", state)

    def test_same_input_same_tree(self):
        ledger = self.ledger(GOOD)
        a = self.run_main("B06", ledger)[1].strip()
        b = self.run_main("B06", ledger)[1].strip()
        self.assertEqual(git(self.repo, "rev-parse", f"{a}^{{tree}}"), git(self.repo, "rev-parse", f"{b}^{{tree}}"))

    def test_without_ledger_progress_unchanged(self):
        sha = self.run_main("B06")[1].strip()
        self.assertEqual(git(self.repo, "rev-parse", f"{sha}:docs/progress.yaml"),
                         git(self.repo, "rev-parse", f"{self.base}:docs/progress.yaml"))

    def test_invalid_ledger_makes_no_commit(self):
        bad = self.ledger([{"id": "P-01", "title": "x", "depends_on": ["P-99"]}])
        before = git(self.repo, "count-objects", "-v")
        rc, out, err = self.run_main("B06", bad)
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("P-99", err)
        self.assertEqual(before, git(self.repo, "count-objects", "-v"))

    def test_unreadable_ledger_is_one(self):
        p = self.root / "broken.yaml"
        p.write_text("not: a list\n", encoding="utf-8")
        self.assertEqual(self.run_main("B06", p)[0], 1)

    def test_spec_without_package_is_two(self):
        rc, out, _ = self.run_main("B07")
        self.assertEqual(rc, 2)
        self.assertEqual(out, "")

    def test_missing_spec_is_two(self):
        self.assertEqual(self.run_main("B99")[0], 2)


if __name__ == "__main__":
    unittest.main()
