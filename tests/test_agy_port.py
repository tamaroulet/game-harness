"""実装役 agy の実行口（harness/agy_port.py）の、docker に依らない部品の試験。

    python -m unittest discover -s tests -v
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import agy_port  # noqa: E402
import agy_stream  # noqa: E402
import sandbox  # noqa: E402


def step(i, tool, state, error=None):
    """agy の stream-json の手番（道具のエラーは state: ERROR と tool_info.error。実際の agy の記録の形）。"""
    su = {"step_index": i, "step_type": "tool", "tool_name": tool, "state": state}
    if error:
        su["tool_info"] = {"name": tool, "error": {"type": "TOOL_ERROR", "message": error}}
    return {"event": "step_update", "step_update": su}


class ConfigTests(unittest.TestCase):
    def test_config_pins_model_and_stream(self):
        agent = agy_port.load_config()["agent"]
        self.assertEqual(agent["model_name"], "gemini-4-hydrogen-medium")
        self.assertEqual(agent["model_flag"], "--model")
        self.assertIn("stream-json", agent["output_format_args"])
        self.assertEqual(agent["install"]["version"], "1.5.3")

    def test_hooks_deny_every_denied_tool(self):
        hooks = json.loads((agy_port.ASSETS / "hooks.json").read_text(encoding="utf-8"))
        entries = hooks["agy-deny-tools"]["PreToolUse"]
        script = {h["matcher"]: h["hooks"][0]["command"] for h in entries}
        self.assertEqual(set(script), set(agy_port.DENIED_TOOLS))
        self.assertTrue(all(c.startswith(agy_port.HOOKS_DIR + "/") for c in script.values()))
        name = {t: c.rsplit("/", 1)[-1] for t, c in script.items()}
        self.assertEqual({t for t, s in name.items() if s == "deny_web.sh"}, set(agy_port.WEB_TOOLS))
        self.assertEqual({t for t, s in name.items() if s == "deny_subagent.sh"}, set(agy_port.SUBAGENT_TOOLS))
        for s in agy_port.HOOK_SCRIPTS:
            self.assertIn('"decision": "deny"', (agy_port.ASSETS / s).read_text(encoding="utf-8"))


class AuditTests(unittest.TestCase):
    def test_denied_tool_steps_read_state_and_hook_error(self):
        text = "\n".join(json.dumps(e) for e in [
            {"event": "init", "init": {"model": "m", "conversation_id": "c"}},
            step(0, "run_command", "DONE"),
            step(1, "search_web", "ACTIVE"),
            step(1, "search_web", "ERROR", "Tool call denied by pre-tool hook: Web access is not available in this environment."),
            step(2, "invoke_subagent", "ERROR", "tool call denied by pre-tool hook: Subagents are not available in this environment."),
            step(3, "read_url_content", "ERROR", "dial tcp: lookup example.com: no such host"),
            step(4, "webview_browser", "DONE"),
            {"event": "result", "result": {"status": "SUCCESS", "conversation_id": "c"}}])
        steps = agy_port.denied_tool_steps(agy_stream.parse(text)["steps"], agy_port.step_errors(text))
        got = {s["index"]: (s["tool"], s["state"], s["hook_denied"], s["ran"]) for s in steps}
        self.assertEqual(got, {1: ("search_web", "ERROR", True, False),        # フックが止めた：無効にしない
                               2: ("invoke_subagent", "ERROR", True, False),
                               3: ("read_url_content", "ERROR", False, False),  # ほかのエラーでも走っていない
                               4: ("webview_browser", "DONE", False, True)})    # 走った：無効

    def test_turn_valid_only_when_no_denied_tool_ran(self):
        ok = {"denied_tools": [{"ran": False}], "hooks_sha256_after": "h"}
        self.assertTrue(agy_port.turn_valid(ok, "h"))
        self.assertFalse(agy_port.turn_valid(dict(ok, denied_tools=[{"ran": False}, {"ran": True}]), "h"))
        self.assertFalse(agy_port.turn_valid(ok, "other"))
        self.assertFalse(agy_port.turn_valid(dict(ok, model_error="報告が無い"), "h"))

    def test_timeout_output_keeps_bytes_and_text(self):
        e = subprocess.TimeoutExpired(["agy"], 1, output=b'{"event": "init"}\n', stderr=b"late\n")
        self.assertEqual(agy_port.timeout_output(e), '{"event": "init"}\nlate\n')
        self.assertEqual(agy_port.timeout_output(subprocess.TimeoutExpired(["agy"], 1, output="x\n")), "x\n")
        self.assertEqual(agy_port.timeout_output(subprocess.TimeoutExpired(["agy"], 1)), "")

    def test_timeout_output_from_a_real_timeout(self):
        code = "import time; print('{\"event\": \"init\"}', flush=True); time.sleep(30)"
        with self.assertRaises(subprocess.TimeoutExpired) as cm:
            sandbox.sh3([sys.executable, "-c", code], timeout=3)
        self.assertIn('{"event": "init"}', agy_port.timeout_output(cm.exception))

    def test_timeout_kind_reads_both_the_hard_limit_and_agy_print_timeout(self):
        self.assertEqual(agy_port.timeout_kind(124, {}, ""), "hard")
        self.assertEqual(agy_port.timeout_kind(3, {"finished_reason": "print_timeout"}, ""), "print_timeout")
        self.assertEqual(agy_port.timeout_kind(0, {"finished_reason": "print_timeout"}, ""), "print_timeout")   # 古い版は符号 0
        self.assertEqual(agy_port.timeout_kind(3, {}, "...\n[agy] print timeout after 3580s with turn in progress\n"),
                         "print_timeout")
        self.assertIsNone(agy_port.timeout_kind(3, {"finished_reason": "error"}, 'AGY_ERROR: {"short_error":"x"}\n'))
        self.assertIsNone(agy_port.timeout_kind(0, {"status": "SUCCESS"}, ""))

    def test_print_timeout_from_a_stream(self):
        text = "\n".join(json.dumps(e) for e in (
            {"event": "init", "init": {"model": "gemini-4-hydrogen-medium", "conversation_id": "c1"}},
            step(0, "run_command", "DONE"),
            {"event": "result", "result": {"status": "SUCCESS", "finished_reason": "print_timeout", "conversation_id": "c1"}},
        )) + "\n[agy] print timeout after 3580s with turn in progress; returning partial output\n"
        parsed = agy_stream.parse(text)
        self.assertEqual(agy_port.timeout_kind(3, parsed["outcome"], text), "print_timeout")

    def test_turn_problem_names_why_a_turn_is_not_ok(self):
        ok = {"agent": {"rc": 0, "timeout": None}, "complete": True, "outcome": {"status": "SUCCESS"},
              "denied_tools": [{"tool": "search_web", "ran": False}], "valid": True}
        self.assertIsNone(agy_port.turn_problem(ok))
        self.assertTrue(agy_port.turn_problem(dict(ok, model_error="報告が無い", valid=False)).startswith("model:"))
        self.assertEqual(agy_port.turn_problem(dict(ok, denied_tools=[{"tool": "invoke_subagent", "ran": True}], valid=False)),
                         "invalid: denied tool ran: invoke_subagent")
        self.assertEqual(agy_port.turn_problem(dict(ok, valid=False)), "invalid: hooks changed")
        self.assertEqual(agy_port.turn_problem(dict(ok, agent={"rc": 3, "timeout": "print_timeout"})), "timeout: print_timeout")
        self.assertTrue(agy_port.turn_problem(dict(ok, agent={"rc": 3, "timeout": None},
                                                   outcome={"status": "ERROR"})).startswith("agent:"))
        self.assertTrue(agy_port.turn_problem(dict(ok, complete=False)).startswith("agent:"))

    def test_quota_floor(self):
        floor = {"gemini-weekly": 0.35, "gemini-5h": 0.15}
        ok = {"buckets": {"gemini-weekly": {"remaining_fraction": 0.5}, "gemini-5h": {"remaining_fraction": 0.2}}}
        low = {"buckets": {"gemini-weekly": {"remaining_fraction": 0.3}, "gemini-5h": {"remaining_fraction": 0.9}}}
        self.assertEqual(agy_port.quota_ok(ok, floor), (True, None))
        self.assertFalse(agy_port.quota_ok(low, floor)[0])
        self.assertFalse(agy_port.quota_ok(None, floor)[0])


if __name__ == "__main__":
    unittest.main()
