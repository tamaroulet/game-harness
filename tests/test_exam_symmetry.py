"""V2-6 の試験制度の対称化（docs/design/v2_6_exam_symmetry.md）。

    python -m unittest tests.test_exam_symmetry

**なぜ要るか**: B は呼び出しの後に全テスト・whitelist・P2P・非公開シード・不変条件で検査され、落ちれば知らされて
再試行したが、A は公開シードの受入だけを見て終わっていた。A/B の差に「試験制度の差」が混ざる。ここでは、agy も
dotnet も使わずに次を縛る。
- A の判定（pipeline.judge）は、B と同じ関数（check_whitelist_inner・check_fast・check_outer・outer_feedback）を同じ順で通る
- A の再試行の知らせは判定の知らせそのもので、生の出力の末尾は入らない
- B の試行（attempt）も同じ関数を通る（振る舞いは既存のテストで担保）
"""
import inspect
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import narrow_dir  # noqa: E402
import pipeline  # noqa: E402
from ab import common, driver  # noqa: E402


class SameFunctions(unittest.TestCase):
    def test_a_and_b_go_through_the_same_checks_in_the_same_order(self):
        b = inspect.getsource(pipeline.attempt)
        a = inspect.getsource(pipeline.judge)
        for src in (a, b):
            i = [src.index(x) for x in ("check_whitelist_inner(c)", "check_fast(c, ", "check_outer(c, fast)")]
            self.assertEqual(i, sorted(i), "whitelist の内側 → 全テストの高速検査 → 外側の門")
        self.assertIn("outer_feedback(c, ", a)
        self.assertIn("outer_feedback(c, msg)", inspect.getsource(pipeline.run_unit))


class Judge(unittest.TestCase):
    def setUp(self):
        self.c = SimpleNamespace(metrics={}, cur=None, gate=None, sandbox=Path("sb"), ttl={"git": 5},
                                 ctrl_fail="control_must_fail", unit={"whitelist": ["Core/A.cs"]})

    def run_judge(self, inner=None, fast_text=None, outer=None, outside=()):
        with mock.patch.object(pipeline, "sandbox_reset"), \
                mock.patch.object(pipeline, "copy_changes"), \
                mock.patch.object(pipeline, "capture_diff"), \
                mock.patch.object(pipeline, "gate_whitelist", return_value=list(outside)), \
                mock.patch.object(pipeline, "changed_entries", return_value=[]), \
                mock.patch.object(pipeline, "check_whitelist_inner", return_value=inner), \
                mock.patch.object(pipeline, "check_fast", return_value=({}, None, fast_text)) as cf, \
                mock.patch.object(pipeline, "check_outer", return_value=outer) as co:
            return pipeline.judge(self.c, Path("wt"), 2), cf, co

    def test_success_when_every_check_passes(self):
        (v, fb), cf, co = self.run_judge()
        self.assertEqual((v, fb), ("SUCCESS", ""))
        cf.assert_called_once_with(self.c, "judge_2")
        co.assert_called_once()

    def test_inner_failures_stop_before_the_outer_gate(self):
        (v, fb), _, co = self.run_judge(fast_text="PROPERTY_FAIL id=P-T5-01 rule=RL-39 ops=[RotateCw]")
        self.assertEqual(v, "INNER")
        self.assertEqual(fb, "PROPERTY_FAIL id=P-T5-01 rule=RL-39 ops=[RotateCw]", "B と同じ 1 行の反例だけ")
        co.assert_not_called()
        (v, fb), cf, _ = self.run_judge(inner="書き換えてよいファイルの外を変更しました", outside=["X.cs"])
        self.assertEqual(v, "INNER")
        cf.assert_not_called()

    def test_outer_failures_carry_bs_feedback_for_the_next_call(self):
        msg = "非公開シードで性質が破れました（反例は開示しません）\nholdout: secret\ncontrol_must_fail x"
        (v, fb), _, _ = self.run_judge(outer=("RETRY", msg))
        self.assertEqual(v, "RETRY")
        self.assertEqual(fb, "非公開シードで性質が破れました（反例は開示しません）", "B の outer_feedback と同じ")


class CopyChanges(unittest.TestCase):
    def test_changes_new_and_deleted_files_reach_the_sandbox(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        src, sb = tmp / "wt", tmp / "sb"
        for d in (src, sb):
            (d / "Core").mkdir(parents=True)
            (d / "Core" / "A.cs").write_text("a", encoding="utf-8")
            (d / "Core" / "Gone.cs").write_text("g", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=src, check=True)
        subprocess.run(["git", "add", "-A"], cwd=src, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"], cwd=src,
                       check=True)
        (src / "Core" / "A.cs").write_text("edited", encoding="utf-8")
        (src / "Core" / "New.cs").write_text("n", encoding="utf-8")
        (src / "Core" / "Gone.cs").unlink()
        copied = pipeline.copy_changes(src, sb, 30)
        self.assertEqual(sorted(copied), ["Core/A.cs", "Core/Gone.cs", "Core/New.cs"])
        self.assertEqual((sb / "Core" / "A.cs").read_text(encoding="utf-8"), "edited")
        self.assertEqual((sb / "Core" / "New.cs").read_text(encoding="utf-8"), "n")
        self.assertFalse((sb / "Core" / "Gone.cs").exists())


class ConditionA(unittest.TestCase):
    """A の再試行ループは判定を通り、知らせは判定の知らせそのもの。上限は B と同じ 9 回。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.m = common.load_manifest(ROOT / "experiments" / "b4_ab" / "tasks.json")
        self.unit = dict(common.unit_of(self.m, self.m["tasks"][0]), whitelist=["Core/GameState.cs"])
        wt = self.tmp / "wt"
        (wt / "Core").mkdir(parents=True)
        (wt / "Core" / "GameState.cs").write_text("class GameState {}", encoding="utf-8")
        v2 = json.loads((ROOT / "experiments" / "v2" / "tasks.json").read_text(encoding="utf-8"))
        self.ctx = {"m": self.m, "wt": wt, "out": self.tmp / "out", "ttl": 60, "impl_dir": "Core",
                    "imp": {"output_format_args": ["--output-format", "stream-json"]},
                    "test_project": "t.csproj", "index": 1, "classes": {"B4abT1Cases": "T1"},
                    "templates": {k: (ROOT / "experiments" / "v2" / v2["templates"][k]).read_text(encoding="utf-8")
                                  for k in ("initial", "retry")}}
        self.ctx["out"].mkdir(parents=True)
        self.prompts = []

    def call(self, imp, prompt, cwd, cid, ttl):
        self.prompts.append(prompt)
        return {"rc": 0, "seconds": 1.0, "conversation_id": "c", "usage": {}, "out": "", "err": ""}

    def test_retries_carry_the_judge_feedback_and_stop_on_success(self):
        wt = self.ctx["wt"]
        verdicts = iter([("INNER", f"PROPERTY_FAIL id=P-T1-01 at {wt}\\Core\\GameState.cs"),
                         ("RETRY", "非公開シードで性質が破れました（反例は開示しません）"), ("SUCCESS", "")])
        seen = []

        def judge(n):
            seen.append(n)
            return next(verdicts)

        def fast(*a, **k):
            raise AssertionError("判定のあるときは公開の受入だけの測りをしない")
        rec = driver.run_task_a(self.ctx, self.m["tasks"][0], self.unit, {}, call=self.call, fast=fast,
                                judge=judge)
        self.assertEqual((rec["attempts"], rec["accepted"], seen), (3, True, [1, 2, 3]))
        self.assertEqual([c["verdict"] for c in rec["calls"]], ["INNER", "RETRY", "SUCCESS"])
        self.assertIn("## 検査の知らせ\n\nPROPERTY_FAIL id=P-T1-01", self.prompts[1])
        self.assertNotIn(str(wt), self.prompts[1], "作業ツリーのパスは作業場所に置き換える")
        self.assertIn(str(narrow_dir.path_for(wt)), self.prompts[1])
        self.assertIn("非公開シードで性質が破れました（反例は開示しません）", self.prompts[2])
        self.assertNotIn("失敗の出力", self.prompts[1], "生の出力の末尾は渡さない")

    def test_gives_up_at_the_same_budget_as_b(self):
        rec = driver.run_task_a(self.ctx, self.m["tasks"][0], self.unit, {}, call=self.call,
                                judge=lambda n: ("INNER", "x"))
        self.assertEqual((rec["attempts"], rec["accepted"]), (driver.call_budget(self.m), False))

    def test_abort_stops_the_task_like_b(self):
        rec = driver.run_task_a(self.ctx, self.m["tasks"][0], self.unit, {}, call=self.call,
                                judge=lambda n: ("ABORT", "検査系の故障"))
        self.assertEqual((rec["attempts"], rec["accepted"]), (1, False))

    def test_implementer_failure_is_reported_without_checks(self):
        calls = []

        def failing(imp, prompt, cwd, cid, ttl):
            self.prompts.append(prompt)
            return {"rc": 124 if not self.prompts[1:] else 0, "seconds": 1.0, "conversation_id": "c", "usage": {},
                    "out": "", "err": "TTL超過 (300s)"}

        def judge(n):
            calls.append(n)
            return "SUCCESS", ""
        rec = driver.run_task_a(self.ctx, self.m["tasks"][0], self.unit, {}, call=failing, judge=judge)
        self.assertEqual(rec["calls"][0]["verdict"], "IMPLEMENTER_FAILED")
        self.assertEqual(calls, [2], "異常終了の回は検査しない（B の内側ループと同じ）")
        self.assertIn("実装AI が異常終了 (rc=124)", self.prompts[1])


if __name__ == "__main__":
    unittest.main()
