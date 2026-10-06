"""H ラインの自己変更の区切り：ライン自身の変更を積んだら、その走行は次の What を取らずに終える。"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import hline  # noqa: E402
import hline_report  # noqa: E402
from test_hline_queue import World  # noqa: E402


class SelfChangeFunction(unittest.TestCase):
    MODS = frozenset({"harness/hline.py", "harness/hline_git.py"})

    def test_modules_configs_and_backslash_paths_match(self):
        got = hline.self_change(["harness\\hline.py", "config/hline.json", "config\\taskspec.schema.json"], self.MODS)
        self.assertEqual(got, ["config/hline.json", "config/taskspec.schema.json", "harness/hline.py"])

    def test_tests_docs_and_other_files_do_not_match(self):
        paths = ["tests/test_hline.py", "docs/design/foo.md", "harness/textnorm.py", "config/other.json"]
        self.assertEqual(hline.self_change(paths, self.MODS), [])

    def test_result_is_sorted_and_inputs_are_not_changed(self):
        paths = ["harness/hline_git.py", "harness/hline.py"]
        mods = set(self.MODS)
        self.assertEqual(hline.self_change(paths, mods), ["harness/hline.py", "harness/hline_git.py"])
        self.assertEqual(paths, ["harness/hline_git.py", "harness/hline.py"])
        self.assertEqual(mods, set(self.MODS))

    def test_default_modules_come_from_line_modules(self):
        self.assertEqual(hline.self_change(["harness/hline.py", "tests/x.py"]), ["harness/hline.py"])

    def test_line_modules_contains_the_line_and_ignores_modules_without_file(self):
        with mock.patch.dict(sys.modules, {"fake_nofile": type(sys)("fake_nofile"), "fake_none": None}):
            mods = hline.line_modules()
        self.assertIsInstance(mods, frozenset)
        self.assertIn("harness/hline.py", mods)
        self.assertIn("harness/hline_git.py", mods)
        self.assertFalse(any(m.startswith(("tests/", "docs/")) for m in mods))


class SelfChangeRun(World):
    def setUp(self):
        super().setUp()
        self.changed = []
        self.patch(hline, "changed_paths", side_effect=lambda wt, c: list(self.changed))

    def saved(self):
        return json.loads((self.out / "queue.json").read_text(encoding="utf-8"))

    def two_whats(self, first_paths):
        self.put("010-a")
        self.put("020-b")
        self.changed = first_paths

    def test_run_stops_after_the_what_that_changed_the_line(self):
        self.two_whats(["harness/hline.py"])
        with mock.patch.object(hline, "process", wraps=hline.process) as proc_spy:
            self.assertEqual(self.poll(), 0)
        self.assertEqual(proc_spy.call_count, 1)
        self.assertEqual(self.integrated, ["010-a"])
        self.assertEqual((self.status("010-a"), self.status("020-b")), ("done", "waiting"))
        self.assertEqual(self.saved()["self_change"], {"name": "010-a", "paths": ["harness/hline.py"]})
        self.assertEqual(self.created, [])

    def test_config_change_also_stops_the_run(self):
        self.two_whats(["config/hline.json"])
        self.assertEqual(self.poll(), 0)
        self.assertEqual(self.status("020-b"), "waiting")

    def test_next_run_continues_with_the_waiting_what(self):
        self.two_whats(["harness/hline.py"])
        self.poll()
        self.changed = []
        self.assertEqual(self.poll(), 0)
        self.assertEqual(self.integrated, ["010-a", "020-b"])
        self.assertIsNone(self.saved()["self_change"])

    def test_unrelated_files_do_not_stop_the_run(self):
        self.two_whats(["docs/design/foo.md", "tests/test_x.py"])
        self.assertEqual(self.poll(), 0)
        self.assertEqual(self.integrated, ["010-a", "020-b"])
        self.assertFalse(self.saved().get("self_change"))
        self.assertEqual(len(self.created), 1)

    def test_unconverged_in_the_run_still_returns_one(self):
        self.put("010-a")
        self.put("020-b")
        self.put("030-c")
        self.changed = ["harness/hline.py"]
        self.failing = {"T-010-a"}
        self.assertEqual(self.poll(), 1)
        self.assertEqual(self.integrated, ["020-b"])
        self.assertEqual(self.status("030-c"), "waiting")

    def test_integrate_failure_does_not_set_self_change(self):
        self.put("010-a")
        self.changed = ["harness/hline.py"]
        with mock.patch.object(hline, "integrate", side_effect=hline.Infra("push 失敗")):
            self.assertEqual(self.poll(), 2)
        self.assertFalse(self.saved().get("self_change"))

    def test_report_shows_the_break_and_the_what_name(self):
        self.two_whats(["harness/hline.py"])
        self.poll()
        self.assertIn("ライン自身の変更を積んだので走行を区切った", self.report())
        self.assertIn("010-a", self.report().split("ライン自身の変更を積んだので走行を区切った")[1].splitlines()[0])


class SelfChangeReport(unittest.TestCase):
    def test_h_section_is_unchanged_without_self_change(self):
        st = {"items": {}, "skipped": [], "awaiting_pr": None, "infra_halt": None}
        base = hline_report.h_section(hline.load_config(), st)
        self.assertEqual(hline_report.h_section(hline.load_config(), dict(st, self_change=None)), base)
        self.assertNotIn("ライン自身の変更", base)
        with_break = hline_report.h_section(hline.load_config(), dict(st, self_change={"name": "x-1", "paths": ["a"]}))
        self.assertIn("x-1", with_break)
        self.assertIn("ライン自身の変更を積んだので走行を区切った", with_break)


if __name__ == "__main__":
    unittest.main()
