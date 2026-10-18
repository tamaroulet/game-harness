"""agy の命令の道具の呼び出しは、成功したときだけ試行を打ち切り、sandbox に拒まれた呼び出しは数えるだけにする（W-105）の試験。
本物の claude・agy の CLI と導入先は使わず、host.proc.run を偽物に替える。"""
import contextlib
import io
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
import hline_abort  # noqa: E402
import hline_agy  # noqa: E402
import hline_prompt  # noqa: E402
import hline_task  # noqa: E402

MODEL = "gemini-3.8-flash-medium"
SPLIT = "\n--- stderr ---\n"
USAGE = {"input_tokens": 100, "output_tokens": 10, "thinking_tokens": 2, "cache_read_tokens": 50}
ERROR = {"type": "TOOL_ERROR", "message": "createAppContainer: ShellExecute failed: The request is not supported."}


def ev(name, body):
    return json.dumps({"event": name, name: body})


def command_step(index, state, line="Get-ChildItem -Force"):
    """命令の道具の手番の 1 行。ERROR のときは smoke-2 と同じ形（tool_info.error）。"""
    body = {"step_index": index, "step_type": "tool_call", "state": state, "tool_name": "run_command",
            "tool_info": {"parameters": {"CommandLine": line}}}
    if state == "ERROR":
        body["tool_info"] = {**body["tool_info"], "error": ERROR}
    if state == "DONE":
        body["usage"] = USAGE
    return ev("step_update", body)


def read_step(index):
    return ev("step_update", {"step_index": index, "step_type": "tool_call", "state": "DONE", "tool_name": "view_file", "usage": USAGE})


def stream(*step_lines):
    result = {"status": "SUCCESS", "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}}
    lines = [ev("init", {"model": MODEL, "conversation_id": "c1"}), *step_lines, ev("result", result)]
    return "\n".join(lines) + "\n"


def blocked(index):   # 同じ step_index の ACTIVE の行と ERROR の行
    return [command_step(index, "ACTIVE"), command_step(index, "ERROR")]


def agy_cfg():
    imp = json.loads((ROOT / "config" / "bench_implementer_agy.json").read_text(encoding="utf-8"))
    return {**json.loads((ROOT / "config" / "hline.json").read_text(encoding="utf-8")), "implementer": imp, "max_attempts": 1}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.wt = self.root / "wt"
        self.out = self.root / "out"
        for p in (self.wt, self.out):
            p.mkdir()
        self.calls = []
        self.reply = (0, stream(), "")
        self.host = types.SimpleNamespace(
            symbolmap=types.SimpleNamespace(prompt_text=lambda *a, **k: ""), build_prompt=lambda what, fb, shown: "PROMPT",
            proc=types.SimpleNamespace(run=self.fake_run, resolve_cli=lambda cli: [cli]))
        patch = mock.patch.object(hline_agy.agy_pinned, "require", return_value={})
        patch.start()
        self.addCleanup(patch.stop)

    def fake_run(self, args, cwd, ttl, label, env=None, input=None):
        self.calls.append((args, input))
        return self.reply

    def implement(self, *step_lines, cfg=None):
        self.reply = (0, stream(*step_lines), "")
        self.log = self.out / "implementer-0-1.log"
        return hline_prompt.implement(self.host, cfg or agy_cfg(), self.wt, "WHAT", None, self.log)

    def reason(self):
        return hline_abort.abort_reason(self.log, "d", None)

    def summary(self):
        return json.loads(self.log.read_text(encoding="utf-8").split(SPLIT, 1)[0])


class Steps(Base):
    def test_a_blocked_step_alone_does_not_abort_and_is_counted(self):
        self.implement(read_step(0), *blocked(1))
        self.assertIsNone(self.reason())
        self.assertEqual((self.summary()["commands"], self.summary()["blocked_commands"], self.host.blocked_commands), ([], 1, 1))

    def test_a_done_command_still_aborts_with_the_step_number(self):
        self.implement(read_step(0), command_step(1, "DONE", "git status"))
        self.assertEqual(self.reason(), "命令の実行（REQ-09）：手番 2 run_command git")
        self.assertEqual(self.summary()["blocked_commands"], 0)

    def test_a_command_left_active_still_aborts(self):
        self.implement(command_step(0, "ACTIVE", "ls -la"))
        self.assertEqual(self.reason(), "命令の実行（REQ-09）：手番 1 run_command ls")

    def test_blocked_and_running_steps_mixed_report_the_running_step_number(self):
        self.implement(*blocked(0), read_step(1), command_step(2, "DONE", "python -V"))
        self.assertEqual(self.reason(), "命令の実行（REQ-09）：手番 3 run_command python")
        head = self.summary()
        self.assertEqual((head["commands"], head["blocked_commands"]),
                         ([{"step": 3, "tool": "run_command", "command": "python"}], 1))


class Prompt(Base):
    def test_agy_input_starts_with_the_note(self):
        self.implement(read_step(0))
        stdin = self.calls[0][1]
        self.assertEqual(stdin, agy_stream.stdin_for(agy_cfg()["implementer"], hline_agy.NOTE + "PROMPT"))
        content = json.loads(stdin)["message"]["content"]
        self.assertTrue(content.startswith("命令の実行の道具（run_command など）は使えません"))
        self.assertEqual(content.split("\n", 1)[0].count("読みと検索の道具"), 1)

    def test_the_claude_input_has_no_note(self):
        cfg = json.loads((ROOT / "config" / "hline.json").read_text(encoding="utf-8"))
        self.reply = (0, json.dumps({"result": "ok", "num_turns": 1, "modelUsage": {"claude-sonnet-5-5": {}}}), "")
        self.log = self.out / "implementer-0-1.log"
        hline_prompt.implement(self.host, cfg, self.wt, "WHAT", None, self.log)
        self.assertEqual(self.calls[0][1], "PROMPT")
        self.assertNotIn("使えません", self.calls[0][1])


class Flow(Base):
    def run_task(self, implement, cfg=None):
        self.gated = []

        def gate(cfg, wt, paths, spec=None, task=None, **kw):
            self.gated.append(paths)
            return False, "GATE-OUT"

        host = self.host
        host.new_worktree, host.implement, host.gate = mock.Mock(), implement, gate
        host.changed_paths = lambda w, c: ["new.py"]
        with contextlib.redirect_stdout(io.StringIO()):
            return hline_task.run_task(host, cfg or agy_cfg(), "t", "# T\n\n## What\n本文\n", self.out, (self.wt, "b")), host

    def agy_implement(self, *step_lines):
        self.reply = (0, stream(*step_lines), "")
        return lambda cfg, wt, what, feedback, log: hline_agy.implement(self.host, cfg, wt, "PROMPT", log)

    def test_a_blocked_only_attempt_reaches_gate_1_with_the_count_in_the_record(self):
        (wt, branch, tries), _ = self.run_task(self.agy_implement(*blocked(0)))
        self.assertEqual(len(self.gated), 1)
        self.assertNotIn("abort", tries[0])
        self.assertEqual(tries[0]["blocked_commands"], 1)

    def test_a_running_command_attempt_is_aborted_before_gate_1(self):
        (wt, branch, tries), _ = self.run_task(self.agy_implement(*blocked(0), command_step(1, "DONE", "git log")))
        self.assertEqual(self.gated, [])
        self.assertEqual(tries[0]["abort"], "命令の実行（REQ-09）：手番 2 run_command git")

    def test_a_claude_attempt_record_has_no_blocked_commands(self):
        def implement(cfg, wt, what, feedback, log):
            Path(log).write_text("ふつうのログ\n", encoding="utf-8")
            return 0, ["claude-sonnet-5-5"], None, None, 3

        (wt, branch, tries), host = self.run_task(implement)
        self.assertNotIn("blocked_commands", tries[0])
        self.assertIsNone(host.blocked_commands)


if __name__ == "__main__":
    unittest.main()
