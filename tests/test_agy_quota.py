"""目盛りを読む部品（harness/agy_quota.py）の試験。agy は呼ばない（出力の形だけを確かめる）。

    python -m unittest tests.test_agy_quota -v
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import agy_quota  # noqa: E402


def quota_json(tokens=0, turns=0, buckets=("gemini-weekly", "gemini-5h", "3p-weekly")):
    return json.dumps({
        "status": "SUCCESS", "num_turns": turns,
        "usage": {"input_tokens": tokens, "output_tokens": 0, "total_tokens": tokens},
        "command": {"name": "quota", "data": {"description": "x", "groups": [
            {"buckets": [{"id": b, "remaining_fraction": 0.5 + i / 10, "reset_time": f"2026-11-2{i}T01:15:38Z"}
                         for i, b in enumerate(buckets)]}]}}})


class Parse(unittest.TestCase):
    def test_reads_the_two_gemini_buckets(self):
        rec = agy_quota.parse(quota_json())
        self.assertEqual(set(rec["buckets"]), {"gemini-weekly", "gemini-5h"})
        self.assertEqual(rec["buckets"]["gemini-5h"]["remaining_fraction"], 0.6)
        self.assertEqual(rec["buckets"]["gemini-weekly"]["reset_time"], "2026-11-20T01:15:38Z")
        self.assertEqual((rec["tokens"], rec["turns"]), (0, 0))

    def test_a_missing_bucket_is_an_error(self):
        with self.assertRaises(ValueError):
            agy_quota.parse(quota_json(buckets=("gemini-weekly",)))

    def test_token_use_is_flagged_in_the_line(self):
        rec = agy_quota.parse(quota_json(tokens=12, turns=1))
        rec["at"] = "t"
        self.assertIn("tokens 12", agy_quota.line("before", rec))
        rec = agy_quota.parse(quota_json())
        rec["at"] = "t"
        self.assertNotIn("tokens", agy_quota.line("before", rec))


if __name__ == "__main__":
    unittest.main()
