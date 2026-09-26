"""v2r の上限・一時的な失敗の呼び直し・実行環境の照合（docs/design/v2r_protocol.md §4・§5・§10）。

    python -m unittest tests.test_v2r_limits

**なぜ要るか**: 門が働いて再試行が起きると、費用は初手一発合格の見積もりを超えうる。上限と止め方を機械で縛る。
レートリミットは実装役の振る舞いではないので、呼び出しの上限に数えずに呼び直し、続けば繰り返しを止める。
実行環境（OS・CLI・Python・依存）は固定値と違えば起動しない。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import envcheck  # noqa: E402
import pipeline  # noqa: E402
import transient  # noqa: E402
from ab import common, driver, guard  # noqa: E402


class Ledger(unittest.TestCase):
    def test_the_limits_of_the_protocol(self):
        self.assertEqual(guard.LIMITS, {"per_call": 0.5, "per_replicate": 15.0, "warn": 25.0, "hard_cap": 50.0})

    def test_a_call_over_the_per_call_limit_stops_its_run(self):
        led = guard.Ledger()
        self.assertEqual(led.add("r1", 0.5), [], "ちょうど 0.5 は超えていない")
        self.assertEqual([e[:2] for e in led.add("r1", 0.51)], [("stop_run", "r1")])

    def test_a_replicate_over_its_limit_stops_that_replicate(self):
        led = guard.Ledger(per_call=100)
        for _ in range(3):
            self.assertEqual(led.add("r1", 5.0), [])
        self.assertEqual([e[:2] for e in led.add("r1", 0.01)], [("stop_replicate", "r1")])
        self.assertEqual(led.add("r2", 1.0), [], "ほかの繰り返しは別に数える")

    def test_the_warning_line_is_reported_once_and_does_not_stop(self):
        led = guard.Ledger(per_call=100, per_replicate=100)
        self.assertEqual(led.add("r1", 24.9), [])
        self.assertEqual([e[0] for e in led.add("r2", 0.1)], ["warn"])
        self.assertEqual(led.add("r3", 1.0), [], "2 回目は知らせない")

    def test_the_hard_cap_stops_everything_when_reached(self):
        led = guard.Ledger(per_call=100, per_replicate=100, warn=None)
        self.assertEqual(led.add("r1", 49.99), [])
        self.assertEqual([e[:2] for e in led.add("r2", 0.01)], [("stop_all", None)], "50 に達した時点で止める")

    def test_stop_driver_targets_one_run_or_all(self):
        with mock.patch.object(guard.subprocess, "run") as run:
            guard.stop_driver("v2r-run-02")
            one = run.call_args[0][0][-1]
            guard.stop_driver()
            every = run.call_args[0][0][-1]
        self.assertIn("*v2r-run-02*", one)
        self.assertIn("*run-all*", one, "全走行を 1 つのプロセスで回す run-all も止める")
        self.assertNotIn("v2r-run-02", every)

    def test_watch_stops_on_a_ledger_event(self):
        """watch は、ログを読むたびに台帳に足し、止める出来事で止める（止め方は差し替えて確かめる）。"""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out"
            (out / "T1").mkdir(parents=True)
            stream = json.dumps({"event": "step_update", "step_update": {
                "step_index": 1, "state": "DONE", "step_type": "agent_response",
                "usage": {"input_tokens": 1_000_000, "output_tokens": 0}}})
            (out / "T1" / "implementer_attempt_1.log").write_text(
                f"=== プロンプト ===\nx\n\n=== stdout ===\n{stream}\n\n=== stderr ===\n", encoding="utf-8")
            paths = {"out": out, "wt": Path(d) / "wt", "sandbox": Path(d) / "sb"}
            stopped, lines = [], []
            with mock.patch.object(guard.common, "CONDITIONS", ("B",)), \
                    mock.patch.object(guard.common, "paths", return_value=paths):
                rc = guard.watch(["r1"], 50, interval=0, out=lines.append, ledger=guard.Ledger(),
                                 stop=lambda rid=None: stopped.append(rid))
        self.assertEqual(rc, 1)
        self.assertEqual(stopped, ["r1"], "1 呼び出し 0.75 USD > 0.5 で、その走行を止める")
        self.assertTrue(any(l.startswith("STOP stop_run") for l in lines))


class Transient(unittest.TestCase):
    ERR = {"status": "ERROR", "error": "429 Too Many Requests: RESOURCE_EXHAUSTED"}

    def test_classify(self):
        self.assertTrue(transient.classify(self.ERR)[0])
        for oc in (None, {"status": "SUCCESS"}, {"status": None},
                   {"status": "ERROR", "error": "Your previous response was cut off"}):
            self.assertFalse(transient.classify(oc)[0], oc)

    def test_backoff_waits_60_120_240_and_returns_on_success(self):
        results, waits = [self.ERR, self.ERR, {"status": "SUCCESS"}], []
        got, retries = transient.with_backoff(lambda: results.pop(0), transient.classify, sleep=waits.append,
                                              log=lambda s: None)
        self.assertEqual((got, waits, len(retries)), ({"status": "SUCCESS"}, [60, 120], 2))

    def test_three_failed_retries_raise(self):
        waits = []
        with self.assertRaises(transient.TransientFailure) as ctx:
            transient.with_backoff(lambda: self.ERR, transient.classify, sleep=waits.append, log=lambda s: None)
        self.assertEqual(waits, [60, 120, 240])
        self.assertEqual(len(ctx.exception.retries), 4, "最初の呼び出しと 3 回の呼び直し")

    def test_a_non_transient_error_is_not_retried(self):
        waits = []
        got, retries = transient.with_backoff(lambda: {"status": "ERROR", "error": "cut off"}, transient.classify,
                                              sleep=waits.append)
        self.assertEqual((waits, retries), ([], []))


def stream(status, error=None, tokens=10):
    lines = [json.dumps({"event": "init", "conversation_id": "c1", "init": {"model": "m"}}),
             json.dumps({"event": "step_update", "step_update": {
                 "step_index": 1, "state": "DONE", "step_type": "agent_response",
                 "usage": {"input_tokens": tokens, "output_tokens": 1}}}),
             json.dumps({"event": "result", "result": {"status": status, "error": error, "response": "ok"}})]
    return "\n".join(lines)


class PipelineRetry(unittest.TestCase):
    IMP = {"cli": "agy", "headless_flag": "-p", "auto_approve_flag": "-y", "model_flag": "--model",
           "model_name": "m", "output_format_args": ["--output-format", "stream-json"]}

    def ctx(self, d):
        return SimpleNamespace(unit={"prompt": "作る", "whitelist": ["a.cs"]}, sandbox=Path(d), sb=lambda rel: Path(d) / rel,
                               cfg={"implementer": self.IMP}, ttl={"implementer": 300}, metrics={"attempt": 1},
                               cur={}, tel={}, out=Path(d) / "out")

    def test_a_rate_limit_is_retried_and_recorded_in_the_same_call(self):
        outs = [stream("ERROR", "429 RESOURCE_EXHAUSTED", 7), stream("SUCCESS")]
        with tempfile.TemporaryDirectory() as d:
            c = self.ctx(d)
            with mock.patch.object(pipeline, "run", side_effect=lambda *a, **k: (0, outs.pop(0), "")), \
                    mock.patch.object(pipeline, "resolve_cli", side_effect=lambda n: [n]), \
                    mock.patch.object(pipeline, "implementer_log_dir", return_value=Path(d) / "logs"), \
                    mock.patch.object(pipeline.transient.time, "sleep"):
                pipeline.call_implementer(c)
            logs = sorted(p.name for p in (Path(d) / "logs").iterdir())
        rec = c.cur["implementer"]
        self.assertEqual(rec["transient"][0]["usage"]["input_tokens"], 7, "一時的な失敗の利用量を残す")
        self.assertEqual(rec["usage"]["input_tokens"], 10, "呼び出しの利用量は、成功した呼び出しのもの")
        self.assertEqual(logs, ["implementer_attempt_1.log", "implementer_attempt_1_transient1.log"])

    def test_three_failed_retries_abort_and_mark_the_telemetry(self):
        with tempfile.TemporaryDirectory() as d:
            c = self.ctx(d)
            with mock.patch.object(pipeline, "run", side_effect=lambda *a, **k: (0, stream("ERROR", "503 UNAVAILABLE"), "")), \
                    mock.patch.object(pipeline, "resolve_cli", side_effect=lambda n: [n]), \
                    mock.patch.object(pipeline, "implementer_log_dir", return_value=Path(d) / "logs"), \
                    mock.patch.object(pipeline.transient.time, "sleep"), \
                    self.assertRaises(SystemExit) as ctx:
                pipeline.call_implementer(c)
        self.assertIn("ABORT", str(ctx.exception.code))
        self.assertTrue(c.tel["transient_abort"])
        self.assertEqual(len(c.cur["implementer"]["transient"]), 4)


class DriverReplicate(unittest.TestCase):
    def test_condition_b_transient_abort_stops_the_replicate(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            ctx = {"out": out, "m": {"_base": d}, "wt": out / "wt", "sandbox": out / "sb"}

            def runner(args, cwd, ttl, label):
                tel = Path(args[args.index("--telemetry") + 1])
                tel.write_text(json.dumps({"transient_abort": "一時的な失敗が 4 回続きました"}), encoding="utf-8")
                return 2, "", ""
            with self.assertRaises(common.ReplicateStop):
                driver.run_task_b(ctx, {"id": "T1", "unit": "u.json"}, {}, {}, runner=runner)

    def test_run_all_skips_the_rest_of_a_stopped_replicate(self):
        seen = []

        def fake(manifest, cond, run_id):
            seen.append((run_id, cond))
            if (run_id, cond) == ("p-01", "A"):
                raise common.ReplicateStop("429")
        with mock.patch.object(driver, "run_condition", side_effect=fake), mock.patch("builtins.print"):
            rc = driver.run_all("m.json", 2, "p")
        self.assertEqual(seen, [("p-01", "A"), ("p-02", "B"), ("p-02", "A")], "p-01 の B は飛ばし、次の繰り返しへ")
        self.assertNotEqual(rc, 0)


class Environment(unittest.TestCase):
    PINNED = {"os": "Microsoft Windows NT 10.0.26200.0", "python": "3.12.10", "python_packages": {"pyyaml": "6.0.3"},
              "cli": {"agy": "1.2.11", "claude": "2.1.258", "dotnet": "8.0.413"}}

    def measured(self, **over):
        m = json.loads(json.dumps({k: v for k, v in self.PINNED.items()}))
        for k, v in over.items():
            group, _, name = k.partition("__")
            if name:
                m[group][name] = v
            else:
                m[group] = v
        return m

    def test_the_repository_pins_match_the_protocol_and_the_lock(self):
        pinned = envcheck.load_pinned()
        self.assertEqual({k: v for k, v in pinned.items() if not k.startswith("_")}, self.PINNED)
        self.assertEqual(envcheck.lock_packages(), {"pyyaml": "6.0.3"})

    def test_matching_environment_passes_and_is_written(self):
        with tempfile.TemporaryDirectory() as d:
            rec = envcheck.require(Path(d) / "env.json", measured=self.measured(), pinned=self.PINNED)
            saved = json.loads((Path(d) / "env.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["problems"], [])
        self.assertEqual(saved["measured"]["cli"]["agy"], "1.2.11")
        self.assertIn("CLI で指定できない", saved["sampling"], "サンプリングの値は推測で書かない")

    def test_any_mismatch_refuses_to_start_and_is_still_recorded(self):
        for over in ({"os": "Microsoft Windows NT 10.0.26100.0"}, {"python": "3.12.11"},
                     {"cli__agy": "1.2.12"}, {"cli__claude": None}, {"cli__dotnet": "9.0.100"},
                     {"python_packages__pyyaml": "6.0.2"}):
            with self.subTest(over=over), tempfile.TemporaryDirectory() as d:
                with self.assertRaises(envcheck.EnvError):
                    envcheck.require(Path(d) / "env.json", measured=self.measured(**over), pinned=self.PINNED)
                self.assertTrue(json.loads((Path(d) / "env.json").read_text(encoding="utf-8"))["problems"],
                                "止めた実行も、実測値を残す")

    def test_cli_version_reads_the_first_version(self):
        fake = lambda *a, **k: SimpleNamespace(returncode=0, stdout="2.1.258 (Claude Code)\n", stderr="")
        self.assertEqual(envcheck.cli_version("claude", run=fake), "2.1.258")
        failed = lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="not found")
        self.assertIsNone(envcheck.cli_version("agy", run=failed))

    def test_the_driver_refuses_to_start_in_another_environment(self):
        with mock.patch.object(driver.envcheck, "require", side_effect=envcheck.EnvError("agy：実測 1.2.12")), \
                mock.patch.object(driver, "run_condition") as rc_, mock.patch("builtins.print"):
            rc = driver.main(["run", "--condition", "B", "--run-id", "x"])
        self.assertNotEqual(rc, 0)
        rc_.assert_not_called()


if __name__ == "__main__":
    unittest.main()
