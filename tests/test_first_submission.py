"""V2-6 の最初の提出の記録（docs/design/v2_6_exam_symmetry.md §3.2）。

    python -m unittest tests.test_first_submission

**なぜ要るか**: 試験制度を A・B で同じにした（A にも門がある）ので、門そのものの効果は A と B の比較では見えない。
最初の実装役の呼び出しの直後（門を通す前）を、最終と同じ測定器で測って残し、条件の中の比較（最初 → 最終）で示す。
ここでは、agy も dotnet も使わずに次を縛る。
- 最初の提出の変更（変えた・足した・消したファイル）を残し、同じ HEAD の別の作業ツリーに当て直せる
- A は最初の呼び出しの後に、B は試行 1 の 1 ターン目の後にだけ残す。実装役には知らせない
- 集計に「最初 → 最終」の節が出る
"""
import inspect
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import pipeline  # noqa: E402
from ab import common, driver, report  # noqa: E402


def git_repo(path, files):
    path.mkdir(parents=True)
    for rel, body in files.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(body, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"], cwd=path, check=True)


class SaveRestore(unittest.TestCase):
    def test_round_trip_to_another_tree_at_the_same_head(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        base = {"Core/A.cs": "a", "Core/Gone.cs": "g"}
        git_repo(tmp / "src", base)
        git_repo(tmp / "other", base)
        (tmp / "src" / "Core" / "A.cs").write_text("edited", encoding="utf-8")
        (tmp / "src" / "Core" / "New.cs").write_text("n", encoding="utf-8")
        (tmp / "src" / "Core" / "Gone.cs").unlink()
        pipeline.save_changes(tmp / "src", tmp / "saved", 30)
        self.assertEqual(json.loads((tmp / "saved" / "deleted.json").read_text(encoding="utf-8")), ["Core/Gone.cs"])
        pipeline.restore_changes(tmp / "saved", tmp / "other")
        self.assertEqual((tmp / "other" / "Core" / "A.cs").read_text(encoding="utf-8"), "edited")
        self.assertEqual((tmp / "other" / "Core" / "New.cs").read_text(encoding="utf-8"), "n")
        self.assertFalse((tmp / "other" / "Core" / "Gone.cs").exists())


class WhenSaved(unittest.TestCase):
    def test_b_saves_only_after_the_first_call_of_the_first_attempt(self):
        src = inspect.getsource(pipeline.attempt)
        i_call, i_save = src.index("call_implementer(c, inner_feedback)"), src.index("save_changes(c.sandbox, first")
        self.assertLess(i_call, i_save)
        self.assertIn('turn == 1 and c.metrics.get("attempt") == 1', src[i_call:i_save])
        args = driver.pipeline_args("u.json", "wt", "sb", "out", "tel.json", first=Path("o/T1.first"))
        self.assertEqual(args[args.index("--first-submission") + 1], str(Path("o/T1.first")))
        self.assertNotIn("--first-submission", driver.pipeline_args("u.json", "wt", "sb", "out", "tel.json"))

    def test_b_keeps_telemetry_and_implementer_logs_per_task(self):
        """v2-smoke-03：走行の直下に置くと、T2 以降の実装役の生ログが T1 のものを上書きしていた。"""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        m = common.load_manifest(ROOT / "experiments" / "b4_ab" / "tasks.json")
        seen = {}

        def runner(args, cwd, ttl, label):
            seen["tel"] = Path(args[args.index("--telemetry") + 1])
            seen["first"] = Path(args[args.index("--first-submission") + 1])
            return 0, "", ""
        ctx = {"m": m, "out": tmp, "wt": tmp / "wt", "sandbox": tmp / "sb"}
        driver.run_task_b(ctx, m["tasks"][1], None, {}, runner=runner)
        self.assertEqual(seen["tel"].parent, tmp / m["tasks"][1]["id"])
        self.assertEqual(seen["first"], driver.first_dir(tmp, m["tasks"][1]))

    def test_a_saves_the_state_right_after_its_first_call(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        m = common.load_manifest(ROOT / "experiments" / "b4_ab" / "tasks.json")
        unit = dict(common.unit_of(m, m["tasks"][0]), whitelist=["Core/GameState.cs"])
        wt = tmp / "wt"
        git_repo(wt, {"Core/GameState.cs": "class GameState {}"})
        v2 = json.loads((ROOT / "experiments" / "v2" / "tasks.json").read_text(encoding="utf-8"))
        ctx = {"m": m, "wt": wt, "out": tmp / "out", "ttl": 60, "impl_dir": "Core",
               "imp": {"output_format_args": ["--output-format", "stream-json"]},
               "test_project": "t.csproj", "index": 1, "classes": {"B4abT1Cases": "T1"},
               "templates": {k: (ROOT / "experiments" / "v2" / v2["templates"][k]).read_text(encoding="utf-8")
                             for k in ("initial", "retry")}}
        ctx["out"].mkdir(parents=True)
        n = []

        def call(imp, prompt, cwd, cid, ttl):
            n.append(1)
            (Path(cwd) / "Core" / "GameState.cs").write_text(f"call {len(n)}", encoding="utf-8")
            return {"rc": 0, "seconds": 1.0, "conversation_id": "c", "usage": {}, "out": "", "err": ""}
        driver.run_task_a(ctx, m["tasks"][0], unit, {}, call=call,
                          judge=lambda k: ("SUCCESS", "") if k >= 2 else ("INNER", "x"))
        saved = driver.first_dir(ctx["out"], m["tasks"][0])
        self.assertEqual((saved / "files" / "Core" / "GameState.cs").read_text(encoding="utf-8"), "call 1",
                         "2 回目の呼び出しの後ではなく、最初の呼び出しの直後")


class MeasureFirst(unittest.TestCase):
    def test_measures_in_a_temporary_tree_at_the_task_start_and_removes_it(self):
        from unittest import mock
        from ab import measure
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        repo = tmp / "repo"
        git_repo(repo, {"Core/A.cs": "a"})
        start = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
        (repo / "Core" / "A.cs").write_text("first", encoding="utf-8")
        task = {"id": "T1"}
        out = tmp / "out"
        pipeline.save_changes(repo, driver.first_dir(out, task), 30)
        seen = {}

        def run_fast(wt, proj, out_, tag, env=None):
            seen["content"] = (Path(wt) / "Core" / "A.cs").read_text(encoding="utf-8")
            seen["tag"], seen["env"] = tag, env
            return {"x": "Passed"}, ""
        ctx = {"test_project": "t", "classes": {}, "m": {"invariant_seeds": [1]}}
        with mock.patch.object(measure, "run_fast", side_effect=run_fast), \
                mock.patch.object(measure, "acceptance", return_value=(8, 8)), \
                mock.patch.object(measure, "p2p_broken", return_value=[]), \
                mock.patch.object(measure, "invariants", return_value={"failures": 0}):
            r = driver.measure_first(ctx, {"repo_dir": str(repo), "impl_dir": "Core"}, "s", "A", task, start,
                                     set(), {"SEEDS": "1"}, None, out, wt_root=tmp / "wts")
        self.assertEqual(seen, {"content": "first", "tag": "T1_first", "env": {"SEEDS": "1"}},
                         "同じ非公開シード（env）で、最初の提出の中身を測る")
        self.assertTrue(r["accepted"])
        self.assertFalse((tmp / "wts" / "s-A-first").exists(), "一時の作業ツリーは消す")
        self.assertIsNone(driver.measure_first(ctx, {"repo_dir": str(repo)}, "s", "A", {"id": "T9"}, start,
                                               set(), None, None, out), "残した変更が無ければ測らない")


class Report(unittest.TestCase):
    def row(self, cond, first_ok, last_ok, first_p2p, last_p2p):
        return {"run_id": "s", "condition": cond, "task": "T1", "index": 1, "accepted": last_ok, "attempts": 1,
                "p2p_broken": last_p2p, "invariants": {"failures": 0}, "diff": {"added": 1, "deleted": 0},
                "budget_exceeded": False, "tokens": {}, "seconds": 1.0,
                "first_submission": {"accepted": first_ok, "p2p_broken": first_p2p, "invariants": {"failures": 1}}}

    def test_section_lists_first_and_final_per_condition(self):
        text = report.summarize([self.row("A", False, True, 5, 0), self.row("B", True, True, 0, 0)])
        self.assertIn("## 最初の提出（門を通す前）と最終", text)
        self.assertIn("| T1 | A | 0/1 | 1/1 | 5 | 0 | 1 | 0 |", text)
        self.assertIn("| T1 | B | 1/1 | 1/1 | 0 | 0 | 1 | 0 |", text)

    def test_no_section_without_records(self):
        r = self.row("A", True, True, 0, 0)
        del r["first_submission"]
        self.assertNotIn("最初の提出", report.summarize([r]))


if __name__ == "__main__":
    unittest.main()
