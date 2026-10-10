"""試験台の判定役：性質のテストと浸漬試験。REQ-02, REQ-03 を確かめる。

    python -m unittest tests.test_testbed_judge_stress

本体（testbed）が無いあいだは skip する。
"""
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from testbed_judge_util import JudgeCase, skip_without_body  # noqa: E402

SEED = 20261010


@skip_without_body
class PropertyHeights(JudgeCase):
    """性質：固定のシードで選んだ範囲内（0.2 以上 2.0 以下）の複数の height で REQ-02 と REQ-03 が成り立つ。"""

    def test_determinism_and_floor_for_random_heights(self):
        rng = random.Random(SEED)
        heights = [round(rng.uniform(0.2, 2.0), 6) for _ in range(5)]
        for h in heights:
            with self.subTest(height=h):
                self.assert_deterministic(h, 400)
                self.assert_no_penetration(self.run_body(h, 400), f"height={h} ")


@skip_without_body
class Soak(JudgeCase):
    """浸漬試験：height 2.0 で 20000 歩進めても REQ-03 が全歩で成り立つ。"""

    def test_long_run_stays_above_floor(self):
        self.assert_no_penetration(self.run_body(2.0, 20000))


if __name__ == "__main__":
    unittest.main()
