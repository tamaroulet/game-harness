"""変えてはならないパスの一覧（config/protected_paths.json、harness/hline_protect.py）の検査。

    python -m unittest tests.test_protected_paths -v

**なぜ要るか**: 実装役が一覧や守りのテストを自分の作業ツリーで弱めても、Gate 1 の判定が変わってはならない。
一覧は走っているハーネスの根のファイルから読み、gate は設定の表（config/hline.json）を見ない。外部の CLI・git・gh は呼ばない。
"""
import copy
import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import hline  # noqa: E402
import hline_base  # noqa: E402
import hline_protect  # noqa: E402

CFG = hline.load_config()
ROOT = Path(__file__).resolve().parent.parent


def sample_file(pattern):
    return pattern + "settings.json" if pattern.endswith("/") else pattern


def write_config(d, table):
    path = Path(d) / "protected_paths.json"
    path.write_text(table if isinstance(table, str) else json.dumps(table), encoding="utf-8")
    return path


class Listed(unittest.TestCase):
    def test_the_list_has_the_required_paths_in_order(self):
        self.assertEqual(hline_protect.load()[:len(hline_protect.REQUIRED)], hline_protect.REQUIRED)
        for p in ("config/protected_paths.json", "tests/test_protected_paths.py",
                  "tests/test_size_limits.py", "tests/test_no_inline_review.py", "docs/progress.yaml", ".claude/"):
            self.assertIn(p, hline_protect.load())

    def test_every_listed_path_fails_the_gate_without_running_the_tests(self):
        for p in hline_protect.load():
            f = sample_file(p)
            with self.subTest(p=p), mock.patch.object(hline.proc, "run") as run:
                ok, msg = hline.gate(CFG, Path("."), ["harness/x.py", f])
            self.assertFalse(ok)
            self.assertIn(f, msg)
            run.assert_not_called()

    def test_progress_and_claude_settings_are_violations(self):
        self.assertEqual(hline_protect.violations([".claude/settings.json", "docs/progress.yaml"]),
                         [".claude/settings.json", "docs/progress.yaml"])


class Decoupled(unittest.TestCase):
    def test_hline_json_has_no_list_and_gate_does_not_read_one(self):
        self.assertNotIn("protected_paths", CFG)
        self.assertNotIn("_protected_note", CFG)
        self.assertNotIn("protected_paths", inspect.getsource(hline.gate))

    def test_the_verdict_does_not_depend_on_the_config_table(self):
        for extra in ([], ["harness/x.py"]):
            cfg = copy.deepcopy(CFG)
            cfg["protected_paths"] = extra
            for paths, want in ((["harness/x.py", "docs/progress.yaml"], False), (["harness/x.py"], True)):
                with self.subTest(extra=extra, paths=paths), mock.patch.object(hline.proc, "run", return_value=(0, "", "")), \
                        mock.patch.object(hline, "boundary_problems", return_value=[]), \
                        mock.patch.object(hline.gate_order, "focused", return_value=None), \
                        mock.patch.object(hline.size_limits, "scan", return_value=[]):
                    ok, _ = hline.gate(cfg, Path("."), paths)
                self.assertEqual(ok, want)

    def test_the_list_comes_from_the_harness_root_not_from_the_working_tree(self):
        with tempfile.TemporaryDirectory() as d:
            write_config(Path(d), {"protected_paths": []})   # 作業ツリーで弱めた一覧は見ない
            with mock.patch.object(hline_protect, "PROTECTED_FILE", ROOT / "config" / "protected_paths.json"):
                self.assertEqual(hline_protect.violations(["docs/progress.yaml"]), ["docs/progress.yaml"])


class LoadFaults(unittest.TestCase):
    def test_faults_are_environment_faults(self):
        self.assertTrue(issubclass(hline_protect.ProtectError, hline_base.Infra))

    def test_a_missing_file_a_broken_json_and_a_non_array_stop(self):
        with tempfile.TemporaryDirectory() as d:
            cases = {
                "missing": Path(d) / "none.json",
                "broken": write_config(d, "{not json"),
            }
            for name, table in (("not-array", {"protected_paths": "docs/"}), ("no-key", {"x": 1}),
                                ("non-string", {"protected_paths": [*hline_protect.REQUIRED, 1]}),
                                ("empty-string", {"protected_paths": [*hline_protect.REQUIRED, ""]}),
                                ("not-table", list(hline_protect.REQUIRED))):
                cases[name] = write_config(d, table)
            for name, path in cases.items():
                with self.subTest(name), self.assertRaises(hline_protect.ProtectError):
                    hline_protect.load(path)

    def test_a_missing_required_item_stops(self):
        for gone in ("config/protected_paths.json", "tests/test_size_limits.py", *hline_protect.REQUIRED):
            items = [r for r in hline_protect.REQUIRED if r != gone]
            with self.subTest(gone=gone), tempfile.TemporaryDirectory() as d, self.assertRaises(hline_protect.ProtectError):
                hline_protect.load(write_config(d, {"protected_paths": items}))

    def test_other_keys_are_ignored_and_the_order_is_kept(self):
        items = [*reversed(hline_protect.REQUIRED), "extra/"]
        with tempfile.TemporaryDirectory() as d:
            got = hline_protect.load(write_config(d, {"_comment": "x", "protected_paths": items, "other": 1}))
        self.assertEqual(got, tuple(items))


class Matching(unittest.TestCase):
    def test_unrelated_changes_do_not_match(self):
        self.assertEqual(hline_protect.violations(["harness/x.py", "tests/test_other.py"]), [])
        self.assertEqual(hline_protect.violations([]), [])

    def test_backslash_paths_match(self):
        self.assertEqual(hline_protect.violations([".claude\\settings.json", "docs\\progress.yaml"]),
                         [".claude\\settings.json", "docs\\progress.yaml"])

    def test_order_is_kept_without_duplicates_and_inputs_are_not_changed(self):
        paths = ["tests/test_size_limits.py", "harness/x.py", ".claude/a", "tests/test_size_limits.py", ".claude/a", "docs/progress.yaml"]
        patterns = ["docs/progress.yaml", ".claude/", "tests/test_size_limits.py"]
        before = (list(paths), list(patterns))
        self.assertEqual(hline_protect.violations(paths, patterns),
                         ["tests/test_size_limits.py", ".claude/a", "docs/progress.yaml"])
        self.assertEqual((paths, patterns), before)

    def test_a_directory_pattern_matches_below_and_a_file_pattern_matches_exactly_or_below(self):
        self.assertEqual(hline_protect.violations([".claude", "docs/progress.yaml.bak", "docs/progress.yaml/x"],
                                                  ["docs/progress.yaml", ".claude/"]), ["docs/progress.yaml/x"])

    def test_the_reason_names_every_path_and_asks_to_revert(self):
        msg = hline_protect.reason(["a/b.py", "c/d.py"])
        self.assertNotIn("\n", msg)
        self.assertIn("a/b.py", msg)
        self.assertIn("c/d.py", msg)
        self.assertIn("元に戻", msg)


if __name__ == "__main__":
    unittest.main()
