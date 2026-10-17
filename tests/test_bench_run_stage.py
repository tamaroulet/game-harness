"""段の走らせ役（W-88）：bench_run stage / stage --finish の試験。factory と finish と OS の呼び出しは偽物。"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import bench_run  # noqa: E402

TASKS = [f"B{i:02d}" for i in range(1, 11)]


class StageTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name) / "clone"
        self.out = Path(tmp.name) / "out"
        self.repo.mkdir()
        self.calls = []      # factory の呼び出し (task, run_id)
        self.finished = []   # finish の呼び出し (task, run_id)
        self.plan = {}       # (task, run_id) -> 無効の理由
        self.states = []
        for target, new in (("stage_tasks", lambda repo, base: list(TASKS)), ("factory", self.fake_factory),
                            ("finish", self.fake_finish), ("set_execution_state", self.states.append),
                            ("version_problem", lambda repo, rev: None)):
            p = mock.patch.object(bench_run, target, new)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(bench_run.sys, "platform", "win32")
        p.start()
        self.addCleanup(p.stop)

    def fake_factory(self, a):
        self.calls.append((a.task, a.run_id))
        d = bench_run.run_dir(self.out, a.task, a.run_id) / "factory"
        d.mkdir(parents=True)
        reason = self.plan.get((a.task, a.run_id))
        (d / "run.json").write_text(json.dumps({"invalid": "REQ-X" if reason else None, "reason": reason}), encoding="utf-8")
        return 0

    def fake_finish(self, a):
        self.finished.append((a.task, a.run_id))
        d = self.repo / "bench" / "records" / a.task
        d.mkdir(parents=True, exist_ok=True)
        run = json.loads((bench_run.run_dir(self.out, a.task, a.run_id) / "factory" / "run.json").read_text(encoding="utf-8"))
        (d / f"factory-{a.run_id}.json").write_text(json.dumps({"invalid": run["invalid"] or False}), encoding="utf-8")
        return 0

    def stage(self, *extra):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = bench_run.main(["stage", "--tag", "t", "--repo", str(self.repo), "--out", str(self.out), *extra])
        return code, err.getvalue()

    def run_stage(self):
        return self.stage("--base", "rev")

    def test_all_tasks_in_order(self):
        code, _ = self.run_stage()
        self.assertEqual(code, 0)
        self.assertEqual(self.calls, [(t, "t-1") for t in TASKS])

    def test_skips_done_and_waiting(self):
        rec = self.repo / "bench" / "records" / "B01"
        rec.mkdir(parents=True)
        (rec / "factory-t-1.json").write_text(json.dumps({"invalid": False}), encoding="utf-8")
        # B02: 無効の記録だけ → 未済。次の回 2 から
        rec = self.repo / "bench" / "records" / "B02"
        rec.mkdir(parents=True)
        (rec / "factory-t-1.json").write_text(json.dumps({"invalid": "REQ-11"}), encoding="utf-8")
        # B03: 仕上げ待ち
        d = bench_run.run_dir(self.out, "B03", "t-1") / "factory"
        d.mkdir(parents=True)
        (d / "run.json").write_text(json.dumps({"invalid": None}), encoding="utf-8")
        code, _ = self.run_stage()
        self.assertEqual(code, 0)
        self.assertEqual(self.calls[0], ("B02", "t-2"))
        self.assertEqual([t for t, _ in self.calls], TASKS[1:2] + TASKS[3:])

    def test_invalid_rerun_then_stop_and_resume(self):
        for n in (1, 2, 3):
            self.plan[("B02", f"t-{n}")] = "漏れ"
        code, err = self.run_stage()
        self.assertEqual(code, 1)
        self.assertIn("B02", err)
        self.assertIn("漏れ", err)
        self.assertEqual(self.calls, [("B01", "t-1"), ("B02", "t-1"), ("B02", "t-2"), ("B02", "t-3")])
        self.calls.clear()
        code, _ = self.run_stage()   # 前の命令の無効は数えない。回 4 から
        self.assertEqual(code, 0)
        self.assertEqual(self.calls[0], ("B02", "t-4"))
        self.assertEqual(len(self.calls), 9)

    def test_invalid_twice_then_valid(self):
        self.plan[("B01", "t-1")] = "a"
        self.plan[("B01", "t-2")] = "b"
        code, _ = self.run_stage()
        self.assertEqual(code, 0)
        self.assertEqual(self.calls[:3], [("B01", "t-1"), ("B01", "t-2"), ("B01", "t-3")])

    def test_finish_all_waiting(self):
        self.run_stage()
        code, _ = self.stage("--finish")
        self.assertEqual(code, 0)
        self.assertEqual(sorted(self.finished), sorted((t, "t-1") for t in TASKS))
        self.calls.clear()
        code, _ = self.run_stage()   # すべて済み
        self.assertEqual((code, self.calls), (0, []))

    def test_keep_awake_during_stage_and_released_on_error(self):
        self.run_stage()
        self.assertEqual(self.states, [bench_run.ES_CONTINUOUS | bench_run.ES_SYSTEM_REQUIRED, bench_run.ES_CONTINUOUS])
        self.states.clear()
        with mock.patch.object(bench_run, "factory", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError), contextlib.redirect_stderr(io.StringIO()):
                # 最初の命令で t の全課題が仕上げ待ちなので、別のタグで未済にする
                bench_run.main(["stage", "--tag", "u", "--repo", str(self.repo), "--out", str(self.out), "--base", "rev"])
        self.assertEqual(self.states[-1], bench_run.ES_CONTINUOUS)
        self.assertEqual(len(self.states), 2)


if __name__ == "__main__":
    unittest.main()
