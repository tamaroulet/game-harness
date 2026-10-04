"""H ライン（harness/hline_gc.py）の作業ツリーの掃除の検査（S1-2）。git は一時ディレクトリの小さなリポジトリで本物を使い、
worktrees・out・ROOT は必ず一時ディレクトリに差し替える（実物の置き場を対象にしない）。再試行の待ちは 0 秒にする。"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import fileops  # noqa: E402
import hline  # noqa: E402
import hline_gc  # noqa: E402
import hline_git  # noqa: E402

CFG = hline.load_config()
REAL_RUN = hline_gc.proc.run
NO_SLEEP = lambda s: None  # noqa: E731


def removal_fails(args):
    return args[:3] == ["git", "worktree", "remove"]


class Repo(unittest.TestCase):
    def git(self, cwd, *args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                              capture_output=True, text=True, encoding="utf-8").stdout

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin, self.clone, self.wt, self.out = (self.tmp / n for n in ("origin", "clone", "wt", "out"))
        self.origin.mkdir()
        self.wt.mkdir()
        self.git(self.origin, "init", "-q", "-b", "main")
        self.git(self.origin, "commit", "-q", "--allow-empty", "-m", "main")
        self.git(self.tmp, "clone", "-q", str(self.origin), str(self.clone))
        self.cfg = {**CFG, "worktrees": str(self.wt), "out": str(self.out)}
        for mod in (hline_gc, hline_git):
            patcher = mock.patch.object(mod, "ROOT", self.clone)
            patcher.start()
            self.addCleanup(patcher.stop)

    def add_worktree(self, name, branch=None):
        self.git(self.clone, "worktree", "add", "-q", "-b", branch or hline_gc.branch_of(name), str(self.wt / name), "main")
        return self.wt / name

    def branches(self):
        return set(self.git(self.clone, "branch", "--format=%(refname:short)").split())

    def recording_run(self, failing=None):
        calls = []

        def run(args, *rest, **kw):
            calls.append(list(args))
            return (1, "", "denied") if failing and failing(args) else REAL_RUN(args, *rest, **kw)

        run.calls = calls
        return run

    def sweep(self, keep=(), run=None):
        with mock.patch.object(hline_gc.proc, "run", run or self.recording_run()):
            return hline_gc.sweep(self.cfg, keep, sleep=NO_SLEEP)

    def new(self, *numbers):
        fake = mock.patch.object(hline_git.uuid, "uuid4", side_effect=[uuid.UUID(int=n << 96) for n in numbers])
        with fake as uuid4, mock.patch.object(hline_git, "implementer_room"):
            return hline_git.new_worktree(self.cfg, "t"), uuid4.call_count


class NewWorktree(Repo):
    def test_a_second_worktree_with_the_same_tid_does_not_overlap_the_first(self):
        first, second = self.new(1)[0], self.new(1, 2)[0]
        self.assertNotEqual(first, second)

    def test_a_leftover_directory_or_branch_is_never_chosen(self):
        (self.wt / "hline-t-00000001").mkdir()
        self.git(self.clone, "branch", "hline/t-00000002", "main")
        self.assertEqual(self.new(1, 2, 3), ((self.wt / "hline-t-00000003", "hline/t-00000003"), 3))

    def test_five_collisions_in_a_row_are_an_environment_fault(self):
        (self.wt / "hline-t-00000001").mkdir()
        with self.assertRaises(hline_git.Infra):
            self.new(1, 1, 1, 1, 1)


class Parts(Repo):
    def test_targets_are_the_hline_directories_directly_under_the_base_in_name_order(self):
        self.assertEqual(hline_gc.branch_of("hline-20260101-1200-x-abcd1234"), "hline/20260101-1200-x-abcd1234")
        self.assertIsNone(hline_gc.branch_of("other-20260101"))
        for d in ("hline-b", "hline-a", "other", "hline-c/hline-grandchild"):
            (self.wt / d).mkdir(parents=True)
        (self.wt / "hline-file").write_text("x", encoding="utf-8")
        self.assertEqual([p.name for p in hline_gc.targets(self.cfg)], ["hline-a", "hline-b", "hline-c"])
        self.assertEqual([p.name for p in hline_gc.targets(self.cfg, keep=[self.wt / "hline-a"])], ["hline-b", "hline-c"])
        with mock.patch.object(hline_gc, "ROOT", self.wt / "hline-c" / "hline-grandchild"):
            self.assertEqual([p.name for p in hline_gc.targets(self.cfg)], ["hline-a", "hline-b"])
        self.assertEqual(hline_gc.targets({**self.cfg, "worktrees": str(self.tmp / "none")}), [])

    def test_branches_that_must_not_be_removed(self):
        self.git(self.clone, "branch", "hline/remote-one", "main")
        self.git(self.clone, "push", "-q", "origin", "hline/remote-one")
        self.git(self.clone, "fetch", "-q", "origin")
        for branch in (self.cfg["integration_branch"], "main", self.cfg["base"], "hline/remote-one", "feature/x", None):
            self.assertFalse(hline_gc.removable_branch(self.cfg, branch), branch)
        self.assertTrue(hline_gc.removable_branch(self.cfg, "hline/local-only"))


class Sweep(Repo):
    def test_both_kinds_of_leftover_are_cleared_and_the_local_branch_goes_with_them(self):
        present, gone = self.add_worktree("hline-p-1"), self.add_worktree("hline-g-1")
        (present / "dirty.txt").write_text("途中の変更", encoding="utf-8")
        shutil.rmtree(gone)
        rec = self.sweep()
        self.assertEqual(len(self.git(self.clone, "worktree", "list").splitlines()), 1)
        self.assertNotIn("hline/p-1", self.branches())
        self.assertEqual((rec["removed"], rec["branches"], rec["kept"], rec["pruned"]), ([str(present)], ["hline/p-1"], [], True))

    def test_nothing_outside_the_rules_is_removed_and_no_push_is_made(self):
        keep, victim = self.add_worktree("hline-keep-1"), self.add_worktree("hline-victim-1")
        other = self.add_worktree("other-1", branch="feature/other-1")
        (self.wt / "hline-file").write_text("x", encoding="utf-8")
        outside = self.tmp / "outside" / "hline-out-1"
        outside.mkdir(parents=True)
        run = self.recording_run()
        rec = self.sweep(keep=[keep], run=run)
        self.assertTrue(keep.exists() and other.exists() and outside.exists() and (self.wt / "hline-file").exists())
        self.assertEqual((victim.exists(), rec["removed"]), (False, [str(victim)]))
        self.assertTrue({"hline/keep-1", "feature/other-1", "main"} <= self.branches())
        for args in run.calls:
            self.assertNotIn("push", args)
            self.assertIn(args[1:3], (["worktree", "remove"], ["worktree", "prune"], ["branch", "-D"], ["rev-parse", "--verify"]))

    def test_the_record_with_removed_and_kept_is_written_to_gc_json(self):
        gone, stuck = self.add_worktree("hline-a-9"), self.add_worktree("hline-b-9")
        rec = self.sweep(run=self.recording_run(lambda a: removal_fails(a) and a[-1] == str(stuck)))
        written = json.loads((self.out / "gc.json").read_text(encoding="utf-8"))
        self.assertEqual(written, rec)
        self.assertEqual((written["removed"], written["kept"]), ([str(gone)], [{"path": str(stuck), "reason": "denied"}]))
        self.assertEqual(hline_gc.targets(self.cfg), [stuck])


class RemovalFailure(Repo):
    def test_remove_worktree_reports_the_reason_and_waits_between_retries(self):
        wt = self.add_worktree("hline-f-1")
        sleeps, run = [], self.recording_run(removal_fails)
        with mock.patch.object(hline_gc.proc, "run", run):
            self.assertEqual(hline_gc.remove_worktree(self.cfg, wt, sleeps.append), (False, "denied"))
        self.assertEqual(sleeps, list(fileops.DELAYS))
        self.assertEqual(sum(map(removal_fails, run.calls)), len(fileops.DELAYS) + 1)

    def test_exceptions_from_proc_run_are_caught(self):
        wt = self.add_worktree("hline-f-2")
        with mock.patch.object(hline_gc.proc, "run", side_effect=OSError("git が呼べない")):
            ok, reason = hline_gc.remove_worktree(self.cfg, wt, NO_SLEEP)
            rec = hline_gc.sweep(self.cfg, sleep=NO_SLEEP)
        self.assertFalse(ok)
        self.assertIn("git が呼べない", reason)
        self.assertEqual((len(rec["kept"]), rec["pruned"]), (1, False))


class RunLine(Repo):
    def run_line(self, sweep):
        calls = []
        fakes = dict(fetch=lambda c: calls.append("fetch"), recover=lambda *a: None, intake=lambda *a, **k: None,
                     save_state=lambda *a: None, write_report=lambda *a: None,
                     load_state=lambda c: {"awaiting_pr": None, "items": {}}, sweep=lambda c: calls.append("sweep") or sweep(c))
        with mock.patch.multiple(hline, **{k: mock.Mock(side_effect=v) for k, v in fakes.items()}):
            return hline.run_line(self.cfg), calls

    def test_the_sweep_runs_once_before_the_fetch(self):
        self.assertEqual(self.run_line(lambda c: {}), (0, ["sweep", "fetch"]))

    def test_a_sweep_that_cannot_remove_anything_leaves_the_exit_code_unchanged(self):
        self.add_worktree("hline-f-6")
        with mock.patch.object(hline_gc.proc, "run", self.recording_run(removal_fails)), \
                mock.patch.object(hline_gc.time, "sleep"):
            self.assertEqual(self.run_line(hline_gc.sweep)[0], 0)
        self.assertEqual([p.name for p in hline_gc.targets(self.cfg)], ["hline-f-6"])


if __name__ == "__main__":
    unittest.main()
