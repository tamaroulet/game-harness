"""採点役（harness/bench_score.py）：非公開オラクルの指紋を確かめ、木を変えずに隠しテストだけの通過率を出す。
置き場・木は一時ディレクトリに作った偽物。本物の config/hline.json の oracles には触れない。
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

    def test_add_negative(self):
        self.assertEqual(toypkg.add(-1, -2), -3)

    def test_wrong_on_purpose(self):
        self.assertEqual(toypkg.add(1, 1), 3)
'''
HIDDEN_B = '''import unittest
import toypkg


class B(unittest.TestCase):
    def test_double(self):
        self.assertEqual(toypkg.double(4), 8)
'''
STALE = '''import unittest


class Stale(unittest.TestCase):
    def test_stale_a(self):
        pass

    def test_stale_b(self):
        pass
'''
SLOW = '''import time
import unittest


class Slow(unittest.TestCase):
    def test_sleeps(self):
        time.sleep(60)
'''


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in Path(root).rglob("*") if p.is_file()}


def call(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = bench_score.main(argv)
    return code, out.getvalue(), err.getvalue()


class Fixture(unittest.TestCase):
    TASK = "toy"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="bench-score-test-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.oracles, self.tree, self.out = self.base / "oracles", self.base / "tree", self.base / "score.json"
        self.hidden = self.oracles / "bench" / self.TASK / "hidden"
        write(self.hidden / "test_hidden_a.py", HIDDEN_A)
        write(self.hidden / "test_hidden_b.py", HIDDEN_B)
        write(self.hidden / "data" / "note.txt", "付属のファイル\n")
        write(self.tree / "toypkg" / "__init__.py", "def add(a, b):\n    return a + b\n\n\ndef double(x):\n    return x * 2\n")
        write(self.tree / "tests" / "test_visible.py", "import unittest\n\n\nclass V(unittest.TestCase):\n    def test_v(self):\n        pass\n")
        write(self.tree / "tests" / "test_hidden_stale.py", STALE)
        self.pin()

    def pin(self):
        write(self.tree / "docs" / "bench" / self.TASK / "oracle.sha256", bench_score.fingerprint(self.hidden) + "\n")

    def score(self, *extra):
        return call(["score", "--task", self.TASK, "--tree", str(self.tree), "--out", str(self.out),
                     "--oracles", str(self.oracles), *extra])


class Hash(Fixture):
    def test_prints_one_line_and_exits_zero(self):
        code, out, _ = call(["hash", "--task", self.TASK, "--oracles", str(self.oracles)])
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines(), [bench_score.fingerprint(self.hidden)])
        self.assertRegex(out.strip(), r"^[0-9a-f]{64}$")

    def test_changes_when_one_file_changes(self):
        before = bench_score.fingerprint(self.hidden)
        write(self.hidden / "data" / "note.txt", "付属のファイル。変えた\n")
        self.assertNotEqual(bench_score.fingerprint(self.hidden), before)

    def test_changes_when_a_file_is_renamed_or_added(self):
        before = bench_score.fingerprint(self.hidden)
        write(self.hidden / "extra.txt", "")
        self.assertNotEqual(bench_score.fingerprint(self.hidden), before)

    def test_without_the_directory_exits_two(self):
        code, out, _ = call(["hash", "--task", "absent", "--oracles", str(self.oracles)])
        self.assertEqual((code, out), (2, ""))


class Score(Fixture):
    def test_mismatch_exits_two_and_writes_no_record(self):
        write(self.hidden / "test_hidden_b.py", HIDDEN_B + "\n# 指紋が合わなくなる\n")
        code, _, err = self.score()
        self.assertEqual(code, 2)
        self.assertFalse(self.out.exists())
        self.assertIn("指紋", err)

    def test_missing_record_of_the_fingerprint_exits_two(self):
        (self.tree / "docs" / "bench" / self.TASK / "oracle.sha256").unlink()
        code, _, _ = self.score()
        self.assertEqual(code, 2)
        self.assertFalse(self.out.exists())

    def test_rate_counts_only_hidden_tests_and_ignores_those_in_the_tree(self):
        code, _, _ = self.score()
        self.assertEqual(code, 0)
        rec = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual((rec["task"], rec["passed"], rec["total"], rec["timed_out"]), (self.TASK, 3, 4, False))
        self.assertAlmostEqual(rec["rate"], 0.75)
        self.assertEqual(rec["fingerprint"], bench_score.fingerprint(self.hidden))

    def test_an_import_failure_counts_as_not_passed(self):
        write(self.hidden / "test_hidden_c.py", "import no_such_module_anywhere\n")
        self.pin()
        code, _, _ = self.score()
        rec = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual((code, rec["passed"], rec["total"]), (0, 3, 5))

    def test_no_hidden_tests_exits_two(self):
        (self.hidden / "test_hidden_a.py").unlink()
        (self.hidden / "test_hidden_b.py").unlink()
        self.pin()
        code, _, _ = self.score()
        self.assertEqual(code, 2)
        self.assertFalse(self.out.exists())

    def test_the_tree_is_left_untouched(self):
        write(self.tree / ".git" / "HEAD", "ref: refs/heads/main\n")
        before = snapshot(self.tree)
        code, _, _ = self.score()
        self.assertEqual(code, 0)
        self.assertEqual(snapshot(self.tree), before)

    def test_over_the_time_limit_the_rate_is_zero_and_timed_out(self):
        write(self.hidden / "test_hidden_slow.py", SLOW)
        self.pin()
        code, _, _ = self.score("--timeout", "5")
        rec = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual((code, rec["passed"], rec["rate"], rec["timed_out"]), (0, 0, 0, True))
        self.assertGreater(rec["total"], 0)


if __name__ == "__main__":
    unittest.main()
