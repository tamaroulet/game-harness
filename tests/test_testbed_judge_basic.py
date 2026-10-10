"""試験台の判定役：個別の要件。REQ-01, REQ-02, REQ-03, REQ-04 を確かめる。

    python -m unittest tests.test_testbed_judge_basic

本体（testbed）が無いあいだは skip する。入口や返り値の形の欠落は失敗にする。
"""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from testbed_judge_util import JudgeCase, skip_without_body  # noqa: E402


@skip_without_body
class Req01Interface(JudgeCase):
    """REQ-01：返り値の長さが steps、各状態が qpos 7 個と qvel 6 個。"""

    def test_length_and_shape(self):
        for steps in (1, 5, 50):
            states = self.run_body(0.5, steps)
            self.assertEqual(len(states), steps)


@skip_without_body
class Req02Determinism(JudgeCase):
    """REQ-02：同じ引数で 2 回呼ぶとビット単位で一致する。"""

    def test_bitwise_same(self):
        self.assert_deterministic(1.0, 300)


@skip_without_body
class Req03NoPenetration(JudgeCase):
    """REQ-03：全歩で有限、qpos の z が 0.03 m 以上。"""

    def test_finite_and_above_floor(self):
        self.assert_no_penetration(self.run_body(1.0, 1000))


@skip_without_body
class Req04AtRest(JudgeCase):
    """REQ-04：height 1.0・steps 2000 の最後で静止する。"""

    def test_rests_on_floor(self):
        qpos, qvel = self.run_body(1.0, 2000)[-1]
        speed = math.sqrt(sum(v * v for v in qvel[:3]))
        self.assertLess(speed, 0.01)
        self.assertGreaterEqual(qpos[2], 0.04)
        self.assertLessEqual(qpos[2], 0.06)


if __name__ == "__main__":
    unittest.main()
