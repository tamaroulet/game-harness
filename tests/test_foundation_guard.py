"""基盤のパス（config/foundation_paths.json、harness/hline_foundation.py）の検査。

    python -m unittest tests.test_foundation_guard -v

**なぜ要るか**: 課題・製品を解く What（マイルストーン bench など）が、判定に使うハーネスや指示を書き換えてはならない（D-057・D-058）。
一覧は走っているハーネスの根のファイルから読み、作業ツリーの写しは見ない。外部の CLI・git・gh は呼ばない。
"""
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import hline  # noqa: E402
import hline_base  # noqa: E402
import hline_foundation  # noqa: E402
import hline_task  # noqa: E402

CFG = hline.load_config()
ROOT = Path(__file__).resolve().parent.parent
TOUCHING = ["harness/x.py", "config/y.json"]


def write_table(d, table):
    path = Path(d) / "foundation_paths.json"
    path.write_text(table if isinstance(table, str) else json.dumps(table), encoding="utf-8")
    return path


def gate(paths, wt=Path("."), **kw):
    """影響テスト・規模の検査を差し替えて、基盤のパスの検査だけを見る。"""
    with mock.patch.object(hline.gate_order, "focused", return_value=None), \
            mock.patch.object(hline.proc, "run", return_value=(0, "", "")), \
            mock.patch.object(hline.size_limits, "scan", return_value=[]):
        return hline.gate(CFG, wt, paths, **kw)


class Listed(unittest.TestCase):
    def test_the_list_has_the_paths_and_the_outer_milestones(self):
        paths, milestones = hline_foundation.load()
        self.assertEqual(paths, ("harness/", "config/", "docs/prompts/", "docs/bench/prompts/"))
        self.assertEqual(milestones, ("W-17", "W-18", "outer"))


class Verdict(unittest.TestCase):
    def test_a_bench_what_touching_the_foundation_fails_and_names_path_and_milestone(self):
        ok, msg = gate(["tests/test_a.py", *TOUCHING, "harness/x.py"], milestone="bench")
        self.assertFalse(ok)
        self.assertIn("harness/x.py, config/y.json", msg)
        self.assertEqual(msg.count("harness/x.py"), 1)
        self.assertIn("bench", msg)
        self.assertNotIn("tests/test_a.py", msg)

    def test_outer_milestones_are_not_stopped_by_this_check(self):
        for m in ("W-17", "W-18", "outer"):
            with self.subTest(m=m):
                self.assertEqual(hline_foundation.violations(TOUCHING, m), [])
                ok, msg = gate(TOUCHING, milestone=m)
                self.assertNotIn("基盤", msg)

    def test_paths_outside_the_foundation_pass_for_every_milestone(self):
        for m in ("bench", "W-17", "W-18", "outer", None):
            with self.subTest(m=m):
                self.assertEqual(hline_foundation.violations(["tests/test_a.py", "src/x.py"], m), [])
                self.assertEqual(gate(["tests/test_a.py"], milestone=m), gate(["tests/test_a.py"]))

    def test_a_call_without_a_milestone_behaves_as_before(self):
        self.assertEqual(hline_foundation.violations(TOUCHING, None), [])
        ok, msg = gate(TOUCHING)
        self.assertNotIn("基盤", msg)

    def test_the_prompt_dirs_are_in_the_list(self):
        self.assertEqual(hline_foundation.violations(["docs/bench/prompts/a.md", "docs/prompts/b.md"], "bench"),
                         ["docs/bench/prompts/a.md", "docs/prompts/b.md"])

    def test_a_protected_path_still_fails_for_the_protected_reason(self):
        ok, msg = gate(["harness/x.py", "docs/progress.yaml"], milestone="bench")
        self.assertFalse(ok)
        self.assertIn("変えてはならないパス", msg)

    def test_no_change_still_fails_first(self):
        ok, msg = gate([], milestone="bench")
        self.assertFalse(ok)
        self.assertNotIn("基盤", msg)


class Source(unittest.TestCase):
    def test_a_working_tree_copy_of_the_list_is_not_read(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "config").mkdir()
            write_table(Path(d) / "config", {"paths": ["nothing/"], "milestones": ["bench"]})   # 作業ツリーで弱めた一覧は見ない
            ok, msg = gate(TOUCHING, wt=Path(d), milestone="bench")
        self.assertFalse(ok)
        self.assertIn("harness/x.py", msg)

    def test_the_list_file_is_under_the_harness_root(self):
        self.assertEqual(hline_foundation.FOUNDATION_FILE, hline_base.ROOT / "config" / "foundation_paths.json")
        self.assertEqual(hline_base.ROOT, ROOT)


class LoadFaults(unittest.TestCase):
    def test_faults_are_environment_faults(self):
        self.assertTrue(issubclass(hline_foundation.FoundationError, hline_base.Infra))

    def test_a_missing_file_a_broken_json_and_a_bad_shape_stop(self):
        good = ["harness/"]
        with tempfile.TemporaryDirectory() as d:
            cases = {"missing": Path(d) / "none.json", "broken": write_table(d, "{not json")}
            for name, table in (("no-paths", {"milestones": ["outer"]}), ("no-milestones", {"paths": good}),
                                ("paths-not-array", {"paths": "harness/", "milestones": ["outer"]}),
                                ("non-string", {"paths": good, "milestones": ["outer", 1]}),
                                ("empty-string", {"paths": [""], "milestones": ["outer"]}),
                                ("empty-array", {"paths": [], "milestones": ["outer"]}),
                                ("not-table", good)):
                cases[name] = write_table(d, table)
            for name, path in cases.items():
                with self.subTest(name), self.assertRaises(hline_foundation.FoundationError):
                    hline_foundation.load(path)

    def test_a_broken_list_stops_the_gate_with_an_environment_fault(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(hline_foundation, "FOUNDATION_FILE", write_table(d, "{not json")):
            with self.assertRaises(hline_base.Infra):
                gate(["tests/test_a.py"], milestone="bench")


class FromTheWhat(unittest.TestCase):
    def run_what(self, what):
        seen = []

        def fake_gate(cfg, wt, paths, spec=None, task=None, **kw):
            seen.append(kw)
            return True, "ok"

        host = types.SimpleNamespace(new_worktree=lambda cfg, tid: (Path("."), "b"), implement=lambda *a: (0, ["m"], None, None, 1),
                                     changed_paths=lambda wt, cfg: ["tests/test_a.py"], gate=fake_gate)
        cfg = {**CFG, "max_attempts": 1}
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(hline_task, "digest_of", return_value="d"), \
                mock.patch.object(hline_task, "map_size", return_value={}), \
                mock.patch.object(hline_task.hline_abort, "abort_reason", return_value=None), \
                mock.patch.object(hline_task.hline_resume, "keep"):
            hline_task.run_task(host, cfg, "t", what, Path(d))
        return seen

    def test_run_task_hands_the_milestone_of_the_what_to_the_gate(self):
        self.assertEqual(self.run_what("# t\nマイルストーン: bench\nタスク: T-1\n")[0]["milestone"], "bench")

    def test_a_what_without_a_milestone_passes_none_along(self):
        self.assertNotIn("milestone", self.run_what("# t\n本文だけ\n")[0])


if __name__ == "__main__":
    unittest.main()
