"""進捗の記録（harness.progress complete）を Gate 1 の最後の段にする（hline_task.run_task、hline_git.record）。
0 = 積む、1 = 試行の不合格で出力を次の試行へ、2 = 呼び直さず Fatal。"""
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import hline_base  # noqa: E402
import hline_git  # noqa: E402
import hline_task  # noqa: E402

CFG = {"max_attempts": 2, "ttl_seconds": {"git": 5, "gate": 5}, "out": "."}


def host(records, feedbacks):
    """記録は呼ばれるたびに records の先頭から返す。実装役に渡された feedback は feedbacks に積む。"""
    def implement(cfg, wt, what, feedback, log):
        feedbacks.append(feedback)
        return 0, ["m"], None, None, None

    return types.SimpleNamespace(
        new_worktree=lambda cfg, tid: ("wt", "b"),
        implement=implement,
        changed_paths=lambda wt, cfg: ["harness/x.py"],
        gate=lambda cfg, wt, paths, spec, task, *a, **kw: (True, "gate ok"),
        record=mock.Mock(side_effect=lambda cfg, wt, task: records.pop(0)),
        record_refused=hline_git.record_refused,
    )


class RecordInGate(unittest.TestCase):
    def run_task(self, records, task="W-27"):
        feedbacks = []
        h = host(records, feedbacks)
        with tempfile.TemporaryDirectory() as tmp:
            result = hline_task.run_task(h, CFG, "t", "本文", Path(tmp), task=task)
        return h, result, feedbacks

    def test_a_record_that_passes_lets_the_change_through(self):
        h, (wt, branch, tries), _ = self.run_task([(0, "")])
        self.assertEqual((wt, branch), ("wt", "b"))
        self.assertEqual(len(tries), 1)
        h.record.assert_called_once()

    def test_exit_code_1_fails_the_attempt_and_hands_the_output_to_the_next_one(self):
        h, (wt, branch, tries), feedbacks = self.run_task([(1, "VERIFY-OUTPUT"), (0, "")])
        self.assertEqual((wt, branch), ("wt", "b"))
        self.assertEqual(len(tries), 2)
        self.assertFalse(tries[0]["gate"])
        self.assertIn("VERIFY-OUTPUT", feedbacks[1])
        self.assertIn("W-27", feedbacks[1])

    def test_exit_code_1_on_every_attempt_leaves_nothing_to_integrate(self):
        h, (wt, branch, tries), _ = self.run_task([(1, "a"), (1, "b")])
        self.assertIsNone(wt)
        self.assertEqual(len(tries), 2)

    def test_exit_code_2_is_fatal_without_another_attempt(self):
        records, feedbacks = [(2, "x" * 500 + "LEDGER-TAIL")], []
        h = host(records, feedbacks)
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(hline_base.Fatal) as cm:
            hline_task.run_task(h, CFG, "t", "本文", Path(tmp), task="W-27")
        self.assertIn("W-27", str(cm.exception))
        self.assertTrue(str(cm.exception).endswith("LEDGER-TAIL"))
        self.assertEqual(len(feedbacks), 1)

    def test_a_what_without_a_task_does_not_record(self):
        h, (wt, _, _), _ = self.run_task([], task=None)
        self.assertEqual(wt, "wt")
        h.record.assert_not_called()

    def test_a_failing_gate_does_not_reach_the_record(self):
        feedbacks = []
        h = host([], feedbacks)
        h.gate = lambda *a, **kw: (False, "gate ng")
        with tempfile.TemporaryDirectory() as tmp:
            wt, _, tries = hline_task.run_task(h, CFG, "t", "本文", Path(tmp), task="W-27")
        self.assertIsNone(wt)
        h.record.assert_not_called()


class RecordCommand(unittest.TestCase):
    def test_a_failed_record_restores_the_ledger_file(self):
        calls = []

        def fake_run(args, cwd, ttl, label, env=None, input=None):
            calls.append(args)
            return (1, "out", "err") if "complete" in args else (0, "", "")

        with mock.patch.object(hline_git.proc, "run", side_effect=fake_run):
            code, text = hline_git.record(CFG, Path("."), "W-27")
        self.assertEqual((code, text), (1, "outerr"))
        self.assertEqual(calls[-1][:2], ["git", "checkout"])
        self.assertIn("docs/progress.yaml", calls[-1])

    def test_a_passing_record_keeps_the_ledger_change(self):
        with mock.patch.object(hline_git.proc, "run", return_value=(0, "ok", "")) as run:
            code, _ = hline_git.record(CFG, Path("."), "W-27")
        self.assertEqual(code, 0)
        self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
