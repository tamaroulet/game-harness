"""起動可否の報告（harness/scheduler.py の --preflight）。

    python -m unittest discover -s tests -v

**なぜ要るか**: 2026-09-19 に、起動できない理由（ロック / worktree の把持 / ラベル / ハーネスの版）が
外から一目で分からず、推測による手戻りを何度も生んだ。固定 worktree が `ms4/issue-14` を掴んだままで、
`git branch -D` すら失敗する状態を、人間が git の内部構造を知って初めて特定できるという構造だった。

**この報告は何も直さない。** 直す側（`preflight()` / `process()`）と同じ述語を通す。報告と判定で
別実装にすると、「起動できます」と言うのに起動できない嘘が生まれる（数える側と当てる側が分かれていた
実例が tests/mutate.py にある。PR 9 で 6 件の変異が黙って実行不能になっていた）。
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import scheduler as ms4  # noqa: E402

# 実測（2026-09-19 15:01 の ABORT）。理由を落とさないことを、この文面で固定する。
ABORTED = "ms3_pipeline.py が rc=2 で終了しました（環境異常）"

WORKTREE_LIST = """worktree C:/src/falling-blocks
HEAD 1111111111111111111111111111111111111111
branch refs/heads/main

worktree C:/src/.local/wt/falling-blocks-issue-runner
HEAD 2222222222222222222222222222222222222222
branch refs/heads/ms4/issue-14

worktree C:/src/.local/wt/falling-blocks-sandbox
HEAD 3333333333333333333333333333333333333333
detached
"""


def fake_scheduler(tmp):
    """blockers() が触る属性だけを持つ Scheduler。重い初期化を通さない。"""
    s = ms4.Scheduler.__new__(ms4.Scheduler)
    s.cfg = {"required_clis": ["git", "gh"], "ttl_seconds": {"git": 60}}
    s.base = "main"
    s.repo = tmp / "repo"
    s.lock = tmp / "scheduler.lock"
    s.harness_sha = "a" * 40
    return s


class LockSummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.s = fake_scheduler(self.tmp)

    def write(self, *records):
        self.s.lock.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
                               encoding="utf-8")

    def test_the_abort_reason_is_not_dropped(self):
        """これが退行そのもの。止まった理由こそ、人間が消す前に見るべきもの。"""
        self.write({"pid": 3096, "run_id": "20260919-144656"}, {"aborted": ABORTED})
        with mock.patch.object(ms4, "pid_alive", return_value=False):
            summary = self.s.lock_summary()
        self.assertIn(ABORTED, summary)
        self.assertIn("20260919-144656", summary)
        self.assertIn("終了している", summary)

    def test_a_live_lock_says_so(self):
        self.write({"pid": 4242, "run_id": "r"})
        with mock.patch.object(ms4, "pid_alive", return_value=True):
            self.assertIn("生きている", self.s.lock_summary())

    def test_an_unreadable_lock_does_not_raise(self):
        self.s.lock.write_text("これは JSON ではありません\n", encoding="utf-8")
        self.assertIn("形が読めません", self.s.lock_summary())


class WorktreeBranchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.s = fake_scheduler(self.tmp)
        self.runner = Path("C:/src/.local/wt/falling-blocks-issue-runner")

    def branch_of(self, listing):
        with mock.patch.object(ms4, "run_cmd", return_value=(0, listing, "")), \
             mock.patch.object(ms4.Scheduler, "runner_worktree", return_value=self.runner):
            return self.s.runner_worktree_branch()

    def test_it_finds_the_branch_the_runner_holds(self):
        """掴まれたままだと `git branch -D` すら失敗する（2026-09-19 に実測）。"""
        self.assertEqual(self.branch_of(WORKTREE_LIST), "ms4/issue-14")

    def test_a_detached_runner_is_not_blocking(self):
        detached = WORKTREE_LIST.replace("branch refs/heads/ms4/issue-14", "detached")
        self.assertIsNone(self.branch_of(detached))

    def test_other_worktrees_are_not_confused_with_the_runner(self):
        """本体や sandbox が main を掴んでいても、それは阻害ではない。"""
        without = WORKTREE_LIST.split("worktree C:/src/.local/wt/falling-blocks-issue-runner")[0]
        self.assertIsNone(self.branch_of(without))


class BlockersTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.s = fake_scheduler(self.tmp)

    def rows(self, **over):
        """既定は全部 OK。over で 1 つずつ壊す。"""
        d = {"harness_version": (self.s.harness_sha, self.s.harness_sha, True),
             "cli_missing": [], "wrong_branch": None, "dirty_paths": [],
             "runner_worktree_branch": None}
        d.update(over)
        with mock.patch.multiple(ms4.Scheduler,
                                 runner_worktree=mock.DEFAULT,
                                 **{k: mock.DEFAULT for k in d}) as m:
            for k, v in d.items():
                m[k].return_value = v
            m["runner_worktree"].return_value = Path("wt")
            return self.s.blockers()

    def ok_of(self, rows, label):
        return next(ok for name, ok, _, _ in rows if name == label)

    def detail_of(self, rows, label):
        return next(detail for name, _, detail, _ in rows if name == label)

    def test_everything_green_is_startable(self):
        with mock.patch("builtins.print"):
            rc = self._show()
        self.assertEqual(rc, 0)

    def _show(self, **over):
        with mock.patch.object(ms4.Scheduler, "blockers", return_value=self.rows(**over)):
            return self.s.show_preflight()

    def test_the_harness_version_is_shown(self):
        """どの版のハーネスで走るかは、実行の意味を変える。"""
        rows = self.rows(harness_version=("a" * 40, "b" * 40, False))
        self.assertFalse(self.ok_of(rows, "ハーネス"))
        self.assertIn("bbbbbbb", self.detail_of(rows, "ハーネス"))

    def test_a_held_worktree_blocks(self):
        rows = self.rows(runner_worktree_branch="ms4/issue-14")
        self.assertFalse(self.ok_of(rows, "worktree"))
        self.assertIn("ms4/issue-14", self.detail_of(rows, "worktree"))

    def test_a_dirty_tree_blocks(self):
        self.assertFalse(self.ok_of(self.rows(dirty_paths=["a.cs"]), "作業ツリー"))

    def test_a_missing_cli_blocks(self):
        self.assertFalse(self.ok_of(self.rows(cli_missing=["gh"]), "CLI"))

    def test_the_wrong_branch_blocks(self):
        self.assertFalse(self.ok_of(self.rows(wrong_branch="elsewhere"), "ブランチ"))

    def test_a_lock_with_an_abort_record_blocks(self):
        self.s.lock.write_text(json.dumps({"pid": 1}) + "\n"
                               + json.dumps({"aborted": ABORTED}, ensure_ascii=False) + "\n",
                               encoding="utf-8")
        with mock.patch.object(ms4.Scheduler, "stale_lock_reason", return_value=None), \
             mock.patch.object(ms4, "pid_alive", return_value=False):
            rows = self.rows()
        self.assertFalse(self.ok_of(rows, "ロック"))
        self.assertIn(ABORTED, self.detail_of(rows, "ロック"), "理由を出す")

    def test_an_auto_releasable_lock_does_not_block(self):
        self.s.lock.write_text(json.dumps({"pid": 1}) + "\n", encoding="utf-8")
        with mock.patch.object(ms4.Scheduler, "stale_lock_reason", return_value="pid 1 は終了している"):
            self.assertTrue(self.ok_of(self.rows(), "ロック"))

    def test_every_blocker_carries_a_remedy(self):
        """阻害を出すだけでは、また推測で直すことになる。"""
        rows = self.rows(runner_worktree_branch="ms4/issue-14", dirty_paths=["a.cs"],
                         wrong_branch="elsewhere", cli_missing=["gh"],
                         harness_version=("a" * 40, "b" * 40, False))
        without = [name for name, ok, _, remedy in rows if not ok and not remedy]
        self.assertEqual(without, [], "阻害には直し方を添える")


class ReadOnlyTests(unittest.TestCase):
    """**何も変えない**こと。報告が状態を動かすと、報告自体が信用できなくなる。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.s = fake_scheduler(self.tmp)

    def test_it_only_runs_read_only_git_commands(self):
        calls = []

        def spy(args, cwd, ttl):
            calls.append(args)
            return 0, "", ""

        with mock.patch.object(ms4, "run_cmd", side_effect=spy), \
             mock.patch.object(ms4.Scheduler, "cli_missing", return_value=[]), \
             mock.patch.object(ms4.Scheduler, "wrong_branch", return_value=None), \
             mock.patch.object(ms4.Scheduler, "dirty_paths", return_value=[]), \
             mock.patch.object(ms4.Scheduler, "runner_worktree", return_value=Path("wt")):
            self.s.blockers()

        allowed = {("git", "rev-parse"), ("git", "worktree")}
        bad = [a for a in calls if tuple(a[:2]) not in allowed]
        self.assertEqual(bad, [], "読み取り以外のコマンドを出している")
        self.assertNotIn(["git", "fetch"], calls, "fetch もしない（古ければ古いまま見せる）")


if __name__ == "__main__":
    unittest.main()
