"""規模の制約（harness/size_limits.py、Gate 1）の検査。一時ディレクトリと偽の source_of だけで済ませ、push・PR・モデルは呼ばない。"""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import hline  # noqa: E402
import hline_spec  # noqa: E402
import pipeline  # noqa: E402
import size_limits as sl  # noqa: E402

CFG, L = hline.load_config(), sl.limits()
BOUND = {"allowed_files": ["x.py", "harness/"], "forbidden_files": [], "max_diff_lines": 300}
FEW = {"added": 5, "deleted": 0, "deleted_files": ()}


def bound(added=0, gone=(), **edit):
    counts = {"added": added, "deleted": 5000, "deleted_files": tuple(gone)}
    return hline_spec.boundary_problems({"edit_boundary": dict(BOUND, **edit)}, list(gone), counts)


def branchy(n):
    return "def f(x):\n" + "    if x:\n        x += 1\n" * n + "    return x\n"


class SizeLimits(unittest.TestCase):
    def files(self, files):
        d = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name, text in files.items():
            (d / name).write_text(text, encoding="utf-8")
        return d

    def test_boundary_counts_only_added_lines_and_removes_only_listed_files(self):
        self.assertEqual(bound(added=300), [])
        (why,) = bound(added=301)
        self.assertTrue("301" in why and "300" in why)
        out = "3\t0\ta.py\n0\t40\told.py\n-\t-\tlogo.png\n delete mode 100644 old.py\n"
        with mock.patch.object(hline_spec, "must", return_value=out):
            self.assertEqual(hline_spec.diff_counts(CFG, "."), {"added": 3, "deleted": 40, "deleted_files": ("old.py",)})
        for pattern in ("harness/old.py", "harness/o?d.py", "harness/*.py", "harness/"):
            self.assertEqual(bound(gone=["harness/old.py"], deletable_files=[pattern]), [], pattern)
        for edit in ({}, {"deletable_files": ["harness/other.py"]}):
            self.assertIn("harness/old.py", bound(gone=["harness/old.py"], **edit)[0])
        (why,) = bound(gone=["harness/a.py", "harness/b.py"], deletable_files=["harness/a.py"])
        self.assertTrue("b.py" in why.split("（")[0] and "a.py" not in why.split("（")[0])
        edit = hline_spec.load_schema(CFG)["properties"]["edit_boundary"]
        self.assertEqual(hline_spec.validate(edit, dict(BOUND, deletable_files=["a.py"])), [])
        self.assertTrue(hline_spec.validate(edit, dict(BOUND, deletable_files="a.py")))

    def test_module_size_and_complexity(self):
        def problem(before, after):
            return sl.module_size_problem("m.py", before, after, L)
        self.assertEqual((L["max_added_lines"], L["new_module_max_lines"], L["max_complexity"]), (300, 500, 15))
        self.assertIsNone(problem(None, 500))
        self.assertIn("501", problem(None, 501))
        self.assertIn("2147", problem(2146, 2147))
        self.assertIn("501", problem(400, 501))
        for before, after in ((2146, 2146), (2146, 2000), (400, 500)):
            self.assertIsNone(problem(before, after))
        self.assertLessEqual(len((ROOT / "harness" / "size_limits.py").read_text(encoding="utf-8").splitlines()), 500)
        self.assertEqual(sl.complexities("def f():\n    return 1\n"), {"f": 1})
        nested = "class C:\n  def m(s, x, y):\n    def g(z):\n      return z if x and y else [i for i in z if i]\n    for a in x:\n      pass\n"
        self.assertEqual(sl.complexities(nested), {"C.m": 2, "C.m.g": 4})
        rest = "def f(x):\n while x:\n  with x:\n   assert x\n try:\n  pass\n except E:\n  pass\n match x:\n  case 1: pass\n  case _: pass\n"
        self.assertEqual(sl.complexities(rest), {"f": 7})
        big = branchy(16)
        (why,) = sl.complexity_problems("m.py", "def g():\n    pass\n", "def g():\n    pass\n" + big, 15)
        self.assertTrue(all(word in why for word in ("m.py", "f", "17", "15")))
        self.assertEqual(len(sl.complexity_problems("m.py", big, big.replace("return x", "return 0"), 15)), 1)
        self.assertEqual(sl.complexity_problems("m.py", big, big + "# コメント\n", 15), [])
        self.assertEqual(sl.complexity_problems("m.py", None, branchy(14), 15), [])
        self.assertEqual(sl.complexity_problems("m.py", None, "def f(:\n", 15), [])

    def test_scan(self):
        d = self.files({"a.py": branchy(3), "b.py": "x = 1\n" * 501, "c.py": branchy(16), "n.txt": "x\n" * 900})
        self.assertEqual(sl.scan(d, ["a.py", "n.txt", "gone.py"], L, {"a.py": branchy(2)}.get), [])
        found = sl.scan(d, ["b.py", "c.py"], L, lambda p: None)
        self.assertTrue(len(found) == 2 and "b.py" in found[0] and "c.py" in found[1])

    def test_hline_gate(self):
        """規模の制約は Gate 1 を不合格にせず、警告として記録される（C2）。"""
        def gate(files, spec, counts=FEW):
            warn = []
            with mock.patch.object(hline.size_limits, "head_source", return_value=lambda p: None), \
                    mock.patch.object(hline, "diff_counts", return_value=counts), \
                    mock.patch.object(hline.proc, "run", return_value=(0, "", "")) as run:
                return hline.gate(CFG, self.files(files), list(files), spec, None, warnings=warn), run, warn
        for spec in (None, {"edit_boundary": BOUND}):
            (ok, _), run, warn = gate({"x.py": branchy(2)}, spec)
            self.assertTrue(ok and run.call_args.args[0] == CFG["gate_command"])
            self.assertEqual(warn, [])
        (ok, _), run, warn = gate({"x.py": "x = 1\n" * 501}, None)
        self.assertTrue(ok and "x.py" in warn[0] and "501" in warn[0])
        (ok, _), run, warn = gate({"x.py": branchy(16)}, {"edit_boundary": BOUND}, dict(FEW, added=301))
        self.assertTrue(ok and any("301" in w for w in warn) and any("循環的複雑度" in w for w in warn))
        run.assert_called()

    def test_game_line_budget_and_scan(self):
        def gate(text, max_lines=250):
            unit = {"whitelist": ["x.py", "A.cs"], "max_impl_lines": max_lines}
            c = SimpleNamespace(sandbox=self.files({"x.py": text}), unit=unit, ttl={"git": 5}, metrics={})
            with mock.patch.object(pipeline, "run", return_value=(0, "10\t0\tx.py\n", "")), \
                    mock.patch.object(sl.proc, "run", return_value=(128, "", "")):
                return pipeline.gate_diff_lines(c, verbose=False)
        self.assertIsNone(gate(branchy(3)))
        self.assertIn("循環的複雑度", gate(branchy(16)))
        self.assertIn("差分超過", gate(branchy(16), 5))
