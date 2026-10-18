"""非劣性の検査（W-57）：bench_regress の試験。stage は偽物、記録は一時ディレクトリ。"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import bench_regress  # noqa: E402

TASKS = [f"B{i:02d}" for i in range(1, 11)]
REV = "abc1234"
BASE = {t: 9 for t in TASKS}   # 各課題 10 件中 9 件合格


class RegressTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = self.root / "clone"
        self.records = self.root / "records"
        self.new = dict(BASE)       # 課題 -> 新の合格数
        self.invalid = set()
        self.stage_code = {False: 0, True: 0}
        self.calls = []
        for t in TASKS:
            self.write(self.records, t, "factory-base-1.json", BASE[t])
        for target, new in (("tasks_of", lambda repo, rev: list(TASKS)), ("run_stage", self.fake_stage)):
            p = mock.patch.object(bench_regress, target, new)
            p.start()
            self.addCleanup(p.stop)

    def write(self, root, task, name, passed, invalid=False):
        d = Path(root) / "bench" / "records" / task
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(json.dumps({"passed": passed, "total": 10, "invalid": invalid}), encoding="utf-8")

    def fake_stage(self, argv):
        self.calls.append(list(argv))
        finish = "--finish" in argv
        if finish and self.stage_code[True] == 0:
            tag = argv[argv.index("--tag") + 1]
            for t in TASKS:
                self.write(self.repo, t, f"factory-{tag}-1.json", self.new[t], t in self.invalid)
        return self.stage_code[finish]

    def run_main(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = bench_regress.main(["--head", REV, "--repo", str(self.repo), "--records", str(self.records), *extra])
        return code, out.getvalue(), err.getvalue()

    def result_path(self):
        return self.records / "bench" / "records" / "regress" / f"{REV}.json"

    def test_same_scores_pass(self):
        code, _, _ = self.run_main()
        self.assertEqual(code, 0)
        doc = json.loads(self.result_path().read_text(encoding="utf-8"))
        self.assertTrue(doc["passed"])
        self.assertEqual(doc["rev"], REV)
        self.assertGreater(doc["lower"], -0.05)
        self.assertAlmostEqual(doc["new_rate"], 0.9)
        self.assertAlmostEqual(doc["base_rate"], 0.9)
        self.assertEqual(sorted(doc["tasks"]), TASKS)
        self.assertEqual(doc["tasks"]["B01"], {"new": {"passed": 9, "total": 10}, "base": {"passed": 9, "total": 10}})
        self.assertTrue(doc["records"]["B01"]["new"].startswith("factory-regress-"))
        self.assertEqual(doc["records"]["B01"]["base"], "factory-base-1.json")

    def test_ten_points_lower_fails(self):
        self.new = {t: 8 for t in TASKS}
        code, _, _ = self.run_main()
        self.assertEqual(code, 1)
        doc = json.loads(self.result_path().read_text(encoding="utf-8"))
        self.assertFalse(doc["passed"])
        self.assertAlmostEqual(doc["lower"], -0.1)
        self.assertAlmostEqual(doc["upper"], -0.1)
        self.assertAlmostEqual(doc["new_rate"], 0.8)

    def test_swapped_task_scores_still_pass(self):
        self.new = dict(BASE, B01=10, B02=8)   # 2 課題の点が入れ替わる。合計は同じ 90
        code, _, _ = self.run_main()
        self.assertEqual(code, 0)
        doc = json.loads(self.result_path().read_text(encoding="utf-8"))
        self.assertAlmostEqual(doc["new_rate"], doc["base_rate"])
        self.assertTrue(doc["passed"])

    def test_interval_is_deterministic(self):
        self.new = {t: (10 if i % 2 == 0 else 7) for i, t in enumerate(TASKS)}
        self.run_main()
        first = self.result_path().read_text(encoding="utf-8")
        self.result_path().unlink()
        self.run_main()
        self.assertEqual(first, self.result_path().read_text(encoding="utf-8"))

    def test_invalid_run_is_exit_2(self):
        self.invalid = {"B03"}
        code, _, err = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn("B03", err)
        self.assertFalse(self.result_path().exists())

    def test_missing_base_record_is_exit_2(self):
        (self.records / "bench" / "records" / "B05" / "factory-base-1.json").unlink()
        code, _, err = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn("B05", err)
        self.assertFalse(self.result_path().exists())
        self.assertEqual(self.calls, [])

    def test_stage_nonzero_is_exit_2(self):
        for finish in (False, True):
            with self.subTest(finish=finish):
                self.stage_code = {False: 0, True: 0}
                self.stage_code[finish] = 1
                code, _, err = self.run_main()
                self.assertEqual(code, 2)
                self.assertFalse(self.result_path().exists())

    def test_stage_receives_head_and_args(self):
        code, _, _ = self.run_main("--out", "O", "--oracles", "R", "--plans", "P", "--implementer", "agy")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.calls), 2)
        run, finish = self.calls
        self.assertNotIn("--finish", run)
        self.assertIn("--finish", finish)
        for argv in self.calls:
            for flag, value in (("--base", REV), ("--repo", str(self.repo)), ("--out", "O"), ("--oracles", "R"),
                                ("--plans", "P"), ("--implementer", "agy")):
                self.assertEqual(argv[argv.index(flag) + 1], value)
            self.assertNotEqual(argv[argv.index("--tag") + 1], "base")

    def test_bad_arguments_exit_2(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(bench_regress.main([]), 2)


if __name__ == "__main__":
    unittest.main()
