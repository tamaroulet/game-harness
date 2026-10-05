"""base（実装前）の検査を、合格済みのタスクのテストだけに絞る（harness/base_whitelist.py、S2-2）。本物のプロセスは起動しない。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import base_whitelist as bw  # noqa: E402
import hline  # noqa: E402

CFG = hline.load_config()
U = "python -m unittest "


def task(i, status="completed", repo="game-harness", cmd=U + "tests.test_a"):
    return {"id": i, "status": status, "target_repo": repo, "verification": {"command": cmd} if cmd else None}


STATE = {"tasks": [task("S1-1"), task("S1-10", "pending", cmd=U + "tests.test_b"), task("S1-2"), task("S1-3", cmd=None),
                   task("S1-4", cmd=U + "discover -s tests"), task("T1", repo="falling-blocks", cmd=U + "tests.test_f"),
                   task("S1-5", cmd=U + "tests.test_g")]}


class Pure(unittest.TestCase):
    def test_a_number_next_to_the_id_is_another_task(self):
        for name, tid, want in (("P_S1_10_x", "S1-1", False), ("P_S1_1_x", "S1-1", True), ("P_S1_1_x", "S1-10", False),
                                ("PropertiesT10Cases.x", "T1", False), ("PropertiesT1Cases.x", "T1", True),
                                ("A1T1", "T1", False), ("test_S2_2", "S2-2", True), ("test_s2_2", "S2-2", False)):
            self.assertIs(bw.belongs(name, tid), want, (name, tid))

    def test_only_passed_tasks_failures_are_selected_once_in_order(self):
        names = ["S.PropertiesT2Cases.a", "S.PropertiesT1Cases.b", "S.PropertiesT10Cases.c", "S.PropertiesT1Cases.b"]
        self.assertEqual((bw.selected(names, []), bw.selected(names, ["T3"])), ((), ()), "未実装のタスクのテストは見ない")
        self.assertEqual(bw.selected(names, ["T1"]), ("S.PropertiesT1Cases.b",))
        self.assertEqual(bw.selected(names, iter(["T2", "T1"])), ("S.PropertiesT2Cases.a", "S.PropertiesT1Cases.b"))
        self.assertEqual(bw.parse_ids("T1, T2 ,S2-2"), ("T1", "T2", "S2-2"))
        self.assertEqual([bw.parse_ids(t) for t in (None, "", " , ,")], [()] * 3)

    def test_completed_ids_modules_and_command(self):
        self.assertEqual(bw.completed_ids(STATE, "game-harness"), ("S1-1", "S1-2", "S1-3", "S1-4", "S1-5"))
        self.assertEqual((bw.completed_ids(STATE, "falling-blocks"), len(bw.completed_ids(STATE))), (("T1",), 6))
        self.assertEqual(bw.completed_modules(STATE, "game-harness"), ("tests.test_a", "tests.test_g"))
        self.assertIsNone(bw.unittest_command(CFG["gate_command"], ()))


class BaseCheck(unittest.TestCase):
    def check(self, state, result=(0, "", "")):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(hline.proc, "run", return_value=result) as run, \
                mock.patch.object(hline.progress, "load", return_value=state):
            if state is not None:
                (Path(d) / "docs").mkdir()
                (Path(d) / "docs" / "progress.yaml").write_text("x", encoding="utf-8")
            return hline.base_check(CFG, d), run

    def test_a_failure_of_a_passed_task_stops_and_only_one_unittest_command_runs(self):
        (ok, out), run = self.check(STATE, (1, "out", "err"))
        self.assertEqual((ok, out), (False, "errout"))
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], bw.unittest_command(CFG["gate_command"], ["tests.test_a", "tests.test_g"]))
        self.assertEqual(run.call_args.args[0][-2:], ["tests.test_a", "tests.test_g"])
        self.assertEqual(run.call_args.args[2:4], (CFG["ttl_seconds"]["gate"], "Base 検査"))
        self.assertFalse({"git", "gh", "claude", "agy"} & set(run.call_args.args[0]))
        self.assertTrue(self.check(STATE)[0][0])

    def test_nothing_to_run_starts_no_process(self):
        for state in ({"tasks": [task("S1-1", "pending")]}, {"tasks": []}, None):
            (ok, out), run = self.check(state, (1, "x", "y"))
            self.assertEqual((ok, out), (True, ""))
            run.assert_not_called()

    def test_base_is_checked_once_before_the_implementer_and_a_failure_is_infra(self):
        for ok in (True, False):
            with tempfile.TemporaryDirectory() as d, mock.patch.object(hline, "new_worktree", return_value=(Path(d), "b")), \
                    mock.patch.object(hline, "base_check", return_value=(ok, "出力")) as bc, \
                    mock.patch.object(hline, "implement", return_value=(0, ["m"])) as imp, \
                    mock.patch.object(hline, "changed_paths", return_value=["x"]), \
                    mock.patch.object(hline, "gate", return_value=(True, "out")):
                if ok:
                    hline.run_task(CFG, "t", "# x", Path(d))
                    self.assertIsNone(imp.call_args.args[3], "base の出力は実装役に渡さない")
                else:
                    self.assertRaises(hline.Infra, hline.run_task, CFG, "t", "# x", Path(d))
                self.assertEqual((bc.call_count, imp.called), (1, ok))

    def test_an_empty_diff_never_starts_the_gate(self):
        with mock.patch.object(hline.proc, "run") as run:
            self.assertFalse(hline.gate(CFG, Path("."), [])[0])
        run.assert_not_called()

    def test_no_route_that_reads_names_allowed_to_fail_remains(self):
        words = ("known_failures", "known-failures", "task_test_classes", "_add_known", "failing_names")
        for f in sorted((ROOT / "harness").rglob("*.py")) + sorted((ROOT / "config").glob("*.json")):
            for word in words:
                self.assertNotIn(word, f.read_text(encoding="utf-8"), f.name)
