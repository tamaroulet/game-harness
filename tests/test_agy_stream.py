"""agy の呼び出しの引数と、stream-json の読み取り（harness/agy_stream.py）。

    python -m unittest tests.test_agy_stream -v

本物の agy は呼ばない。手番ごとの利用量・打ち切られた呼び出し・会話の続き・終わり方の記録を、作った stream-json で縛る。
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import agy_stream  # noqa: E402


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

    def test_resumed_conversation_counts_only_this_calls_steps(self):
        """会話を続けた呼び出しの result は会話全体の累計。この呼び出しの利用量は手番の足し込み。"""
        text = "\n".join([step(1, "DONE", usage=U1), step(2, "DONE", "tool_call", usage=U2, tool_name="edit_file"),
                          ev(event="result", result={"conversation_id": "c1", "response": "done", "usage": {
                              "input_tokens": 900000, "output_tokens": 280000, "thinking_tokens": 270000,
                              "cache_read_tokens": 4000000, "total_tokens": 1180000}})])
        u = agy_stream.parse(text)["usage"]
        self.assertEqual((u["input_tokens"], u["output_tokens"], u["cache_read_tokens"], u["thinking_tokens"]),
                         (33000, 500, 5000, 250))
        self.assertFalse(u["partial"])
        self.assertEqual((u["conversation_input_tokens"], u["conversation_output_tokens"]), (900000, 280000),
                         "累計は監査のために残す")

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
        # 頼みは引数に載せず標準入力で渡す（Windows のコマンドラインの上限）
        self.assertEqual(a[:7], ["agy", "-p=", "--input-format", "stream-json", "--dangerously-skip-permissions",
                                 "--model", "m"])
        self.assertNotIn("P", a)
        self.assertEqual(json.loads(agy_stream.stdin_for(IMP, "P" * 40000)),
                         {"event": "user", "message": {"content": "P" * 40000}})
        self.assertIsNone(agy_stream.stdin_for({}, "P"), "stream-json でなければ標準入力は塞いだまま")
        for pair in (["--output-format", "stream-json"], ["--print-timeout", "280s"], ["--effort", "medium"],
                     ["--conversation", "c1"]):
            i = a.index(pair[0])
            self.assertEqual(a[i:i + 2], pair)
        self.assertIn("--sandbox", a)
        self.assertNotIn("--effort", agy_stream.args(IMP, ["agy"], "P", 300))


class Outcome(unittest.TestCase):
    def step(self, i, stype, think=None, out=None):
        su = {"step_index": i, "state": "DONE", "step_type": stype}
        if out is not None:
            su["usage"] = {"input_tokens": 10, "output_tokens": out, "thinking_tokens": think}
        return json.dumps({"event": "step_update", "step_update": su})

    def test_thinking_only_step_error_step_and_status_are_recorded(self):
        text = "\n".join([self.step(1, "agent_response", 13122, 13122), self.step(2, "error_message"),
                          self.step(3, "agent_response", 500, 900),
                          json.dumps({"event": "result", "result": {
                              "status": "ERROR", "error": "Your previous response was cut off\nRetries remaining: 3",
                              "usage": {"input_tokens": 20}}})])
        oc = agy_stream.parse(text)["outcome"]
        self.assertEqual(oc, {"status": "ERROR", "error": "Your previous response was cut off",
                              "error_steps": 1, "thinking_only_steps": 1})

    def test_cut_call_has_no_status(self):
        self.assertIsNone(agy_stream.parse(self.step(1, "agent_response", 5, 9))["outcome"]["status"])


if __name__ == "__main__":
    unittest.main()
