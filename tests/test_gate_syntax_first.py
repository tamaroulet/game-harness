"""Gate 1 の入口の構文の検査（試験より先）と、実装役の時間切れの扱い（呼び直さずに未収束）の検査。"""
import sys
import tempfile
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "harness")]
import failure_classes  # noqa: E402
import hline_abort  # noqa: E402
import hline_gate  # noqa: E402

NS = types.SimpleNamespace
CFG = {"gate_command": ["python", "-m", "x"], "ttl_seconds": {"gate": 1}, "gate_tail_chars": 100, "canary_modules": []}


def host(ran):
    return NS(hline_protect=NS(violations=lambda p: [], reason=str), boundary_problems=lambda s, p, c: [], diff_counts=lambda c, w: {},
              size_limits=NS(scan=lambda *a: [], limits=lambda: {}, head_source=lambda c, w: lambda p: None),
              gate_order=NS(focused=lambda *a: ran.append(a) or (True, "ran"), report=lambda *a: "ran"),
              proc=NS(run=lambda *a, **kw: ran.append(a) or (0, "", "")))


class SyntaxFirst(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "harness").mkdir()

    def put(self, name, text):
        (self.root / "harness" / name).write_text(text, encoding="utf-8")

    def run_gate(self, paths):
        ran = []
        return hline_gate.gate(host(ran), CFG, str(self.root), paths), ran

    def test_a_syntax_error_stops_the_gate_before_any_test_runs(self):
        self.put("good.py", "x = 1\n")
        self.put("bad.py", "x = 1\ny = (\nz = 2\n")
        (ok, text), ran = self.run_gate(["harness/good.py", "harness/bad.py"])
        self.assertFalse(ok)
        self.assertEqual(ran, [])
        self.assertIn("harness/bad.py:", text)
        self.assertNotIn("good.py", text)

    def test_the_text_names_the_file_the_line_and_the_message(self):
        self.put("bad.py", "a = 1\nb = 2\ndef f(:\n    pass\n")
        (_, text), _ = self.run_gate(["harness/bad.py"])
        self.assertRegex(text, r"harness/bad\.py:3: \S")

    def test_an_indentation_error_is_caught(self):
        self.put("bad.py", "def f():\nreturn 1\n")
        (ok, text), ran = self.run_gate(["harness/bad.py"])
        self.assertEqual((ok, ran), (False, []))
        self.assertIn("harness/bad.py:2:", text)

    def test_the_failure_class_is_test(self):
        self.put("bad.py", "def f(:\n")
        (_, text), _ = self.run_gate(["harness/bad.py"])
        self.assertEqual(failure_classes.describe({}, text)["class"], "test")

    def test_without_an_error_the_tests_run_as_before(self):
        self.put("good.py", "x = 1\n")
        (ok, text), ran = self.run_gate(["harness/good.py"])
        self.assertEqual((ok, text, len(ran)), (True, "ran", 1))

    def test_a_deleted_or_non_python_file_is_not_checked(self):
        self.put("notes.txt", "def f(:\n")
        (ok, _), ran = self.run_gate(["harness/gone.py", "harness/notes.txt"])
        self.assertEqual((ok, len(ran)), (True, 1))


class TimeoutClass(unittest.TestCase):
    def test_a_timed_out_try_is_classed_timeout_and_gives_the_reason(self):
        rec = {"timeout": "実装役が時間切れになりました（終了コード 124、経過 30 秒）"}
        self.assertEqual(failure_classes.describe(rec, None)["class"], "timeout")
        self.assertIn("30 秒", hline_abort.unconverged_reason([rec]))


if __name__ == "__main__":
    unittest.main()
