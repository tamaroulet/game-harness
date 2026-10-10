"""H ライン（harness/hline_sync.py）の、統合ブランチを main に追いつかせる検査。git は一時ディレクトリの本物
（origin を模した bare のリポジトリ・H ラインの clone・main を進める別の clone）を使い、ROOT と worktrees は必ず一時ディレクトリに差し替える。"""
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import hline  # noqa: E402
import hline_git  # noqa: E402
import hline_report  # noqa: E402
import hline_sync  # noqa: E402
import hline_task  # noqa: E402
from hline_base import SyncConflict  # noqa: E402

CFG = hline.load_config()
BRANCH = CFG["integration_branch"]


class Sync(unittest.TestCase):
    def git(self, cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, encoding="utf-8").stdout.strip()

    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin, self.clone, self.dev, self.wt = (self.tmp / n for n in ("origin.git", "clone", "dev", "wt"))
        self.git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.git(self.tmp, "clone", "-q", str(self.origin), str(self.dev))
        self.config(self.dev)
        self.commit(self.dev, "f.txt", "first\n", "first")
        self.git(self.dev, "push", "-q", "origin", "main")
        self.git(self.tmp, "clone", "-q", str(self.origin), str(self.clone))
        self.config(self.clone)
        self.cfg = {**CFG, "worktrees": str(self.wt), "base": "origin/main"}
        patcher = mock.patch.object(hline_git, "ROOT", self.clone)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.pushes = []
        real_run = hline_sync.proc.run

        def recording(args, *rest, **kw):
            if args[:2] == ["git", "push"]:
                self.pushes.append(list(args))
            return real_run(args, *rest, **kw)

        patcher = mock.patch.object(hline_sync.proc, "run", side_effect=recording)
        patcher.start()
        self.addCleanup(patcher.stop)

    def config(self, repo):
        self.git(repo, "config", "user.name", "t")
        self.git(repo, "config", "user.email", "t@t")

    def commit(self, repo, name, text, message):
        (repo / name).write_text(text, encoding="utf-8")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", message)

    def on_main(self, name, text):
        self.git(self.dev, "checkout", "-q", "main")
        self.commit(self.dev, name, text, f"main: {name}")
        self.git(self.dev, "push", "-q", "origin", "main")

    def on_integration(self, name, text):
        self.git(self.dev, "checkout", "-q", "-B", BRANCH, "origin/main" if not self.remote(BRANCH) else f"origin/{BRANCH}")
        self.commit(self.dev, name, text, f"integration: {name}")
        self.git(self.dev, "push", "-q", "origin", f"{BRANCH}:refs/heads/{BRANCH}")
        self.git(self.dev, "checkout", "-q", "main")
        self.git(self.dev, "fetch", "-q", "origin")

    def remote(self, branch):
        out = subprocess.run(["git", "rev-parse", "--verify", "-q", f"refs/heads/{branch}"], cwd=self.origin,
                             capture_output=True, text=True)
        return out.stdout.strip()

    def new_worktree(self):
        with mock.patch.object(hline_git, "implementer_room"):
            return hline_git.new_worktree(self.cfg, "t")

    def test_an_integration_branch_with_nothing_of_its_own_is_fast_forwarded_to_main(self):
        self.git(self.dev, "push", "-q", "origin", f"main:refs/heads/{BRANCH}")   # 統合ブランチは main と同じ先端
        self.on_main("second.txt", "2\n")
        self.assertNotEqual(self.remote(BRANCH), self.remote("main"))
        wt, _ = self.new_worktree()
        self.assertEqual(self.remote(BRANCH), self.remote("main"))
        self.assertEqual((wt / "second.txt").read_text(encoding="utf-8"), "2\n")

    def test_an_integration_branch_with_its_own_commits_gets_main_merged_in(self):
        self.on_integration("own.txt", "x")
        own = self.remote(BRANCH)
        self.on_main("second.txt", "2\n")
        wt, _ = self.new_worktree()
        tip = self.remote(BRANCH)
        self.assertNotIn(tip, (own, self.remote("main")))
        parents = self.git(self.origin, "rev-list", "--parents", "-n", "1", tip).split()[1:]
        self.assertEqual(sorted(parents), sorted([own, self.remote("main")]))
        self.assertTrue((wt / "own.txt").exists() and (wt / "second.txt").exists())
        self.assertEqual(self.git(self.origin, "merge-base", "--is-ancestor", self.remote("main"), tip), "")
        self.assertFalse(list(self.wt.glob("hline-sync-*")))   # 一時の作業ツリーは残さない

    def test_a_conflict_pushes_nothing_creates_no_worktree_and_names_the_paths(self):
        self.on_integration("f.txt", "integration side\n")
        before = self.remote(BRANCH)
        self.on_main("f.txt", "main side\n")
        with self.assertRaises(SyncConflict) as caught:
            self.new_worktree()
        self.assertEqual(caught.exception.paths, ["f.txt"])
        self.assertEqual(self.remote(BRANCH), before)
        self.assertEqual(self.pushes, [])
        self.assertEqual([p.name for p in self.wt.glob("*")], [])
        self.assertEqual(self.git(self.clone, "branch", "--list", "hline/*"), "")

    def test_nothing_is_done_when_main_is_already_an_ancestor(self):
        self.on_integration("own.txt", "x")
        before = self.remote(BRANCH)
        wt, _ = self.new_worktree()
        self.assertEqual((self.remote(BRANCH), self.pushes), (before, []))
        self.assertTrue((wt / "own.txt").exists())

    def test_nothing_is_done_when_the_integration_branch_does_not_exist(self):
        wt, _ = self.new_worktree()
        self.assertEqual((self.remote(BRANCH), self.pushes), ("", []))
        self.assertTrue((wt / "f.txt").exists())

    def test_the_conflict_is_a_human_task_and_the_what_goes_back_to_waiting(self):
        st = {"items": {}, "awaiting_pr": None, "infra_halt": None, "sync_conflict": {"name": "010-a", "paths": ["a.txt", "b/c.txt"]}}
        self.assertEqual(hline_report.human_line(self.cfg, st), "- 人間作業: SYNC_CONFLICT a.txt／b/c.txt")
        cfg = {"out": str(self.tmp / "out"), "infra_retry": {"max_retries": 3, "wait_seconds": [60]}}
        what = self.tmp / "what.md"
        what.write_text("# T\n本文\n", encoding="utf-8")
        state = {"items": {"010-a": {"title": "T", "task": None}}}
        host = mock.Mock(what_path=lambda c, n: what, slug=lambda n: "a", today=lambda: "2026-10-11",
                         new_worktree=mock.Mock(side_effect=SyncConflict(["f.txt"])))
        with redirect_stdout(io.StringIO()):
            done = hline_task.process(host, cfg, state, "010-a")
        self.assertFalse(done)
        self.assertEqual((state["items"]["010-a"]["status"], state["sync_conflict"]), ("waiting", {"name": "010-a", "paths": ["f.txt"]}))
        host.new_worktree.assert_called_once()   # 呼び直さない
        host.run_task.assert_not_called()

    def test_no_force_option_is_used_by_the_sync(self):
        text = Path(hline_sync.__file__).read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"--force|\"-f\"|'-f'|\"\+|'\+|\+refs", text))
        self.assertIsNone(re.search(r"--force|\"-f\"|'-f'|\"\+|'\+|\+refs", Path(hline_git.__file__).read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
