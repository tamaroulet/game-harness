"""分解役の上限・TaskSpec の再利用・利用量の検査（proc.run を差し替え、外部の CLI は呼ばない）。"""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "harness")]
import hline  # noqa: E402
import hline_budget as hb  # noqa: E402
import test_hline_queue as base  # noqa: E402

CFG = base.CFG
LIM = hb.limits(CFG["decomposer"])
USAGE = {"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 30, "cache_creation_input_tokens": 40}


def reply(result, model="claude-opus-5", **extra):
    return json.dumps({"result": result, "modelUsage": {model: {}}, "num_turns": 3, "total_cost_usd": 0.5,
                       "usage": USAGE, **extra})


class Pure(unittest.TestCase):
    def test_limits_args_env_and_ttl_follow_the_config(self):
        self.assertEqual(LIM, {"max_thinking_tokens": 6000, "max_turns": LIM["max_turns"], "timeout_seconds": 300})
        args = hb.agent_args(CFG["decomposer"], ["claude"], LIM)
        self.assertEqual(args[-2:], ["--max-turns", str(LIM["max_turns"])])
        self.assertEqual(args[:-2], hline.implementer_args(CFG["decomposer"], ["claude"]))
        given, before = {"A": "1"}, dict(os.environ)
        self.assertEqual(hb.agent_env(LIM, given), {"A": "1", "MAX_THINKING_TOKENS": "6000"})
        self.assertEqual((given, dict(os.environ)), ({"A": "1"}, before))
        self.assertEqual((hb.ttl(CFG["ttl_seconds"]["decomposer"], LIM), hb.ttl(100, LIM)), (300, 100))

    def test_a_missing_or_bad_budget_is_an_environment_fault(self):
        for bad in (None, {}, {"max_turns": 1}, {**LIM, "max_turns": 0}, {**LIM, "max_turns": True}, {**LIM, "max_turns": "5"}):
            with self.subTest(bad=bad), self.assertRaises(hline.Infra):
                hb.limits({} if bad is None else {"budget": bad})

    def test_cutoff_reason(self):
        self.assertIn("300", hb.cutoff_reason(124, "", LIM))
        for out in (json.dumps({"subtype": "error_max_turns"}), json.dumps({"num_turns": LIM["max_turns"]}),
                    json.dumps({"is_error": True, "subtype": "x_max_turns"})):
            self.assertIn(str(LIM["max_turns"]), hb.cutoff_reason(0, out, LIM))
        for out in (json.dumps({"result": "x"}), json.dumps({"num_turns": LIM["max_turns"] - 1}), json.dumps({"num_turns": "99"}),
                    "not json", "[1]", ""):
            self.assertIsNone(hb.cutoff_reason(0, out, LIM), out)

    def test_read_files_collects_read_grep_glob_targets(self):
        def use(name, **inp):
            return {"type": "tool_use", "name": name, "input": inp}
        doc = {"messages": [{"content": [use("Read", file_path="harness\\hline_spec.py"), use("Grep", path="harness", pattern="x"),
                                         use("Glob", path="tests"), use("Read", file_path="harness/hline_spec.py"),
                                         use("Bash", command="ls"), {"type": "text", "name": "Read", "input": {"file_path": "no"}}]}]}
        self.assertEqual(hb.read_files(json.dumps(doc)), ("harness", "harness/hline_spec.py", "tests"))
        for out in ("", "not json", "[1]", json.dumps({"result": "x"}), json.dumps({"a": [use("Bash", command="ls")]})):
            self.assertEqual(hb.read_files(out), (), out)
        many = {"c": [use("Read", file_path=f"f{n:03}.py") for n in range(50)]}
        self.assertEqual(len(hb.read_files(json.dumps(many))), 40)


class Flow(base.World):
    def patch_agents(self):
        self.runs, self.impl_out = [], reply("できた", "claude-sonnet-5-5")

        def fake_run(args, cwd, ttl, label, env=None, input=None):
            self.runs.append({"args": args, "ttl": ttl, "env": env, "label": label, "input": input})
            return 0, self.impl_out, ""
        self.patch(hline.proc, "run", side_effect=fake_run)
        self.patch(hline.proc, "resolve_cli", return_value=["claude"])
        self.patch(hline, "changed_paths", return_value=["harness/textnorm.py"])
        self.patch(hline, "gate", side_effect=lambda c, w, p, spec=None, task=None, **kw: (True, "ok"))

    def test_the_implementer_carries_only_the_turn_cap(self):
        self.put("010-a")
        self.poll()
        impl = next(r for r in self.runs if r["label"] == "実装役")
        self.assertEqual(impl["ttl"], CFG["ttl_seconds"]["implementer"])
        self.assertEqual(impl["args"][-2:], ["--max-turns", str(CFG["implementer"]["budget"]["max_turns"])])
        self.assertNotIn("MAX_THINKING_TOKENS", impl["env"] or {})

    def test_every_call_leaves_its_usage_in_the_queue_state(self):
        self.put("010-a")
        self.assertEqual(self.poll(), 0)
        item = self.state()["items"]["010-a"]
        for u in [t["usage"] for t in item["tries"]]:
            self.assertEqual((u["input_tokens"], u["output_tokens"], u["cache_read_tokens"], u["cost_usd"]), (10, 20, 30, 0.5))

    def test_an_output_without_usage_is_none_with_a_reason(self):
        self.impl_out = base.claude_json("できた", "claude-sonnet-5-5")
        self.put("010-a")
        self.poll()
        item = self.state()["items"]["010-a"]
        for u in (item["tries"][0]["usage"],):
            self.assertIsNone(u["input_tokens"])
            self.assertIn("input_tokens_null_reason", u)


if __name__ == "__main__":
    unittest.main()
