"""鎖の段の計装（harness/chain_metrics.py。v3 §15 の 11-08 その 4 の決め 6）：走りのディレクトリから費用・時間・手番・コマンド・
門の働きを数えること、走り直しの試しを足すこと、finish_chain が公開の集計の段の行に足すこと、延長の前の試行を残すこと
（hline_extend・bench_run.pass_rates）、鎖の記録に予定の段の数を書くこと。箱・実装役・採点は呼ばない。"""
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import bench_chain  # noqa: E402
import bench_run  # noqa: E402
import bench_solo  # noqa: E402
import chain_metrics  # noqa: E402
import chain_score  # noqa: E402
import hline_base  # noqa: E402
import hline_extend  # noqa: E402

SPLIT = bench_run.LOG_SPLIT


def step(i, kind="tool", state="DONE", secs=None, usage=None):
    su = {"step_index": i, "step_type": kind, "state": state}
    if secs is not None:
        su["duration_seconds"] = secs
    if usage is not None:
        su["usage"] = usage
    return json.dumps({"event": "step_update", "step_update": su})


def log_text(lines, head=None):
    return json.dumps(head if head is not None else {"usage": {}, "commands_run": 3}) + SPLIT + "\n".join(lines) + SPLIT + ""


STREAM = [step(0, "user_input"), step(1, "agent_response", "ACTIVE"), step(1, "agent_response", secs=2.0, usage={"input_tokens": 5}),
          step(2, secs=1.5), step(3, "agent_response", secs=4.0, usage={"input_tokens": 7}), "not json",
          json.dumps({"event": "result", "result": {"duration_seconds": 9.25}})]


def write_run(rundir, run, env=None, queue=None, logs=()):
    """走りのディレクトリ（factory/run.json・env.json・factory/queue.json・items/<What>/implementer-*.log）を書く。"""
    (rundir / "factory").mkdir(parents=True, exist_ok=True)
    hline_base.write_json(rundir / "factory" / "run.json", run)
    if env is not None:
        hline_base.write_json(rundir / "env.json", env)
    if queue is not None:
        hline_base.write_json(rundir / "factory" / "queue.json", queue)
    for name, text in logs:
        p = rundir / "items" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


TOKENS = {"input_tokens": 100, "cache_creation_tokens": 0, "cache_read_tokens": 1000, "output_tokens": 20, "total": 1120}
ENV = {"started_at": "2026-11-08T12:00:00+09:00", "finished_at": "2026-11-08T12:10:30+09:00", "quota_wait_seconds": 30}


class Stream(unittest.TestCase):
    def test_steps_answers_and_the_result_seconds(self):
        self.assertEqual(chain_metrics.stream_facts("\n".join(STREAM)), (4, 2, 9.2))

    def test_without_a_result_the_step_seconds_are_added(self):
        self.assertEqual(chain_metrics.stream_facts("\n".join(STREAM[:-1])), (4, 2, 7.5))

    def test_the_summary_gives_the_commands_and_a_broken_summary_gives_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "implementer-1.log"
            p.write_text(log_text(STREAM), encoding="utf-8")
            self.assertEqual(chain_metrics.log_facts(p), {"turns": 4, "model_turns": 2, "agent_seconds": 9.2, "commands": 3})
            p.write_text("not json" + SPLIT + "\n".join(STREAM), encoding="utf-8")
            self.assertIsNone(chain_metrics.log_facts(p)["commands"])


class Runs(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out = Path(tmp.name)

    def rundir(self, run_id):
        return bench_run.run_dir(self.out, "B07", run_id)

    def test_an_a_run_counts_tokens_time_turns_commands_and_dropped_files(self):
        write_run(self.rundir("t-s1"), {"tokens": TOKENS, "dropped": 2, "stop": "done"}, ENV,
                  logs=[("P-01/implementer-1.log", log_text(STREAM))])
        got = chain_metrics.fields(self.out, "B07", [{"k": 1, "run_id": "t-s1"}])
        self.assertEqual(got["cost"], {"input": 100, "output": 20, "cache_read": 1000, "cache_creation": 0, "total": 1120, "non_cache": 120,
                                       "seconds": 630.0, "agent_seconds": 9.2, "wait_seconds": 30, "turns": 4, "model_turns": 2,
                                       "commands": 3, "calls": 1, "dropped": 2, "runs": 1})
        self.assertIsNone(got["gate"])

    def test_b_counts_the_gate_from_the_queue(self):
        tries = [{"gate": False, "failure": {"class": "full_suite"}, "box_dropped": ["x.py", "y.py"]}, {"gate": True}]
        write_run(self.rundir("t-s1"), {"tokens": TOKENS}, ENV, {"items": {"P-01": {"status": "done", "tries": tries}}},
                  [("a/implementer-0-1.log", log_text(STREAM)), ("a/implementer-0-2.log", log_text(STREAM))])
        got = chain_metrics.fields(self.out, "B07", [{"k": 1, "run_id": "t-s1"}])
        self.assertEqual(got["gate"], {"passed": True, "first": False, "tries": 2, "extended": 0, "full_suite": 1})
        self.assertEqual((got["cost"]["calls"], got["cost"]["turns"], got["cost"]["dropped"]), (2, 8, 2))

    def test_the_tries_before_an_extension_are_counted_and_an_old_extension_fails_the_first_try(self):
        item = {"status": "done", "extended": {"passed": 3}, "tries_before": [{"gate": False}, {"gate": False}], "tries": [{"gate": True}]}
        write_run(self.rundir("t-s1"), {"tokens": TOKENS}, queue={"items": {"P-01": item}})
        self.assertEqual(chain_metrics.gate_facts(self.rundir("t-s1")), {"passed": True, "first": False, "tries": 3, "extended": 1, "full_suite": 0})
        old = {"status": "done", "extended": {"passed": 3}, "tries": [{"gate": True}]}   # tries_before の無い前の記録
        write_run(self.rundir("t-s2"), {"tokens": TOKENS}, queue={"items": {"P-01": old}})
        self.assertFalse(chain_metrics.gate_facts(self.rundir("t-s2"))["first"])

    def test_retried_attempts_are_added_and_the_gate_is_the_last_ones(self):
        write_run(self.rundir("t-s1"), {"tokens": {**TOKENS, "input_tokens": 1, "output_tokens": 2}}, ENV,
                  {"items": {"P-01": {"status": "unconverged", "tries": [{"gate": False}]}}})
        write_run(self.rundir("t-s1-r1"), {"tokens": TOKENS}, ENV, {"items": {"P-01": {"status": "done", "tries": [{"gate": True}]}}})
        attempts = [{"k": 1, "run_id": "t-s1"}, {"k": 1, "run_id": "t-s1-r1"}, {"k": 1, "run_id": "t-s1-r2"}]   # r2 は走りのディレクトリが無い
        got = chain_metrics.fields(self.out, "B07", attempts)
        self.assertEqual((got["cost"]["runs"], got["cost"]["non_cache"], got["cost"]["seconds"]), (2, 123, 1260.0))
        self.assertEqual((got["gate"]["passed"], got["gate"]["first"]), (True, True))

    def test_a_missing_or_broken_record_gives_none_not_an_error(self):
        write_run(self.rundir("t-s1"), {"tokens": {"total": 50}}, {"started_at": "x"})
        got = chain_metrics.fields(self.out, "B07", [{"k": 1, "run_id": "t-s1"}])
        self.assertEqual((got["cost"]["total"], got["cost"]["non_cache"], got["cost"]["seconds"], got["cost"]["calls"]), (50, None, None, 0))
        self.assertEqual(chain_metrics.fields(self.out, "B07", [{"k": 1, "run_id": "nothing"}]),
                         {"cost": {**dict.fromkeys(chain_metrics.SUMS), "runs": 0}, "gate": None})


class Finish(unittest.TestCase):
    def test_the_public_row_gets_the_cost_and_gate_and_the_record_its_planned_stages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "B07" / "s0" / "hidden").mkdir(parents=True)
            write_run(bench_run.run_dir(root, "B07", "t-s1"), {"tokens": TOKENS}, ENV)
            rec = {"tag": "t", "set": "dev", "base": "B07", "rev": "r", "planned": 6,
                   "stages": [{"k": 1, "attempt": 0, "run_id": "t-s1", "invalid": "REQ-22", "head": None, "tokens": 1120}]}
            with patch.object(chain_score, "grade", return_value={}):
                public = chain_score.finish_chain(rec, root, root, {}, root / "priv")
                old = chain_score.finish_chain({**rec, "planned": None}, root, root, {}, root / "priv")
        row = public["stages"][0]
        self.assertEqual((public["planned"], row["cost"]["non_cache"], row["cost"]["runs"], row["gate"]), (6, 120, 1, None))
        self.assertNotIn("planned", old)
        self.assertNotIn("x.py", json.dumps(public))


class Extension(unittest.TestCase):
    def test_an_extension_keeps_the_earlier_tries_and_the_rates_count_them(self):
        item = {"tries": [{"gate": False}, {"gate": False, "failure": {"class": "test"}}]}
        hline_extend.extend(item, {"passed": 3, "failed": 1, "previous_passed": 2})
        self.assertEqual([t["gate"] for t in item["tries_before"]], [False, False])
        item["tries"] = [{"gate": True}]   # 次の処理が tries を数え直した後
        self.assertEqual(bench_run.pass_rates({"P-01": item}), (0.0, 1.0))
        self.assertEqual(bench_run.pass_rates({"P-01": {"tries": [{"gate": True}]}}), (1.0, None))


class Planned(unittest.TestCase):
    def test_both_runners_write_the_planned_stages(self):
        entry = {"base": "B07", "package": "pkg", "stages": 6}
        with patch.object(bench_chain, "s0_commit", return_value="S0"), patch.object(bench_chain, "run_step", return_value=None):
            rec = bench_chain.run_chain(types.SimpleNamespace(tag="t"), "repo", "out", Path("staged"), "rev", entry, "dev")
        self.assertEqual((rec["planned"], rec["stopped"]), (6, "s1"))
        ctx = types.SimpleNamespace(tag="t", repo="repo", out="out", staged=Path("staged"), entry=entry, base="B07", pkg="pkg")
        with patch.object(bench_chain, "s0_commit", return_value="S0"), patch.object(bench_solo, "run_step", return_value=None):
            rec = bench_solo.run_chain(ctx, "rev", "dev")
        self.assertEqual((rec["planned"], rec["arm"], rec["stopped"]), (6, "solo", "s1"))


if __name__ == "__main__":
    unittest.main()
