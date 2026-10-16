"""前の差分から続ける試行（2 回目から・再開・延長）のターン数の上限と、渡す失敗の出力の絞り込みの検査（proc.run を差し替える）。"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "harness")]
import hline_followup as hf  # noqa: E402
import test_hline_turn_cap as cap_tests  # noqa: E402

CFG, WHAT = cap_tests.CFG, cap_tests.WHAT
FIRST = CFG["implementer"]["budget"]["max_turns"]
FOLLOW = CFG["implementer"]["budget"]["resume_max_turns"]
TB = "Traceback (most recent call last):\n  File \"a.py\", line 1, in f\nAssertionError: boom\n"
SEP = "=" * 70


def failing(*names):
    """名前ごとの FAIL 行とトレースバックを、unittest の出力の形に並べる。"""
    return "\n".join(f"{SEP}\nFAIL: {n} (tests.test_x.T)\n{'-' * 70}\n{TB}" for n in names)


class Narrow(unittest.TestCase):
    def test_passing_output_is_dropped_and_names_and_tracebacks_stay(self):
        text = "検証コマンド `x` が終了コード 1（期待 0）\ntest_ok ... ok\n" + failing("test_a") + "\nRan 2 tests\n"
        got = hf.narrow(text, 4000)
        self.assertIn("FAIL: test_a", got)
        self.assertIn("AssertionError: boom", got)
        self.assertIn("検証コマンド", got)
        self.assertNotIn("test_ok", got)

    def test_the_same_traceback_is_kept_once(self):
        got = hf.narrow(failing("test_a", "test_b", "test_c"), 4000)
        self.assertEqual(got.count("AssertionError: boom"), 1)
        for name in ("test_a", "test_b", "test_c"):
            self.assertIn(f"FAIL: {name}", got)

    def test_the_total_stays_within_the_limit(self):
        self.assertLessEqual(len(hf.narrow(failing(*[f"test_{i}" for i in range(50)]), 100)), 100)
        self.assertLessEqual(len(hf.narrow("構文の誤り\n" * 100, 50)), 50)

    def test_output_without_failures_is_kept_up_to_the_limit(self):
        self.assertEqual(hf.narrow("時間切れ", 4000), "時間切れ")
        self.assertEqual(hf.narrow(None, 10), "")


class Caps(cap_tests.Flow):
    def caps(self):
        return [int(r["args"][-1]) for r in self.runs if r["label"] == "実装役"]

    def test_the_first_attempt_uses_max_turns_and_the_second_uses_resume_max_turns(self):
        self.gates = [(False, failing("test_a")), (True, "ok")]
        _, _, tries = self.run_task()
        self.assertEqual(self.caps(), [FIRST, FOLLOW])
        self.assertEqual([t["max_turns"] for t in tries], [FIRST, FOLLOW])

    def test_a_resumed_first_attempt_uses_resume_max_turns_and_a_narrowed_failure(self):
        (self.out / "previous-failure.txt").write_text(failing("test_a", "test_b") + "\ntest_ok ... ok\n", encoding="utf-8")
        _, _, tries = self.run_task()
        self.assertEqual(self.caps(), [FOLLOW])
        self.assertEqual(tries[0]["max_turns"], FOLLOW)
        sent = self.runs[-1]["input"]
        self.assertEqual(sent.count("AssertionError: boom"), 1)
        self.assertNotIn("test_ok", sent)

    def test_the_failure_passed_to_a_second_attempt_is_narrowed_to_the_limit(self):
        limit = self.cfg.get("gate_tail_chars", 4000)
        self.gates = [(False, "test_ok ... ok\n" * 5 + failing("test_a", "test_b") + "x" * (limit * 2)), (True, "ok")]
        self.run_task()
        second = [r["input"] for r in self.runs if r["label"] == "実装役"][1]
        tail = second.split("次の出力を読んで直してください。", 1)[1]
        self.assertNotIn("test_ok", tail)
        self.assertEqual(tail.count("AssertionError: boom"), 1)
        self.assertLessEqual(len(tail.strip()), limit)

    def test_the_first_attempt_of_a_fresh_what_is_unchanged(self):
        self.run_task()
        self.assertNotIn("前回", self.runs[-1]["input"])
        self.assertEqual(self.caps(), [FIRST])


if __name__ == "__main__":
    unittest.main()
