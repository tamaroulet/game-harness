"""統合 PR の前の衛生検査（harness/hline_hygiene.py）。git・gh・モデルは差し替え、受信箱などは一時ディレクトリ。"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hline # noqa: E402
import hline_hygiene as hyg  # noqa: E402
import hline_report  # noqa: E402
import test_hline_queue as base  # noqa: E402

LIMIT = 1048576
CFG = {"inbox": "C:\\src\\.local\\inbox", "worktrees": "C:\\src\\.local\\wt", "out": "C:\\src\\.local\\out\\hline",
       "base": "origin/main", "integration_branch": "hline/integration", "ttl_seconds": {"git": 300},
       "pr_hygiene": {"forbidden_globs": ["*.log", "*.tmp", "*.bak", "__pycache__/", "queue.json", "report.md", "TODO.md"],
                      "max_file_bytes": LIMIT}}
DIRTY = [{"path": "out/run.log", "size": 10}, {"path": "harness/__pycache__/x.pyc", "size": 5},
         {"path": "notes.tmp", "size": 1}, {"path": "inbox/010-a.md", "size": 20},
         {"path": "data/big.bin", "size": LIMIT + 1}]
CLEAN = [{"path": "harness/a.py", "size": 100}, {"path": "tests/test_a.py", "size": LIMIT}]


class Matches(unittest.TestCase):
    def test_a_trailing_slash_matches_a_directory_segment_only(self):
        self.assertEqual(hyg.matches("a/__pycache__/x.pyc", ["__pycache__/"]), "__pycache__/")
        self.assertEqual(hyg.matches("__pycache__/x.pyc", ["__pycache__/"]), "__pycache__/")
        self.assertIsNone(hyg.matches("a/__pycache__", ["__pycache__/"]))

    def test_a_pattern_with_a_slash_is_compared_with_the_whole_path(self):
        self.assertEqual(hyg.matches("docs/x.md", ["docs/*.md"]), "docs/*.md")
        self.assertIsNone(hyg.matches("a/docs/x.md", ["docs/*.md"]))

    def test_a_pattern_without_a_slash_is_compared_with_the_file_name(self):
        self.assertEqual(hyg.matches("a/b/run.log", ["*.log"]), "*.log")
        self.assertEqual(hyg.matches("a/queue.json", ["queue.json"]), "queue.json")
        self.assertIsNone(hyg.matches("log/readme.md", ["*.log"]))

    def test_the_first_matching_pattern_wins_and_empty_patterns_never_match(self):
        self.assertEqual(hyg.matches("a.log", ["*.tmp", "a.*", "*.log"]), "a.*")
        self.assertIsNone(hyg.matches("a.log", []))


class Problems(unittest.TestCase):
    def test_one_message_per_violating_file_in_the_given_order(self):
        out = hyg.problems(DIRTY, CFG)
        self.assertEqual(len(out), len(DIRTY))
        for line, f in zip(out, DIRTY):
            self.assertIn(f["path"], line)

    def test_the_reason_is_the_pattern_or_the_size_and_the_limit(self):
        out = hyg.problems(DIRTY, CFG)
        self.assertIn("*.log", out[0])
        self.assertIn("__pycache__/", out[1])
        self.assertIn("inbox/", out[3])
        self.assertIn(str(LIMIT + 1), out[4])
        self.assertIn(str(LIMIT), out[4])

    def test_the_outside_rooms_are_forbidden_by_their_last_directory_name(self):
        files = [{"path": f"{d}/x.txt", "size": 1} for d in ("inbox", "wt", "hline", "src")]
        self.assertEqual(len(hyg.problems(files, CFG)), 3)

    def test_a_file_exactly_at_the_limit_is_not_a_violation(self):
        self.assertEqual(hyg.problems(CLEAN, CFG), [])

    def test_a_file_that_hits_both_gets_one_message_with_the_pattern(self):
        out = hyg.problems([{"path": "big.log", "size": LIMIT * 2}], CFG)
        self.assertEqual(len(out), 1)
        self.assertIn("*.log", out[0])

    def test_missing_keys_are_an_error_not_a_default(self):
        with self.assertRaises(KeyError):
            hyg.problems(CLEAN, {k: v for k, v in CFG.items() if k != "pr_hygiene"})
        with self.assertRaises(KeyError):
            hyg.problems(CLEAN, {**CFG, "pr_hygiene": {"forbidden_globs": []}})

    def test_the_real_config_has_the_keys_and_catches_the_listed_kinds(self):
        hy = base.CFG["pr_hygiene"]
        for name in ("a.log", "a.tmp", "a.bak", "x/__pycache__/y.pyc", "queue.json", "report.md", "TODO.md"):
            self.assertTrue(hyg.matches(name, hy["forbidden_globs"]), name)


class DiffFiles(unittest.TestCase):
    def run_diff(self, exists, outputs):
        calls = []

        def fake_must(args, cwd, ttl, label):
            calls.append(args)
            return outputs[args[1]]

        with mock.patch.object(hyg.hline_git, "integration_exists", return_value=exists), \
                mock.patch.object(hyg.hline_git, "must", side_effect=fake_must):
            return hyg.diff_files(CFG), calls

    def test_no_remote_integration_ref_gives_an_empty_list_without_reading_git_further(self):
        got, calls = self.run_diff(False, {})
        self.assertEqual((got, calls), ([], []))

    def test_paths_come_from_the_diff_and_sizes_from_the_integration_tree(self):
        tree = "\0".join(["100644 blob aaa      12\ta.py", "100644 blob bbb    3000\tdir/b.log", "160000 commit ccc       -\tsub",
                          "100644 blob ddd 9\tz.py", ""])
        got, calls = self.run_diff(True, {"diff": "a.py\0dir/b.log\0", "ls-tree": tree})
        self.assertEqual(got, [{"path": "a.py", "size": 12}, {"path": "dir/b.log", "size": 3000}])
        self.assertIn("--diff-filter=d", calls[0])
        self.assertEqual(calls[0][-1], "origin/main...origin/hline/integration")


class OpenPr(base.World):
    def setUp(self):
        super().setUp()
        self.opened = mock.Mock(return_value=None)
        self.patch(hline, "open_pr", side_effect=self.opened)
        self.diff_calls = []
        self.patch(hline, "diff_files", side_effect=lambda c: self.diff_calls.append(1) or self.diff_list)
        self.ahead_n = 1

    def st(self, **items):
        return {"items": {n: {"status": s, "milestone": "B1.1", "title": "t", **extra} for n, (s, extra) in items.items()}, "awaiting_pr": None,
                "skipped": [], "infra_halt": None}

    def open_it(self, st):
        with mock.patch("builtins.print"):
            hline.open_integration_pr(self.cfg, st)

    def test_a_dirty_diff_opens_no_pr_and_records_the_halt(self):
        st = self.st(a=("done", {}))
        self.diff_list = DIRTY
        self.open_it(st)
        self.opened.assert_not_called()
        self.assertEqual(self.created, [])
        self.assertIsNone(st["awaiting_pr"])
        self.assertNotIn("pr", st["items"]["a"])
        self.assertEqual(st["hygiene_halt"], {"files": hyg.problems(DIRTY, self.cfg)})

    def test_the_halt_is_saved_and_shown_as_unclean_diff_with_every_file_name(self):
        st = self.st(a=("done", {}))
        self.diff_list = DIRTY
        self.open_it(st)
        saved = base.hline_queue.state_path(self.cfg).read_text(encoding="utf-8")
        self.assertIn("hygiene_halt", saved)
        line = hline_report.human_line(self.cfg, st)
        self.assertTrue(line.startswith("- 人間作業: UNCLEAN_DIFF"))
        for f in DIRTY:
            self.assertIn(f["path"], line)

    def test_a_clean_diff_opens_the_pr_as_before(self):
        st = self.st(a=("done", {}), b=("done", {"merged": True}))
        self.diff_list = CLEAN
        self.open_it(st)
        self.assertEqual(len(self.created), 1)
        url = "https://github.com/o/r/pull/1"
        self.assertEqual(st["awaiting_pr"], url)
        self.assertEqual(st["items"]["a"]["pr"], url)

    def test_removing_the_dirty_files_from_the_same_diff_lets_the_pr_through(self):
        dirty = {f["path"] for f in DIRTY}
        st = self.st(a=("done", {}))
        self.diff_list = DIRTY
        self.open_it(st)
        self.assertEqual(self.created, [])
        self.diff_list = [f for f in DIRTY + CLEAN if f["path"] not in dirty]
        self.open_it(st)
        self.assertEqual(len(self.created), 1)
        self.assertIsNotNone(st["awaiting_pr"])

    def test_nothing_fresh_or_nothing_ahead_never_looks_at_the_diff(self):
        self.diff_list = DIRTY
        self.open_it(self.st(a=("done", {"pr": "u"})))
        self.open_it(self.st(a=("done", {"merged": True})))
        self.open_it(self.st(a=("waiting", {})))
        self.ahead_n = 0
        st = self.st(a=("done", {}))
        self.open_it(st)
        self.assertEqual(self.diff_calls, [])
        self.assertEqual(self.created, [])


class HumanLine(base.World):
    def test_priority_is_infra_then_blocked_then_hygiene_then_awaiting(self):
        halt = {"name": "n", "reason": "r", "attempts": 3}
        st = {"items": {}, "awaiting_pr": "https://x/pull/1", "skipped": [], "infra_halt": None,
              "hygiene_halt": {"files": ["a.log: x"]}}
        self.assertTrue(hline_report.human_line(self.cfg, st).startswith("- 人間作業: UNCLEAN_DIFF "))
        st["hygiene_halt"] = None
        self.assertEqual(hline_report.human_line(self.cfg, st), "- 人間作業: REVIEW_REQUIRED https://x/pull/1")
        st["hygiene_halt"] = {"files": ["a.log: x"]}
        with mock.patch.object(hline_report, "blocked", return_value={"B7.1": ["u1", "u2"]}):
            self.assertTrue(hline_report.human_line(self.cfg, st).startswith("- 人間作業: BLOCKED"))
            st["infra_halt"] = halt
            self.assertTrue(hline_report.human_line(self.cfg, st).startswith("- 人間作業: INFRA_HALTED"))


class RunLine(base.World):
    def test_a_halt_from_a_previous_run_is_cleared_at_the_head(self):
        seen = []
        base.hline_queue.save_state(self.cfg, {"items": {}, "awaiting_pr": None, "skipped": [], "infra_halt": None,
                                               "hygiene_halt": {"files": ["a.log: x"]}})
        with mock.patch.object(hline, "open_integration_pr", side_effect=lambda c, st: seen.append(st["hygiene_halt"])):
            self.assertEqual(self.poll(), 0)
        self.assertEqual(seen, [None])
        self.assertNotIn("UNCLEAN_DIFF", self.report())

    def test_a_dirty_line_stops_with_a_report_and_the_next_clean_run_makes_the_pr(self):
        self.put("010-a")
        self.diff_list = DIRTY
        self.assertEqual(self.poll(), 0)
        self.assertEqual(self.created, [])
        self.assertIsNone(self.state()["awaiting_pr"])
        self.assertIn("UNCLEAN_DIFF", self.report())
        self.diff_list = CLEAN
        self.assertEqual(self.poll(), 0)
        self.assertEqual(len(self.created), 1)
        self.assertNotIn("UNCLEAN_DIFF", self.report())


if __name__ == "__main__":
    unittest.main()
