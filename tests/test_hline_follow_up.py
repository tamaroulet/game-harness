"""前の差分から続ける試行（2 回目から・再開・延長）のターン数の上限と、渡す失敗の出力の絞り込みの検査（proc.run を差し替える）。"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "harness")]
import box_tree  # noqa: E402
import gate_order  # noqa: E402
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


def distinct(*pairs):
    """(名前, 例外の行) ごとに、別々のトレースバックを持つ落ちを、unittest の生の出力の形に並べる（集計の行まで）。"""
    blocks = [f"{SEP}\nFAIL: {n} (tests.test_x.T)\n{'-' * 70}\nTraceback (most recent call last):\n"
              f"  File \"a.py\", line {i}, in {n}\n    check()\n{exc}\n" for i, (n, exc) in enumerate(pairs, 1)]
    return "\n".join(blocks) + f"\n{'-' * 70}\nRan {len(pairs) + 1} tests in 0.1s\n\nFAILED (failures={len(pairs)})\n"


def gate_report(raw):
    """Gate 1 が実装役に返す形（gate_order.report。区切り線を除いて塊をつないだ digest）。"""
    return gate_order.report("python -m unittest tests.test_x", 1, "", raw, 4000)


PAIRS = (("test_a", "AssertionError: 2 not less than or equal to 1"),
         ("test_b", "AssertionError: 3 not greater than or equal to 5"),
         ("test_c", "ValueError: bad input"))


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

    def test_every_failure_of_a_gate_report_stays(self):
        """Gate 1 の出力には区切り線が無い。それでも 2 件目からの落ちを捨てない（11-05 まで、最初の 1 件だけが残っていた）。"""
        report = gate_report(distinct(*PAIRS))
        self.assertNotIn(SEP, report)   # 前提：Gate 1 の出力は区切り線を除いてある
        got = hf.narrow(report, 4000)
        for name, exc in PAIRS:
            self.assertIn(f"FAIL: {name}", got)
            self.assertIn(exc, got)
        self.assertEqual(got.count("Traceback (most recent call last):"), 3)
        self.assertIn("検証コマンド", got)
        self.assertNotIn("Ran 4 tests", got)

    def test_each_traceback_of_a_gate_report_ends_at_its_own_exception_line(self):
        got = hf.narrow(gate_report(distinct(*PAIRS[:2])), 4000).splitlines()
        first = got.index(PAIRS[0][1])
        self.assertEqual(got[first + 1], f"FAIL: {PAIRS[1][0]} (tests.test_x.T)")

    def test_the_same_traceback_in_a_gate_report_is_kept_once(self):
        got = hf.narrow(gate_report(failing("test_a", "test_b", "test_c")), 4000)
        self.assertEqual(got.count("AssertionError: boom"), 1)
        for name in ("test_a", "test_b", "test_c"):
            self.assertIn(f"FAIL: {name}", got)

    def test_raw_and_gate_report_forms_keep_the_same_failures(self):
        """見出しの 1 行（生の出力では最初の区切り線、Gate 1 の出力では検証コマンドの行）の後は、同じ落ちが同じ順に並ぶ。"""
        raw = distinct(*PAIRS)
        self.assertEqual(hf.narrow(raw, 4000).splitlines()[1:], hf.narrow(gate_report(raw), 4000).splitlines()[1:])

    def test_the_note_of_files_not_received_stays_after_narrowing(self):
        note = box_tree.dropped_note([["scratch.py", "外"]])
        got = hf.narrow(f"{gate_report(distinct(*PAIRS))}\n\n{note}", 4000)
        self.assertIn("FAIL: test_a", got)
        self.assertTrue(got.endswith(note))

    def test_the_note_is_not_cut_by_the_limit(self):
        note = box_tree.dropped_note([["scratch.py", "外"]])
        got = hf.narrow("構文の誤り\n" * 100 + f"\n\n{note}", 50)
        self.assertLessEqual(len(got), 50 + len(note) + 2)
        self.assertTrue(got.endswith(note))


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

    def test_every_failure_of_the_gate_reaches_the_second_attempt(self):
        """Gate 1 の出力（区切り線の無い digest）の 2 件目からの落ちも、2 回目の試行の入力に入る。"""
        self.gates = [(False, gate_report(distinct(*PAIRS))), (True, "ok")]
        self.run_task()
        second = [r["input"] for r in self.runs if r["label"] == "実装役"][1]
        tail = second.split("次の出力を読んで直してください。", 1)[1]
        for name, exc in PAIRS:
            self.assertIn(f"FAIL: {name}", tail)
            self.assertIn(exc, tail)

    def test_the_first_attempt_of_a_fresh_what_is_unchanged(self):
        self.run_task()
        self.assertNotIn("前回", self.runs[-1]["input"])
        self.assertEqual(self.caps(), [FIRST])


if __name__ == "__main__":
    unittest.main()
