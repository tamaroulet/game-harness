"""前提の台帳（W-123、ADR-004 の K-15）：docs/design/premises.yaml の形と、期限切れの検出（harness/premises.py）。

    python -m unittest tests.test_premises -v
"""
import contextlib
import copy
import datetime
import io
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import premises  # noqa: E402

GOOD = {"premises": [
    {"id": "P-001", "text": "t", "kind": "research", "basis": ["R-20261023-review-tiers#C1"],
     "checked_at": "2026-10-23", "recheck_by": "2026-11-22", "used_by": ["ADR-004 K-1"]},
    {"id": "P-002", "text": "t", "kind": "assumption", "basis": [],
     "checked_at": "2026-10-23", "recheck_by": "2026-11-06", "used_by": ["W-130"]},
]}


def broken(**fields):
    state = copy.deepcopy(GOOD)
    state["premises"][0].update(fields)
    return state


class Shape(unittest.TestCase):
    def test_the_ledger_in_the_repository_is_well_formed(self):
        self.assertEqual(premises.problems(premises.load()), [])

    def test_a_good_ledger_has_no_problems(self):
        self.assertEqual(premises.problems(GOOD), [])

    def test_each_wrong_field_is_named(self):
        cases = {"id": ("P-1", "P-nnn"), "kind": ("guess", "kind"), "text": ("", "text"),
                 "basis": (["not-a-claim"], "主張の ID"), "checked_at": ("昨日", "日付"),
                 "recheck_by": ("2026-10-01", "より前"), "used_by": ([], "used_by")}
        for key, (value, word) in cases.items():
            with self.subTest(key=key):
                found = premises.problems(broken(**{key: value}))
                self.assertTrue(any(word in f for f in found), found)

    def test_a_research_premise_needs_a_claim(self):
        self.assertTrue(premises.problems(broken(basis=[])))

    def test_duplicate_ids_and_an_empty_ledger_are_problems(self):
        dup = copy.deepcopy(GOOD)
        dup["premises"][1]["id"] = "P-001"
        self.assertTrue(any("重複" in f for f in premises.problems(dup)))
        self.assertTrue(premises.problems({"premises": []}))


class Expiry(unittest.TestCase):
    def test_a_premise_past_its_date_is_expired(self):
        self.assertEqual(premises.expired(GOOD, datetime.date(2026, 11, 7)), ["P-002"])
        self.assertEqual(premises.expired(GOOD, datetime.date(2026, 11, 6)), [])

    def test_strict_fails_only_on_expiry_and_plain_check_only_reports_it(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "premises.yaml"
            path.write_text(yaml.safe_dump(GOOD, allow_unicode=True), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                plain = premises.main(["check", "--path", str(path), "--today", "2026-12-01"])
                strict = premises.main(["check", "--path", str(path), "--today", "2026-12-01", "--strict"])
                fresh = premises.main(["check", "--path", str(path), "--today", "2026-10-24", "--strict"])
            self.assertEqual((plain, strict, fresh), (0, 1, 0))
            self.assertIn("期限切れ: P-001", out.getvalue())


if __name__ == "__main__":
    unittest.main()
