"""床の検査（harness/bench_score.py の floor）：参照実装を置いた木と空の木を採点し、floor.json を書く。
置き場・木・記録の置き場は一時ディレクトリに作った偽物。本物の config/hline.json の oracles と bench/records/ には触れない。
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import bench_score  # noqa: E402

HIDDEN_A = '''import unittest
import toypkg


class A(unittest.TestCase):
    def test_add(self):
        self.assertEqual(toypkg.add(1, 2), 3)
'''
HIDDEN_B = '''import unittest
import toypkg


class B(unittest.TestCase):
    def test_double(self):
        self.assertEqual(toypkg.double(4), 8)
'''
HIDDEN_FREE = '''import unittest


class Free(unittest.TestCase):
    def test_always(self):
        pass
'''
REFERENCE = "def add(a, b):\n    return a + b\n\n\ndef double(x):\n    return x * 2\n"


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def call(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = bench_score.main(argv)
    return code, out.getvalue(), err.getvalue()


class Floor(unittest.TestCase):
    TASK = "toy"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="bench-floor-test-")
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.oracles, self.tree, self.records = base / "oracles", base / "tree", base / "records"
        self.hidden = self.oracles / "bench" / self.TASK / "hidden"
        write(self.hidden / "test_hidden_a.py", HIDDEN_A)
        write(self.hidden / "test_hidden_b.py", HIDDEN_B)
        write(self.oracles / "bench" / self.TASK / "reference" / "toypkg" / "__init__.py", REFERENCE)
        write(self.tree / "README.md", "木\n")
        self.pin()

    def pin(self):
        write(self.tree / "docs" / "bench" / self.TASK / "oracle.sha256", bench_score.fingerprint(self.hidden) + "\n")

    def floor_path(self, task=None):
        return self.records / "bench" / "records" / (task or self.TASK) / "floor.json"

    def run_floor(self, *tasks):
        return call(["floor", "--task", *(tasks or (self.TASK,)), "--tree", str(self.tree),
                     "--oracles", str(self.oracles), "--records", str(self.records)])

    def test_reference_passes_and_empty_fails_writes_true_and_exits_zero(self):
        code, _, _ = self.run_floor()
        self.assertEqual(code, 0)
        rec = json.loads(self.floor_path().read_text(encoding="utf-8"))
        self.assertEqual(rec, {"task": self.TASK, "fingerprint": bench_score.fingerprint(self.hidden),
                               "reference_rate": 1.0, "empty_rate": 0.0, "passed": True})
        self.assertIs(rec["passed"], True)

    def test_a_hidden_test_that_passes_on_empty_writes_false_and_exits_one(self):
        write(self.hidden / "test_hidden_free.py", HIDDEN_FREE)
        self.pin()
        code, _, _ = self.run_floor()
        self.assertEqual(code, 1)
        rec = json.loads(self.floor_path().read_text(encoding="utf-8"))
        self.assertEqual((rec["reference_rate"], rec["empty_rate"], rec["passed"]), (1.0, 1 / 3, False))

    def test_fingerprint_mismatch_writes_nothing_and_exits_two(self):
        write(self.hidden / "test_hidden_b.py", HIDDEN_B + "\n# 指紋が合わなくなる\n")
        code, _, err = self.run_floor()
        self.assertEqual(code, 2)
        self.assertFalse(self.floor_path().exists())
        self.assertIn("指紋", err)

    def test_missing_reference_exits_two_and_writes_nothing(self):
        (self.oracles / "bench" / self.TASK / "reference" / "toypkg" / "__init__.py").unlink()
        (self.oracles / "bench" / self.TASK / "reference" / "toypkg").rmdir()
        code, _, _ = self.run_floor()
        self.assertEqual(code, 2)
        self.assertFalse(self.floor_path().exists())

    def test_one_bad_task_does_not_stop_the_others(self):
        code, _, _ = self.run_floor("absent", self.TASK)
        self.assertEqual(code, 2)
        self.assertFalse(self.floor_path("absent").exists())
        self.assertTrue(self.floor_path().exists())

    def test_the_tree_is_left_untouched(self):
        write(self.tree / "toypkg" / "__init__.py", "# 木にもともとある実装\n")
        self.pin()
        before = {p.relative_to(self.tree).as_posix(): p.read_bytes() for p in self.tree.rglob("*") if p.is_file()}
        self.run_floor()
        after = {p.relative_to(self.tree).as_posix(): p.read_bytes() for p in self.tree.rglob("*") if p.is_file()}
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
