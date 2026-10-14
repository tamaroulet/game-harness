"""report.md の「## キュー」：済みは直近 10 件と件数だけ、未収束には最後の試行の落ちた試験の要点を出す（W-83）。"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import hline_report as hr  # noqa: E402
import hline_resume  # noqa: E402


def item(status, **extra):
    return {"status": status, "title": "題", "milestone": "W-17", "task": None, "deps": [], "tries": [], **extra}


def failure(*tests):
    """unittest の失敗の出力の形。tests は (種別, 名前, ファイル, 行, 例外の行)。"""
    out = []
    for kind, name, file, line, exc in tests:
        out += ["=" * 70, f"{kind}: {name}", "-" * 70, "Traceback (most recent call last):",
                f'  File "{file}", line 3, in outer', "    helper()",
                f'  File "{file}", line {line}, in inner', "    self.assertEqual(1, 2)", exc, ""]
    return "\n".join(out + ["-" * 70, "Ran 3 tests in 0.001s", "", "FAILED (failures=3)"])


class Queue(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cfg = {"integration_branch": "i", "out": tmp.name}

    def section(self, items):
        text = hr.h_section(self.cfg, {"items": items, "awaiting_pr": None, "skipped": []})
        lines = text.splitlines()
        start = lines.index("## キュー")
        return lines[start + 1:]

    def put_failure(self, name, text):
        d = hline_resume.resume_dir(self.cfg, name)
        d.mkdir(parents=True, exist_ok=True)
        (d / hline_resume.FAILURE).write_text(text, encoding="utf-8")

    def unconverged(self, lines):
        start = next(i for i, l in enumerate(lines) if l.startswith("- 未収束"))
        end = next((i for i, l in enumerate(lines) if i > start and l.startswith("- ")), len(lines))
        return lines[start:end]


class Done(Queue):
    def test_only_the_ten_newest_by_completion_time_show_and_the_count_is_all(self):
        items = {f"d{n:02d}": item("done", done_at=f"2026-10-{n:02d}T10:00:00+00:00") for n in range(1, 13)}
        row = next(l for l in self.section(items) if l.startswith("- 済み"))
        self.assertTrue(row.startswith("- 済み（12 件）: "))
        shown = row.split(": ", 1)[1].split(", ")
        self.assertEqual(shown, [f"d{n:02d}" for n in range(12, 2, -1)])

    def test_done_items_without_a_time_are_counted_but_never_shown(self):
        items = {f"d{n:02d}": item("done", done_at=f"2026-10-{n:02d}T10:00:00+00:00") for n in range(1, 11)}
        items["old-a"] = item("done")
        items["old-b"] = item("done", done_at="読めない")
        row = next(l for l in self.section(items) if l.startswith("- 済み"))
        self.assertTrue(row.startswith("- 済み（12 件）: "))
        self.assertNotIn("old-a", row)
        self.assertNotIn("old-b", row)
        self.assertEqual(len(row.split(": ", 1)[1].split(", ")), 10)

    def test_no_done_item_is_none(self):
        self.assertIn("- 済み（0 件）: なし", self.section({}))


class Unconverged(Queue):
    def test_failed_tests_come_under_the_row_as_name_file_line_and_exception(self):
        self.put_failure("a", failure(
            ("FAIL", "test_x (tests.test_m.C.test_x)", "tests/test_m.py", 42, "AssertionError: 1 != 2"),
            ("ERROR", "test_y (tests.test_m.C.test_y)", "tests/test_m.py", 50, "KeyError: 'k'")))
        lines = self.section({"a": item("unconverged", reason="打ち切り")})
        block = self.unconverged(lines)
        self.assertEqual(block[0], "- 未収束（1 件）:")
        self.assertTrue(block[1].startswith("  - a（打ち切り）"))
        self.assertEqual(block[2:], ["    - test_x (tests.test_m.C.test_x): tests/test_m.py:42: AssertionError: 1 != 2",
                                     "    - test_y (tests.test_m.C.test_y): tests/test_m.py:50: KeyError: 'k'"])

    def test_at_most_five_tests_and_the_exception_line_is_cut(self):
        tests = [("FAIL", f"test_{n} (m.C.test_{n})", "tests/m.py", n + 10, "AssertionError: " + "あ" * 300) for n in range(7)]
        self.put_failure("a", failure(*tests))
        points = [l for l in self.unconverged(self.section({"a": item("unconverged")})) if l.startswith("    - ")]
        self.assertEqual(len(points), 5)
        self.assertTrue(points[0].startswith("    - test_0 (m.C.test_0): tests/m.py:10: AssertionError: "))
        self.assertEqual(len(points[0].split("tests/m.py:10: ", 1)[1]), 200)

    def test_each_item_gets_its_own_failures(self):
        self.put_failure("a", failure(("FAIL", "test_a (m.C.test_a)", "tests/a.py", 1, "AssertionError: a")))
        self.put_failure("b", failure(("FAIL", "test_b (m.C.test_b)", "tests/b.py", 2, "AssertionError: b")))
        block = "\n".join(self.unconverged(self.section({"a": item("unconverged"), "b": item("unconverged")})))
        self.assertLess(block.index("  - a"), block.index("test_a"))
        self.assertLess(block.index("test_a"), block.index("  - b"))
        self.assertLess(block.index("  - b"), block.index("test_b"))

    def test_without_a_failure_output_the_line_says_so(self):
        block = self.unconverged(self.section({"a": item("unconverged", reason="r")}))
        self.assertEqual(block[2:], ["    - 失敗の出力なし"])

    def test_the_whole_failure_output_is_not_in_the_report(self):
        self.put_failure("a", failure(("FAIL", "test_a (m.C.test_a)", "tests/a.py", 1, "AssertionError: a")))
        text = "\n".join(self.section({"a": item("unconverged")}))
        for s in ("Traceback", "Ran 3 tests", "helper()"):
            self.assertNotIn(s, text)

    def test_an_unreadable_failure_output_does_not_stop_the_report(self):
        d = hline_resume.resume_dir(self.cfg, "a")
        (d / hline_resume.FAILURE).mkdir(parents=True)   # ファイルの場所がディレクトリで、読めない
        block = self.unconverged(self.section({"a": item("unconverged")}))
        self.assertEqual(block[2:], ["    - 失敗の出力なし"])
        self.assertEqual(self.unconverged(hr.h_section({"integration_branch": "i"}, {
            "items": {"a": item("unconverged")}, "awaiting_pr": None, "skipped": []}).splitlines())[2:], ["    - 失敗の出力なし"])

    def test_no_unconverged_item_is_none(self):
        self.assertIn("- 未収束（0 件）: なし", self.section({}))


if __name__ == "__main__":
    unittest.main()
