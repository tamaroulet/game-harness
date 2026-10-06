"""進捗の記録を、現在のタスクかどうかに依らせない（complete の require_active、advance_active、環境変数 HLINE_PROGRESS_ORDER_FREE）。

    python -m unittest tests.test_progress_order_free -v

**なぜ要るか**: H ラインは依存の順で What を積むが、進捗の現在地は progress.yaml の順で 1 つだけ。現在のタスクでない What を
積むたびに complete が拒絶されると、ラインが止まる。H ラインだけが環境変数で順に依らない記録を選び、検証コマンドの照合と実行は
同じに通す。実コマンド・実際の push は起こさない（subprocess.run・proc.run・main_version を差し替える）。
"""
import contextlib
import copy
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import exitcode  # noqa: E402
import hline  # noqa: E402
import hline_git  # noqa: E402
import progress  # noqa: E402

CFG = hline.load_config()
VERIFY = {"command": "python -m unittest x", "expected_exit_code": 0}


def make_state(statuses, active):
    return {
        "project_goal": "g",
        "nodes": [{"id": "S2", "title": "段", "parent": None}],
        "tasks": [{"id": tid, "title": tid, "group": "S2", "target_repo": "game-harness", "status": st,
                   "verification": dict(VERIFY)} for tid, st in statuses],
        "constraints": ["c"],
        "active_task_id": active,
    }


def four_tasks():
    return make_state([("S2-3", "in_progress"), ("S2-4", "pending"), ("S2-5", "pending"), ("S2-6", "pending")], "S2-3")


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        (self.dir / "docs").mkdir()
        self.path = self.dir / progress.REL_PATH

    def put(self, state):
        progress.save(state, self.path)
        served = copy.deepcopy(state)
        patcher = mock.patch.object(progress, "main_version", side_effect=lambda *a, **k: copy.deepcopy(served))
        patcher.start()
        self.addCleanup(patcher.stop)

    def complete(self, task_id, rc=0, **kw):
        result = subprocess.CompletedProcess("cmd", rc, stdout="", stderr="")
        with mock.patch.object(progress.subprocess, "run", return_value=result) as run:
            code = progress.complete(task_id, repo_root=self.dir, fetch=False, out=lambda s: None, **kw)
        return code, run

    def saved(self):
        return progress.load(self.path)

    def status(self, task_id):
        return progress.task(self.saved(), task_id)["status"]


class OrderFreeComplete(Base):
    def test_completes_a_task_that_is_not_the_active_one_and_keeps_the_active_one(self):
        self.put(four_tasks())
        code, run = self.complete("S2-4", require_active=False)
        self.assertEqual(code, 0)
        self.assertEqual(run.call_count, 1)
        s = self.saved()
        self.assertEqual(self.status("S2-4"), "completed")
        self.assertEqual(s["active_task_id"], "S2-3")
        self.assertEqual(self.status("S2-3"), "in_progress")
        self.assertEqual(progress.validate(s), [])

    def test_a_failing_verification_does_not_complete_it_and_returns_1_or_2(self):
        for rc, expected in ((1, 1), (exitcode.ABORT, 2)):
            with self.subTest(rc=rc):
                self.put(four_tasks())
                code, _ = self.complete("S2-4", rc=rc, require_active=False)
                self.assertEqual(code, expected)
                self.assertEqual(self.status("S2-4"), "pending")
                self.assertEqual(self.saved()["last_verification"]["exit_code"], rc)

    def test_the_default_still_refuses_a_task_that_is_not_the_active_one_without_running_anything(self):
        self.put(four_tasks())
        with self.assertRaises(progress.ProgressError) as cm:
            self.complete("S2-4")
        self.assertIn("現在のタスクではありません", str(cm.exception))
        with mock.patch.object(progress.subprocess, "run") as run:
            with self.assertRaises(progress.ProgressError):
                progress.complete("S2-4", repo_root=self.dir, fetch=False, out=lambda s: None)
        self.assertEqual(run.call_count, 0)
        self.assertEqual(self.status("S2-4"), "pending")

    def test_the_comparison_with_main_is_kept_when_order_free(self):
        state = four_tasks()
        self.put(state)
        tampered = copy.deepcopy(state)
        progress.task(tampered, "S2-4")["verification"]["command"] = "true"
        progress.save(tampered, self.path)
        with mock.patch.object(progress.subprocess, "run") as run:
            with self.assertRaises(progress.ProgressError):
                progress.complete("S2-4", repo_root=self.dir, fetch=False, out=lambda s: None, require_active=False)
        self.assertEqual(run.call_count, 0)

    def test_completing_the_active_task_moves_on_and_runs_out_to_none(self):
        self.put(four_tasks())
        self.assertEqual(self.complete("S2-3")[0], 0)
        self.assertEqual(self.saved()["active_task_id"], "S2-4")
        self.assertEqual(self.status("S2-4"), "in_progress")
        for tid in ("S2-4", "S2-5", "S2-6"):
            self.assertEqual(self.complete(tid)[0], 0)
        s = self.saved()
        self.assertIsNone(s["active_task_id"])
        self.assertEqual(progress.validate(s), [])

    def test_an_order_free_completion_of_the_last_open_task_clears_the_active_one(self):
        self.put(make_state([("S2-3", "completed"), ("S2-4", "pending")], None))
        self.assertEqual(self.complete("S2-4", require_active=False)[0], 0)
        self.assertIsNone(self.saved()["active_task_id"])


class AdvanceActive(unittest.TestCase):
    def test_picks_the_first_open_task_and_returns_a_later_in_progress_to_pending(self):
        state = make_state([("T1", "pending"), ("T2", "in_progress"), ("T3", "pending")], "T2")
        self.assertEqual(progress.advance_active(state), "T1")
        self.assertEqual([t["status"] for t in state["tasks"]], ["in_progress", "pending", "pending"])
        self.assertEqual(state["active_task_id"], "T1")
        self.assertEqual(progress.validate(state), [])

    def test_skips_completed_tasks(self):
        state = make_state([("T1", "completed"), ("T2", "pending")], None)
        self.assertEqual(progress.advance_active(state), "T2")
        self.assertEqual(state["tasks"][1]["status"], "in_progress")

    def test_returns_none_when_nothing_is_open(self):
        state = make_state([("T1", "completed")], None)
        self.assertIsNone(progress.advance_active(state))
        self.assertIsNone(state["active_task_id"])


class Cli(unittest.TestCase):
    def call_main(self, env):
        with mock.patch.dict(os.environ, env), mock.patch.object(progress, "complete", return_value=0) as comp:
            if progress.ORDER_FREE_ENV not in env:
                os.environ.pop(progress.ORDER_FREE_ENV, None)
            code = progress.main(["complete", "S2-4"])
        return code, comp

    def test_the_environment_variable_name(self):
        self.assertEqual(progress.ORDER_FREE_ENV, "HLINE_PROGRESS_ORDER_FREE")

    def test_without_the_variable_the_active_task_is_required(self):
        code, comp = self.call_main({})
        self.assertEqual(code, 0)
        comp.assert_called_once_with("S2-4")

    def test_with_1_it_is_order_free(self):
        code, comp = self.call_main({progress.ORDER_FREE_ENV: "1"})
        self.assertEqual(code, 0)
        comp.assert_called_once_with("S2-4", require_active=False)

    def test_other_values_do_not_switch_it_on(self):
        _, comp = self.call_main({progress.ORDER_FREE_ENV: "0"})
        comp.assert_called_once_with("S2-4")

    def test_no_option_was_added_to_complete(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            progress.main(["complete", "S2-4", "--order-free"])


class IntegrateRecords(unittest.TestCase):
    def run_integrate(self, task, complete_code=0):
        calls = []

        def fake_run(args, cwd, ttl, label, env=None, input=None):
            calls.append((args, env))
            return (complete_code, "", "") if "complete" in args else (0, "", "")

        with mock.patch.object(hline_git.proc, "run", side_effect=fake_run):
            try:
                hline_git.integrate(CFG, Path("."), "120-x", "題", task)
            except hline_git.Infra:
                return calls, True
        return calls, False

    def test_the_record_is_made_order_free_with_the_same_arguments_and_the_inherited_environment(self):
        calls, failed = self.run_integrate("S2-4")
        self.assertFalse(failed)
        args, env = calls[0]
        self.assertEqual(args[-3:], ["harness.progress", "complete", "S2-4"])
        self.assertEqual(args[1:3], ["-m", "harness.progress"])
        self.assertEqual(len(args), 5)
        self.assertEqual(env[progress.ORDER_FREE_ENV], "1")
        for key, value in os.environ.items():
            if key != progress.ORDER_FREE_ENV:
                self.assertEqual(env.get(key), value)
        self.assertEqual([c[0][1] for c in calls[1:]], ["add", "commit", "push"])

    def test_the_variable_is_not_leaked_into_this_process(self):
        before = os.environ.get(progress.ORDER_FREE_ENV)
        self.run_integrate("S2-4")
        self.assertEqual(os.environ.get(progress.ORDER_FREE_ENV), before)

    def test_a_failing_record_raises_infra_and_stops_before_git(self):
        calls, failed = self.run_integrate("S2-4", complete_code=1)
        self.assertTrue(failed)
        self.assertEqual(len(calls), 1)

    def test_what_without_a_task_does_not_touch_the_progress(self):
        calls, failed = self.run_integrate(None)
        self.assertFalse(failed)
        self.assertEqual([c[0][1] for c in calls], ["add", "commit", "push"])
        self.assertTrue(all(env is None for _, env in calls))


if __name__ == "__main__":
    unittest.main()
