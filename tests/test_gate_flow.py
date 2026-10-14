"""gate_flow.place（関門と中間関門の並び、タスクの置き場、現在の関門）の検査。台帳は小さな偽物で、git は呼ばない。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import gate_flow as gf  # noqa: E402


def task(tid, status="pending", deps=(), rung=None, by=None):
    t = {"id": tid, "status": status, "depends_on": list(deps)}
    if rung:
        t["rung"] = rung
    if by:
        t["by"] = by
    return t


def ledger(m2_status="completed", t_status="completed"):
    return {"tasks": [
        task("T1", "completed"),
        task("M1", "completed", ["T1"], by="pilot"),
        task("TG", "completed"),
        task("G1", "completed", ["M1", "TG"], rung="R1", by="pilot"),
        task("T2", t_status, ["G1"]),
        task("M2", m2_status, ["T2", "TG"], by="pilot"),
        task("G2", "completed", ["M2"], rung="R2", by="pilot"),
        task("P", "completed", by="pilot"),
    ]}


class Place(unittest.TestCase):
    def setUp(self):
        self.gates, self.current = gf.place(ledger())
        self.by = {g["id"]: g for g in self.gates}

    def test_gates_and_mids_are_ordered(self):
        self.assertEqual([g["id"] for g in self.gates], ["G1", "G2"])
        self.assertEqual([m["id"] for m in self.by["G1"]["mids"]], ["M1"])
        self.assertEqual([m["id"] for m in self.by["G2"]["mids"]], ["M2"])

    def test_rung_pilot_task_is_a_gate_not_a_mid(self):
        self.assertNotIn("G1", [m["id"] for g in self.gates for m in g["mids"]])

    def test_pilot_task_nobody_waits_for_is_not_a_mid(self):
        self.assertNotIn("P", [m["id"] for g in self.gates for m in g["mids"]])

    def test_task_waited_by_gate_and_later_mid_goes_under_earlier_gate(self):
        self.assertIn("TG", self.by["G1"]["tasks"])
        self.assertNotIn("TG", self.by["G2"]["tasks"])

    def test_task_waited_by_gate_and_its_own_mid_goes_under_the_mid(self):
        self.assertEqual(self.by["G2"]["mids"][0]["tasks"].count("T2"), 1)
        self.assertNotIn("T2", self.by["G2"]["tasks"])

    def test_gate_and_mid_tasks_are_in_no_list(self):
        listed = [i for g in self.gates for i in g["tasks"] + [j for m in g["mids"] for j in m["tasks"]]]
        for tid in ("G1", "G2", "M1", "M2"):
            self.assertNotIn(tid, listed)

    def test_current_gate_follows_open_task_under_a_mid(self):
        self.assertIsNone(self.current)
        self.assertEqual(gf.place(ledger(t_status="pending"))[1], "G2")


if __name__ == "__main__":
    unittest.main()
