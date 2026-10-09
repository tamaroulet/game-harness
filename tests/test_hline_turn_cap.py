"""実装役のターン数の上限・打ち切りの扱い・試行の記録の検査（proc.run を差し替え、外部の CLI・push・PR は起こさない）。"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "harness")]
import hline  # noqa: E402
import hline_budget as hb  # noqa: E402
import test_hline_queue as base  # noqa: E402

CFG, WHAT = base.CFG, "# T-010-a\n\n## What\n本文"
CAP = CFG["implementer"]["budget"]["max_turns"]
USAGE = {"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 30, "cache_creation_input_tokens": 40}


def out(model="claude-sonnet-5-5", **extra):
    doc = {"result": "できた", "modelUsage": {model: {}}, "num_turns": 3, "total_cost_usd": 0.5, "usage": USAGE}
    return json.dumps({k: v for k, v in {**doc, **extra}.items() if v is not None})


CUTOFFS = {"error_max_turns": out(subtype="error_max_turns"), "at the cap": out(num_turns=CAP),
           "is_error": out(is_error=True, subtype="x_max_turns"),
           "no modelUsage": json.dumps({"subtype": "error_max_turns", "num_turns": CAP, "is_error": True})}


class Pure(unittest.TestCase):
    def test_the_cap_comes_from_the_config_only(self):
        self.assertEqual(hb.turn_cap(CFG["implementer"]), CAP)
        self.assertEqual(hb.capped_args(CFG["implementer"], ["claude"], 7),
                         hline.implementer_args(CFG["implementer"], ["claude"]) + ["--max-turns", "7"])

    def test_turns(self):
        self.assertEqual(hb.turns('{"num_turns": 7}'), 7)
        for o in ("not json", "[1]", "", "{}", '{"num_turns": "7"}', '{"num_turns": true}', '{"num_turns": 1.5}'):
            self.assertIsNone(hb.turns(o), o)

    def test_turn_cutoff(self):
        for name, o in CUTOFFS.items():
            self.assertIn(str(CAP), hb.turn_cutoff(o, CAP), name)
        self.assertIn("打ち切", hb.turn_cutoff(out(subtype="error_max_turns"), 99))
        for o in ("not json", "[1]", "", json.dumps({}), out(), out(num_turns=CAP - 1), out(num_turns="99"),
                  out(is_error=True, subtype="other"), out(is_error=False, subtype="x_max_turns")):
            self.assertIsNone(hb.turn_cutoff(o, CAP), o)

    def test_attempt_record_fills_what_is_missing_with_none(self):
        self.assertEqual(hb.attempt_record(1, 2, 0, ["m"], True, [USAGE, "理由", 4]),
                         {"run": 1, "attempt": 2, "cli_exit": 0, "models": ["m"], "gate": True, "usage": USAGE, "cutoff": "理由", "turns": 4})
        for rest, want in (([], (None, None, None)), ([USAGE], (USAGE, None, None)), ((USAGE, None), (USAGE, None, None))):
            rec = hb.attempt_record(0, 1, 0, [], False, rest)
            self.assertEqual((rec["usage"], rec["cutoff"], rec["turns"]), want)

    def test_with_cutoff_keeps_the_feedback_whole(self):
        self.assertEqual((hb.with_cutoff(None, "出力"), hb.with_cutoff("", "出力")), ("出力", "出力"))
        got = hb.with_cutoff("ターン数の上限 15 で打ち切られました", "Gate 1 の出力\n2 行目")
        for word in ("打ち切", "15", "続き", "Gate 1 の出力\n2 行目"):
            self.assertIn(word, got)


class Flow(base.World):
    def patch_agents(self):
        self.runs, self.impl_outs, self.gates, self.gated_in, self.made = [], [], [], [], []

        def fake_run(args, cwd, ttl, label, env=None, input=None):
            self.runs.append({"args": args, "ttl": ttl, "env": env, "label": label, "input": input})
            if label != "実装役":   # 残骸の掃除（git worktree prune）など
                return 0, "", ""
            o = self.impl_outs.pop(0) if self.impl_outs else out()
            return o if isinstance(o, tuple) else (0, o, "")

        def fake_gate(c, w, p, spec=None, task=None, **kw):
            self.gated_in.append(w)
            return self.gates.pop(0) if self.gates else (True, "ok")
        self.patch(hline.proc, "run", side_effect=fake_run)
        self.patch(hline.proc, "resolve_cli", return_value=["claude"])
        self.patch(hline, "changed_paths", return_value=["harness/textnorm.py"])
        self.patch(hline, "gate", side_effect=fake_gate)
        self.patch(hline, "new_worktree", side_effect=lambda c, t: (self.made.append(t), (self.wt, "b"))[1])

    def implement(self, cfg=None):
        return hline.implement(cfg or self.cfg, self.wt, WHAT, None, self.out / "log")

    def run_task(self):
        return hline.run_task(self.cfg, "t", WHAT, self.out)

    def test_the_implementer_call_carries_the_turn_cap_and_nothing_else(self):
        self.implement()
        (call,) = self.runs
        self.assertEqual(call["args"][-2:], ["--max-turns", str(CAP)])
        self.assertEqual(call["ttl"], CFG["ttl_seconds"]["implementer"])
        self.assertNotIn("MAX_THINKING_TOKENS", call["env"] or {})

    def test_a_bad_cap_stops_before_any_cli_starts(self):
        for bad in ({}, {"max_turns": 0}, {"max_turns": True}, {"max_turns": "5"}, None):
            cfg = json.loads(json.dumps(self.cfg))
            cfg["implementer"].update(budget=bad)
            with self.subTest(bad=bad), self.assertRaises(hline.Infra):
                self.implement(cfg)
        self.assertEqual(self.runs, [])

    def test_a_cutoff_is_returned_not_raised_and_the_models_are_not_checked(self):
        for name, o in CUTOFFS.items():
            for code in (0, 1):
                self.impl_outs = [(code, o, "")]
                with self.subTest(name=name, code=code):
                    got = self.implement()
                    self.assertEqual((len(got), got[0], got[1]), (5, code, []))
                    self.assertIn(str(CAP), got[3])
        self.impl_outs = [(0, out(num_turns=4), "")]
        code, models, usage, cutoff, n = self.implement()
        self.assertEqual((models, cutoff, n, usage["input_tokens"]), (["claude-sonnet-5-5"], None, 4, 10))

    def test_the_time_limit_is_still_an_environment_fault(self):
        self.impl_outs = [(124, "", "")]
        self.assertRaises(hline.Infra, self.implement)

    def test_a_cut_off_attempt_goes_to_gate_1_in_the_same_worktree_and_a_failure_carries_the_reason(self):
        self.impl_outs = [(1, CUTOFFS["error_max_turns"], ""), (0, out(), "")]
        self.gates = [(False, "Gate 1 の出力\n全文 1"), (True, "ok")]
        wt, branch, tries = self.run_task()
        self.assertEqual((wt, self.made, self.gated_in), (self.wt, ["t"], [self.wt] * 2))   # 作業ツリーを作るのは最初の 1 回だけ
        first, second = (r["input"] for r in self.runs)
        for word in ("打ち切", str(CAP), "Gate 1 の出力\n全文 1"):
            self.assertIn(word, second)
        self.assertNotIn("打ち切", first)
        self.assertEqual((self.out / "gate-0-1.log").read_text(encoding="utf-8"), "Gate 1 の出力\n全文 1")

    def test_attempt_records_carry_turns_and_cutoff_including_the_passing_one(self):
        self.impl_outs = [(1, CUTOFFS["is_error"], ""), (0, out(num_turns=6), "")]
        self.gates = [(False, "x"), (True, "ok")]
        _, _, tries = self.run_task()
        self.assertEqual([(t["gate"], t["turns"], bool(t["cutoff"]), t["models"], t["usage"]["output_tokens"]) for t in tries],
                         [(False, 3, True, [], 20), (True, 6, False, ["claude-sonnet-5-5"], 20)])

    def test_an_implement_that_returns_fewer_elements_still_works(self):
        for short in ((0, ["m"]), (0, ["m"], {"input_tokens": 1})):
            with self.subTest(n=len(short)), mock.patch.object(hline, "implement", return_value=short):
                _, _, tries = self.run_task()
            self.assertEqual((tries[0]["cutoff"], tries[0]["turns"]), (None, None))
            self.assertEqual(tries[0]["usage"], short[2] if len(short) > 2 else None)

    def test_the_queue_state_keeps_turns_and_cutoff_for_every_try(self):
        self.impl_outs = [(1, CUTOFFS["error_max_turns"], ""), (0, out(num_turns=5), "")]
        self.gates = [(False, "x"), (True, "ok")]
        self.put("010-a")
        self.assertEqual(self.poll(), 0)
        item = self.state()["items"]["010-a"]
        self.assertEqual(item["status"], "done")
        self.assertEqual([(t["turns"], bool(t["cutoff"])) for t in item["tries"]], [(3, True), (5, False)])


if __name__ == "__main__":
    unittest.main()
