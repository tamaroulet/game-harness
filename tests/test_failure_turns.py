"""失敗の分類 turns（実装役のターン切れ）の検査（W-121）。

    python -m unittest tests.test_failure_turns -v

試行の記録と Gate 1 の出力を手で組み、分類・表の列・週次の報告の metrics を確かめる（ネットワークは使わない）。
"""
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import failure_classes as fc  # noqa: E402
import hline_abort  # noqa: E402
import weekly_report  # noqa: E402

TURN_CUT = "ターン数の上限 15 で打ち切られました"
TIME_CUT = "時間の上限 600 秒で打ち切られました"
TEST_FAIL = ("検証コマンド `python -m unittest tests.test_a` が終了コード 1（期待 0）\n"
             "FAIL: test_x (tests.test_a.A.test_x)\nAssertionError: 1 != 2\n")
PROTECTED = "変えてはならないパスを変えています: docs/progress.yaml。元に戻してください。"


class Classify(unittest.TestCase):
    def test_turn_cutoff_that_fell_on_a_test_is_turns(self):
        self.assertEqual(fc.classify({"cutoff": TURN_CUT}, TEST_FAIL), "turns")
        self.assertNotEqual(fc.classify({"cutoff": TURN_CUT}, TEST_FAIL), "test")
        self.assertEqual(fc.describe({"cutoff": TURN_CUT}, TEST_FAIL)["class"], "turns")
        self.assertEqual(fc.describe({"cutoff": TURN_CUT}, TEST_FAIL)["tests"], ["test_x (tests.test_a.A.test_x)"])

    def test_without_a_cutoff_or_with_a_time_cutoff_the_class_is_unchanged(self):
        self.assertEqual(fc.classify({}, TEST_FAIL), "test")
        self.assertEqual(fc.classify({"cutoff": None}, TEST_FAIL), "test")
        self.assertEqual(fc.classify({"cutoff": TIME_CUT}, TEST_FAIL), "test")
        self.assertEqual(fc.classify({"cutoff": TIME_CUT}, "作業ツリーに変更がありません。"), "cutoff")
        self.assertEqual(fc.classify({}, "作業ツリーに変更がありません。"), "other")

    def test_earlier_classes_still_win_over_a_turn_cutoff(self):
        rec = {"cutoff": TURN_CUT}
        self.assertEqual(fc.classify({**rec, "abort": "断念"}, TEST_FAIL), "abandon")
        self.assertEqual(fc.classify({**rec, "abort": hline_abort.REPEAT + "。繰り返し"}, TEST_FAIL), "repeat")
        self.assertEqual(fc.classify({**rec, "timeout": True}, TEST_FAIL), "timeout")
        self.assertEqual(fc.classify(rec, TEST_FAIL + "TTL超過"), "timeout")
        self.assertEqual(fc.classify(rec, PROTECTED), "protected")

    def test_turns_is_a_column(self):
        self.assertIn("turns", fc.COLUMNS)
        items = {"a": {"status": "unconverged", "at": "2026-10-10", "failure_class": "turns",
                       "tries": [{"gate": False, "failure": {"class": "turns"}}]}}
        out = fc.render(items)
        self.assertIn("turns", out.splitlines()[2])
        self.assertEqual(out.count("| 2026-10-10 |"), 2)


class Weekly(unittest.TestCase):
    def test_metrics_have_failure_turns_for_the_week(self):
        def bad(cls):
            return {"gate": False, "turns": 1, "usage": {"total_tokens": 1, "output_tokens": 1}, "failure": {"class": cls}}
        items = {
            "a": {"status": "unconverged", "at": "2026-10-14", "failure_class": "turns", "tries": [bad("turns"), bad("turns"), bad("test")]},
            "b": {"status": "unconverged", "at": "2026-10-19", "failure_class": "turns", "tries": [bad("turns")]},   # 次の週
        }
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        queue, out = tmp / "queue.json", tmp / "weekly"
        queue.write_text(json.dumps({"items": items}), encoding="utf-8")
        args = ["--queue", str(queue), "--records", str(tmp / "records"), "--out", str(out), "--week", "2026-W42"]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(weekly_report.main(args), 0)
        rep = json.loads((out / "2026-W42.json").read_text(encoding="utf-8"))
        self.assertEqual(rep["metrics"]["failure.turns"], 2)
        self.assertEqual(rep["metrics"]["failure.test"], 1)


if __name__ == "__main__":
    unittest.main()
