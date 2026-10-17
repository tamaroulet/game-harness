"""固定の計画（W-97）の試験：bench_run plan・plan --check --all・factory --plan・stage --plans・first_pass と recovered。
計画役・監修役・実装役の CLI と非公開オラクルは使わず、子プロセス（proc.run）を偽物に差し替える。git は一時のディレクトリのリポジトリだけ。"""
import contextlib
import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_run  # noqa: E402
import test_bench_run_factory as tf  # noqa: E402

TASKS = [f"B{i:02d}" for i in range(1, 11)]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stderr_of(fn):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = fn()
    return code, err.getvalue()


class Helper:
    """tf.FactoryTests の偽物の道具（clone の作成・子プロセスの偽物・走行）を借りる。"""

    def __init__(self, case):
        self.f = tf.FactoryTests("test_head_must_be_rev")
        self.f.setUp()
        case.addCleanup(self.f.doCleanups)


class PlanCommandTests(unittest.TestCase):
    def setUp(self):
        self.f = Helper(self).f
        f = self.f
        f.plan_code, f.plan_rounds, f.failed, f.plan_advance, f.plan_usage = 0, 2, [], 0, ()
        f.base = f.build_repo()

    def plan(self):
        f = self.f
        return stderr_of(lambda: f.invoke(["plan", "--task", "B1", "--repo", str(f.repo), "--base", f.base, "--out", str(f.out)]))

    @property
    def dest(self):
        return self.f.repo / "bench" / "plans" / "B1"

    def test_confirmed_writes_three_kinds_byte_for_byte(self):
        code, _ = self.plan()
        self.assertEqual(code, 0)
        work = self.f.out / "work" / "B1" / "factory-plan" / "plan"
        names = ["P-01.md", "P-02.md", "P-03.md", "ledger.yaml", "planner.json"]
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), names)
        for name in names:
            self.assertEqual((self.dest / name).read_bytes(), (work / name).read_bytes())
        self.assertEqual(sum(1 for c in self.f.children if c[0] == "plan_loop"), 1)

    def test_unconfirmed_writes_nothing_and_exits_1(self):
        self.f.plan_code = 1
        self.f.failed = [{"stage": "review", "target": "P-01", "reason": "差し戻し"}]
        code, _ = self.plan()
        self.assertEqual(code, 1)
        self.assertFalse(self.dest.exists())

    def test_environment_fault_exits_2_and_writes_nothing(self):
        self.f.plan_code = 2
        code, _ = self.plan()
        self.assertEqual(code, 2)
        self.assertFalse(self.dest.exists())

    def test_existing_plan_is_not_overwritten(self):
        self.assertEqual(self.plan()[0], 0)
        before = {p.name: p.read_bytes() for p in self.dest.iterdir()}
        self.f.children.clear()
        code, _ = self.plan()
        self.assertEqual(code, 2)
        self.assertEqual({p.name: p.read_bytes() for p in self.dest.iterdir()}, before)
        self.assertEqual(self.f.children, [])   # plan_loop を呼ばない

    def test_check_requires_all(self):
        f = self.f
        code, _ = stderr_of(lambda: f.invoke(["plan", "--check", "--repo", str(f.repo), "--base", f.base]))
        self.assertEqual(code, 2)


class PlanCheckTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        for task in TASKS:
            self.make(task)
        self.failing = set()
        for obj, target, new in ((bench_run, "stage_tasks", lambda repo, base: list(TASKS)),
                                 (bench_run.plan_check, "check", self.fake_plan_check)):   # plan_check の規則そのものは plan_check の試験の持ち分
            p = mock.patch.object(obj, target, new)
            p.start()
            self.addCleanup(p.stop)

    def make(self, task):
        spec = self.repo / "docs" / "bench" / task / "spec.md"
        spec.parent.mkdir(parents=True)
        spec.write_text("# 仕様\n\n- REQ-01：一つ目\n", encoding="utf-8")
        d = self.repo / "bench" / "plans" / task
        d.mkdir(parents=True)
        (d / "P-01.md").write_text("# What\n要件: REQ-01\n", encoding="utf-8")
        (d / "ledger.yaml").write_text("- id: P-01\n", encoding="utf-8")
        files = {n: sha256(d / n) for n in ("P-01.md", "ledger.yaml")}
        (d / "planner.json").write_text(json.dumps({"files": files, "problems": [], "loop": {"rounds": 1}}), encoding="utf-8")

    def fake_plan_check(self, d):
        return [("粒度", "P-01")] if Path(d).name in self.failing else []

    def check(self):
        return stderr_of(lambda: bench_run.main(["plan", "--check", "--all", "--repo", str(self.repo), "--base", "rev"]))

    def test_all_good_exits_0(self):
        self.assertEqual(self.check(), (0, ""))

    def test_missing_plan(self):
        shutil.rmtree(self.repo / "bench" / "plans" / "B03")
        code, err = self.check()
        self.assertEqual(code, 1)
        self.assertIn("B03", err)
        self.assertNotIn("B04", err)

    def test_wrong_sha256(self):
        with (self.repo / "bench" / "plans" / "B05" / "P-01.md").open("a", encoding="utf-8") as fh:
            fh.write("改ざん\n")
        code, err = self.check()
        self.assertEqual(code, 1)
        self.assertIn("B05", err)
        self.assertIn("sha256", err)

    def test_plan_check_failure(self):
        self.failing = {"B07"}
        code, err = self.check()
        self.assertEqual(code, 1)
        self.assertIn("B07", err)
        self.assertIn("plan_check", err)

    def test_requirement_not_covered(self):
        d = self.repo / "bench" / "plans" / "B09"
        (d / "P-01.md").write_text("# What\n要件: なし\n", encoding="utf-8")
        doc = json.loads((d / "planner.json").read_text(encoding="utf-8"))
        doc["files"]["P-01.md"] = sha256(d / "P-01.md")   # sha256 は合わせる。落ちるのは網羅だけ
        (d / "planner.json").write_text(json.dumps(doc), encoding="utf-8")
        code, err = self.check()
        self.assertEqual(code, 1)
        self.assertIn("B09", err)
        self.assertIn("REQ-01", err)


class FrozenFactoryTests(unittest.TestCase):
    def setUp(self):
        self.f = Helper(self).f
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.plan_dir = Path(tmp.name) / "B1"
        self.plan_dir.mkdir()
        for name in ("P-01.md", "P-02.md", "P-03.md", "ledger.yaml"):
            (self.plan_dir / name).write_text(f"固定の {name}\n", encoding="utf-8")
        files = {p.name: sha256(p) for p in self.plan_dir.iterdir()}
        (self.plan_dir / "planner.json").write_text(
            json.dumps({"files": files, "problems": [], "loop": {"rounds": 3, "confirmed": True, "failed": []}}), encoding="utf-8")
        self.files = files

    def run_factory(self, plan_dir=None, **kw):
        f = self.f
        plan_dir = self.plan_dir if plan_dir is None else plan_dir
        f.factory_rc = lambda: f.invoke(f.argv("factory", ["--base", f.base, "--sessions", str(f.sessions), "--plan", str(plan_dir)]))
        return f.go(plan_usage=(), **kw)

    def test_runs_from_frozen_plan_without_plan_loop(self):
        f = self.f
        self.assertEqual(self.run_factory(), 0)
        self.assertNotIn("plan_loop", [c[0] for c in f.children])
        self.assertIn(("bench_start", None), f.children)
        inbox = f.out / "work" / "B1" / "factory-r1" / "inbox"
        self.assertEqual(sorted(p.name for p in inbox.iterdir()), ["P-01.md", "P-02.md", "P-03.md"])
        for name in ("P-01.md", "ledger.yaml", "planner.json", "spec.md"):
            self.assertTrue((f.rundir / "inputs" / name).is_file(), name)
        run = f.run_json()
        self.assertEqual((run["plan_frozen"], run["plan_confirmed"], run["plan_rounds"], run["invalid"]), (True, True, 3, None))
        record = f.finish()
        self.assertEqual(record["planner_sha256"], self.files)
        self.assertEqual(record["planner_sha256"]["ledger.yaml"], sha256(self.plan_dir / "ledger.yaml"))
        self.assertEqual((record["plan_frozen"], record["plan_confirmed"], record["plan_rounds"]), (True, True, 3))

    def test_fresh_plan_is_not_frozen(self):
        f = self.f
        self.assertEqual(f.go(), 0)
        self.assertIn("plan_loop", [c[0] for c in f.children])
        self.assertIs(f.run_json()["plan_frozen"], False)

    def test_incomplete_frozen_plan_aborts(self):
        (self.plan_dir / "planner.json").unlink()
        self.assertEqual(self.run_factory(), 2)
        self.assertEqual(self.f.children, [])

    def test_first_pass_and_recovered_in_record(self):
        f = self.f
        gates = {"P-01": [True], "P-02": [False, True], "P-03": [False, False]}
        real = f.fake_poll

        def poll(cwd, ttl, args):
            result = real(cwd, ttl, args)
            cfg = json.loads(Path(args[args.index("--config") + 1]).read_text(encoding="utf-8"))
            path = Path(cfg["out"]) / "queue.json"
            st = json.loads(path.read_text(encoding="utf-8"))
            for name, item in st["items"].items():
                item["tries"] = [{"gate": g} for g in gates[name]]
            path.write_text(json.dumps(st), encoding="utf-8")
            return result
        f.fake_poll = poll
        self.assertEqual(self.run_factory(), 0)
        run = f.run_json()
        self.assertEqual((run["first_pass"], run["recovered"]), (1 / 3, 1 / 2))
        record = f.finish()
        self.assertEqual((record["first_pass"], record["recovered"]), (1 / 3, 1 / 2))
        self.assertEqual(record["tries"], 5)


class PassRatesTests(unittest.TestCase):
    @staticmethod
    def items(*gates):
        return {f"P-{i}": {"tries": [{"gate": g} for g in t]} for i, t in enumerate(gates, 1)}

    def test_mixed(self):
        rates = bench_run.pass_rates(self.items([True], [False, True], [False, False], [False, False, True]))
        self.assertEqual(rates, (1 / 4, 2 / 3))

    def test_nobody_failed_first_is_null_recovered(self):
        self.assertEqual(bench_run.pass_rates(self.items([True], [True])), (1.0, None))

    def test_all_failed_first_none_recovered(self):
        self.assertEqual(bench_run.pass_rates(self.items([False], [False, False])), (0.0, 0.0))

    def test_no_tries_is_null(self):
        self.assertEqual(bench_run.pass_rates({}), (None, None))
        self.assertEqual(bench_run.pass_rates({"P-1": {"tries": []}, "P-2": {}}), (None, None))

    def test_run_without_plan_has_null_in_record(self):
        h = Helper(self).f
        self.assertEqual(h.go(plan_code=1, failed=[{"stage": "review", "target": None, "reason": "r"}]), 0)
        record = h.finish()
        self.assertEqual((record["first_pass"], record["recovered"]), (None, None))


class StagePlansTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo, self.out, self.plans = self.root / "clone", self.root / "out", self.root / "plans"
        self.repo.mkdir()
        self.calls = []
        for target, new in (("stage_tasks", lambda repo, base: list(TASKS)), ("factory", self.fake_factory),
                            ("set_execution_state", lambda flags: None), ("version_problem", lambda repo, rev: None)):
            p = mock.patch.object(bench_run, target, new)
            p.start()
            self.addCleanup(p.stop)

    def fake_factory(self, a):
        self.calls.append((a.task, a.plan))
        d = bench_run.run_dir(self.out, a.task, a.run_id) / "factory"
        d.mkdir(parents=True)
        (d / "run.json").write_text(json.dumps({"invalid": None}), encoding="utf-8")
        return 0

    def stage(self, *extra):
        return stderr_of(lambda: bench_run.main(["stage", "--tag", "t", "--repo", str(self.repo), "--out", str(self.out),
                                                 "--base", "rev", *extra]))

    def test_passes_each_tasks_plan(self):
        for t in TASKS:
            (self.plans / t).mkdir(parents=True)
        code, _ = self.stage("--plans", str(self.plans))
        self.assertEqual(code, 0)
        self.assertEqual(self.calls, [(t, str(self.plans / t)) for t in TASKS])

    def test_one_missing_runs_nothing(self):
        for t in TASKS:
            if t != "B06":
                (self.plans / t).mkdir(parents=True)
        code, err = self.stage("--plans", str(self.plans))
        self.assertEqual(code, 1)
        self.assertEqual(self.calls, [])
        self.assertIn("B06", err)
        self.assertNotIn("B05", err)

    def test_without_plans_is_unchanged(self):
        code, _ = self.stage()
        self.assertEqual(code, 0)
        self.assertEqual(self.calls, [(t, None) for t in TASKS])


if __name__ == "__main__":
    unittest.main()
