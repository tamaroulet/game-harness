"""差分バジェット（harness/pipeline.py の diff_budget_problem、ADR-003 §3.7、B3.3）。

    python -m unittest discover -s tests -v

**なぜ要るか**: 合計行数だけの上限は、機能の追加とリファクタリングを 1 つの単位に混ぜることを止めない。
feature は追加と削除を別枠で数え、refactor は合計で数えることを確かめる。境界は上限ちょうどと 1 行超え。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import pipeline  # noqa: E402

FEATURE = {"task_kind": "feature", "max_impl_lines": 250, "max_add_lines": 250, "max_del_lines": 100}


class Feature(unittest.TestCase):
    def test_limits_are_inclusive(self):
        self.assertIsNone(pipeline.diff_budget_problem(FEATURE, 250, 100))

    def test_one_line_over_either_limit_is_rejected(self):
        self.assertIn("追加", pipeline.diff_budget_problem(FEATURE, 251, 0))
        self.assertIn("削除", pipeline.diff_budget_problem(FEATURE, 0, 101))

    def test_total_may_exceed_the_old_single_limit(self):
        # 緩和：合計の上限は 250 から最大 350 に上がる（PR 本文に明記する）
        self.assertIsNone(pipeline.diff_budget_problem(FEATURE, 250, 100))

    def test_unit_without_task_kind_is_feature(self):
        legacy = {"max_impl_lines": 250}
        self.assertIsNone(pipeline.diff_budget_problem(legacy, 250, 0))
        self.assertIsNotNone(pipeline.diff_budget_problem(legacy, 251, 0))


class Refactor(unittest.TestCase):
    def test_total_is_limited(self):
        unit = dict(FEATURE, task_kind="refactor")
        self.assertIsNone(pipeline.diff_budget_problem(unit, 150, 100))
        self.assertIn("refactor", pipeline.diff_budget_problem(unit, 151, 100))


if __name__ == "__main__":
    unittest.main()
