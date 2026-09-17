"""テレメトリ（Step 4）。

    python -m unittest discover -s tests -v

- 取れない値は 0 ではなく null ＋理由になること（0 と「不明」を混ぜない）
- CLI の JSON は、実測した形（agy / claude）どおりに読めること
- 査読用の指標（p2p_violation_rate / retry_entropy / token_to_accepted_loc）を手で計算できる例で確かめる
- pipeline が ABORT（sys.exit）で終わっても、試行の記録が残ること

runs.jsonl への載り方（scheduler）は tests/test_scheduler.py の本物の git と偽 GitHub で確かめる。
"""
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
HARNESS = ROOT / "harness"
sys.path.insert(0, str(HARNESS))

import pipeline  # noqa: E402
import telemetry  # noqa: E402

# 2026-09-17 に 1 回ずつ実測した出力（ID と余分なキーを落としたもの）
AGY_JSON = ('{"conversation_id": "00000000-0000-0000-0000-000000000000", "status": "SUCCESS", '
            '"response": "OK\\n", "duration_seconds": 2.18, "num_turns": 1, "usage": {"input_tokens": 13582, '
            '"output_tokens": 111, "thinking_tokens": 110, "cache_read_tokens": 0, "total_tokens": 13693}}')
CLAUDE_JSON = ('{"type": "result", "subtype": "success", "is_error": false, "result": "OK", "num_turns": 1, '
               '"total_cost_usd": 0.08646, "duration_ms": 3277, "usage": {"input_tokens": 2, '
               '"cache_creation_input_tokens": 7534, "cache_read_input_tokens": 22020, "output_tokens": 4}}')


def assert_null(test, d, key):
    test.assertIsNone(d[key], key)
    test.assertTrue(d.get(key + "_null_reason"), f"{key} の理由が無い")


class CliUsageTests(unittest.TestCase):
    def test_agy_measured_shape(self):
        u = telemetry.cli_usage(AGY_JSON, "agy")
        self.assertEqual((u["input_tokens"], u["output_tokens"], u["cache_read_tokens"], u["total_tokens"],
                          u["num_turns"]), (13582, 111, 0, 13693, 1))
        self.assertEqual(u["cache_read_tokens"], 0, "報告された 0 は 0 のまま")
        assert_null(self, u, "cache_creation_tokens")
        assert_null(self, u, "cost_usd")

    def test_claude_measured_shape_sums_total(self):
        u = telemetry.cli_usage(CLAUDE_JSON, "claude")
        self.assertEqual(u["total_tokens"], 2 + 7534 + 22020 + 4)
        self.assertAlmostEqual(u["cost_usd"], 0.08646)
        self.assertEqual(telemetry.response_text(CLAUDE_JSON, "result"), ("OK", None))

    def test_unavailable_values_are_null_not_zero(self):
        cases = {
            "JSON ではない": ("rate limited", "agy"),
            "配列": ("[1, 2]", "agy"),
            "キーが無い": ('{"usage": {}}', "agy"),
            "文字列の数": ('{"usage": {"total_tokens": "12"}, "num_turns": "1"}', "agy"),
            "bool": ('{"usage": {"total_tokens": true}, "num_turns": false}', "agy"),
            "知らない形式": (AGY_JSON, "gpt"),
        }
        for label, (text, fmt) in cases.items():
            with self.subTest(label):
                u = telemetry.cli_usage(text, fmt)
                for k in ("total_tokens", "num_turns", "input_tokens"):
                    assert_null(self, u, k)

    def test_sum_is_unknown_if_any_part_is_unknown(self):
        partial = '{"usage": {"input_tokens": 2, "cache_read_input_tokens": 1, "output_tokens": 4}}'
        u = telemetry.cli_usage(partial, "claude")
        assert_null(self, u, "total_tokens")
        self.assertIn("cache_creation_tokens", u["total_tokens_null_reason"])

    def test_usage_unknown(self):
        u = telemetry.usage_unknown("呼ぶ前に終了")
        for k in telemetry.USAGE_KEYS:
            assert_null(self, u, k)

    def test_write_is_atomic_and_readable(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a" / "t.json"
            telemetry.write(p, {"x": "日本語"})
            self.assertEqual(telemetry.read(p), ({"x": "日本語"}, None))
            self.assertEqual(list(p.parent.iterdir()), [p], "一時ファイルを残さない")
            p.write_text("{broken", encoding="utf-8")
            data, why = telemetry.read(p)
            self.assertIsNone(data)
            self.assertTrue(why)
            self.assertEqual(telemetry.read(Path(d) / "none.json")[0], None)


class AttemptMetricsTests(unittest.TestCase):
    def test_added_lines_normalizes(self):
        diff = "+++ b/X.cs\n+  int a = 1;  \n+\n-old\n+int a = 1;\n context\n+return a;\n"
        self.assertEqual(telemetry.added_lines(diff), {"int a = 1;", "return a;"})

    def test_retry_entropy_by_hand(self):
        a, b, c = {"x", "y"}, {"x", "y"}, {"z"}
        self.assertEqual(telemetry.retry_entropy([a, b]), (0.0, None), "同じ差分の繰り返しは 0")
        self.assertEqual(telemetry.retry_entropy([a, c]), (1.0, None), "全面的に別なら 1")
        # (x,y)→(x,y,z): 1 − 2/3 = 0.3333、(x,y,z)→(z): 1 − 1/3 = 0.6667、平均 0.5
        self.assertEqual(telemetry.retry_entropy([a, {"x", "y", "z"}, c]), (0.5, None))
        self.assertEqual(telemetry.retry_entropy([set(), set()]), (0.0, None))
        value, why = telemetry.retry_entropy([a])
        self.assertIsNone(value)
        self.assertTrue(why)

    def test_p2p_violation_rate_by_hand(self):
        attempts = [{"p2p_broken": 2}, {"p2p_broken": None}, {"p2p_broken": 0}, {}]
        self.assertEqual(telemetry.p2p_violation_rate(attempts), (50.0, 2, None))
        rate, judged, why = telemetry.p2p_violation_rate([{"p2p_broken": None}])
        self.assertEqual((rate, judged), (None, 0))
        self.assertTrue(why)

    def test_same_failure_repeats(self):
        s = telemetry.sha256_text
        attempts = [{"verdict": "RETRY", "stage": "fast", "reason_sha256": s("x")},
                    {"verdict": "RETRY", "stage": "fast", "reason_sha256": s("x")},
                    {"verdict": "RETRY", "stage": "static", "reason_sha256": s("x")},
                    {"verdict": "SUCCESS", "stage": "ci", "reason_sha256": s("")}]
        self.assertEqual(telemetry.same_failure_repeats(attempts), 1)


class IssueMetricsTests(unittest.TestCase):
    def rec(self, *steps):
        return {"steps": list(steps)}

    def test_tokens_total_includes_failed_runs(self):
        records = [
            self.rec({"name": "decompose", "telemetry": {"usage": {"total_tokens": 10}}},
                     {"name": "pipeline", "telemetry": {"attempts": [
                         {"implementer": {"usage": {"total_tokens": 5}}}]}}),
            self.rec({"name": "decompose", "telemetry": {"usage": {"total_tokens": 7}}},
                     {"name": "audit", "telemetry": None, "telemetry_null_reason": "書かない"},
                     {"name": "pipeline", "telemetry": {"attempts": [
                         {"implementer": {"usage": {"total_tokens": 1}}},
                         {"implementer": {"usage": {"total_tokens": 2}}}]}}),
            self.rec(),   # マージの行（AI を呼ばない）
        ]
        self.assertEqual(telemetry.tokens_total(records), (25, None))

    def test_tokens_total_is_unknown_if_any_call_is_unknown(self):
        unknown = telemetry.usage_unknown("JSON ではない")
        cases = {
            "テレメトリ無し": [self.rec({"name": "pipeline", "telemetry": None,
                                         "telemetry_null_reason": "無い"})],
            "利用量が不明": [self.rec({"name": "decompose", "telemetry": {"usage": {"total_tokens": 9}}},
                                     {"name": "pipeline", "telemetry": {"attempts": [
                                         {"implementer": {"usage": unknown}}]}})],
            "実装役の記録が無い試行": [self.rec({"name": "pipeline", "telemetry": {"attempts": [
                {"implementer": None}]}})],
            "呼び出しが 0 回": [self.rec()],
        }
        for label, records in cases.items():
            with self.subTest(label):
                value, why = telemetry.tokens_total(records)
                self.assertIsNone(value)
                self.assertTrue(why)

    def test_token_to_accepted_loc(self):
        self.assertEqual(telemetry.token_to_accepted_loc(300, None, 4, None), (75.0, None))
        for args in ((None, "t", 4, None), (300, None, None, "l"), (300, None, 0, None)):
            with self.subTest(args):
                value, why = telemetry.token_to_accepted_loc(*args)
                self.assertIsNone(value)
                self.assertTrue(why)


class PipelineTelemetryTests(unittest.TestCase):
    """実物の Ctx を使う（Ctx の既存の属性をテレメトリが上書きしていないことも確かめる）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        unit = self.tmp / "u.json"
        unit.write_text(json.dumps({"id": "u", "whitelist": ["a.cs"],
                                    "acceptance": {"required_tests": ["UTests"]}}), encoding="utf-8")
        cfg = {"paths": {"repo": str(self.tmp), "out_dir": str(self.tmp), "sandbox": str(self.tmp)},
               "ttl_seconds": {}, "control_groups": {"must_pass": "p", "must_fail": "f"},
               "adapters": {"engine": "unity", "fast": "dotnet"}, "project": {},
               "gates": {"max_retry": 2}}
        self.c = pipeline.Ctx(cfg, unit)
        self.c.tel_path = self.tmp / "t.json"
        self.c.tel = {"schema": 1, "attempts": []}

    def fake_attempts(self):
        calls = []

        def attempt(c, feedback):
            calls.append(feedback)
            if len(calls) == 1:
                c.gate = "acceptance"
                c.cur["implementer"] = {"rc": 0, "seconds": 1.0,
                                        "usage": telemetry.cli_usage(AGY_JSON, "agy")}
                c.cur["diff_sha256"], c.cur["added_lines"] = "h", 2
                c.line_sets.append({"a", "b"})
                c.cur["p2p_broken"], c.cur["p2p_base"] = 1, 10
                return "RETRY", "先祖返り（P2P 破壊）1 件: X"
            c.gate = "fast"
            c.cur["implementer"] = {"rc": 0, "seconds": 1.0, "usage": telemetry.usage_unknown("x")}
            c.line_sets.append({"a"})
            sys.exit("ABORT: TRX が生成されませんでした")
        return attempt

    def test_all_attempts_are_recorded_even_when_aborted_by_sys_exit(self):
        def base(c):
            c.base = SimpleNamespace(fast=None)
            c.metrics.update(p2p_base=10, quarantined=0)
            return None, ""

        args = SimpleNamespace(selftest=False, skip_selftest=True)
        with mock.patch.object(pipeline, "establish_base", side_effect=base), \
                mock.patch.object(pipeline, "attempt", side_effect=self.fake_attempts()), \
                mock.patch.object(pipeline, "sandbox_reset"):
            with self.assertRaises(SystemExit):
                pipeline.run_unit(self.c, args)

        tel, why = telemetry.read(self.c.tel_path)
        self.assertIsNone(why)
        first, second = tel["attempts"]
        self.assertEqual((first["verdict"], first["stage"], first["p2p_broken"]), ("RETRY", "acceptance", 1))
        self.assertEqual(first["implementer"]["usage"]["total_tokens"], 13693)
        self.assertEqual(second["verdict"], "ABORT")
        self.assertEqual(second["stage"], "fast")
        self.assertIn("TRX", second["reason"])
        self.assertGreater(second["feedback_chars"], 0, "前回の理由をフィードバックとして渡した長さ")
        assert_null(self, second, "p2p_broken")
        assert_null(self, second, "diff_sha256")
        self.assertEqual(tel["p2p_violation_rate"], 100.0)
        self.assertEqual(tel["attempts_judged"], 1)
        self.assertEqual(tel["retry_entropy"], 0.5)
        assert_null(self, tel, "selftest")
        self.assertEqual(tel["base"]["p2p_base"], 10)
        self.assertIsInstance(self.c.stage, Path, "ゴールデンの置き場（Ctx.stage）を上書きしていない")

    def test_early_abort_still_writes_telemetry(self):
        """require_unit_safe で止まる本物の起動。テレメトリの枠だけは残る。"""
        with tempfile.TemporaryDirectory() as d:
            unit, tel = Path(d) / "bad.json", Path(d) / "t.json"
            unit.write_text(json.dumps({
                "id": "neg", "title": "neg", "prompt": "x", "max_impl_lines": 1,
                "whitelist": ["tests/Core.Tests/XTests.cs"],
                "acceptance": {"required_tests": ["XTests"]}}), encoding="utf-8")
            r = subprocess.run([sys.executable, str(HARNESS / "pipeline.py"), "--project", "unity-2d",
                                "--unit", str(unit), "--telemetry", str(tel)],
                               capture_output=True, stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 2)
            data, why = telemetry.read(tel)
        self.assertIsNone(why)
        self.assertEqual(data["unit_id"], "neg")
        self.assertEqual(data["attempts"], [])
        assert_null(self, data, "exit_code")
        assert_null(self, data, "p2p_violation_rate")
        self.assertIn("total_seconds", data)


class DecomposeEnvelopeTests(unittest.TestCase):
    def call(self, stdout, rc=0):
        import decompose
        with tempfile.TemporaryDirectory() as d:
            cfg = {"prompt_file": str(Path(d) / "p.md"), "cli": "claude", "headless_flag": "-p",
                   "prompt_arg_template": "{prompt_file}", "extra_flags": [],
                   "output_format_args": ["--output-format", "json"], "usage_format": "claude",
                   "response_key": "result", "ttl_seconds": {"claude": 1}}
            with mock.patch.object(decompose, "CFG", cfg), \
                    mock.patch.object(decompose, "TEL", {}) as tel, \
                    mock.patch.object(decompose, "resolve_cli", return_value="claude"), \
                    mock.patch.object(decompose, "run", return_value=(rc, stdout, "")) as run:
                try:
                    return decompose.call_claude("prompt"), tel, run.call_args[0][0]
                except SystemExit as e:
                    return e, tel, run.call_args[0][0]

    def test_result_is_unwrapped_and_usage_recorded(self):
        text, tel, args = self.call(CLAUDE_JSON)
        self.assertEqual(text, "OK")
        self.assertEqual(args[-2:], ["--output-format", "json"])
        self.assertEqual(tel["usage"]["total_tokens"], 29560)

    def test_unreadable_envelope_is_an_environment_abort(self):
        err, tel, _ = self.call('```json\n{"id": "x"}\n```')
        self.assertIsInstance(err, SystemExit)
        self.assertIsInstance(err.code, str, "文字列の exit は rc=2（LLM の出力不良 rc=1 と区別）")
        assert_null(self, tel["usage"], "total_tokens")


class DecisionWaitTests(unittest.TestCase):
    def gh(self, events):
        cfg = {"repo_slug": "o/r", "ttl_seconds": {"gh": 1}, "net_retries": 0,
               "net_retry_interval_seconds": 0, "labels": {}, "repo_dir": "."}
        import scheduler
        return scheduler.GitHub(cfg, lambda args, cwd, ttl: (0, json.dumps(events), ""), lambda s: None)

    def ev(self, name, at, who="h"):
        return {"event": "labeled", "label": {"name": name}, "created_at": at, "actor": {"login": who}}

    def test_last_awaiting_to_first_decision(self):
        events = [self.ev("ms4:awaiting-approval", "2026-09-17T01:00:00Z"),
                  self.ev("ms4:approved", "2026-09-17T01:30:00Z", "old"),   # push で外れた古い承認
                  self.ev("ms4:awaiting-approval", "2026-09-17T02:00:00Z"),
                  self.ev("ms4:approved", "2026-09-17T02:05:00Z", "human"),
                  self.ev("ms4:approved", "2026-09-17T03:00:00Z", "late")]
        wait, who, why = self.gh(events).decision_wait(3, "ms4:awaiting-approval",
                                                       {"ms4:approved", "ms4:declined"})
        self.assertEqual((wait, who, why), (300.0, "human", None))

    def test_no_decision_is_unknown(self):
        wait, who, why = self.gh([self.ev("ms4:awaiting-approval", "2026-09-17T01:00:00Z")]).decision_wait(
            3, "ms4:awaiting-approval", {"ms4:approved"})
        self.assertIsNone(wait)
        self.assertTrue(why)


if __name__ == "__main__":
    unittest.main()
