"""Gate 1 の順序（harness/gate_order.py）の検査：タスク個別の検証が先、落ちたら全件テストは走らせず、出力は落ちたテストの名前を残す。

    python -m unittest tests.test_gate_order -v

**なぜ要るか**: 全件テストの出力の末尾だけを実装役に返すと、落ちたテストの名前が長い出力に押し流されて直せない。
実際の git・gh・エージェントの CLI は呼ばない（proc.run を差し替える）。失敗の出力は文字列の見本で、実際に失敗するテストは走らせない。
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import gate_order  # noqa: E402
import hline  # noqa: E402

CFG = hline.load_config()
LIMIT = CFG["gate_tail_chars"]
VERIFY = "python -m unittest tests.test_x"
TRACE = ("Traceback (most recent call last):\n  File \"x.py\", line 3, in test_a\n    self.assertEqual(1, 2)\n"
         "AssertionError: 1 != 2")
SAMPLE = ("=" * 70 + "\nFAIL: test_a (tests.test_x.A)\n" + "-" * 70 + "\n" + TRACE + "\n\n" + "-" * 70
          + "\nRan 2 tests in 0.001s\n\nFAILED (failures=1)\n")


def worktree(d, command=VERIFY):
    (Path(d) / "docs").mkdir()
    state = {"tasks": [{"id": "S1-2", "verification": {"command": command, "expected_exit_code": 0}}, {"id": "S1-3"}]}
    (Path(d) / "docs" / "progress.yaml").write_text(yaml.safe_dump(state), encoding="utf-8")
    return Path(d)


class Run:
    """proc.run の差し替え。ラベルが "Gate 1" ならカナリア（影響テストが無いときだけ走る束）、それ以外は個別の検証の結果を返す。"""

    def __init__(self, focused=(0, "", ""), full=(0, "", "")):
        self.focused, self.full, self.commands = focused, full, []

    def __call__(self, args, cwd, ttl, label, env=None, input=None):
        self.commands.append(args)
        return self.full if label == "Gate 1" else self.focused


class TestModules(unittest.TestCase):
    def test_only_test_files_become_dotted_module_names_without_duplicates(self):
        paths = ["harness/x.py", "tests/test_b.py", "tests\\test_a.py", "tests/test_b.py", "tests/sub/test_c.py",
                 "tests/helper.py", "docs/tests/test_d.py"]
        self.assertEqual(gate_order.test_modules(paths), ("tests.test_b", "tests.test_a"))
        self.assertEqual(gate_order.test_modules(["harness/x.py"]), ())


class Digest(unittest.TestCase):
    def test_the_failed_test_names_and_tracebacks_are_taken_out_in_order(self):
        self.assertEqual(gate_order.failure_digest(SAMPLE), "FAIL: test_a (tests.test_x.A)\n" + TRACE)
        two = SAMPLE + "=" * 70 + "\nERROR: test_b (tests.test_y.B)\n" + "-" * 70 + "\n" + TRACE + "\n"
        self.assertEqual(gate_order.failure_digest(two).splitlines()[0::5],
                         ["FAIL: test_a (tests.test_x.A)", "ERROR: test_b (tests.test_y.B)"])
        self.assertEqual(gate_order.failure_digest(two).count("Traceback (most recent call last):"), 2)

    def test_output_without_failures_has_an_empty_digest(self):
        self.assertEqual(gate_order.failure_digest("Ran 2 tests\n\nOK\n"), "")


class Report(unittest.TestCase):
    def test_heading_then_the_digest_and_no_raw_log(self):
        """返すのは見出しと digest だけ（生ログは連結しない。C4）。"""
        text = gate_order.report("python -m unittest x", 1, "OUT\n", SAMPLE, 10000, 0)
        heading = "検証コマンド `python -m unittest x` が終了コード 1（期待 0）\n"
        self.assertEqual(text, heading + gate_order.failure_digest(SAMPLE))
        self.assertNotIn("OUT", text)
        self.assertNotIn("Ran 2 tests", text)

    def test_without_a_digest_the_raw_tail_takes_its_place_within_thirty_lines(self):
        text = gate_order.report("c", 0, "\n".join(f"line{n}" for n in range(100)), "", 10000)
        lines = text.splitlines()
        self.assertEqual(len(lines), gate_order.MAX_LINES)
        self.assertEqual(lines[-1], "line99")
        self.assertNotIn("line70", text)

    def test_the_length_stays_within_the_limit_and_the_head_survives_a_long_raw_output(self):
        text = gate_order.report("c", 1, "." * 50000, SAMPLE, 800)
        self.assertLessEqual(len(text), 800)
        self.assertIn("FAIL: test_a", text)
        self.assertIn(TRACE, text)
        self.assertNotIn("." * 100, text)

    def test_a_head_longer_than_the_limit_is_cut_from_its_start_keeping_the_test_names(self):
        text = gate_order.report("c", 1, "", SAMPLE, 120)
        self.assertEqual(len(text), 120)
        self.assertIn("FAIL: test_a", text)
        self.assertEqual(text, gate_order.report("c", 1, "", SAMPLE, 1000)[:120])


class Focused(unittest.TestCase):
    def focused(self, paths, task, run):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(gate_order.proc, "run", side_effect=run):
            return gate_order.focused(CFG, worktree(d), paths, task)

    def test_a_task_runs_the_progress_verification_and_the_verdict_is_its_expected_exit_code(self):
        for code, want in ((0, True), (1, False)):
            run = Run(focused=(code, "o", ""))
            ok, msg = self.focused(["harness/x.py"], "S1-2", run)
            self.assertEqual(ok, want)
            self.assertEqual(run.commands, [["python", "-m", "unittest", "tests.test_x"]])
            self.assertIn(f"`{VERIFY}` が終了コード {code}（期待 0）", msg)

    def test_a_task_without_a_verification_is_a_warning_and_the_impacted_tests_decide(self):
        """検証コマンドが無くても不合格にせず、警告を記録する（C6）。走らせるものが無ければ None。"""
        run, warn = Run(), []
        with tempfile.TemporaryDirectory() as d, mock.patch.object(gate_order.proc, "run", side_effect=run):
            self.assertIsNone(gate_order.focused(CFG, worktree(d), ["harness/x.py"], "S1-3", None, warn))
        self.assertEqual(run.commands, [])
        self.assertIn("S1-3", "\n".join(warn))

    def test_without_a_task_the_changed_test_modules_run_and_nothing_to_run_returns_none(self):
        run = Run(focused=(1, "", SAMPLE))
        ok, msg = self.focused(["harness/x.py", "tests/test_a.py"], None, run)
        self.assertFalse(ok)
        self.assertEqual(run.commands, [hline.base_whitelist.unittest_command(CFG["gate_command"], ["tests.test_a"])])
        self.assertIn("FAIL: test_a", msg)
        run = Run()
        self.assertIsNone(self.focused(["harness/x.py"], None, run))
        self.assertEqual(run.commands, [])


class GateOrder(unittest.TestCase):
    def gate(self, run, task=None, paths=("harness/x.py", "tests/test_a.py")):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(hline.proc, "run", side_effect=run):
            return hline.gate(CFG, worktree(d), list(paths), None, task)

    def test_a_failing_focused_verification_returns_its_output_without_running_the_full_suite(self):
        for task in (None, "S1-2"):
            run = Run(focused=(1, "FOCUSED-OUT", SAMPLE))
            ok, msg = self.gate(run, task)
            self.assertFalse(ok)
            self.assertNotIn(CFG["gate_command"], run.commands)
            self.assertEqual(len(run.commands), 1)
            self.assertIn("FAIL: test_a (tests.test_x.A)", msg)
            self.assertNotIn("FOCUSED-OUT", msg, "生ログは連結しない（C4）")

    def test_the_focused_verification_decides_and_no_full_suite_follows_it(self):
        """影響テストに走らせるものがあれば、それだけで判定する（内側のループで全件テストは走らせない。C3）。"""
        for code, want in ((0, True), (1, False)):
            run = Run(focused=(code, "", SAMPLE))
            ok, msg = self.gate(run, "S1-2")
            self.assertEqual(ok, want)
            self.assertEqual(len(run.commands), 1)
            self.assertNotIn(CFG["gate_command"], run.commands)

    def test_only_when_both_pass_is_the_gate_true(self):
        for task in (None, "S1-2"):
            run = Run()
            self.assertTrue(self.gate(run, task)[0])
            self.assertEqual(len(run.commands), 1)

    def test_with_nothing_focused_to_run_the_canary_modules_decide(self):
        for code in (0, 1):
            run = Run(full=(code, "", ""))
            self.assertEqual(self.gate(run, None, ["harness/x.py"])[0], code == 0)
            self.assertEqual(run.commands, [hline.base_whitelist.unittest_command(CFG["gate_command"], CFG["canary_modules"])])

    def test_a_long_raw_output_does_not_push_out_the_failed_names_and_the_length_is_bounded(self):
        long_fail = (1, SAMPLE + "." * (LIMIT * 3), "")
        for run, paths in ((Run(focused=long_fail), ("harness/x.py", "tests/test_a.py")),   # 影響テスト
                           (Run(full=long_fail), ("harness/x.py",))):                        # カナリア
            ok, msg = self.gate(run, None, paths)
            self.assertFalse(ok)
            self.assertLessEqual(len(msg), LIMIT)
            self.assertIn("FAIL: test_a (tests.test_x.A)", msg)
            self.assertIn("Traceback (most recent call last):", msg)


class Prompt(unittest.TestCase):
    def test_the_instructions_name_the_focused_verification_and_never_the_full_suite_command(self):
        """実装役にはテストを走らせないことだけを伝える（C4）。全件テストの命令は入力に現れない。"""
        prompt = hline.build_prompt("# 題")
        instructions = prompt.split("---\n", 1)[0]
        self.assertNotIn("discover", instructions)
        self.assertIn("テストは走らせない", instructions)
        self.assertIn("ハーネスが試行の後に影響テストを走らせ", instructions)

    def test_hline_stays_within_300_lines_and_the_old_verification_is_gone(self):
        text = (Path(hline.__file__)).read_text(encoding="utf-8")
        self.assertLessEqual(len(text.splitlines()), 300)
        self.assertFalse(hasattr(hline, "task_verification"))


if __name__ == "__main__":
    unittest.main()
