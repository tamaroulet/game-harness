import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import hline_report  # noqa: E402


class TodoDedupeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.inbox = Path(self._tmp.name)
        self.cfg = {"inbox": str(self.inbox)}
        self.todo = self.inbox / "TODO.md"

    def _st(self, *names):
        return {"items": {n: {"status": "unconverged", "title": "t", "milestone": "B7.4", "at": "x", "reason": "r"}
                          for n in names}}

    def _lines(self):
        return self.todo.read_text(encoding="utf-8").splitlines()

    def test_duplicates_keep_first(self):
        self.todo.write_text("- a\n- b\n- a\n- c\n- b\n- a\n", encoding="utf-8")
        hline_report.write_todo(self.cfg, self._st())
        self.assertEqual(self._lines(), ["- a", "- b", "- c"])

    def test_marked_rows_unchanged(self):
        self.todo.write_text("- a\n- a\n", encoding="utf-8")
        hline_report.write_todo(self.cfg, self._st("020-b", "010-a"))
        lines = self._lines()
        self.assertEqual(lines[0], "- a")
        self.assertEqual(len(lines), 3)
        self.assertIn("010-a", lines[1])
        self.assertIn("020-b", lines[2])

    def test_missing_file_and_no_unmarked(self):
        hline_report.write_todo(self.cfg, self._st())
        self.assertEqual(self.todo.read_text(encoding="utf-8"), "")
        hline_report.write_todo(self.cfg, self._st("010-a"))
        self.assertEqual(len(self._lines()), 1)


if __name__ == "__main__":
    unittest.main()
