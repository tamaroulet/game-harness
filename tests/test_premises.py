"""前提の台帳（W-123、ADR-004 の K-15）：docs/design/premises.yaml の形と、期限切れの検出（harness/premises.py）。

    python -m unittest tests.test_premises -v
"""
import contextlib
import copy
import datetime
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import premises  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import quiet  # noqa: E402

GOOD = {"premises": [
    {"id": "P-001", "text": "t", "kind": "research", "basis": ["R-20261023-review-tiers#C1", "R-20261023-review-tiers#C2"],
     "checked_at": "2026-10-23", "recheck_by": "2026-11-22", "used_by": ["ADR-004 K-1"]},
    {"id": "P-002", "text": "t", "kind": "assumption", "basis": [],
     "checked_at": "2026-10-23", "recheck_by": "2026-11-06", "used_by": ["W-130"]},
]}


def strong_claim(cid, url, **kw):
    return {"id": cid, "source_url": url, "source_tier": "primary", "kind": "fact", "verify": {"status": "verified"}, **kw}


def write_records(d, claims):
    """GOOD の P-001 が引く記録を d に書き、d を返す。"""
    Path(d).mkdir(parents=True, exist_ok=True)
    rec = {"id": "R-20261023-review-tiers", "claims": claims}
    (Path(d) / "R-20261023-review-tiers.json").write_text(json.dumps(rec), encoding="utf-8")
    return str(d)


STRONG = [strong_claim("C1", "https://www.anthropic.com/a"), strong_claim("C2", "https://arxiv.org/abs/1")]


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
                rec = ["--records", write_records(Path(d) / "research", STRONG)]
                plain = premises.main(["check", "--path", str(path), "--today", "2026-12-01", *rec])
                strict = premises.main(["check", "--path", str(path), "--today", "2026-12-01", "--strict", *rec])
                fresh = premises.main(["check", "--path", str(path), "--today", "2026-10-24", "--strict", *rec])
            self.assertEqual((plain, strict, fresh), (0, 1, 0))
            self.assertIn("期限切れ: P-001", out.getvalue())


class Strength(quiet.Quiet, unittest.TestCase):
    """根拠の強さ（docs/design/research_protocol.md §6）。"""

    def weak(self, claims):
        with tempfile.TemporaryDirectory() as d:
            return premises.weak(GOOD, premises.load_claims(write_records(d, claims)))

    def test_two_verified_primary_domains_are_strong(self):
        self.assertEqual(self.weak(STRONG), [])

    def test_one_domain_is_weak(self):
        same = [strong_claim("C1", "https://www.anthropic.com/a"), strong_claim("C2", "https://anthropic.com/b")]
        self.assertEqual([p for p, _ in self.weak(same)], ["P-001"])

    def test_vendor_claims_marketing_and_unverified_do_not_count(self):
        for bad in ({"kind": "vendor_claim"}, {"source_tier": "marketing"}, {"verify": {"status": "quote_not_found"}},
                    {"source_tier": None}):
            claims = [STRONG[0], {**STRONG[1], **bad}]
            self.assertEqual([p for p, _ in self.weak(claims)], ["P-001"], bad)

    def test_a_claim_missing_from_the_records_is_weak(self):
        self.assertIn("記録に無い", self.weak(STRONG[:1])[0][1])

    def test_strict_fails_on_a_weak_premise(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "premises.yaml"
            path.write_text(yaml.safe_dump(GOOD, allow_unicode=True), encoding="utf-8")
            rec = ["--records", write_records(Path(d) / "research", STRONG[:1])]
            self.assertEqual(premises.main(["check", "--path", str(path), "--today", "2026-10-24", *rec]), 0)
            self.assertEqual(premises.main(["check", "--path", str(path), "--today", "2026-10-24", "--strict", *rec]), 1)
            self.assertIn("弱い: P-001", self.written())


if __name__ == "__main__":
    unittest.main()
