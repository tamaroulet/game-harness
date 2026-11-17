"""制御核（harness/core.py）の試験。実行環境とモデルは偽物で置き換える（利用枠 0）。

    python -m unittest tests.test_core -v
"""
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
        self.assertTrue(core.decide(lg.checks, advisory_fail), "助言の確かめは合否を止めない")

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


if __name__ == "__main__":
    unittest.main()
