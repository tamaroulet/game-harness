"""タスクを持たない What の `検証:` の行（harness/hline_queue.py の取り込みと harness/gate_order.py の Gate 1）の検査。

    python -m unittest tests.test_hline_what_verification -v

**なぜ要るか**: タスクを持たない What は合格条件を機械で確かめられず、ターンの上限で打ち切られた未完の変更が「済み」になった。
`検証:` を必須にし、Gate 1 が影響テストと一緒に走らせる。実際の git・gh・エージェントの CLI は呼ばない（proc.run・実装役を差し替える）。
受信箱・作業ツリーは一時ディレクトリで、固定のパスやポートは使わない。
"""
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import gate_order  # noqa: E402
import hline  # noqa: E402
import hline_queue  # noqa: E402
import yaml  # noqa: E402

CFG = hline.load_config()
VERIFY = "python -m unittest tests.test_ok"
LEDGER = yaml.safe_dump({"tasks": [{"id": "T1", "status": "pending", "verification": {"command": "python -m unittest tests.test_ok",
                                                                                      "expected_exit_code": 0}}]})
TRACE = "Traceback (most recent call last):\n  File \"x.py\", line 3, in test_a\n    self.assertEqual(1, 2)\nAssertionError: 1 != 2"
SAMPLE = "=" * 70 + "\nFAIL: test_a (tests.test_ok.A)\n" + "-" * 70 + "\n" + TRACE + "\n\n" + "-" * 70 + "\nRan 1 test\n\nFAILED (failures=1)\n"


def what(*lines):
    return "\n".join(["# 題", "マイルストーン: B8.1", *lines, "", "本文"])


class Intake(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = json.loads(json.dumps(CFG))
        self.inbox, self.out = self.tmp / "inbox", self.tmp / "out"
        self.inbox.mkdir()
        self.out.mkdir()
        self.cfg.update(inbox=str(self.inbox), out=str(self.out), milestones=["B8.1"])

    def take(self, name, text):
        (self.inbox / f"{name}.md").write_text(text, encoding="utf-8")
        st = hline_queue.load_state(self.cfg)
        with mock.patch.object(hline_queue.proc, "run", return_value=(0, LEDGER, "")):
            taken = hline_queue.intake(self.cfg, st)
        return st, taken

    def test_the_verification_line_is_read_into_the_header(self):
        meta = hline_queue.parse_header(what(f"検証: {VERIFY}"))
        self.assertEqual(meta["verify"], VERIFY)
        self.assertEqual(hline_queue.parse_header(what(f"- 検証： `{VERIFY}`"))["verify"], VERIFY)
        self.assertIsNone(hline_queue.parse_header(what())["verify"])

    def test_a_what_without_a_task_and_without_a_verification_is_left_in_the_inbox_with_a_reason(self):
        st, taken = self.take("a", what())
        self.assertEqual(taken, [])
        self.assertTrue((self.inbox / "a.md").exists())
        self.assertEqual(st["items"], {})
        self.assertEqual([s["file"] for s in st["skipped"]], ["a.md"])
        self.assertIn("検証:", st["skipped"][0]["reason"])

    def test_a_what_with_both_a_task_and_a_verification_is_left_in_the_inbox_with_a_reason(self):
        st, taken = self.take("a", what("タスク: T1", f"検証: {VERIFY}"))
        self.assertEqual(taken, [])
        self.assertTrue((self.inbox / "a.md").exists())
        self.assertIn("T1", st["skipped"][0]["reason"])
        self.assertIn("検証:", st["skipped"][0]["reason"])

    def test_a_what_with_only_a_verification_or_only_a_task_is_taken(self):
        st, taken = self.take("a", what(f"検証: {VERIFY}"))
        self.assertEqual(taken, ["a"])
        self.assertEqual(st["items"]["a"]["verify"], VERIFY)
        st, taken = self.take("b", what("タスク: T1"))
        self.assertEqual(taken, ["b"])
        self.assertEqual(st["skipped"], [])


class Gate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.wt = self.tmp / "wt"
        (self.wt / "tests").mkdir(parents=True)
        (self.wt / "docs").mkdir()
        (self.wt / "docs" / "progress.yaml").write_text(LEDGER, encoding="utf-8")
        self.results, self.commands, self.feedbacks = [], [], []

    def fake_run(self, args, cwd, ttl, label, env=None, input=None):
        self.commands.append(list(args))
        return self.results.pop(0) if self.results else (0, "", "")

    def fake_implement(self, cfg, wt, what_text, feedback, log):
        self.feedbacks.append(feedback)
        return 0, ["m"], {}

    def run_task(self, text, task=None, tests=("test_ok",), results=()):
        for t in tests:
            (self.wt / "tests" / f"{t}.py").write_text("", encoding="utf-8")
        self.results = list(results)
        cfg = dict(CFG, max_attempts=2)
        patches = [mock.patch.object(gate_order.proc, "run", side_effect=self.fake_run),
                   mock.patch.object(hline, "new_worktree", side_effect=lambda c, t: (self.wt, "b")),
                   mock.patch.object(hline, "implement", side_effect=self.fake_implement),
                   mock.patch.object(hline, "changed_paths", return_value=["docs/x.md"]),
                   mock.patch.object(hline.hline_protect, "violations", return_value=[]),
                   mock.patch.object(hline.size_limits, "scan", return_value=[]),
                   mock.patch.object(hline.size_limits, "head_source", return_value=None),
                   mock.patch.object(hline.hline_suite, "run", return_value=None)]
        with contextlib.ExitStack() as stack, contextlib.redirect_stdout(io.StringIO()):
            for p in patches:
                stack.enter_context(p)
            return hline.run_task(cfg, "t", text, self.tmp, task=task)

    def test_a_passing_verification_lets_the_change_through(self):
        wt, branch, tries = self.run_task(what(f"検証: {VERIFY}"))
        self.assertIsNotNone(wt)
        self.assertEqual(len(tries), 1)
        self.assertEqual(self.commands, [["python", "-m", "unittest", "tests.test_ok"]])

    def test_a_failing_verification_rejects_the_attempt_and_the_output_goes_to_the_next_one(self):
        wt, branch, tries = self.run_task(what(f"検証: {VERIFY}"), results=[(1, "", SAMPLE), (0, "", "")])
        self.assertIsNotNone(wt)
        self.assertEqual([t["gate"] for t in tries], [False, True])
        self.assertIsNone(self.feedbacks[0])
        self.assertIn("FAIL: test_a", str(self.feedbacks[1]))
        self.assertIn("AssertionError", str(self.feedbacks[1]))

    def test_a_verification_that_keeps_failing_gives_up(self):
        wt, branch, tries = self.run_task(what(f"検証: {VERIFY}"), results=[(1, "", SAMPLE)] * 2)
        self.assertIsNone(wt)
        self.assertEqual(len(tries), 2)

    def test_a_missing_test_module_in_the_command_is_a_failure_that_names_it(self):
        wt, branch, tries = self.run_task(what("検証: python -m unittest tests.test_gone"), tests=("test_other",))
        self.assertIsNone(wt)
        self.assertEqual(self.commands, [])
        self.assertIn("tests.test_gone", str(self.feedbacks[1]))

    def test_a_what_queued_before_the_change_is_checked_from_its_copy_of_the_body(self):
        cfg = json.loads(json.dumps(CFG))
        cfg.update(out=str(self.tmp / "out"))
        path = hline_queue.what_path(cfg, "old")
        path.parent.mkdir(parents=True)
        path.write_text(what(f"検証: {VERIFY}"), encoding="utf-8")
        old_item = {"status": "waiting", "title": "題", "milestone": "B8.1", "task": None, "deps": []}   # verify の欄が無い古い記録
        self.assertNotIn("verify", old_item)
        wt, branch, tries = self.run_task(path.read_text(encoding="utf-8"), results=[(1, "", SAMPLE), (0, "", "")])
        self.assertIsNotNone(wt)
        self.assertEqual(len(self.commands), 2)
        self.assertIn("FAIL: test_a", str(self.feedbacks[1]))

    def test_a_what_with_a_task_is_still_judged_by_the_ledger_verification(self):
        with tempfile.TemporaryDirectory() as d:
            (self.wt / "tests" / "test_ok.py").write_text("", encoding="utf-8")
            with mock.patch.object(gate_order.proc, "run", side_effect=self.fake_run):
                ok, msg = gate_order.focused(CFG, self.wt, ["docs/x.md"], "T1", verify="python -m unittest tests.test_ignored")
        self.assertTrue(ok)
        self.assertEqual(self.commands, [["python", "-m", "unittest", "tests.test_ok"]])


if __name__ == "__main__":
    unittest.main()
