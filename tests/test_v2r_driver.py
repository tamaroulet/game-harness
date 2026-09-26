"""再実験 v2r の 4 条件のドライバ・前提の成立回数・思考と出力の検査（docs/design/v2r_protocol.md §1・§8・§9）。

    python -m unittest tests.test_v2r_driver

**なぜ要るか**: 2 × 2 の要因計画（仕様の形 × 門）で、条件の違いは「仕様の節」と「門の知らせの有無」だけでなければならない。
ここでは、A0・A1・B-G・B が、それぞれ期待どおりの仕様の形と門で動き、それ以外（作業場所・interface・埋め込み・決まり・
ステートレス）が全条件で同じであることを、agy を使わずに縛る。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "tests"))

import agy_stream  # noqa: E402
import propgen  # noqa: E402
import test_propgen as tp  # noqa: E402
from ab import common, driver, v2r  # noqa: E402
from adapters import dotnet  # noqa: E402

PROPS = {"properties": [
    {"id": "P-T1-01", "task": "T1", "rule": "RL-16", "given": "input.Left && !input.Right",
     "then": "after.Score == before.Score"},
]}
FAIL = "PROPERTY_FAIL id=P-T1-01 rule=RL-16 ops=[Left] expected=Score:0 actual=Score:1"
FORMAL = "## タスク：左右移動\n\n## このタスクの性質（受入）\n\n- P-T1-01（RL-16）：前提 input.Left && !input.Right のとき、after.Score == before.Score"


class Factors(unittest.TestCase):
    def test_the_two_by_two_design(self):
        self.assertEqual({c: (f["form"], f["gate"]) for c in v2r.ORDER for f in [v2r.factors(c)]},
                         {"A0": ("nl", False), "A1": ("nl", True), "B-G": ("formal", False), "B": ("formal", True)})
        with self.assertRaises(common.ABError):
            v2r.factors("A")

    def test_the_order_rotates_per_replicate(self):
        self.assertEqual([v2r.order_for(k) for k in (1, 2, 5)],
                         [("A0", "A1", "B-G", "B"), ("A1", "B-G", "B", "A0"), ("A0", "A1", "B-G", "B")])

    def test_run_all_uses_the_rotation_for_a_v2r_manifest(self):
        seen = []
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{}")
        self.addCleanup(Path(f.name).unlink)
        with mock.patch.object(driver.common, "load_manifest", return_value={"kind": "v2r"}), \
                mock.patch.object(driver, "run_condition", side_effect=lambda m, c, r: seen.append((r, c))):
            driver.run_all(f.name, 2, "v2r-run")
        self.assertEqual(seen, [("v2r-run-01", c) for c in v2r.order_for(1)] + [("v2r-run-02", c) for c in v2r.order_for(2)])


class Dispatch(unittest.TestCase):
    """条件ごとに、仕様の形と門の有無が期待どおりで、ほかは同じ。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        base, wt = self.tmp / "exp", self.tmp / "wt"
        (base).mkdir()
        (base / "properties.json").write_text(json.dumps(PROPS), encoding="utf-8")
        (wt / "Core").mkdir(parents=True)
        (wt / "Core" / "GameState.cs").write_text("class GameState {}", encoding="utf-8")
        self.unit = {"title": "左右移動", "prompt": FORMAL, "whitelist": ["Core/GameState.cs"], "interface": {"types": []}}
        self.task = {"id": "T1", "title": "左右移動"}
        self.m = {"_base": str(base), "properties": "properties.json", "max_attempts": 3, "test_dir": "tests"}
        self.wt = wt
        for patch in (mock.patch.object(driver.measure, "place_frozen_tests"),
                      mock.patch.object(driver.pipeline, "save_changes"), mock.patch("builtins.print")):
            patch.start()
            self.addCleanup(patch.stop)

    def run_condition(self, condition):
        prompts, cids, judged = [], [], []

        def call(imp, prompt, cwd, cid, ttl):
            prompts.append(prompt)
            cids.append(cid)
            return {"rc": 0, "seconds": 1.0, "conversation_id": "c", "model": "m", "usage": {"input_tokens": 1},
                    "out": "", "err": "", "outcome": {"status": "SUCCESS"}, "steps": []}

        def judge(n):
            judged.append(n)
            return ("SUCCESS", "") if n >= 2 else ("PROPERTY_FAIL", FAIL)
        ctx = {"m": self.m, "wt": self.wt, "out": self.tmp / f"out-{condition}", "impl_dir": "Core", "index": 1,
               "imp": {"model_name": "m", "output_format_args": ["--output-format", "stream-json"]}, "ttl": 60,
               "v2r": v2r.factors(condition), "condition": condition}
        ctx["out"].mkdir()
        rec = driver.run_task_v2r(ctx, self.task, self.unit, {}, call=call,
                                  judge=judge if ctx["v2r"]["gate"] else None)
        return rec, prompts, cids, judged

    def test_a0_is_natural_language_without_the_gate(self):
        rec, prompts, cids, judged = self.run_condition("A0")
        self.assertEqual((len(prompts), judged, rec["attempts"]), (1, [], 1))
        self.assertIn("左の入力がある、かつ右の入力が無い", prompts[0])
        self.assertNotIn("input.Left", prompts[0], "自然言語の条件に式を出さない")

    def test_b_g_is_formal_without_the_gate(self):
        rec, prompts, cids, judged = self.run_condition("B-G")
        self.assertEqual((len(prompts), judged), (1, []))
        self.assertIn("前提 input.Left && !input.Right のとき", prompts[0])

    def test_a1_is_natural_language_with_the_gate_and_annotated_feedback(self):
        rec, prompts, cids, judged = self.run_condition("A1")
        self.assertEqual((len(prompts), judged, rec["accepted"]), (2, [1, 2], True))
        self.assertIn(FAIL + "\n  （この性質：P-T1-01（RL-16）：", prompts[1])
        self.assertNotIn("input.Left", prompts[1])

    def test_b_is_formal_with_the_gate_and_the_same_feedback_line(self):
        rec, prompts, cids, judged = self.run_condition("B")
        self.assertEqual((len(prompts), judged, rec["accepted"]), (2, [1, 2], True))
        self.assertIn("前回の失敗:\n" + FAIL, prompts[1])
        self.assertNotIn("この性質", prompts[1], "形式の条件は知らせの行だけ")

    def test_everything_but_the_spec_and_the_feedback_is_shared_and_stateless(self):
        recs = {c: self.run_condition(c) for c in v2r.ORDER}
        first = {c: r[0]["calls"][0]["prompt_parts"] for c, r in recs.items()}
        for key in ("where", "interface", "embed", "protocol"):
            self.assertEqual(len({first[c][key] for c in v2r.ORDER}), 1, f"{key} の字数が条件で違う")
        self.assertEqual(first["A0"]["spec"] != first["B-G"]["spec"], True, "違うのは仕様の節")
        for c, (_, _, cids, _) in recs.items():
            self.assertEqual(set(cids), {None}, f"{c}：呼び出しごとに新しい会話（ステートレス）")
        for c in ("A0", "B-G"):
            self.assertNotIn("feedback", first[c])


class Hits(unittest.TestCase):
    def test_the_generator_emits_the_hits_line_only_when_asked(self):
        plain = propgen.generate(tp.DECL, tp.SPEC, tp.GDD, tp.INTERFACE, "tests")
        hits = propgen.generate(tp.DECL, tp.SPEC, tp.GDD, tp.INTERFACE, "tests", report_hits=True)
        model = next(k for k in plain if k.endswith("PropertyModel.cs"))
        self.assertNotIn("PROPERTY_HITS", plain[model], "V2 の生成物（sha256 を凍結した）は変えない")
        self.assertIn("PROPERTY_HITS id=", hits[model])
        self.assertEqual({k: v for k, v in plain.items() if k != model}, {k: v for k, v in hits.items() if k != model})

    def test_hits_are_read_from_the_test_output_and_the_smallest_wins(self):
        ns = dotnet.TRX_NS.strip("{}")
        rows = "".join(
            f'<UnitTestResult testName="t{i}" outcome="Passed"><Output><StdOut>{line}</StdOut></Output></UnitTestResult>'
            for i, line in enumerate(["PROPERTY_HITS id=P-T1-01 rule=RL-16 given=12 runs=30",
                                      "PROPERTY_HITS id=P-T1-01 rule=RL-16 given=0 runs=20",
                                      "PROPERTY_HITS id=P-T1-02 rule=RL-40 given=7 runs=30\nほかの出力"]))
        with tempfile.TemporaryDirectory() as d:
            trx = Path(d) / "r.trx"
            trx.write_text(f'<?xml version="1.0"?><TestRun xmlns="{ns}"><Results>{rows}</Results></TestRun>',
                           encoding="utf-8")
            self.assertEqual(dotnet.property_hits(trx), {"P-T1-01": 0, "P-T1-02": 7})
            self.assertEqual(dotnet.property_hits(Path(d) / "none.trx"), {})


def step(i, out, think, inp=10, cache=0):
    return json.dumps({"event": "step_update", "step_update": {
        "step_index": i, "state": "DONE", "step_type": "agent_response",
        "usage": {"input_tokens": inp, "output_tokens": out, "thinking_tokens": think, "cache_read_tokens": cache}}})


class TokensAndCache(unittest.TestCase):
    def test_thinking_above_output_is_reported(self):
        ok = agy_stream.parse("\n".join([step(1, 100, 90), step(2, 10, 10)]))
        self.assertEqual(ok["token_problems"], [])
        bad = agy_stream.parse(step(1, 100, 120))
        self.assertEqual(bad["token_problems"], ["step 1: thinking=120 > output=100"])

    def test_cache_statistics(self):
        c = agy_stream.parse("\n".join([step(1, 5, 1, inp=1000, cache=0), step(2, 5, 1, inp=100, cache=900),
                                        step(3, 5, 1, inp=100, cache=0)]))["cache"]
        self.assertEqual(c, {"first_step_input": 1000, "first_step_cache_read": 0, "later_misses": 1,
                             "hit_ratio": round(900 / 2100, 4)})
        self.assertIsNone(agy_stream.parse("")["cache"])

    def test_guard_stops_on_thinking_above_output(self):
        from ab import guard
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out"
            out.mkdir()
            (out / "T1_a1.implementer.log").write_text(f"# prompt\nx\n\n# stdout\n{step(1, 10, 20)}\n\n# stderr\n",
                                                       encoding="utf-8")
            lines, stopped = [], []
            with mock.patch.object(guard, "CONDITIONS", ("A0",)), \
                    mock.patch.object(guard.common, "paths", return_value={"out": out, "wt": Path(d), "sandbox": Path(d)}):
                rc = guard.watch(["r1"], 50, interval=0, out=lines.append, stop=lambda rid=None: stopped.append(rid))
        self.assertEqual(rc, 1)
        self.assertTrue(any("thinking > output" in l for l in lines), lines)


if __name__ == "__main__":
    unittest.main()
