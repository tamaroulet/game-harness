"""v2.1（docs/design/v2_1_improvement_plan.md）：実装役の呼び出しを小さく・速く・測れるようにする。

    python -m unittest tests.test_v21

**なぜ要るか**: v2-dry-02 の B は、実装役の 1 回が 245〜300 秒かかり、2 回が TTL で打ち切られて利用量を失った。
試行 1 は whitelist の外（Board.cs）への変更で丸ごと捨てた。ここでは、agy も dotnet も使わずに次を縛る。
- stream-json の手番ごとの利用量を読み、打ち切られても終わった手番までは残す（下限の印つき）
- 打ち切ったときも、そこまでの標準出力を捨てない（proc.run）
- 書き換えてよいファイルと契約の中身をプロンプトに埋め込む（実装役が読む手番をなくす）
- whitelist の外への変更を、内側ループで取り消して知らせる（試行を捨てない）
"""
import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import agy_stream  # noqa: E402
import implementer_context  # noqa: E402
import pipeline  # noqa: E402
import proc  # noqa: E402
from ab import driver, report  # noqa: E402


def ev(**kw):
    return json.dumps(kw)


def step(i, state, stype="agent_response", usage=None, **extra):
    su = {"conversation_id": "c1", "step_index": i, "state": state, "step_type": stype, **extra}
    if usage:
        su["usage"] = usage
        su["duration_seconds"] = 2.5
    return ev(event="step_update", step_update=su)


U1 = {"input_tokens": 13000, "output_tokens": 200, "thinking_tokens": 150, "cache_read_tokens": 0}
U2 = {"input_tokens": 20000, "output_tokens": 300, "thinking_tokens": 100, "cache_read_tokens": 5000}
IMP = {"cli": "agy", "headless_flag": "-p", "auto_approve_flag": "--dangerously-skip-permissions",
       "model_flag": "--model", "model_name": "m", "output_format_args": ["--output-format", "stream-json"],
       "extra_flags": ["--sandbox"], "usage_format": "agy"}


class StreamParse(unittest.TestCase):
    def test_complete_stream_uses_the_result_total(self):
        text = "\n".join([ev(event="init", conversation_id="c1", init={"model": "m"}),
                          step(0, "DONE", "user_input"),
                          step(1, "DONE", usage=U1, text_delta="編集"),
                          step(2, "DONE", "tool_call", usage=U2, tool_name="edit_file"),
                          ev(event="result", result={"conversation_id": "c1", "response": "done", "num_turns": 2,
                                                     "usage": {"input_tokens": 33000, "output_tokens": 500,
                                                               "thinking_tokens": 250, "cache_read_tokens": 5000,
                                                               "total_tokens": 33500}})])
        r = agy_stream.parse(text)
        self.assertTrue(r["complete"])
        self.assertEqual(r["conversation_id"], "c1")
        self.assertEqual(r["response"], "done")
        self.assertEqual((r["usage"]["input_tokens"], r["usage"]["partial"], r["usage"]["model_steps"]), (33000, False, 2))
        self.assertEqual([s.get("tool") for s in r["steps"]], [None, None, "edit_file"])
        self.assertNotIn("text_delta", json.dumps(r["steps"]), "本文は手番の記録に残さない")

    def test_cut_stream_keeps_finished_steps_as_a_lower_bound(self):
        text = "\n".join([step(0, "DONE", "user_input"), step(1, "DONE", usage=U1), step(2, "DONE", usage=U2),
                          step(3, "ACTIVE")])
        u = agy_stream.parse(text)["usage"]
        self.assertTrue(u["partial"])
        self.assertEqual((u["input_tokens"], u["output_tokens"], u["cache_read_tokens"]), (33000, 500, 5000))
        self.assertEqual((u["model_steps"], u["lost_steps"]), (2, 1))

    def test_nothing_parsable_is_unknown_not_zero(self):
        u = agy_stream.parse("TTL超過")["usage"]
        self.assertIsNone(u["input_tokens"])
        self.assertTrue(u["partial"])

    def test_args_carry_print_timeout_sandbox_effort_and_conversation(self):
        a = agy_stream.args(dict(IMP, effort="medium"), ["agy"], "P", 300, "c1")
        self.assertEqual(a[:6], ["agy", "-p", "P", "--dangerously-skip-permissions", "--model", "m"])
        for pair in (["--output-format", "stream-json"], ["--print-timeout", "280s"], ["--effort", "medium"],
                     ["--conversation", "c1"]):
            i = a.index(pair[0])
            self.assertEqual(a[i:i + 2], pair)
        self.assertIn("--sandbox", a)
        self.assertNotIn("--effort", agy_stream.args(IMP, ["agy"], "P", 300))


class TimeoutKeepsOutput(unittest.TestCase):
    def test_partial_stdout_survives_the_ttl(self):
        code = "import sys, time; print('STEP_DONE', flush=True); time.sleep(30)"
        rc, out, err = proc.run([sys.executable, "-c", code], ROOT, 3, "t")
        self.assertEqual(rc, 124)
        self.assertIn("STEP_DONE", out)
        self.assertIn("TTL超過", err)


class Embedding(unittest.TestCase):
    def test_contract_files_are_found_by_marker_and_blocks_embed_contents(self):
        with tempfile.TemporaryDirectory() as d:
            core = Path(d) / "Core"
            core.mkdir()
            (core / "GameState.cs").write_text("class GameState {}", encoding="utf-8")
            (core / "IGameState.cs").write_text(implementer_context.CONTRACT_MARKER + " …\ninterface IGameState {}",
                                                encoding="utf-8")
            (core / "IGameState.cs.meta").write_text(implementer_context.CONTRACT_MARKER, encoding="utf-8")
            contracts = implementer_context.contract_files(d, "Core")
            self.assertEqual(contracts, ["Core/IGameState.cs"])
            text = implementer_context.blocks(d, ["Core/GameState.cs", "Core/Board.cs"], contracts)
        self.assertIn("class GameState {}", text)
        self.assertIn("interface IGameState {}", text)
        self.assertIn("Core/Board.cs（書き換えてよいファイル）\nまだありません", text)
        self.assertIn("読み取り専用", text)

    def test_stream_call_embeds_files_and_records_steps(self):
        stream = "\n".join([step(1, "DONE", usage=U1), ev(event="result", result={
            "response": "ok", "usage": {"input_tokens": 13000, "output_tokens": 200, "cache_read_tokens": 0,
                                        "thinking_tokens": 150, "total_tokens": 13200}})])
        seen = {}
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "Core").mkdir()
            (Path(d) / "Core" / "GameState.cs").write_text("class GameState {}", encoding="utf-8")
            c = SimpleNamespace(unit={"prompt": "作る", "whitelist": ["Core/GameState.cs"]}, sandbox=Path(d),
                                sb=lambda rel: Path(d) / rel, cfg={"implementer": IMP, "project": {"impl_dir": "Core"}},
                                ttl={"implementer": 300}, metrics={}, cur={})

            def fake_run(args, cwd, ttl, label):
                seen["prompt"] = args[2]
                return 0, stream, ""
            with mock.patch.object(pipeline, "run", side_effect=fake_run), \
                    mock.patch.object(pipeline, "resolve_cli", side_effect=lambda n: [n]), \
                    mock.patch.object(pipeline, "write_implementer_log"):
                ok, _ = pipeline.call_implementer(c)
        self.assertTrue(ok)
        self.assertIn("class GameState {}", seen["prompt"])
        self.assertEqual(c.cur["implementer"]["usage"]["input_tokens"], 13000)
        self.assertEqual(len(c.cur["implementer"]["steps"]), 1)
        self.assertEqual(c.last_implementer_out, "ok")


class WiderContext(unittest.TestCase):
    """v2.1b：base の既存の型まで埋め込む（v2-dry-03 の実装役はそれらを読みに行った）。
    v2.1c：仕様書の抜き出しはやめた（tests/test_v21c.py）。"""

    def test_existing_type_files_are_embedded_before_the_editable_files(self):
        unit = {"whitelist": ["Core/GameState.cs"], "prompt": "- P-T1-01（RL-16）：前提 … のとき …",
                "interface": {"types": [{"name": "ActiveMino"}, {"name": "GameState"}, {"name": "Cell"}]}}
        with tempfile.TemporaryDirectory() as d:
            core = Path(d) / "Core"
            core.mkdir()
            (core / "GameState.cs").write_text("class GameState {}", encoding="utf-8")
            (core / "ActiveMino.cs").write_text("struct ActiveMino {}", encoding="utf-8")
            self.assertEqual(implementer_context.type_files(d, unit, "Core"), ["Core/ActiveMino.cs"],
                             "whitelist のものと、base に無い型（Cell）は除く")
            text = implementer_context.for_unit(d, unit, "Core")
        self.assertIn("struct ActiveMino {}", text)
        self.assertLess(text.index("struct ActiveMino {}"), text.index("class GameState {}"),
                        "変わらない前置き（読み取り専用）を先に置く")
        self.assertNotIn("仕様の抜き出し", text)


class ImplementerLogPerCall(unittest.TestCase):
    def test_second_call_in_the_same_attempt_gets_its_own_file(self):
        with tempfile.TemporaryDirectory() as d:
            c = SimpleNamespace(tel_path=Path(d) / "t.json", out=Path(d), sandbox=Path(d),
                                cfg={"implementer": {"cli": "agy", "model_name": "m"}}, cur={})
            pipeline.write_implementer_log(c, 1, "P1", 0, "o1", "")
            c.cur["implementer_calls"] = [{"rc": 0}]
            pipeline.write_implementer_log(c, 1, "P2", 0, "o2", "")
            self.assertIn("P1", (Path(d) / "implementer_attempt_1.log").read_text(encoding="utf-8"))
            self.assertIn("P2", (Path(d) / "implementer_attempt_1_call2.log").read_text(encoding="utf-8"))


class InnerWhitelist(unittest.TestCase):
    def test_whitelist_is_checked_right_after_the_implementer_and_before_tests(self):
        src = inspect.getsource(pipeline.attempt)
        i_call, i_inner = src.index("call_implementer(c, inner_feedback)"), src.index('c.gate = "whitelist_inner"')
        i_tests = src.index("run_fast_tests(c, f\"impl_fast_turn_{turn}\")")
        self.assertLess(i_call, i_inner)
        self.assertLess(i_inner, i_tests)
        body = src[i_inner:i_tests]
        self.assertIn("purge_unwhitelisted_in_sandbox(c)", body, "外への変更は取り消す")
        self.assertIn("inner_feedback = msg", body, "試行を捨てずに次のターンで知らせる")


class LowerBound(unittest.TestCase):
    def test_partial_usage_is_marked_in_tokens_and_report(self):
        calls = [{"usage": {"input_tokens": 10, "output_tokens": 1, "cache_read_tokens": 0, "partial": True}}]
        tok = driver._tokens(calls)
        self.assertTrue(tok["partial"])
        row = {"run_id": "r", "condition": "B", "task": "T1", "index": 1, "accepted": True, "attempts": 1,
               "p2p_broken": 0, "invariants": {"failures": 0}, "diff": {"added": 1, "deleted": 0},
               "budget_exceeded": False, "seconds": 1.0, "tokens": tok}
        self.assertIn("10（下限）", report.summarize([row]))


if __name__ == "__main__":
    unittest.main()
