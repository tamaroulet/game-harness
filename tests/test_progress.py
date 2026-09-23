"""進捗の外部主記憶と Gatekeeper（harness/progress.py、ADR-003 §4、B3.1-PREP）。

    python -m unittest discover -s tests -v

**なぜ要るか**: 完了への遷移は、検証コマンドの終了コードを実測したときだけ起きなければならない。
手元で検証コマンドを差し替えても通らないこと、LLM に渡すアンカーが小さいままであること、
リポジトリの progress.yaml が正規の形であることを確かめる。
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import progress  # noqa: E402

PY = f'"{sys.executable}"'


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def sample(first_cmd=f'{PY} -c "import sys; sys.exit(0)"'):
    return {
        "project_goal": "g",
        "active_task_id": "T1",
        "constraints": ["c1"],
        "nodes": [{"id": "A", "title": "root", "parent": None},
                  {"id": "G1", "title": "group 1", "parent": "A"},
                  {"id": "G2", "title": "group 2", "parent": "A"}],
        "tasks": [
            {"id": "T1", "title": "first", "group": "G1", "target_repo": "r", "status": "in_progress",
             "verification": {"command": first_cmd, "expected_exit_code": 0}},
            {"id": "T2", "title": "second", "group": "G1", "target_repo": "r", "status": "pending",
             "verification": None},
            {"id": "T3", "title": "third", "group": "G2", "target_repo": "r", "status": "pending",
             "verification": None},
        ],
        "last_verification": None,
    }


class RepoProgressTests(unittest.TestCase):
    """リポジトリにある本物の docs/progress.yaml。"""

    def setUp(self):
        self.path = ROOT / progress.REL_PATH
        self.state = progress.load(self.path)

    def test_is_valid(self):
        self.assertEqual(progress.validate(self.state), [])

    def test_is_in_canonical_form(self):
        # 手で編集すると書式がずれる。状態の書き換えは CLI だけが行う
        self.assertEqual(progress.dump(self.state), self.path.read_text(encoding="utf-8"))

    def test_anchor_stays_small(self):
        # 350 トークン前後に収める（日本語混じりで 1 文字 ≒ 1 トークンと見ても上限内）
        self.assertLessEqual(len(progress.anchor(self.state)), 1200)

    def test_tree_fits_in_a_report(self):
        self.assertLessEqual(len(progress.tree(self.state).splitlines()), 15)

    def test_report_fits_in_20_lines(self):
        self.assertLessEqual(len(progress.report(self.state).splitlines()), 20)


class ViewTests(unittest.TestCase):
    def test_anchor_has_only_the_current_task(self):
        a = progress.anchor(sample())
        self.assertIn("[T1] first", a)
        self.assertNotIn("second", a)
        self.assertNotIn("group 2", a)
        self.assertIn("python -m harness.progress complete T1", a)

    def test_anchor_says_when_verification_is_undefined(self):
        s = sample()
        s["tasks"][0]["verification"] = None
        self.assertIn("undefined", progress.anchor(s))

    def test_tree_expands_only_the_active_branch(self):
        t = progress.tree(sample())
        self.assertIn("T2 second", t)
        self.assertNotIn("T3", t)
        self.assertIn("G2 group 2（0/1）", t)
        self.assertIn("T3 third", progress.tree(sample(), expand_all=True))

    def test_tree_folds_completed_tasks_into_the_count(self):
        s = sample()
        s["tasks"][0]["status"], s["tasks"][1]["status"], s["active_task_id"] = "completed", "in_progress", "T2"
        t = progress.tree(s)
        self.assertNotIn("T1 first", t)
        self.assertIn("G1 group 1（1/2）", t)
        self.assertIn("T1 first", progress.tree(s, expand_all=True))

    def test_report_has_only_tree_and_state(self):
        r = progress.report(sample())
        self.assertTrue(r.startswith("## 進捗ツリー\n"))
        self.assertIn("- 現在タスク: T1（first）", r)
        self.assertIn("- 人間作業: NONE", r)

    def test_report_refuses_to_exceed_20_lines(self):
        s = sample()
        s["tasks"] += [{"id": f"X{i}", "title": "x", "group": "G1", "target_repo": "r", "status": "pending",
                        "verification": None} for i in range(20)]
        with self.assertRaises(progress.ProgressError):
            progress.report(s)

    def test_validate_rejects_two_active_tasks(self):
        s = sample()
        s["tasks"][1]["status"] = "in_progress"
        self.assertTrue(progress.validate(s))

    def test_validate_rejects_unknown_status(self):
        s = sample()
        s["tasks"][1]["status"] = "done"
        self.assertTrue(progress.validate(s))


class CompleteTests(unittest.TestCase):
    """検証連動の遷移。origin/main の版と照合するので、使い捨ての git リポジトリで確かめる。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        origin, repo = self.tmp / "origin.git", self.tmp / "repo"
        git("init", "-q", "--bare", "-b", "main", str(origin), cwd=self.tmp)
        git("clone", "-q", str(origin), str(repo), cwd=self.tmp)
        git("config", "user.email", "t@example.com", cwd=repo)
        git("config", "user.name", "t", cwd=repo)
        self.repo = repo
        self.path = repo / progress.REL_PATH
        self.path.parent.mkdir(parents=True)

    def publish(self, state):
        progress.save(state, self.path)
        git("add", "-A", cwd=self.repo)
        git("commit", "-q", "-m", "progress", cwd=self.repo)
        git("push", "-q", "origin", "main", cwd=self.repo)

    def complete(self, task_id="T1"):
        lines = []
        rc = progress.complete(task_id, repo_root=self.repo, out=lines.append, now=lambda: _Fixed())
        return rc, "\n".join(lines)

    def test_success_completes_and_promotes_the_next_task(self):
        self.publish(sample())
        rc, _ = self.complete()
        self.assertEqual(rc, 0)
        s = progress.load(self.path)
        self.assertEqual([t["status"] for t in s["tasks"]], ["completed", "in_progress", "pending"])
        self.assertEqual(s["active_task_id"], "T2")
        self.assertEqual(s["last_verification"], {"task": "T1", "exit_code": 0, "at": "2026-09-23T00:00:00"})
        self.assertEqual(progress.validate(s), [])

    def test_review_marks_the_active_task_and_complete_clears_it(self):
        self.publish(sample())
        progress.review("T1", "https://github.com/o/r/pull/1", self.path)
        self.assertIn("REVIEW_REQUIRED https://github.com/o/r/pull/1", progress.report(progress.load(self.path)))
        with self.assertRaises(progress.ProgressError):
            progress.review("T2", "https://github.com/o/r/pull/1", self.path)
        rc, _ = self.complete()
        self.assertEqual(rc, 0)
        self.assertNotIn("review_pr", progress.load(self.path)["tasks"][0])

    def test_failure_is_rejected_and_nothing_is_completed(self):
        self.publish(sample(f'{PY} -c "print(\'boom\'); import sys; sys.exit(3)"'))
        rc, out = self.complete()
        self.assertEqual(rc, 1)
        self.assertIn("boom", out)
        self.assertIn("REJECT", out)
        s = progress.load(self.path)
        self.assertEqual(s["tasks"][0]["status"], "in_progress")
        self.assertEqual(s["last_verification"]["exit_code"], 3)

    def test_environment_failure_is_abort_not_reject(self):
        # 検証コマンドが rc=2（環境異常）なら ABORT。実装の不合格（1）と区別して返す
        self.publish(sample(f'{PY} -c "import sys; sys.exit(2)"'))
        rc, out = self.complete()
        self.assertEqual(rc, 2)
        self.assertIn("ABORT", out)
        self.assertEqual(progress.load(self.path)["tasks"][0]["status"], "in_progress")

    def test_locally_replaced_verification_is_refused(self):
        self.publish(sample(f'{PY} -c "import sys; sys.exit(1)"'))
        s = progress.load(self.path)
        s["tasks"][0]["verification"]["command"] = f'{PY} -c "pass"'
        progress.save(s, self.path)
        with self.assertRaises(progress.ProgressError):
            self.complete()
        self.assertEqual(progress.load(self.path)["tasks"][0]["status"], "in_progress")

    def test_refuses_before_progress_yaml_is_on_main(self):
        git("commit", "-q", "--allow-empty", "-m", "init", cwd=self.repo)
        git("push", "-q", "origin", "main", cwd=self.repo)
        progress.save(sample(), self.path)
        with self.assertRaises(progress.ProgressError):
            self.complete()

    def test_refuses_a_task_that_is_not_active(self):
        self.publish(sample())
        with self.assertRaises(progress.ProgressError):
            self.complete("T2")

    def test_refuses_a_task_without_verification(self):
        s = sample()
        s["tasks"][0]["verification"] = None
        self.publish(s)
        with self.assertRaises(progress.ProgressError):
            self.complete()


class _Fixed:
    def isoformat(self, timespec):
        return "2026-09-23T00:00:00"


if __name__ == "__main__":
    unittest.main()
