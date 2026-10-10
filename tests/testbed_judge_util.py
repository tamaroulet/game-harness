"""試験台の判定役が共有する道具（テストではない）。本体 `testbed.run` の公開の入口だけを使う。"""
import math
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import testbed
except ModuleNotFoundError as e:
    if e.name != "testbed":
        raise
    testbed = None

ABSENT = "本体（testbed）の不在"
skip_without_body = unittest.skipIf(testbed is None, ABSENT)


class JudgeCase(unittest.TestCase):
    def run_body(self, height, steps):
        """run を呼び、各状態を (qpos, qvel) の float 列の組にして返す。形が違えば失敗にする。"""
        run = getattr(testbed, "run", None)
        self.assertTrue(callable(run), "testbed に入口 run(height, steps) が無い")
        states = run(height, steps)
        try:
            states = list(states)
        except TypeError:
            self.fail("run の返り値が列ではない")
        self.assertEqual(len(states), steps, "返り値の長さが steps と違う")
        return [self.read_state(s, i) for i, s in enumerate(states)]

    def read_state(self, state, i):
        out = []
        for name, n in (("qpos", 7), ("qvel", 6)):
            if isinstance(state, dict):
                self.assertIn(name, state, f"{i} 番目の状態に {name} が無い")
                raw = state[name]
            else:
                self.assertTrue(hasattr(state, name), f"{i} 番目の状態に {name} が無い")
                raw = getattr(state, name)
            try:
                vals = [float(v) for v in raw]
            except (TypeError, ValueError):
                self.fail(f"{i} 番目の状態の {name} が数の列ではない")
            self.assertEqual(len(vals), n, f"{i} 番目の状態の {name} の長さが {n} でない")
            out.append(vals)
        return tuple(out)

    @staticmethod
    def bits(states):
        return [[struct.pack("<%dd" % len(v), *v) for v in s] for s in states]

    def assert_deterministic(self, height, steps):
        a = self.bits(self.run_body(height, steps))
        b = self.bits(self.run_body(height, steps))
        self.assertEqual(a, b, f"height={height} steps={steps} で 2 回の結果がビット単位で一致しない")

    def assert_no_penetration(self, states, label=""):
        for i, (qpos, qvel) in enumerate(states):
            self.assertTrue(all(math.isfinite(v) for v in qpos + qvel),
                            f"{label}{i + 1} 歩目に有限でない値がある")
            self.assertGreaterEqual(qpos[2], 0.03, f"{label}{i + 1} 歩目で中心の高さが 0.03 m 未満")
