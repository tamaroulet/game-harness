"""試験台の仕様（docs/testbed/spec.md）。

    python -m unittest tests.test_testbed_spec

合格条件：BOM なし・LF・末尾の改行 1 つの UTF-8 で、reqcov が REQ-01〜REQ-04 をこの順に読み取れる。
"""
import contextlib
import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import reqcov  # noqa: E402

SPEC = ROOT / "docs" / "testbed" / "spec.md"


class TestbedSpec(unittest.TestCase):
    def test_bytes_shape(self):
        raw = SPEC.read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", raw)
        self.assertTrue(raw.endswith(b"\n") and not raw.endswith(b"\n\n"))
        raw.decode("utf-8")

    def test_title_and_sections(self):
        lines = SPEC.read_text(encoding="utf-8").split("\n")
        self.assertEqual(lines[0], "# 試験台の仕様（G1）")
        self.assertIn("## 模型", lines)
        self.assertIn("## 要件", lines)

    def test_requirements_listed_in_order(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = reqcov.main(["--spec", str(SPEC), "--list"])
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue().splitlines(), ["REQ-01", "REQ-02", "REQ-03", "REQ-04"])


if __name__ == "__main__":
    unittest.main()
