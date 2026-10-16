"""改行の正規化（harness/textnorm.py）。

    python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from harness import textnorm  # noqa: E402


class NormalizeNewlinesTests(unittest.TestCase):
    def test_mixed_newlines_become_lf_only(self):
        self.assertEqual(textnorm.normalize_newlines("a\r\nb\rc\nd"), "a\nb\nc\nd")

    def test_crlf_is_one_newline(self):
        self.assertEqual(textnorm.normalize_newlines("a\r\n\r\nb"), "a\n\nb")

    def test_lone_cr_and_trailing(self):
        self.assertEqual(textnorm.normalize_newlines("\r\r\n\n\r"), "\n\n\n\n")

    def test_no_newline_is_unchanged(self):
        self.assertEqual(textnorm.normalize_newlines("abc 日本語"), "abc 日本語")

    def test_empty_is_unchanged(self):
        self.assertEqual(textnorm.normalize_newlines(""), "")

    def test_idempotent(self):
        once = textnorm.normalize_newlines("a\r\nb\rc\n\r\n\r\rd")
        self.assertEqual(textnorm.normalize_newlines(once), once)

    def test_module_is_within_300_lines(self):
        path = ROOT / "harness" / "textnorm.py"
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertLessEqual(len(lines), 300)


if __name__ == "__main__":
    unittest.main()
