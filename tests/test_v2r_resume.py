"""落ちた走行の記録と再開（原則 P5。docs/design/v2r_instrument_redesign.md §6 の 4）。

    python -m unittest tests.test_v2r_resume

**なぜ要るか**: v2r-dry-02 は T6 の途中で止まり、止まった理由は guard の画面の出力にしか残らなかった。T1〜T5 の結果が
あるのに、続けるには最初から走り直すしかなかった（費用も時間もかかる）。止まった場所と理由を記録に残し、行のある最後の
タスクの終わりから、同じ状態で続けられること。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

from ab import common, driver  # noqa: E402

TASKS = [{"id": f"T{i}", "unit": f"units/T{i}.json"} for i in (1, 2, 3)]


def row(task, end):
    return {"task": task, "resume": {"end_commit": end, "passing": ["A.t1"], "known_failures": ["A.t9"]}}


class ResumePoint(unittest.TestCase):
    def write(self, d, rows):
        path = Path(d) / "metrics.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return path

    def test_rows_from_the_start_give_the_point_and_the_carry(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(driver.resume_point(Path(d) / "none.jsonl", TASKS), (0, None))
            done, carry = driver.resume_point(self.write(d, [row("T1", "c1"), row("T2", "c2")]), TASKS)
        self.assertEqual((done, carry["end_commit"]), (2, "c2"))

    def test_rows_out_of_order_or_without_the_resume_record_refuse(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(common.ABError):
                driver.resume_point(self.write(d, [row("T2", "c2")]), TASKS)
            with self.assertRaises(common.ABError) as ctx:
                driver.resume_point(self.write(d, [{"task": "T1"}]), TASKS)
        self.assertIn("再開の情報がありません", str(ctx.exception))


class SetAside(unittest.TestCase):
    def test_only_the_interrupted_task_is_moved_and_nothing_is_deleted(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            for name in ("T2_a1.implementer.log", "T2_final.trx", "T3_a1.implementer.log", "T3_a2.implementer.log",
                         "metrics.jsonl"):
                (out / name).write_text("x", encoding="utf-8")
            (out / "T3.first" / "files").mkdir(parents=True)
            (out / "T3").mkdir()
            (out / "judge" / "T3").mkdir(parents=True)
            (out / "judge" / "T2").mkdir(parents=True)
            moved = driver.set_aside(out, "T3")
            self.assertEqual(moved, ["T3", "T3.first", "T3_a1.implementer.log", "T3_a2.implementer.log", "judge/T3"])
            left = sorted(x.name for x in out.iterdir())
            self.assertEqual(left, ["T2_a1.implementer.log", "T2_final.trx", "aborted", "judge", "metrics.jsonl"])
            self.assertEqual(len(list((out / "aborted").rglob("T3_a*.implementer.log"))), 2, "guard は rglob で数え続ける")


class RunCondition(unittest.TestCase):
    """run_condition を偽の部品で回し、再開・記録の流れを確かめる（実装役・dotnet・git は呼ばない）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        (d / "exp" / "templates").mkdir(parents=True)
        for k in ("initial", "retry"):
            (d / "exp" / "templates" / f"{k}.md").write_text(k, encoding="utf-8")
        self.m = {"kind": "v2r", "tasks": TASKS, "base_commit": "base", "_base": str(d / "exp"),
                  "templates": {"initial": "templates/initial.md", "retry": "templates/retry.md"},
                  "measure_hidden_seeds": 2, "invariant_seeds": 1, "test_dir": "tests"}
        self.wt_root, self.out_root = d / "wt", d / "out"
        self.git, self.ran, self.commits = [], [], []

    def run_it(self, resume=False, fail_at=None):
        def runner(ctx, task, unit, state):
            self.ran.append((task["id"], ctx["index"], list(state["known_failures"])))
            if task["id"] == fail_at:
                raise OSError("掴まれていた")
            return {"attempts": 1, "accepted": True, "calls": []}

        def commit(cwd, msg):
            self.commits.append(msg)
            return f"c{len(self.commits)}"
        patches = [
            mock.patch.object(driver.common, "load_manifest", return_value=dict(self.m)),
            mock.patch.object(driver.common, "unit_of", return_value={}),
            mock.patch.object(driver.project, "load", return_value={"repo_dir": ".", "fast_test_project": "x",
                                                                    "impl_dir": "Core"}),
            mock.patch.object(driver.project, "pipeline_config", return_value={
                "implementer": {"model_name": "m"}, "ttl_seconds": {"implementer": 1}}),
            mock.patch.object(driver.model_pin, "require_implementer"),
            mock.patch.object(driver.common, "git", side_effect=lambda args, *a, **k: self.git.append(args) or ""),
            mock.patch.object(driver.measure, "class_to_task", return_value={}),
            mock.patch.object(driver.measure, "run_fast", return_value=({"A.t1": "Passed", "A.t9": "Failed"}, None)),
            mock.patch.object(driver.measure, "place_frozen_tests", return_value=False),
            mock.patch.object(driver.common, "commit_all", side_effect=commit),
            mock.patch.object(driver.measure, "acceptance", return_value=(1, 1)),
            mock.patch.object(driver.measure, "invariants", return_value={"failures": 0}),
            mock.patch.object(driver.common, "numstat", return_value=(1, 0)),
            mock.patch.object(driver.dotnet, "property_hits", return_value={}),
            mock.patch.object(driver, "measure_first", return_value=None),
            mock.patch("builtins.print"),
        ]
        for p in patches:
            p.start()
        try:
            return driver.run_condition("tasks.json", "A0", "v2r-dry-09", self.wt_root, self.out_root,
                                        task_runner=runner, resume=resume)
        finally:
            for p in patches:
                p.stop()

    def out(self):
        return self.out_root / "v2r-dry-09" / "A0"

    def rows(self):
        return [json.loads(line) for line in (self.out() / "metrics.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_a_crash_is_recorded_and_the_run_resumes_from_the_next_task(self):
        with self.assertRaises(OSError):
            self.run_it(fail_at="T2")
        fault = json.loads((self.out() / "fault.json").read_text(encoding="utf-8"))
        self.assertEqual((fault["task"], fault["index"], fault["phase"], fault["type"]), ("T2", 2, "implement", "OSError"))
        self.assertIn("--resume", fault["resume"])
        self.assertEqual([r["task"] for r in self.rows()], ["T1"])
        self.assertEqual(self.rows()[0]["resume"]["end_commit"], "c2")
        (self.out() / "T2_a1.implementer.log").write_text("途中", encoding="utf-8")
        (self.wt_root / "v2r-dry-09-A0").mkdir(parents=True)   # 落ちた走行の作業ツリーは残っている

        self.ran.clear()
        self.git.clear()
        self.assertEqual(self.run_it(resume=True), 0)
        self.assertEqual([(t, i) for t, i, _ in self.ran], [("T2", 2), ("T3", 3)], "T1 は流し直さない。番号は続き")
        self.assertEqual(self.ran[0][2], ["A.t9"], "前のタスクの終わりの失敗の一覧を、行から戻す")
        self.assertIn(["reset", "-q", "--hard", "c2"], self.git, "T1 の終わりのコミットに戻す")
        self.assertNotIn("worktree", [a[0] for a in self.git], "作業ツリーは作り直さない")
        self.assertEqual([r["task"] for r in self.rows()], ["T1", "T2", "T3"])
        self.assertTrue(list((self.out() / "aborted").rglob("T2_a1.implementer.log")))

    def test_resume_refuses_a_v2_manifest_and_a_missing_worktree(self):
        with self.assertRaises(common.ABError):
            self.run_it(resume=True)   # 作業ツリーが無い
        self.m["kind"] = "v2"
        with self.assertRaises(common.ABError) as ctx:
            self.run_it(resume=True)
        self.assertIn("v2r のマニフェストだけ", str(ctx.exception))

    def test_a_fresh_run_on_an_existing_worktree_points_to_resume(self):
        (self.wt_root / "v2r-dry-09-A0").mkdir(parents=True)
        with self.assertRaises(common.ABError) as ctx:
            self.run_it()
        self.assertIn("--resume", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
