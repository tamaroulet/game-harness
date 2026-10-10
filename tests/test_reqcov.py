"""要件の網羅検査器（harness/reqcov.py）。

    python -m unittest tests.test_reqcov

合格条件：網羅なら 0、足りない ID・仕様に無い ID なら 1（出力に ID と側・ファイル）、入力の異常は推測せず 2、出力は毎回同じ。
"""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import reqcov  # noqa: E402

SPEC = "# 仕様\n\n- REQ-01：一つ目\n- REQ-02：二つ目\n\n本文中の REQ-03 は参照にすぎない。\n"


class Reqcov(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def write(self, name, text):
        p = self.dir / name
        p.write_text(text, encoding="utf-8")
        return p

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = reqcov.main([str(a) for a in argv])
        return code, out.getvalue()

    def spec(self, text=SPEC):
        return self.write("spec.md", text)

    def glob(self, pattern):
        return str(self.dir / pattern)

    def test_full_coverage_returns_0(self):
        self.write("test_a.txt", "REQ-01 と REQ-02")
        self.write("w1.md", "要件: REQ-01\n")
        self.write("w2.md", "要件：REQ-02\n")
        code, _ = self.run_cli("--spec", self.spec(), "--tests", self.glob("test_*.txt"),
                               "--whats", self.glob("w*.md"))
        self.assertEqual(code, 0)

    def test_missing_id_names_id_and_side(self):
        self.write("test_a.txt", "REQ-01")
        self.write("w1.md", "要件: REQ-01 REQ-02\n")
        code, out = self.run_cli("--spec", self.spec(), "--tests", self.glob("test_*.txt"),
                                 "--whats", self.glob("w*.md"))
        self.assertEqual(code, 1)
        self.assertIn("REQ-02（tests に無い）", out)
        self.assertNotIn("whats に無い", out)

    def test_whats_count_only_requirement_lines(self):
        self.write("test_a.txt", "REQ-01 REQ-02")
        self.write("w1.md", "REQ-01 は本文\n要件: REQ-02\n")
        code, out = self.run_cli("--spec", self.spec(), "--tests", self.glob("test_*.txt"),
                                 "--whats", self.glob("w*.md"))
        self.assertEqual(code, 1)
        self.assertIn("REQ-01（whats に無い）", out)

    def test_only_the_given_side_is_checked(self):
        self.write("test_a.txt", "REQ-01 REQ-02")
        code, _ = self.run_cli("--spec", self.spec(), "--tests", self.glob("test_*.txt"))
        self.assertEqual(code, 0)

    def test_undefined_reference_names_id_and_file(self):
        self.write("test_a.txt", "REQ-01 REQ-02 REQ-09")
        code, out = self.run_cli("--spec", self.spec(), "--tests", self.glob("test_*.txt"))
        self.assertEqual(code, 1)
        self.assertIn("REQ-09", out)
        self.assertIn("test_a.txt", out)

    def test_id_mentioned_only_in_spec_body_is_not_a_definition(self):
        self.write("test_a.txt", "REQ-01 REQ-02 REQ-03")
        code, out = self.run_cli("--spec", self.spec(), "--tests", self.glob("test_*.txt"))
        self.assertEqual(code, 1)
        self.assertIn("REQ-03", out)

    def test_input_errors_return_2(self):
        self.write("test_a.txt", "REQ-01 REQ-02")
        tests = self.glob("test_*.txt")
        cases = {
            "no spec file": ("--spec", self.dir / "none.md", "--tests", tests),
            "no definitions": ("--spec", self.write("nodef.md", "REQ-01 だけ\n"), "--tests", tests),
            "duplicate": ("--spec", self.write("dup.md", SPEC + "- REQ-01：再定義\n"), "--tests", tests),
            "glob without files": ("--spec", self.spec(), "--tests", self.glob("zzz_*.txt")),
            "whats glob without files": ("--spec", self.spec(), "--whats", self.glob("zzz_*.md")),
            "nothing to do": ("--spec", self.spec()),
        }
        for name, argv in cases.items():
            with self.subTest(name):
                self.assertEqual(self.run_cli(*argv)[0], 2)

    def test_list_prints_definitions_in_order(self):
        code, out = self.run_cli("--spec", self.spec("- REQ-10：a\n- REQ-02：b\n"), "--list")
        self.assertEqual((code, out.split()), (0, ["REQ-10", "REQ-02"]))

    def test_list_with_bad_spec_returns_2(self):
        self.assertEqual(self.run_cli("--spec", self.spec("なし\n"), "--list")[0], 2)

    def test_same_input_same_output(self):
        self.write("test_a.txt", "REQ-01 REQ-09")
        self.write("test_b.txt", "REQ-08")
        argv = ("--spec", self.spec(), "--tests", self.glob("test_*.txt"))
        self.assertEqual(self.run_cli(*argv), self.run_cli(*argv))


if __name__ == "__main__":
    unittest.main()
