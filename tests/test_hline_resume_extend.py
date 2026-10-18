"""H ライン：未収束の What の再開の材料を report.md に出し、試験が最後まで走って通った数が増えていれば 1 回だけ自動で続ける（W-66）。

    python -m unittest tests.test_hline_resume_extend -v

**なぜ要るか**: 前進の無い試行（構文の誤り・途中の落ち）に続けても同じ失敗を繰り返すだけ。通った数が増えている What だけを、
残した差分を載せて 1 回だけ続ける。git は一時ディレクトリのローカルのリポジトリだけを使い、実装役と Gate 1 は差し替える。
"""
import contextlib
import io
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "harness")]
import hline_extend  # noqa: E402
import hline_report  # noqa: E402
import hline_resume  # noqa: E402
import hline_task  # noqa: E402
import test_hline_resume as base  # noqa: E402

NAME = base.NAME
HEAD = "検証コマンド `python -m unittest x` が終了コード 1\n"


def out(ran, failures=0, errors=0, skipped=0):
    """unittest の出力（検証コマンドの Gate 1 の失敗の文面）。"""
    parts = [f"{k}={n}" for k, n in (("failures", failures), ("errors", errors), ("skipped", skipped)) if n]
    tail = f"FAILED ({', '.join(parts)})" if failures or errors else f"OK ({', '.join(parts)})"
    return f"{HEAD}\nRan {ran} tests in 0.1s\n\n{tail}\n"


class Counts(unittest.TestCase):
    def test_a_run_to_the_end_counts_passed_and_failed(self):
        got = hline_extend.test_counts({}, out(10, failures=2, errors=1, skipped=1))
        self.assertEqual(got, {"tests_complete": True, "tests_passed": 6, "tests_failed": 3})

    def test_a_single_test_is_read(self):
        text = f"{HEAD}\nRan 1 test in 0.0s\n\nFAILED (failures=1)\n"
        self.assertEqual(hline_extend.test_counts({}, text), {"tests_complete": True, "tests_passed": 0, "tests_failed": 1})

    def test_runs_that_did_not_reach_the_end_have_no_numbers(self):
        none = {"tests_complete": False, "tests_passed": None, "tests_failed": None}
        cases = [
            ({}, "構文の誤りがあります（試験は走らせていません）:\nx.py\n"),
            ({}, f"{HEAD}\nRan 3 tests in 1s\n\nFAILED (failures=1)\n時間切れ"),
            ({}, f"{HEAD}ERROR: x (unittest.loader._FailedTest.x)\nModuleNotFoundError\n"),
            ({}, f"{HEAD}\nRan 0 tests in 0.0s\n\nFAILED (errors=1)\nFailed to import test module: x\n"),
            ({}, f"{HEAD}\nSyntaxError: invalid syntax\n"),
            ({}, f"{HEAD}出力に集計の行が無い"),
            ({}, None),
            ({"timeout": "時間切れ"}, out(5, failures=1)),
            ({"abort": "同じ差分"}, out(5, failures=1)),
        ]
        for rec, text in cases:
            with self.subTest(text=text):
                self.assertEqual(hline_extend.test_counts(rec, text), none)


class Decide(unittest.TestCase):
    def setUp(self):
        self.kept = True
        self._material = hline_resume.material
        hline_resume.material = lambda cfg, name: {"kept": self.kept, "lines": 3}
        self.addCleanup(setattr, hline_resume, "material", self._material)

    def item(self, prev=(4, 3), last=(6, 1), cls="test", **extra):
        def rec(p, cls):
            if p is None:
                return {"tests_complete": False, "tests_passed": None, "tests_failed": None, "failure": {"class": cls}}
            return {"tests_complete": True, "tests_passed": p[0], "tests_failed": p[1], "failure": {"class": cls}}
        return {"tries": [rec(prev, "test"), rec(last, cls)], **extra}

    def test_progress_extends(self):
        self.assertEqual(hline_extend.decide({}, NAME, self.item()), {"passed": 6, "failed": 1, "previous_passed": 4})
        self.assertIsNotNone(hline_extend.decide({}, NAME, self.item(cls="full_suite")))

    def test_no_extension_without_progress(self):
        cases = {
            "構文の誤りの試行": self.item(last=None),
            "前の試行が最後まで走っていない": self.item(prev=None),
            "通った数が増えない": self.item(last=(4, 3)),
            "通った数が減った": self.item(last=(3, 4)),
            "落ちた数が 0": self.item(last=(6, 0)),
            "分類が cutoff": self.item(cls="cutoff"),
            "分類が timeout": self.item(cls="timeout"),
            "2 回目の延長": self.item(extended={"passed": 5, "failed": 2, "previous_passed": 3}),
            "試行が 1 回": {"tries": self.item()["tries"][-1:]},
        }
        for label, item in cases.items():
            with self.subTest(label):
                self.assertIsNone(hline_extend.decide({}, NAME, item))

    def test_no_extension_without_kept_material(self):
        self.kept = False
        self.assertIsNone(hline_extend.decide({}, NAME, self.item()))


class Report(unittest.TestCase):
    def test_material_note(self):
        self.assertEqual(hline_report.material_note({"material": {"kept": True, "lines": 12}}), "（再開の材料: あり・差分 12 行）")
        self.assertEqual(hline_report.material_note({"material": {"kept": False, "lines": 0}}), "（再開の材料: なし）")
        self.assertIn("記録なし", hline_report.material_note({}))

    def test_extension_note(self):
        item = {"extended": {"passed": 6, "failed": 1, "previous_passed": 4}}
        self.assertEqual(hline_extend.note(item), "（自動で 1 回延長: 通った 6・落ちた 1）")
        self.assertEqual(hline_extend.note({}), "")


class Flow(unittest.TestCase):
    """process を通す。Gate 1 の出力は試行ごとの列で差し替える。"""

    def setUp(self):
        for name in ("new_worktree", "implement", "put_what", "state", "process"):
            setattr(self, name, getattr(base.Resume, name).__get__(self))
        base.Resume.setUp(self)
        self.cfg["max_attempts"] = 2
        self.outputs, self.n = [], 0
        self.edit = self.write_new

    def write_new(self, wt):
        self.n += 1
        (Path(wt) / "b.txt").write_text(f"v{self.n}\n", encoding="utf-8")

    def gate(self, cfg, wt, paths, spec=None, task=None, extended=(), warnings=None, verify=None, milestone=None):
        return False, self.outputs[min(len(self.calls) - 1, len(self.outputs) - 1)]

    def go(self, *outputs):
        self.outputs = list(outputs)
        self.put_what()
        return self.process(self.state(False))

    def test_a_forward_run_is_extended_once_with_the_kept_changes(self):
        ok, item = self.go(out(4, failures=1), out(6, failures=1))
        self.assertEqual([(t["tests_complete"], t["tests_passed"], t["tests_failed"]) for t in item["tries"]],
                         [(True, 3, 1), (True, 5, 1)])
        self.assertTrue(ok)
        self.assertEqual((item["status"], item["replaced"]), ("waiting", True))
        self.assertEqual(item["extended"], {"passed": 5, "failed": 1, "previous_passed": 3})
        self.assertEqual(item["tries"][-1]["extended"], item["extended"])
        self.assertTrue(hline_resume.material(self.cfg, NAME)["kept"])
        self.assertIn("自動で 1 回延長", hline_extend.note(item))
        ok, item = self.process({"items": {NAME: item}})   # 続き：残した差分が載る
        self.assertTrue(self.calls[-1]["had_b"])
        self.assertEqual(item["tries"][0]["resume"]["applied"], True)
        self.assertEqual(item["tries"][0]["extended"], item["extended"])
        self.assertEqual(item["status"], "unconverged")   # 2 回目は延長しない
        self.assertFalse(ok)

    def test_a_syntax_error_is_not_extended(self):
        ok, item = self.go(out(4, failures=1), "構文の誤りがあります（試験は走らせていません）:\nb.txt\n")
        self.assertEqual((ok, item["status"]), (False, "unconverged"))
        self.assertEqual(item["tries"][-1]["tests_complete"], False)
        self.assertNotIn("extended", item)
        self.assertEqual(item["material"]["kept"], True)
        self.assertNotIn("パッチを捨てた", item["reason"])
        self.assertIn("差分を残した", item["reason"])

    def test_no_increase_is_not_extended(self):
        ok, item = self.go(out(6, failures=1), out(6, failures=1))
        self.assertEqual((ok, item["status"]), (False, "unconverged"))
        self.assertNotIn("extended", item)
        line = hline_report.material_note(item)
        self.assertIn("あり・差分", line)


if __name__ == "__main__":
    unittest.main()
