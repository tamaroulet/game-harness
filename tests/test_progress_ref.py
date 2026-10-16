"""進捗の記録の照合先を選べる（progress complete --ref <rev>、H ラインの record は設定の base を渡す）。

    python -m unittest tests.test_progress_ref -v

**なぜ要るか**: ベンチの走行の台帳は origin/main と別の版を持つ。--ref で照合先を選べれば、W-26・W-27 の規則（検証コマンドの
照合）を保ったまま、ベンチでも記録できる。一時ディレクトリの git のリポジトリを使い、ネットワークは使わない（fetch は差し替える）。
"""
import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import hline_base  # noqa: E402
import hline_git  # noqa: E402
import progress  # noqa: E402

CFG = hline_base.load_config()
OLD = {"command": "exit 0", "expected_exit_code": 0}
NEW = {"command": "exit 0 && exit 0", "expected_exit_code": 0}
REAL_RUN = subprocess.run


def make_state(verification):
    return {
        "project_goal": "g",
        "nodes": [{"id": "S2", "title": "段", "parent": None}],
        "tasks": [{"id": "T1", "title": "T1", "group": "S2", "target_repo": "game-harness", "status": "in_progress",
                   "verification": dict(verification)}],
        "constraints": ["c"],
        "active_task_id": "T1",
    }


def git(repo, *args):
    r = REAL_RUN(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *args],
                 cwd=repo, capture_output=True, text=True, check=True)
    return r.stdout.strip()


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        (self.dir / "docs").mkdir()
        self.path = self.dir / progress.REL_PATH
        git(self.dir, "init", "-q")
        self.old = self.commit(OLD, "old")
        self.new = self.commit(NEW, "new")

    def commit(self, verification, msg):
        progress.save(make_state(verification), self.path)
        git(self.dir, "add", "-A")
        git(self.dir, "commit", "-q", "-m", msg)
        return git(self.dir, "rev-parse", "HEAD")

    def put(self, verification):
        progress.save(make_state(verification), self.path)

    def complete(self, **kw):
        logs = []
        code = progress.complete("T1", repo_root=self.dir, out=logs.append, **kw)
        return code, "\n".join(logs)

    def recorded_calls(self, fn):
        calls = []

        def spy(args, *a, **k):
            calls.append(list(args))
            if args[:2] == ["git", "fetch"]:
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            return REAL_RUN(args, *a, **k)

        with mock.patch.object(progress.subprocess, "run", side_effect=spy):
            result = fn()
        return result, calls


class RefSelectsTheComparison(Base):
    def test_refuses_a_local_verification_that_differs_from_the_given_old_commit(self):
        self.put(NEW)
        with self.assertRaises(progress.ProgressError) as cm:
            self.complete(ref=self.old, fetch=False)
        self.assertIn("違います", str(cm.exception))
        self.assertEqual(progress.task(progress.load(self.path), "T1")["status"], "in_progress")

    def test_completes_when_the_local_verification_equals_the_given_old_commit(self):
        self.put(OLD)
        code, _ = self.complete(ref=self.old, fetch=False)
        self.assertEqual(code, 0)
        self.assertEqual(progress.task(progress.load(self.path), "T1")["status"], "completed")

    def test_the_same_local_verification_is_refused_by_the_other_version(self):
        self.put(OLD)
        with self.assertRaises(progress.ProgressError):
            self.complete(ref=self.new, fetch=False)

    def test_without_ref_it_still_compares_with_origin_main(self):
        seen = []

        def fake_version(repo_root, ref, fetch=True):
            seen.append(ref)
            return make_state(OLD)

        self.put(OLD)
        with mock.patch.object(progress, "main_version", side_effect=fake_version):
            code, _ = self.complete()
        self.assertEqual(code, 0)
        self.assertEqual(seen, ["origin/main"])


class FetchOnlyForRemoteShapedRefs(Base):
    def test_a_sha_or_a_local_branch_or_a_relative_rev_does_not_fetch(self):
        git(self.dir, "branch", "feature/old", self.old)
        for ref in (self.old, "feature/old", "HEAD~1"):
            with self.subTest(ref=ref):
                state, calls = self.recorded_calls(lambda: progress.main_version(self.dir, ref, fetch=True))
                self.assertEqual(progress.task(state, "T1")["verification"], OLD)
                self.assertEqual([c for c in calls if c[:2] == ["git", "fetch"]], [])

    def test_a_ref_on_an_existing_remote_fetches_that_branch(self):
        git(self.dir, "remote", "add", "bench", str(self.dir))
        git(self.dir, "update-ref", "refs/remotes/bench/main", self.old)
        state, calls = self.recorded_calls(lambda: progress.main_version(self.dir, "bench/main", fetch=True))
        self.assertEqual(progress.task(state, "T1")["verification"], OLD)
        fetches = [c for c in calls if c[:2] == ["git", "fetch"]]
        self.assertEqual(len(fetches), 1)
        self.assertEqual(fetches[0][-2:], ["bench", "main"])

    def test_the_default_origin_main_fetches_when_origin_exists(self):
        git(self.dir, "remote", "add", "origin", str(self.dir))
        git(self.dir, "update-ref", "refs/remotes/origin/main", self.new)
        state, calls = self.recorded_calls(lambda: progress.main_version(self.dir, "origin/main", fetch=True))
        self.assertEqual(progress.task(state, "T1")["verification"], NEW)
        self.assertEqual(len([c for c in calls if c[:2] == ["git", "fetch"]]), 1)


class Cli(unittest.TestCase):
    def test_ref_is_passed_to_complete(self):
        with mock.patch.object(progress, "complete", return_value=0) as comp, \
                mock.patch.dict("os.environ", {}, clear=False):
            progress.os.environ.pop(progress.ORDER_FREE_ENV, None)
            self.assertEqual(progress.main(["complete", "T1", "--ref", "abc123"]), 0)
        comp.assert_called_once_with("T1", ref="abc123")

    def test_without_ref_complete_is_called_as_before(self):
        with mock.patch.object(progress, "complete", return_value=0) as comp, \
                mock.patch.dict("os.environ", {}, clear=False):
            progress.os.environ.pop(progress.ORDER_FREE_ENV, None)
            progress.main(["complete", "T1"])
        comp.assert_called_once_with("T1")

    def test_ref_is_kept_when_order_free(self):
        with mock.patch.object(progress, "complete", return_value=0) as comp, \
                mock.patch.dict("os.environ", {progress.ORDER_FREE_ENV: "1"}):
            progress.main(["complete", "T1", "--ref", "abc123"])
        comp.assert_called_once_with("T1", require_active=False, ref="abc123")


class RecordPassesTheConfiguredBase(unittest.TestCase):
    def test_record_adds_ref_with_the_base_of_the_config(self):
        for base in (CFG["base"], "origin/bench"):
            with self.subTest(base=base):
                cfg = copy.deepcopy(CFG)
                cfg["base"] = base
                calls = []

                def fake_run(args, cwd, ttl, label, env=None, input=None):
                    calls.append(args)
                    return 0, "", ""

                with mock.patch.object(hline_git.proc, "run", side_effect=fake_run):
                    self.assertEqual(hline_git.record(cfg, Path("."), "T1")[0], 0)
                self.assertEqual(calls[0][-5:], ["harness.progress", "complete", "T1", "--ref", base])


if __name__ == "__main__":
    unittest.main()
