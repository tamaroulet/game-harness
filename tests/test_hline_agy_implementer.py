"""設定の実装役が agy のとき、H ラインが agy を呼び、利用量・モデル・利用枠切れ・呼び出しの記録を claude と同じ形で残す（W-98）の試験。
本物の claude・agy の CLI と導入先は使わず、host.proc.run を偽物に替える。"""
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import agy_stream  # noqa: E402
import bench_run  # noqa: E402
import hline_abort  # noqa: E402
import hline_agy  # noqa: E402
import hline_prompt  # noqa: E402
import hline_quota  # noqa: E402
from hline_base import Infra, Quota  # noqa: E402

MODEL = "gemini-3.8-flash-medium"
SPLIT = "\n--- stderr ---\n"


def ev(name, body):
    return json.dumps({"event": name, name: body})


def stream(model=MODEL, error=None, text=""):
    step1 = {"step_index": 0, "step_type": "tool_call", "state": "DONE", "tool_name": "view_file",
             "usage": {"input_tokens": 100, "output_tokens": 10, "thinking_tokens": 2, "cache_read_tokens": 50}}
    step2 = {"step_index": 1, "step_type": "tool_call", "state": "DONE", "tool_name": "run_command",
             "tool_info": {"parameters": {"CommandLine": "git status --short"}},
             "usage": {"input_tokens": 200, "output_tokens": 20, "thinking_tokens": 3, "cache_read_tokens": 60}}
    result = {"status": "ERROR" if error else "SUCCESS", "usage": {"input_tokens": 300, "output_tokens": 30, "total_tokens": 330}}
    if error:
        result["error"] = error
    lines = [ev("init", {"model": model, "conversation_id": "c1"}), ev("step_update", step1), ev("step_update", step2),
             ev("result", result)]
    return "\n".join(lines + ([text] if text else [])) + "\n"


def stream_without_command():
    return "\n".join(line for line in stream().splitlines() if "CommandLine" not in line) + "\n"


def agy_cfg():
    imp = json.loads((ROOT / "config" / "bench_implementer_agy.json").read_text(encoding="utf-8"))
    return {**json.loads((ROOT / "config" / "hline.json").read_text(encoding="utf-8")), "implementer": imp}


CLAUDE_OUT = json.dumps({"result": "ok", "num_turns": 3, "total_cost_usd": 0.1, "session_id": "s1",
                         "usage": {"input_tokens": 5, "output_tokens": 7, "cache_read_input_tokens": 11, "cache_creation_input_tokens": 13},
                         "modelUsage": {"claude-sonnet-5-5": {}}})


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.wt = self.root / "wt"
        self.wt.mkdir()
        self.calls = []
        self.reply = (0, stream(), "")
        self.host = types.SimpleNamespace(
            symbolmap=types.SimpleNamespace(prompt_text=lambda *a, **k: ""), build_prompt=lambda what, fb, shown: "PROMPT",
            proc=types.SimpleNamespace(run=self.fake_run, resolve_cli=lambda cli: [cli]))
        for target in (mock.patch.object(hline_agy.agy_pinned, "require", return_value={}),):
            target.start()
            self.addCleanup(target.stop)

    def fake_run(self, args, cwd, ttl, label, env=None, input=None):
        self.calls.append((args, input))
        return self.reply

    def implement(self, cfg=None, name="implementer-0-1.log"):
        self.log = self.root / "out" / name
        self.log.parent.mkdir(exist_ok=True)
        return hline_prompt.implement(self.host, cfg or agy_cfg(), self.wt, "WHAT", None, self.log)


class AgyCall(Base):
    def test_the_attempt_record_has_four_usage_parts_and_the_reported_model(self):
        code, models, usage, cutoff, turns = self.implement()
        args, stdin = self.calls[0]
        self.assertEqual((code, models, cutoff, turns), (0, [MODEL], None, 2))
        self.assertEqual({k: usage[k] for k in ("input_tokens", "cache_creation_tokens", "cache_read_tokens", "output_tokens", "total_tokens")},
                         {"input_tokens": 300, "cache_creation_tokens": None, "cache_read_tokens": 110, "output_tokens": 30, "total_tokens": 330})
        self.assertIn("--sandbox", args)
        self.assertNotIn("--disallowedTools", args)
        self.assertNotIn("--max-turns", args)
        self.assertEqual(stdin, agy_stream.stdin_for(agy_cfg()["implementer"], "PROMPT"))

    def test_the_sandbox_flag_is_added_even_if_the_config_lacks_it(self):
        cfg = agy_cfg()
        cfg["implementer"]["extra_flags"] = []
        self.implement(cfg)
        self.assertEqual(self.calls[0][0].count("--sandbox"), 1)

    def test_log_calls_reads_the_agy_call_like_a_claude_one(self):
        self.implement()
        calls = bench_run.log_calls(self.log.parent, set())
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertEqual((call["role"], call["prompt"]), ("implementer", "PROMPT"))
        self.assertEqual(call["result"]["usage"], {"input_tokens": 300, "cache_creation_input_tokens": 0,
                                                    "cache_read_input_tokens": 110, "output_tokens": 30})
        self.assertEqual(call["result"]["modelUsage"][MODEL],
                         {"inputTokens": 300, "cacheCreationInputTokens": 0, "cacheReadInputTokens": 110, "outputTokens": 30})
        self.assertEqual(bench_run.tokens_of(calls)["input_tokens"], 300)

    def test_a_digest_mismatch_is_infra(self):
        with mock.patch.object(hline_agy.agy_pinned, "require", side_effect=hline_agy.agy_pinned.AgyDigestError("違う")):
            with self.assertRaises(Infra):
                self.implement()
        self.assertEqual(self.calls, [])

    def test_a_reported_model_mismatch_is_infra(self):
        self.reply = (0, stream(model="gemini-other"), "")
        with self.assertRaises(Infra):
            self.implement()

    def test_a_missing_model_report_is_infra(self):
        self.reply = (0, "", "")
        with self.assertRaises(Infra):
            self.implement()

    def test_a_config_without_stream_json_is_infra(self):
        cfg = agy_cfg()
        cfg["implementer"]["output_format_args"] = ["--output-format", "json"]
        with self.assertRaises(Infra):
            self.implement(cfg)

    def test_a_ttl_exit_is_returned_without_models(self):
        self.reply = (124, "", "")
        self.assertEqual(self.implement(), (124, [], None, None, None))


class Commands(Base):
    def test_a_command_step_aborts_the_attempt_with_the_step_number(self):
        self.implement()
        why = hline_abort.abort_reason(self.log, "d", None)
        self.assertEqual(why, "命令の実行（REQ-09）：手番 2 run_command git")
        head = json.loads(self.log.read_text(encoding="utf-8").split(SPLIT, 1)[0])
        self.assertEqual(head["commands"], [{"step": 2, "tool": "run_command", "command": "git"}])

    def test_without_a_command_the_summary_has_no_commands_and_nothing_aborts(self):
        self.reply = (0, stream_without_command(), "")
        self.implement()
        head = json.loads(self.log.read_text(encoding="utf-8").split(SPLIT, 1)[0])
        self.assertEqual(head["commands"], [])
        self.assertIsNone(hline_abort.abort_reason(self.log, "d", None))

    def test_a_log_without_a_summary_behaves_as_before(self):
        p = self.root / "plain.log"
        p.write_text(json.dumps({"result": "ok", "num_turns": 1}) + SPLIT, encoding="utf-8")
        self.assertIsNone(hline_abort.abort_reason(p, "d", None))
        self.assertEqual(hline_abort.abort_reason(p, "d", "d"), hline_abort.REPEAT + "。直す手がかりが無いまま同じ変更を繰り返したので、残りの試行を行わずに打ち切りました")
        p.write_text(f"{hline_abort.MARKER} 無理\n", encoding="utf-8")
        self.assertEqual(hline_abort.abort_reason(p, "d", None), "無理")
        self.assertIsNone(hline_abort.abort_reason(self.root / "none.log", "d", None))

    def test_a_command_comes_before_an_abandon_declaration(self):
        self.reply = (0, stream(text=f"{hline_abort.MARKER} 無理"), "")
        self.implement()
        self.assertTrue(hline_abort.abort_reason(self.log, "d", None).startswith("命令の実行（REQ-09）"))


class AgyQuota(Base):
    def hit(self, out, err):
        self.reply = (1, out, err)
        with self.assertRaises(Quota) as cm:
            self.implement()
        return str(cm.exception)

    def test_each_agy_phrase_is_quota(self):
        for text in ("RESOURCE_EXHAUSTED: x", "Quota exceeded for y", "Rate limit exceeded"):
            self.assertIn(text.split(":")[0], self.hit(stream(), text))
            self.assertIn(text.split(":")[0], self.hit(stream(text=text), ""))
        self.assertIn("429 Too Many Requests", self.hit(stream(), "429 Too Many Requests"))
        self.assertIn("HTTP 429", self.hit(stream(error="HTTP 429"), ""))

    def test_a_429_in_the_reply_text_alone_is_not_quota(self):
        for out in (stream(text="line 429 of the file"), stream(text="x = 4290"), stream(text="429")):
            self.assertIsNone(hline_quota.message(out, "", agy=True))
            self.reply = (0, out, "")
            self.implement()   # Quota を出さない
        self.assertIsNone(hline_quota.message(stream(), "line 4290 x429", agy=True))

    def test_quota_wait_text_comes_from_the_hit(self):
        self.assertEqual(hline_quota.message("", "429 Too Many Requests", agy=True), "429 Too Many Requests")

    def test_claude_judgement_is_unchanged(self):
        self.assertIsNone(hline_quota.message("Quota exceeded", "429 Too Many Requests"))
        self.assertIsNotNone(hline_quota.message("", "You hit your limit"))


class ClaudeUnchanged(Base):
    def test_claude_args_and_record(self):
        self.reply = (0, CLAUDE_OUT, "")
        cfg = json.loads((ROOT / "config" / "hline.json").read_text(encoding="utf-8"))
        code, models, usage, cutoff, turns = self.implement(cfg)
        args, stdin = self.calls[0]
        self.assertEqual((code, models, cutoff, turns, stdin), (0, ["claude-sonnet-5-5"], None, 3, "PROMPT"))
        self.assertEqual(args[:5], ["claude", "-p", "--model", "claude-sonnet-5-5", "--permission-mode"])
        self.assertEqual(args[-2:], ["--max-turns", "15"])
        self.assertIn("--disallowedTools", args)
        self.assertNotIn("--sandbox", args)
        self.assertEqual((usage["input_tokens"], usage["cache_creation_tokens"], usage["cache_read_tokens"], usage["output_tokens"]), (5, 13, 11, 7))
        self.assertTrue(self.log.read_text(encoding="utf-8").startswith(CLAUDE_OUT + SPLIT))


if __name__ == "__main__":
    unittest.main()
