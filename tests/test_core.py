"""制御核（harness/core.py）の試験。実行環境とモデルは偽物で置き換える（利用枠 0）。

    python -m unittest tests.test_core -v
"""
import dataclasses
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import core  # noqa: E402

MODEL = "gemini-4-hydrogen-medium"


def turn(ok=True, model=MODEL, steps=3):
    return core.Turn(ok=ok, model=model, seconds=1.0, conversation_id="c1",
                     usage={"input_tokens": 100, "output_tokens": 10, "thinking_tokens": 5, "cache_read_tokens": 1000,
                            "model_steps": steps, "lost_steps": 0, "partial": False})


class FakePort:
    def __init__(self, turns):
        self.turns = list(turns)
        self.sent = []

    def start(self, prompt, budget_s):
        self.sent.append(("start", prompt, budget_s))
        return self.turns.pop(0)

    def send(self, text, budget_s):
        self.sent.append(("send", text, budget_s))
        return self.turns.pop(0)


class FakeAdapter:
    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.calls = 0

    def verify(self, checks):
        self.calls += 1
        return self.rounds.pop(0)


def ledger():
    checks = [core.Check("C1", "tests", ["tests/test_x.py"]), core.Check("C2", "static", [], required=False)]
    reqs = [core.Requirement("R1", "statement", "implement f()", ["C1"]),
            core.Requirement("R2", "test", "tests/test_x.py::test_a", ["C1::tests/test_x.py::test_a"]),
            core.Requirement("R3", "test", "tests/test_x.py::test_b", ["C1::tests/test_x.py::test_b"])]
    return core.Ledger("u1", reqs, checks)


FAIL_B = [core.CheckResult("C1", False, passed=["tests/test_x.py::test_a"], failed=["tests/test_x.py::test_b"],
                           errors=["tests/test_x.py::test_b - AssertionError: 1 != 2"], summary="1 failed, 1 passed"),
          core.CheckResult("C2", True)]
ALL_PASS = [core.CheckResult("C1", True, passed=["tests/test_x.py::test_a", "tests/test_x.py::test_b"]),
            core.CheckResult("C2", True)]


class Decide(unittest.TestCase):
    def test_all_required_must_pass_and_advisory_does_not_block(self):
        lg = ledger()
        self.assertFalse(core.decide(lg.checks, FAIL_B))
        self.assertTrue(core.decide(lg.checks, ALL_PASS))
        advisory_fail = [ALL_PASS[0], core.CheckResult("C2", False, failed=["x.py"])]
        self.assertTrue(core.decide(lg.checks, advisory_fail), "助言の検査は合否を止めない")

    def test_a_missing_result_for_a_required_check_counts_as_failure(self):
        self.assertFalse(core.decide(ledger().checks, [core.CheckResult("C2", True)]))


class LedgerStatus(unittest.TestCase):
    def test_status_follows_results_only_and_can_go_back_to_open(self):
        lg = ledger()
        core.update_ledger(lg, FAIL_B)
        self.assertEqual([r.status for r in lg.requirements], ["open", "done", "open"])
        core.update_ledger(lg, ALL_PASS)
        self.assertEqual([r.status for r in lg.requirements], ["done", "done", "done"])
        core.update_ledger(lg, FAIL_B)
        self.assertEqual(lg.requirements[2].status, "open", "退行すれば open に戻る")

    def test_round_trip_through_dict(self):
        lg = ledger()
        self.assertEqual(core.Ledger.from_dict(lg.to_dict()).to_dict(), lg.to_dict())


class Feedback(unittest.TestCase):
    def test_lists_failures_mechanically_without_requests(self):
        text = core.feedback(ledger().checks, FAIL_B)
        self.assertIn("FAILED", text)
        self.assertIn("tests/test_x.py::test_b - AssertionError: 1 != 2", text)
        self.assertIn("C2 [static]: passed", text)
        for word in ("please", "must", "should", "make sure"):
            self.assertNotIn(word, text.lower(), "押し返しの文にお願いを足さない（憲章 §5）")

    def test_caps_the_number_of_items(self):
        many = [core.CheckResult("C1", False, failed=[f"t{i}" for i in range(60)], errors=[f"t{i} - E" for i in range(60)])]
        text = core.feedback([core.Check("C1", "tests", ["f"])], many)
        self.assertIn("... and 35 more", text)


class RunUnit(unittest.TestCase):
    def run_unit(self, port, adapter, **kw):
        clock = iter(range(0, 100000, 10))
        kw.setdefault("budget_s", 3600)
        return core.run_unit(port, adapter, ledger(), "do the task", expected_model=MODEL,
                             clock=lambda: next(clock), **kw)

    def test_pushes_back_in_the_same_conversation_until_the_gate_passes(self):
        port = FakePort([turn(), turn()])
        adapter = FakeAdapter([FAIL_B, ALL_PASS])
        rec = self.run_unit(port, adapter)
        self.assertEqual(rec["stop"], "pass")
        self.assertEqual(rec["decision"], "pass")
        self.assertEqual([s[0] for s in port.sent], ["start", "send"])
        self.assertIn("test_b - AssertionError", port.sent[1][1])
        self.assertEqual(rec["ledger"]["requirements"][2]["status"], "done")

    def test_without_the_gate_it_is_one_turn_like_plain_a(self):
        port = FakePort([turn()])
        adapter = FakeAdapter([])
        rec = self.run_unit(port, adapter, gate=False)
        self.assertEqual(rec["stop"], "no_gate")
        self.assertEqual(rec["decision"], "unverified")
        self.assertEqual(adapter.calls, 0)
        self.assertEqual(len(port.sent), 1)

    def test_the_ledger_is_attached_only_when_asked(self):
        port = FakePort([turn()])
        self.run_unit(port, FakeAdapter([ALL_PASS]), show_ledger=True)
        self.assertIn("Requirements ledger", port.sent[0][1])
        port = FakePort([turn()])
        self.run_unit(port, FakeAdapter([ALL_PASS]))
        self.assertNotIn("Requirements ledger", port.sent[0][1])

    def test_stops_at_max_rounds(self):
        port = FakePort([turn(), turn(), turn()])
        rec = self.run_unit(port, FakeAdapter([FAIL_B, FAIL_B, FAIL_B]), max_rounds=3)
        self.assertEqual(rec["stop"], "max_rounds")
        self.assertEqual(rec["decision"], "fail")
        self.assertEqual(len(rec["rounds"]), 3)

    def test_stops_when_the_budget_is_too_short_for_another_turn(self):
        port = FakePort([turn(), turn()])
        rec = self.run_unit(port, FakeAdapter([FAIL_B, FAIL_B]), budget_s=core.MIN_TURN_S + 15)
        self.assertEqual(rec["stop"], "budget")
        self.assertEqual(len(port.sent), 1)

    def test_a_model_mismatch_stops_before_verifying(self):
        adapter = FakeAdapter([ALL_PASS])
        rec = self.run_unit(FakePort([turn(model="other-model")]), adapter)
        self.assertEqual(rec["stop"], "model_mismatch")
        self.assertEqual(adapter.calls, 0)

    def test_an_agent_error_stops(self):
        rec = self.run_unit(FakePort([turn(ok=False)]), FakeAdapter([ALL_PASS]))
        self.assertEqual(rec["stop"], "agent_error")
        self.assertEqual(rec["decision"], "unverified")

    def test_total_usage_adds_the_calls(self):
        port = FakePort([turn(steps=3), turn(steps=4)])
        rec = self.run_unit(port, FakeAdapter([FAIL_B, ALL_PASS]))
        tot = core.total_usage(rec)
        self.assertEqual(tot["input_tokens"], 200)
        self.assertEqual(tot["model_steps"], 7)
        self.assertEqual(tot["turns"], 2)
        self.assertFalse(tot["partial"])


# ---------------------------------------------------------------- Step B-1 の拡張（3 値・H_stop_1・第 2 関門・積み重ね）

def R(cid, status, passed=(), failed=(), errors=(), summary=""):
    return core.CheckResult(cid, status=status, passed=list(passed), failed=list(failed), errors=list(errors),
                            summary=summary)


class ScriptAdapter:
    """verify の呼び出しごとに、台本の dict（検査の id → CheckResult）から頼まれた検査の結果を返す。台本が尽きたら最後を
    繰り返す。fingerprint は fps を順に返す（尽きたら最後）。任意の口の呼び出しは calls に残す。"""

    def __init__(self, script, fps=None, protects=False):
        self.script, self.fps, self.protects = list(script), list(fps or []), protects
        self.n, self.f, self.calls, self.asked = 0, 0, [], []

    def verify(self, checks):
        d = self.script[min(self.n, len(self.script) - 1)]
        self.n += 1
        self.asked.append([c.id for c in checks])
        return [dataclasses.replace(d[c.id], id=c.id) for c in checks if c.id in d]

    def fingerprint(self):
        if not self.fps:
            return None
        fp = self.fps[min(self.f, len(self.fps) - 1)]
        self.f += 1
        return fp

    def changes(self):
        return {"modified": ["a.py"]}

    def checkpoint(self, label):
        self.calls.append(("checkpoint", label))

    def restore(self, label):
        self.calls.append(("restore", label))

    def write_handoff(self, text, data):
        self.calls.append(("write_handoff", text, data))

    def guard(self, checks):
        self.calls.append(("guard", [c.id for c in checks]))


class SecondGate:
    def __init__(self, checks):
        self.checks, self.calls = checks, 0

    def prepare(self, ledger, checks):
        self.calls += 1
        return [core.Check(**vars(c)) for c in self.checks]


T_A, T_B = "tests/test_x.py::test_a", "tests/test_x.py::test_b"
C1_FAIL = R("C1", "fail", passed=[T_A], failed=[T_B], errors=[f"{T_B} - AssertionError: 1 != 2"])
C1_PASS = R("C1", "pass", passed=[T_A, T_B])
C2_PASS = R("C2", "pass")


def unit_ledger(name="u1"):
    return core.Ledger(name, [core.Requirement("R1", "statement", "implement f()", ["C1"])],
                       [core.Check("C1", "tests", ["tests/test_x.py"]), core.Check("C2", "static", [], required=False)])


def run(port, adapter, lg=None, **kw):
    clock = iter(range(0, 100000, 10))
    kw.setdefault("budget_s", 3600)
    return core.run_unit(port, adapter, lg or unit_ledger(), "do the task", expected_model=MODEL,
                         clock=lambda: next(clock), **kw)


class ThreeValues(unittest.TestCase):
    def test_status_follows_ok_unless_given_and_infra_is_never_ok(self):
        self.assertEqual(core.CheckResult("C", True).status, "pass")
        self.assertEqual(core.CheckResult("C", False).status, "fail")
        r = core.CheckResult("C", True, status="infra")
        self.assertEqual((r.status, r.ok), ("infra", False))
        with self.assertRaises(ValueError):
            core.CheckResult("C", status="error")
        with self.assertRaises(ValueError):
            core.Check("C", "tests", [], role="judge")

    def test_verdict_infra_wins_over_fail_only_for_required_checks(self):
        lg = unit_ledger()
        self.assertEqual(core.verdict(lg.checks, [R("C1", "infra"), C2_PASS]), "infra")
        self.assertEqual(core.verdict(lg.checks, [C1_PASS, R("C2", "infra")]), "pass", "助言の検査の infra は合否を止めない")
        self.assertEqual(core.verdict(lg.checks, [C1_FAIL, C2_PASS]), "fail")
        self.assertFalse(core.decide(lg.checks, [R("C1", "infra")]))

    def test_items_decide_a_regression_check(self):
        reg = core.Check("u1:C1", "tests", ["tests/test_x.py"], role="regression", items=[T_A])
        self.assertEqual(core.check_status(reg, C1_FAIL), "pass", "前の単位で通った項目だけを見る")
        reg_b = core.Check("u1:C1", "tests", ["tests/test_x.py"], role="regression", items=[T_A, T_B])
        self.assertEqual(core.check_status(reg_b, C1_FAIL), "fail")
        self.assertEqual(core.failing_items(reg_b, C1_FAIL), [T_B])
        self.assertEqual(core.check_status(reg_b, R("u1:C1", "pass")), "fail", "項目の名を返さない結果は通さない")

    def test_skipped_items_are_left_out_of_the_decision(self):
        reg = core.Check("u1:C1", "tests", ["tests/test_x.py"], role="regression", items=[T_A, T_B, "gone"])
        r = core.CheckResult("u1:C1", status="pass", passed=[T_A], skipped=[T_B, "gone"])
        self.assertEqual(core.check_status(reg, r), "pass", "アダプタが数えないとした項目は合否に入れない")
        self.assertEqual(core.failing_items(reg, r), [])
        self.assertEqual(core.check_status(reg, core.CheckResult("u1:C1", status="pass", passed=[T_A])), "fail")

    def test_infra_is_retried_once_without_pushing_back(self):
        port = FakePort([turn()])
        ad = ScriptAdapter([{"C1": R("C1", "infra", summary="container exited")}, {"C1": C1_PASS, "C2": C2_PASS}])
        rec = run(port, ad)
        self.assertEqual((rec["stop"], rec["decision"]), ("pass", "pass"))
        self.assertEqual(len(port.sent), 1, "infra はエージェントに押し返さない")
        self.assertEqual(rec["rounds"][0]["infra_retries"], [[{"id": "C1", "summary": "container exited"}]])

    def test_infra_twice_stops_with_a_resume_summary(self):
        port = FakePort([turn()])
        rec = run(port, ScriptAdapter([{"C1": R("C1", "infra", summary="license")}]))
        self.assertEqual((rec["stop"], rec["decision"]), ("infra", "infra"))
        self.assertEqual(len(port.sent), 1)
        self.assertEqual(rec["resume"]["unmet"][0]["status"], "infra")
        self.assertEqual(rec["resume"]["choices"], ["discard", "fix_and_resume", "change_spec"])

    def test_feedback_marks_infra_and_lists_only_failing_regression_items(self):
        reg = core.Check("u1:C1", "tests", ["tests/test_x.py"], role="regression", items=[T_A, T_B])
        text = core.feedback([reg, core.Check("C9", "build", [])],
                             [R("u1:C1", "fail", passed=[T_A], failed=[T_B], errors=[f"{T_B} - E", "other - E"]),
                              R("C9", "infra", summary="docker error")])
        self.assertIn("u1:C1 [tests, regression]: failed", text)
        self.assertIn(f"{T_B} - E", text)
        self.assertNotIn("other - E", text)
        self.assertIn("C9 [build]: not measured (infrastructure: docker error)", text)


class SafetyStop(unittest.TestCase):
    RULES = {"same_diff": 2, "no_progress": 2, "protect": 2, "second": 2}

    def test_the_same_diff_twice_stops_with_h_stop_1(self):
        port = FakePort([turn(), turn(), turn()])
        ad = ScriptAdapter([{"C1": C1_FAIL, "C2": C2_PASS}], fps=["f0"])
        rec = run(port, ad, stop_rules=self.RULES)
        self.assertEqual((rec["stop"], rec["stop_rule"]), ("h_stop_1", "same_diff"))
        self.assertEqual(len(rec["rounds"]), 2, "単位の始まりと 2 回のターンで状態が変わらない")
        res = rec["resume"]
        self.assertEqual([u["check"] for u in res["unmet"]], ["C1"])
        self.assertEqual(res["unmet"][0]["failing"], [T_B])
        self.assertEqual(res["unmet"][0]["grounds"], ["R1"])
        self.assertEqual(res["changes"], {"modified": ["a.py"]})

    def test_no_progress_stops_when_the_failure_count_does_not_drop(self):
        port = FakePort([turn()] * 4)
        ad = ScriptAdapter([{"C1": C1_FAIL, "C2": C2_PASS}], fps=["f0", "f1", "f2", "f3", "f4"])
        rec = run(port, ad, stop_rules=self.RULES)
        self.assertEqual((rec["stop"], rec["stop_rule"]), ("h_stop_1", "no_progress"))
        self.assertEqual(len(rec["rounds"]), 3)

    def test_rules_off_keep_the_old_behaviour(self):
        port = FakePort([turn()] * 3)
        rec = run(port, ScriptAdapter([{"C1": C1_FAIL, "C2": C2_PASS}], fps=["f0"]), max_rounds=3)
        self.assertEqual(rec["stop"], "max_rounds")
        self.assertIn("resume", rec)

    def test_a_protected_file_change_is_pushed_back_once_then_stops(self):
        lg = unit_ledger()
        prot = core.Check("protect", "protect", ["u0:C1"], role="protect")
        ad = ScriptAdapter([{"C1": C1_PASS, "C2": C2_PASS,
                             "protect": R("protect", "fail", failed=["tests/test_old.py"],
                                          errors=["tests/test_old.py: changed"])}],
                           fps=["f0", "f1", "f2"])
        port = FakePort([turn()] * 3)
        rec = run(port, ad, lg=lg, checks=lg.checks + [prot], stop_rules=self.RULES)
        self.assertEqual((rec["stop"], rec["stop_rule"]), ("h_stop_1", "protect"))
        self.assertIn("protect [protect, protect]: failed", port.sent[1][1])


class SecondGateTests(unittest.TestCase):
    G1 = core.Check("G1", "invariant", ["no legacy np.random"], role="second")

    def test_called_once_after_the_first_gate_passes_and_pushes_back_on_a_counterexample(self):
        g = SecondGate([self.G1])
        ad = ScriptAdapter([{"C1": C1_FAIL, "C2": C2_PASS},
                            {"C1": C1_PASS, "C2": C2_PASS},
                            {"G1": R("G1", "fail", failed=["skutil/a.py:3"], errors=["skutil/a.py:3: np.random.seed"])},
                            {"C1": C1_PASS, "C2": C2_PASS, "G1": R("G1", "pass")}])
        port = FakePort([turn(), turn(), turn()])
        rec = run(port, ad, second_gate=g)
        self.assertEqual((rec["stop"], rec["decision"]), ("pass", "pass"))
        self.assertEqual(g.calls, 1, "押し返しの周回では呼び直さない")
        self.assertEqual(rec["rounds"][1]["second_gate"], ["G1"])
        self.assertIn("skutil/a.py:3: np.random.seed", port.sent[2][1])
        self.assertEqual(ad.asked[-1], ["C1", "C2", "G1"], "固めた検査を当て直す")
        self.assertEqual(rec["second_checks"][0]["origin"], "u1")

    def test_the_second_gate_limit_summarises_invariant_suspect_vs_implementation_bug(self):
        g = SecondGate([self.G1])
        g_fail = R("G1", "fail", failed=["case-7"], errors=["case-7: energy grew"])
        ad = ScriptAdapter([{"C1": C1_PASS, "C2": C2_PASS}, {"G1": g_fail},
                            {"C1": C1_PASS, "C2": C2_PASS, "G1": g_fail}], fps=["f0", "f1", "f2"])
        rec = run(FakePort([turn()] * 3), ad, second_gate=g, stop_rules={"second": 2})
        self.assertEqual((rec["stop"], rec["stop_rule"]), ("h_stop_1", "second_gate"))
        self.assertEqual(rec["resume"]["unmet"][0]["class"], "invariant_suspect")

        ad = ScriptAdapter([{"C1": C1_PASS, "C2": C2_PASS}, {"G1": g_fail},
                            {"C1": C1_FAIL, "C2": C2_PASS, "G1": g_fail}], fps=["f0", "f1", "f2"])
        rec = run(FakePort([turn()] * 3), ad, second_gate=SecondGate([self.G1]), stop_rules={"second": 2})
        cls = {u["check"]: u["class"] for u in rec["resume"]["unmet"]}
        self.assertEqual(cls, {"C1": None, "G1": "implementation_bug"})

    def test_new_second_checks_are_guarded_and_protected(self):
        ad = ScriptAdapter([{"C1": C1_PASS, "C2": C2_PASS},
                            {"G1": R("G1", "pass"), "protect": R("protect", "pass")}], protects=True)
        rec = run(FakePort([turn()]), ad, second_gate=SecondGate([self.G1]))
        self.assertEqual(rec["decision"], "pass")
        self.assertIn(("guard", ["G1"]), ad.calls)
        self.assertEqual(ad.asked[1], ["G1", "protect"])


def unit(name, supersedes=()):
    return core.Unit(unit_ledger(name), f"step {name}", list(supersedes))


class ChainTests(unittest.TestCase):
    def chain(self, port, ad, **kw):
        clock = iter(range(0, 10 ** 6, 10))
        return core.Chain(port, ad, expected_model=MODEL, clock=lambda: next(clock), **kw)

    def test_continue_sends_the_next_step_in_the_same_conversation_and_stacks_regression(self):
        port = FakePort([turn(), turn()])
        ad = ScriptAdapter([{"C1": C1_PASS, "C2": C2_PASS, "u1:C1": C1_PASS}])
        ch = self.chain(port, ad, context="continue")
        r1, r2 = ch.run(unit("u1"), 3600), ch.run(unit("u2"), 3600)
        self.assertEqual([s[0] for s in port.sent], ["start", "send"])
        self.assertEqual(port.sent[1][1], "step u2")
        self.assertEqual(r1["chain"]["regression_added"], ["u1:C1"])
        self.assertEqual(ch.regression[0].items, sorted([T_A, T_B]))
        self.assertIn("u1:C1", ad.asked[1], "次の単位で前の単位の項目を走らせる")
        self.assertEqual(r2["chain"]["regression_in"], ["u1:C1"])
        self.assertEqual([c.id for c in ch.regression], ["u1:C1", "u2:C1"])
        self.assertIn(("checkpoint", "u1:confirmed"), ad.calls)

    def test_fresh_and_handoff_open_a_new_conversation(self):
        port = FakePort([turn(), turn()])
        ch = self.chain(port, ScriptAdapter([{"C1": C1_PASS, "C2": C2_PASS, "u1:C1": C1_PASS}]), context="fresh")
        ch.run(unit("u1"), 3600)
        ch.run(unit("u2"), 3600)
        self.assertEqual([s[0] for s in port.sent], ["start", "start"])
        self.assertEqual(port.sent[1][1], "step u2")

        port = FakePort([turn(), turn()])
        ad = ScriptAdapter([{"C1": C1_PASS, "C2": C2_PASS, "u1:C1": C1_PASS}])
        ch = self.chain(port, ad, context="handoff")
        ch.run(unit("u1"), 3600)
        r2 = ch.run(unit("u2"), 3600)
        self.assertEqual([s[0] for s in port.sent], ["start", "start"])
        text = port.sent[1][1]
        self.assertTrue(text.startswith("step u2\n\nHarness handoff (WORKSPACE_HANDOFF.md)"))
        self.assertIn("- u1: pass (stop: pass, turns: 1)", text)
        self.assertIn("- u1:C1 [tests]: 2 items", text)
        for word in ("please", "must", "should", "make sure"):
            self.assertNotIn(word, text.lower())
        written = [c for c in ad.calls if c[0] == "write_handoff"]
        self.assertEqual(written[0][2]["units"][0]["decision"], "pass")
        self.assertEqual(r2["chain"]["handoff"]["chars"], len(written[0][1]))

    def test_supersede_retires_items_or_whole_checks_and_records_unknown_refs(self):
        port = FakePort([turn(), turn(), turn()])
        ad = ScriptAdapter([{"C1": C1_PASS, "C2": C2_PASS, "u1:C1": C1_PASS, "u2:C1": C1_PASS}])
        ch = self.chain(port, ad)
        ch.run(unit("u1"), 3600)
        r2 = ch.run(unit("u2", [f"u1:C1::{T_A}", "u9:C1"]), 3600)
        self.assertEqual([(r["ref"], r["found"]) for r in r2["chain"]["retired"]],
                         [(f"u1:C1::{T_A}", True), ("u9:C1", False)])
        self.assertEqual(ch.regression[0].items, [T_B])
        r3 = ch.run(unit("u3", ["u1:C1"]), 3600)
        self.assertEqual(r3["chain"]["regression_in"], ["u2:C1"])

    def test_only_checks_with_items_are_stacked(self):
        ch = self.chain(FakePort([turn()]), ScriptAdapter([{"C1": C1_PASS, "C2": C2_PASS}]))
        ch.run(unit("u1"), 3600)
        self.assertEqual([c.id for c in ch.regression], ["u1:C1"], "項目の無い C2 は積まない")

    def test_rollback_restores_a_failed_unit_when_not_halting(self):
        port = FakePort([turn()] * 4)
        ad = ScriptAdapter([{"C1": C1_FAIL, "C2": C2_PASS}])
        ch = self.chain(port, ad, rollback=True, halt_on_stop=False, max_rounds=2)
        rec = ch.run(unit("u1"), 3600)
        self.assertEqual(rec["stop"], "max_rounds")
        self.assertEqual([c for c in ad.calls if c[0] in ("checkpoint", "restore")],
                         [("checkpoint", "u1:start"), ("restore", "u1:start")])
        self.assertTrue(rec["chain"]["rolled_back"])
        self.assertIsNone(ch.halted)
        ch.run(unit("u2"), 3600)

    def test_without_the_gate_nothing_is_rolled_back_or_halted(self):
        ad = ScriptAdapter([{}])
        ch = self.chain(FakePort([turn(), turn()]), ad, gate=False, rollback=True)
        r1, r2 = ch.run(unit("u1"), 3600), ch.run(unit("u2"), 3600)
        self.assertEqual((r1["decision"], r2["decision"]), ("unverified", "unverified"))
        self.assertNotIn(("restore", "u1:start"), ad.calls)
        self.assertEqual(ad.n, 0)

    def test_halt_waits_for_a_human_choice(self):
        port = FakePort([turn()] * 6)
        ad = ScriptAdapter([{"C1": C1_FAIL, "C2": C2_PASS}, {"C1": C1_FAIL, "C2": C2_PASS},
                            {"C1": C1_PASS, "C2": C2_PASS}], fps=["f0"])
        ch = self.chain(port, ad, rollback=True, stop_rules={"same_diff": 2})
        rec = ch.run(unit("u1"), 3600)
        self.assertEqual(rec["stop"], "h_stop_1")
        self.assertIsNotNone(ch.halted)
        self.assertNotIn(("restore", "u1:start"), ad.calls, "人の裁定の前には戻さない")
        with self.assertRaises(RuntimeError):
            ch.run(unit("u2"), 3600)
        again = ch.resume("fix_and_resume", 3600, note="The expected value in test_b is 2.")
        self.assertEqual(port.sent[-1], ("send", "The expected value in test_b is 2.", port.sent[-1][2]))
        self.assertEqual((again["decision"], again["chain"]["resume"]["choice"]), ("pass", "fix_and_resume"))
        self.assertTrue(again["chain"]["confirmed"])
        ch.run(unit("u2"), 3600)

    def test_discard_and_change_spec(self):
        ad = ScriptAdapter([{"C1": C1_PASS, "C2": C2_PASS, "u1:C1": C1_PASS},
                            {"C1": C1_FAIL, "C2": C2_PASS, "u1:C1": C1_FAIL}], fps=["f0"])
        ch = self.chain(FakePort([turn()] * 8), ad, rollback=True, stop_rules={"same_diff": 1})
        ch.run(unit("u1"), 3600)
        rec = ch.run(unit("u2"), 3600)
        self.assertEqual(rec["stop"], "h_stop_1")
        self.assertIsNone(ch.resume("discard", 3600))
        self.assertIn(("restore", "u2:start"), ad.calls)

        ad2 = ScriptAdapter([{"C1": C1_PASS, "C2": C2_PASS}, {"C1": C1_FAIL, "C2": C2_PASS, "u1:C1": C1_FAIL},
                             {"C1": C1_PASS, "C2": C2_PASS}], fps=["f0"])
        ch2 = self.chain(FakePort([turn()] * 8), ad2, stop_rules={"same_diff": 1})
        ch2.run(unit("u1"), 3600)
        ch2.run(unit("u2"), 3600)
        again = ch2.resume("change_spec", 3600, note="Spec changed: test_b now expects 3.", supersedes=["u1:C1"])
        self.assertEqual(again["chain"]["resume"]["retired"], [{"ref": "u1:C1", "by": "u2", "found": True}])
        self.assertEqual(again["chain"]["regression_in"], [])
        self.assertEqual(again["decision"], "pass")

    def test_protect_check_covers_stacked_items(self):
        ad = ScriptAdapter([{"C1": C1_PASS, "C2": C2_PASS, "u1:C1": C1_PASS, "protect": R("protect", "pass")}],
                           protects=True)
        ch = self.chain(FakePort([turn(), turn()]), ad)
        ch.run(unit("u1"), 3600)
        self.assertIn(("guard", ["u1:C1"]), ad.calls)
        ch.run(unit("u2"), 3600)
        self.assertEqual(ad.asked[1], ["C1", "C2", "u1:C1", "protect"])

    def test_protect_off_keeps_checking_items_but_not_files(self):
        ad = ScriptAdapter([{"C1": C1_PASS, "C2": C2_PASS, "u1:C1": C1_PASS, "protect": R("protect", "fail")}],
                           protects=True)
        ch = self.chain(FakePort([turn(), turn()]), ad, protect=False)
        ch.run(unit("u1"), 3600)
        r2 = ch.run(unit("u2"), 3600)
        self.assertNotIn(("guard", ["u1:C1"]), ad.calls)
        self.assertEqual(ad.asked[1], ["C1", "C2", "u1:C1"], "積んだ項目は走らせ、protect は足さない")
        self.assertEqual(r2["decision"], "pass")

    def test_handoff_lists_the_first_errors_of_a_check_without_items(self):
        port = FakePort([turn(), turn(), turn()])
        build_fail = R("C1", "fail", failed=["C1"], errors=["./lib.go:66:9: undefined: foo"])
        ad = ScriptAdapter([{"C1": build_fail, "C2": C2_PASS}, {"C1": build_fail, "C2": C2_PASS},
                            {"C1": C1_PASS, "C2": C2_PASS}], fps=["f0"])
        ch = self.chain(port, ad, context="handoff", halt_on_stop=False, stop_rules={"same_diff": 1})
        ch.run(unit("u1"), 3600)
        ch.run(unit("u2"), 3600)
        self.assertIn("    - ./lib.go:66:9: undefined: foo", port.sent[-1][1])

    def test_run_chain_stops_at_a_halt(self):
        ad = ScriptAdapter([{"C1": C1_FAIL, "C2": C2_PASS}], fps=["f0"])
        ch = core.run_chain(FakePort([turn()] * 4), ad, [unit("u1"), unit("u2")], 3600, expected_model=MODEL,
                            stop_rules={"same_diff": 1})
        self.assertEqual(len(ch.records), 1)
        self.assertIsNotNone(ch.halted)

    def test_unknown_context_is_refused(self):
        with self.assertRaises(ValueError):
            core.Chain(FakePort([]), ScriptAdapter([{}]), expected_model=MODEL, context="summary")


if __name__ == "__main__":
    unittest.main()
