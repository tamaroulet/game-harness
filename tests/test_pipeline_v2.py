"""v2 の門（V2-3、docs/design/v2_contract_foundry.md §5・§7）。

    python -m unittest tests.test_pipeline_v2

**なぜ要るか**: 門と指標に触れる改修なので、v1 の失敗（game-harness#63）が戻らないことを機械で縛る。
- 実装役は再試行を含めて編集だけ（v1 の再試行時の解禁は、T4 で 9 回とも TTL で打ち切られた）
- 実装役への失敗の知らせは、性質テストの反例の 1 行だけ（最大 5 行）。ログもスタックトレースも返さない
- 非公開シードの性質テストは、落ちても反例を返さない（公開シードへの過剰適合を防ぐ）
S1・S2 の標準化は tests/test_oracle.py、シグネチャ照合の廃止は tests/test_required_symbols.py で見る。
"""
import inspect
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import pipeline  # noqa: E402
import propgen  # noqa: E402
import tool_policy  # noqa: E402
from adapters import dotnet  # noqa: E402


def trx(results):
    """[(クラス名, メソッド名, 結果, 失敗の本文)] から TRX を作る。"""
    defs, rows = [], []
    for i, (cls, name, outcome, message) in enumerate(results):
        defs.append(f'<UnitTest name="{name}" id="{i}"><TestMethod className="{cls}" name="{name}" /></UnitTest>')
        body = (f"<Output><ErrorInfo><Message>{message}</Message></ErrorInfo></Output>" if message else "")
        rows.append(f'<UnitTestResult testId="{i}" testName="{name}" outcome="{outcome}">{body}</UnitTestResult>')
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">'
            f"<Results>{''.join(rows)}</Results><TestDefinitions>{''.join(defs)}</TestDefinitions></TestRun>")


class EditOnly(unittest.TestCase):
    """§7：再試行を含めて編集だけ。"""

    def test_policy_has_no_retry_unlock(self):
        self.assertFalse(hasattr(tool_policy, "RETRY"))
        self.assertEqual(tool_policy.text(), tool_policy.EDIT_ONLY)
        self.assertIn("検証は外側のパイプラインが行い", tool_policy.EDIT_ONLY)
        # v2.1c：禁止の頼みは全廃（細い作業場所で、読めるものを仕組みで絞る）
        for phrase in ("読まないで", "使用禁止", "使わないで", "実行しないで"):
            self.assertNotIn(phrase, tool_policy.EDIT_ONLY)

    def test_implementer_call_takes_no_retry_switch(self):
        self.assertEqual(list(inspect.signature(pipeline.call_implementer).parameters), ["c", "feedback"])

    def test_every_call_carries_the_edit_only_text(self):
        seen = []
        c = SimpleNamespace(unit={"prompt": "作る", "whitelist": ["a.cs"]}, sandbox=Path("sb"),
                            sb=lambda rel: Path("sb") / rel, cfg={"implementer": {
                                "cli": "agy", "headless_flag": "-p", "auto_approve_flag": "-y",
                                "model_flag": "-m", "model_name": "x"}},
                            ttl={"implementer": 300}, metrics={}, cur=None)

        def fake_run(args, cwd, ttl, label):
            seen.append((args[2], ttl))
            return 0, "", ""
        with mock.patch.object(pipeline, "run", side_effect=fake_run), \
                mock.patch.object(pipeline, "resolve_cli", side_effect=lambda n: [n]), \
                mock.patch.object(pipeline, "write_implementer_log"):
            pipeline.call_implementer(c)
            pipeline.call_implementer(c, feedback="PROPERTY_FAIL id=P-T1-01 rule=RL-16 ops=[Left] "
                                                  "expected=ActiveMino.X:3 actual=ActiveMino.X:4")
        self.assertEqual(len(seen), 2)
        for prompt, ttl in seen:
            self.assertIn(tool_policy.EDIT_ONLY, prompt)
            self.assertEqual(ttl, 300, "TTL は 300 秒のまま")
            # v2.2：DISPUTE_TEST の段落は外した。手順の結び（直ちに編集の道具を呼ぶ）で終わる（A と同じ）
            self.assertNotIn("DISPUTE_TEST", prompt)
            self.assertTrue(prompt.endswith(tool_policy.EDIT_ONLY))
        self.assertIn("PROPERTY_FAIL id=P-T1-01", seen[1][0])


class CounterexampleFeedback(unittest.TestCase):
    """§4.3・§5.1 の 4：性質テストが落ちたら、反例の 1 行だけを最大 5 行返す。"""

    def write(self, rows):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: [p.unlink() for p in d.iterdir()] and d.rmdir())
        (d / "t.trx").write_text(trx(rows), encoding="utf-8")
        return SimpleNamespace(out=d)

    def test_only_property_lines_at_most_five(self):
        rows = [("G.PropertiesT1Cases", f"P_T1_{k:02d}_Public", "Failed",
                 f"PROPERTY_FAIL id=P-T1-{k:02d} rule=RL-16 ops=[Left] expected=ActiveMino.X:3 actual=ActiveMino.X:4\n"
                 "   at Game.Core.Tests.Properties.PropertyRunner.Shrink(...) in C:\\x\\PropertyModel.cs:line 200")
                for k in range(1, 8)]
        rows.append(("G.PropertiesT1Cases", "P_T1_08_Public", "Passed", ""))
        text = dotnet.failure_detail(self.write(rows), "t")
        lines = text.splitlines()
        self.assertEqual(len(lines), 5)
        self.assertTrue(all(l.startswith("PROPERTY_FAIL id=P-T1-0") for l in lines), lines)
        self.assertNotIn("PropertyModel.cs", text, "スタックトレースを返さない")

    def test_vacuous_property_is_reported_as_one_line(self):
        rows = [("G.PropertiesT5Cases", "P_T5_02_Public", "Failed", "PROPERTY_VACUOUS id=P-T5-02 rule=RL-50 given=0")]
        self.assertEqual(dotnet.failure_detail(self.write(rows), "t"), "PROPERTY_VACUOUS id=P-T5-02 rule=RL-50 given=0")

    def test_non_property_failures_fall_back_to_the_digest(self):
        rows = [("G.Other", "Case_a", "Failed", "Expected: 3\nBut was: 2")]
        text = dotnet.failure_detail(self.write(rows), "t")
        self.assertIn("- G.Other.Case_a", text)


class HiddenSeeds(unittest.TestCase):
    """§5.1 の 5：非公開シード。反例は返さない。"""

    def ctx(self, diff="d1"):
        return SimpleNamespace(unit={"id": "t1"}, metrics={"attempt": 1}, cur={"diff_sha256": diff},
                               cfg={"gates": {"max_retry": 2}}, fast=SimpleNamespace(PASSED="Passed"))

    FAST = {"G.PropertiesT1Cases.P_T1_01_Public": "Passed", "G.PropertiesT1Cases.P_T1_01_Hidden": "NotExecuted"}

    def run_stage(self, c, fast, hidden_outcome):
        calls = []

        def fake(c_, tag, env=None):
            calls.append((tag, env))
            return {n: (hidden_outcome if n.endswith("_Hidden") else "Passed") for n in fast}, None
        with mock.patch.object(pipeline, "run_fast_tests", side_effect=fake):
            return pipeline.attempt_hidden_properties(c, fast), calls

    def test_hidden_failure_is_retry_without_counterexample(self):
        c = self.ctx()
        verdict, calls = self.run_stage(c, self.FAST, "Failed")
        self.assertEqual(verdict[0], "RETRY")
        self.assertNotIn("P_T1_01", verdict[1], "どの性質が落ちたかも返さない")
        self.assertNotIn("PROPERTY_FAIL", verdict[1])
        self.assertEqual(c.cur["hidden_failed"], 1)
        seeds = calls[0][1][propgen.HIDDEN_ENV].split(",")
        self.assertEqual(len(seeds), pipeline.HIDDEN_SEEDS_DEFAULT)
        self.assertTrue(all(1 <= int(s) < 2 ** 31 for s in seeds))

    def test_hidden_must_actually_pass_not_just_be_skipped(self):
        """環境変数が効かずに Ignore されたままなら、合格とは見なさない。"""
        verdict, _ = self.run_stage(self.ctx(), self.FAST, "NotExecuted")
        self.assertEqual(verdict[0], "RETRY")

    def test_hidden_pass_and_no_hidden_tests(self):
        verdict, calls = self.run_stage(self.ctx(), self.FAST, "Passed")
        self.assertIsNone(verdict)
        verdict, calls = self.run_stage(self.ctx(), {"G.X.Case_a": "Passed"}, "Passed")
        self.assertIsNone(verdict)
        self.assertEqual(calls, [], "性質テストが無ければ走らせない（v1 の単位）")

    def test_seeds_follow_the_diff_and_the_config(self):
        a, b = pipeline.hidden_seeds(self.ctx("d1"), 20), pipeline.hidden_seeds(self.ctx("d2"), 20)
        self.assertEqual(a, pipeline.hidden_seeds(self.ctx("d1"), 20), "同じ差分なら同じシード（測り直せる）")
        self.assertNotEqual(a, b, "差分が変われば変わる（合わせ込めない）")
        c = self.ctx()
        c.cfg["gates"]["hidden_seeds"] = 3
        _, calls = self.run_stage(c, self.FAST, "Passed")
        self.assertEqual(len(calls[0][1][propgen.HIDDEN_ENV].split(",")), 3)
        self.assertEqual(calls[0][1].get("PATH"), os.environ.get("PATH"), "ほかの環境変数は引き継ぐ")

    def test_build_error_in_the_hidden_run_is_abort(self):
        with mock.patch.object(pipeline, "run_fast_tests", return_value=(None, "検査系故障: TRX が生成されませんでした")):
            verdict = pipeline.attempt_hidden_properties(self.ctx(), self.FAST)
        self.assertEqual(verdict[0], "ABORT")


class GateOrder(unittest.TestCase):
    """§5.1：テスト駆動の単位では、非公開ゴールデンの代わりに非公開シードの性質テストを通す。"""

    def test_attempt_runs_the_hidden_stage_for_test_driven_units(self):
        # V2-6：外側の門は check_outer に切り出した（A の判定も同じものを通る）。attempt はそれを呼ぶ
        self.assertIn("failed = check_outer(c, fast)", inspect.getsource(pipeline.attempt))
        src = inspect.getsource(pipeline.check_outer)
        self.assertIn("attempt_hidden_properties(c, fast)", src)
        self.assertLess(src.index("check_acceptance(c, fast, engine"), src.index("attempt_hidden_properties(c, fast)"))
        self.assertLess(src.index("attempt_hidden_properties(c, fast)"), src.index("invrun.check(c)"))


if __name__ == "__main__":
    unittest.main()
