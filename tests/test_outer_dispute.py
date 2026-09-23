"""Outer 段の回送（harness/pipeline.py、ADR-003 §3.4、B3.2）。

    python -m unittest discover -s tests -v

**なぜ要るか**: 見えない不変条件に続けて落ちるとき、実装役に試行を回しても情報は増えない。
Outer 段の反例による REJECT が設定の回数続いたら、不合格ではなく DISPUTE にする。数え方（ほかの
結果で 0 に戻る）と、条件が永久に成立しない設定を起動時に拒むことを確かめる。
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import pipeline  # noqa: E402


class Streak(unittest.TestCase):
    def test_counts_only_consecutive_outer_rejects(self):
        s = 0
        for verdict, gate, want in [("REJECT", "invariants", 1), ("REJECT", "invariants", 2),
                                    ("RETRY", "fast", 0), ("REJECT", "invariants", 1),
                                    ("REJECT", "holdout", 0)]:
            s = pipeline.next_outer_streak(s, verdict, gate)
            self.assertEqual(s, want, (verdict, gate))


class Config(unittest.TestCase):
    def test_absent_means_no_dispute(self):
        self.assertIsNone(pipeline.outer_dispute_after({"max_retry": 2}))

    def test_value_within_attempts_is_accepted(self):
        self.assertEqual(pipeline.outer_dispute_after({"max_retry": 2, "outer_dispute_after": 3}), 3)

    def test_value_that_can_never_be_reached_is_refused(self):
        for bad in (4, 0, True, "3"):
            with self.assertRaises(ValueError):
                pipeline.outer_dispute_after({"max_retry": 2, "outer_dispute_after": bad})

    def test_falling_blocks_is_configured_and_reachable(self):
        gates = json.loads((ROOT / "projects" / "falling-blocks" / "pipeline.json").read_text(encoding="utf-8"))["gates"]
        self.assertEqual(pipeline.outer_dispute_after(gates), 3)


if __name__ == "__main__":
    unittest.main()
