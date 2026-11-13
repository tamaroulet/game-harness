"""エージェントの CLI の引数と、利用枠切れ（429）の検出（harness/agent_cli.py）。

    python -m unittest tests.test_agent_cli -v

claude の入力は、2026-10-06 の走行で CLI が実際に返した標準出力と標準エラーをそのまま写したもの（tests/fixtures/claude_quota_429.*）。
本物の CLI は呼ばない。
"""
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "harness"))
import agent_cli  # noqa: E402

OUT = (HERE / "fixtures" / "claude_quota_429.stdout.json").read_text(encoding="utf-8")
ERR = (HERE / "fixtures" / "claude_quota_429.stderr.txt").read_text(encoding="utf-8")
RAW = "You've hit your session limit · resets 12am (Asia/Tokyo)"
OK = json.dumps({"result": "できた", "modelUsage": {"claude-sonnet-5-5": {}}, "num_turns": 3})


def ev(name, body):
    return json.dumps({"event": name, name: body})


def stream(error=None, text=""):
    """agy の stream-json（手番 1 つと result）。text は応答の本文として最後に足す行。"""
    step = {"step_index": 0, "step_type": "tool_call", "state": "DONE", "tool_name": "view_file",
            "usage": {"input_tokens": 100, "output_tokens": 10, "thinking_tokens": 2, "cache_read_tokens": 50}}
    result = {"status": "ERROR" if error else "SUCCESS", "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}}
    if error:
        result["error"] = error
    lines = [ev("init", {"model": "m", "conversation_id": "c1"}), ev("step_update", step), ev("result", result)]
    return "\n".join(lines + ([text] if text else [])) + "\n"


class Args(unittest.TestCase):
    def test_the_model_is_passed_with_its_flag_and_the_prompt_is_not_an_argument(self):
        agent = {"headless_flag": "-p", "model_flag": "--model", "model": "claude-opus-5-5",
                 "extra_flags": ["--tools", "Read"], "output_format_args": ["--output-format", "json"]}
        self.assertEqual(agent_cli.args(agent, ["claude"]),
                         ["claude", "-p", "--model", "claude-opus-5-5", "--tools", "Read", "--output-format", "json"])


class ClaudeQuota(unittest.TestCase):
    def test_the_fixture_is_the_raw_message(self):
        self.assertEqual(json.loads(OUT)["result"], RAW)

    def test_the_message_is_found_in_the_json_result_and_not_in_other_outputs(self):
        self.assertEqual(agent_cli.quota_message(OUT, ERR), RAW)
        self.assertEqual(agent_cli.quota_message("not json", "Error: You've hit your usage limit · resets 3pm"),
                         "Error: You've hit your usage limit · resets 3pm")
        for out, err in ((OK, ERR), ("", ""), ("not json", "rate limit"), ('{"result": "ターン数の上限"}', "")):
            self.assertIsNone(agent_cli.quota_message(out, err), out)

    def test_claude_judgement_ignores_agy_phrases(self):
        self.assertIsNone(agent_cli.quota_message("Quota exceeded", "429 Too Many Requests"))
        self.assertIsNotNone(agent_cli.quota_message("", "You hit your limit"))


class AgyQuota(unittest.TestCase):
    def test_each_agy_phrase_is_quota_in_stdout_or_stderr(self):
        for text in ("RESOURCE_EXHAUSTED: x", "Quota exceeded for y", "Rate limit exceeded"):
            self.assertIn(text.split(":")[0], agent_cli.quota_message(stream(), text, agy=True))
            self.assertIn(text.split(":")[0], agent_cli.quota_message(stream(text=text), "", agy=True))

    def test_a_429_counts_in_stderr_or_in_the_result_error(self):
        self.assertEqual(agent_cli.quota_message("", "429 Too Many Requests", agy=True), "429 Too Many Requests")
        self.assertIn("HTTP 429", agent_cli.quota_message(stream(error="HTTP 429"), "", agy=True))

    def test_a_429_in_the_reply_text_alone_is_not_quota(self):
        for out in (stream(text="line 429 of the file"), stream(text="x = 4290"), stream(text="429")):
            self.assertIsNone(agent_cli.quota_message(out, "", agy=True))
        self.assertIsNone(agent_cli.quota_message(stream(), "line 4290 x429", agy=True))


if __name__ == "__main__":
    unittest.main()
