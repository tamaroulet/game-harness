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
import hline_spec  # noqa: E402
import test_hline_queue as base  # noqa: E402

CFG, SPEC = base.CFG, base.SPEC
LIM = hb.limits(CFG["decomposer"])
USAGE = {"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 30, "cache_creation_input_tokens": 40}


def reply(result, model="claude-opus-5", **extra):
    return json.dumps({"result": result, "modelUsage": {model: {}}, "num_turns": 3, "total_cost_usd": 0.5,
                       "usage": USAGE, **extra})


def good(spec=SPEC):
    return 0, reply(json.dumps(spec)), ""


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

    def test_decompose_prompt_mentions_the_cutoff_and_files_only_after_one(self):
        args = ("何か", {"task": None}, {"type": "object"}, None, None)
        plain = hline_spec.decompose_prompt(*args)
        self.assertEqual(plain, hline_spec.decompose_prompt(*args, cutoff=None, read=("a.py",)))
        for word in ("打ち切", "前回に読んだファイル"):
            self.assertNotIn(word, plain)
        cut = hline_spec.decompose_prompt(*args, cutoff="理由X", read=("a.py", "b/c.py"))
        for word in ("打ち切", "前回に読んだファイル", "理由X", "a.py", "b/c.py"):
            self.assertIn(word, cut)
        self.assertNotIn("記録なし", cut)
        self.assertIn("記録なし", hline_spec.decompose_prompt(*args, cutoff="理由X"))



class Flow(base.World):
    def patch_agents(self):
        self.runs, self.dec_replies, self.impl_out = [], [], reply("できた", "claude-sonnet-5-5")

        def fake_run(args, cwd, ttl, label, env=None, input=None):
            self.runs.append({"args": args, "ttl": ttl, "env": env, "label": label, "input": input})
            return self.dec_replies.pop(0) if label == "分解役" else (0, self.impl_out, "")
        self.patch(hline.proc, "run", side_effect=fake_run)
        self.patch(hline.proc, "resolve_cli", return_value=["claude"])
        self.patch(hline, "changed_paths", return_value=["harness/textnorm.py"])
        self.patch(hline, "gate", side_effect=lambda c, w, p, spec=None, task=None: (True, "ok"))

    def decompose(self, what="# 題\n本文", cfg=None, n=0):
        outdir = self.tmp / f"o{n}"
        outdir.mkdir(exist_ok=True)
        return hline_spec.decompose(cfg or self.cfg, self.wt, what, {"task": None}, outdir), outdir

    def test_a_saved_taskspec_is_reused_without_calling_the_decomposer(self):
        self.dec_replies = [good()]
        (spec, rec), _ = self.decompose()
        self.assertEqual((len(self.runs), rec["reused"]), (1, False))
        (again, rec2), outdir = self.decompose(n=1)
        self.assertEqual((len(self.runs), again, rec2), (1, spec, {"attempts": [], "reused": True, "reason": None}))
        self.assertEqual(json.loads((outdir / "taskspec.json").read_text(encoding="utf-8")), SPEC)

    def test_a_changed_body_or_a_broken_cache_rebuilds_the_taskspec(self):
        what = "# 題\n本文"
        self.dec_replies = [good()] * 4
        self.decompose(what)
        self.decompose(what + "変えた", n=1)   # 同じ名前でも本文が違えばハッシュが違う
        self.assertEqual(len(self.runs), 2)
        path = hline_spec.spec_cache_path(self.cfg, what)
        doc = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**doc, "what_sha256": "0"}), encoding="utf-8")
        self.decompose(what, n=2)
        self.assertEqual(len(self.runs), 3)
        hline_spec.save_spec(self.cfg, what, {"x": 1})   # Gate A に適合しない spec
        self.decompose(what, n=3)
        self.assertEqual(len(self.runs), 4)

    def test_the_implementer_carries_only_the_turn_cap_and_the_decomposer_the_whole_budget(self):
        self.dec_replies = [good()]
        self.put("010-a")
        self.poll()
        dec, impl = [next(r for r in self.runs if r["label"] == label) for label in ("分解役", "実装役")]
        self.assertEqual(dec["ttl"], min(CFG["ttl_seconds"]["decomposer"], LIM["timeout_seconds"]))
        self.assertEqual(dec["args"][-2:], ["--max-turns", str(LIM["max_turns"])])
        self.assertEqual(dec["env"]["MAX_THINKING_TOKENS"], str(LIM["max_thinking_tokens"]))
        self.assertEqual(impl["ttl"], CFG["ttl_seconds"]["implementer"])
        self.assertEqual(impl["args"][-2:], ["--max-turns", str(CFG["implementer"]["budget"]["max_turns"])])
        self.assertNotIn("MAX_THINKING_TOKENS", impl["env"] or {})

    def test_a_config_without_a_budget_stops_before_any_cli_starts(self):
        cfg = json.loads(json.dumps(self.cfg))
        del cfg["decomposer"]["budget"]["max_turns"]
        with self.assertRaises(hline.Infra):
            self.decompose(cfg=cfg)
        self.assertEqual(self.runs, [])

    def test_a_cutoff_is_retried_like_a_gate_a_violation_and_never_reaches_the_implementer(self):
        self.dec_replies = [(124, "", ""), (0, reply("", subtype="error_max_turns"), ""), (0, reply("{}", num_turns=LIM["max_turns"]), "")]
        self.put("010-a")
        self.assertEqual(self.poll(), 1)
        item = self.state()["items"]["010-a"]
        agents = [r for r in self.runs if "役" in r["label"]]
        self.assertEqual([r["label"] for r in agents], ["分解役"] * (1 + CFG["spec_retries"]))
        self.assertEqual((item["status"], item["tries"]), ("unconverged", []))
        self.assertIn("打ち切", item["reason"])
        for a in item["decompose"]["attempts"]:
            self.assertEqual((a["models"], a["valid"]), ([], False))
            self.assertIn("打ち切", a["cutoff"])
        self.assertIn(item["decompose"]["attempts"][0]["cutoff"], agents[1]["input"])

    def test_a_cutoff_retry_names_the_files_read_and_a_plain_violation_forgets_them(self):
        tool = [{"type": "tool_use", "name": "Read", "input": {"file_path": "harness\\hline_spec.py"}}]
        self.dec_replies = [(0, reply("", subtype="error_max_turns", messages=tool), ""), (0, reply("{}"), ""),
                            (0, reply("{}"), "")]
        (spec, rec), _ = self.decompose()
        self.assertIsNone(spec)
        decs = [r for r in self.runs if r["label"] == "分解役"]
        self.assertEqual(len(decs), 1 + CFG["spec_retries"])
        self.assertIn(rec["attempts"][0]["cutoff"], decs[1]["input"])
        self.assertIn("harness/hline_spec.py", decs[1]["input"])
        self.assertEqual(rec["attempts"][0]["read_files"], ["harness/hline_spec.py"])
        for r in decs[2:]:
            self.assertNotIn("前回に読んだファイル", r["input"])
            self.assertNotIn("打ち切", r["input"])

    def test_every_call_leaves_its_usage_in_the_queue_state(self):
        self.dec_replies = [(0, reply("これは JSON ではない"), ""), good()]
        self.put("010-a")
        self.assertEqual(self.poll(), 0)
        item = self.state()["items"]["010-a"]
        for u in [a["usage"] for a in item["decompose"]["attempts"]] + [t["usage"] for t in item["tries"]]:
            self.assertEqual((u["input_tokens"], u["output_tokens"], u["cache_read_tokens"], u["cost_usd"]), (10, 20, 30, 0.5))

    def test_an_output_without_usage_is_none_with_a_reason(self):
        self.dec_replies = [(0, base.claude_json(json.dumps(SPEC), "claude-opus-5"), "")]
        self.impl_out = base.claude_json("できた", "claude-sonnet-5-5")
        self.put("010-a")
        self.poll()
        item = self.state()["items"]["010-a"]
        for u in (item["decompose"]["attempts"][0]["usage"], item["tries"][0]["usage"]):
            self.assertIsNone(u["input_tokens"])
            self.assertIn("input_tokens_null_reason", u)


if __name__ == "__main__":
    unittest.main()
