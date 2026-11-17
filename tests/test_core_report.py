"""core_record.json をまとめる道具（harness/core_report.py）の試験。

    python -m unittest tests.test_core_report -v
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import core_report  # noqa: E402


def check(cid, ok, failed=()):
    return {"id": cid, "ok": ok, "passed": [], "failed": list(failed), "errors": [], "summary": "", "seconds": 0.0, "raw": ""}


RECORD = {"unit": "u1", "arm": "agy-gate", "stop": "pass", "decision": "pass", "seconds": 100.0, "rounds": [
    {"turn": {"ok": True}, "verify": [check("C1", False, ["t::a", "t::b"]), check("C2", False, ["m.py:3:1: undefined name 'f'"])]},
    {"turn": {"ok": True}, "verify": [check("C1", True), check("C2", False, ["m.py:9:1: undefined name 'g'"])]},
    {"turn": {"ok": True}, "verify": [check("C1", True), check("C2", True)]}]}


class Summarize(unittest.TestCase):
    def test_counts_rounds_where_only_the_static_check_caught_a_problem(self):
        s = core_report.summarize(RECORD)
        self.assertEqual(s["c1_failed_by_round"], [2, 0, 0])
        self.assertEqual(s["c2_caught_rounds"], [1, 2])
        self.assertEqual(s["c2_only_rounds"], [2], "C1 が通り C2 が落ちた手番だけを、C2 の単独の拾いに数える")
        self.assertEqual(s["c2_fixed_next"], [False, True])
        self.assertEqual(len(s["c2_items"]), 2)

    def test_a_run_without_the_gate_has_no_checks(self):
        s = core_report.summarize({"unit": "u", "rounds": [{"turn": {"ok": True}, "verify": None}]})
        self.assertEqual(s["c1_failed_by_round"], [None])
        self.assertEqual(s["c2_only_rounds"], [])

    def test_collect_reads_every_record_under_the_run_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("a", "b"):
                p = Path(d, "run_outputs", name, "attempt-1")
                p.mkdir(parents=True)
                (p / "core_record.json").write_text(json.dumps(dict(RECORD, unit=name)), encoding="utf-8")
            rows = core_report.collect([d])
        self.assertEqual([r["unit"] for r in rows], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
