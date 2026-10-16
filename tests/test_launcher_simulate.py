"""launcher.simulate（P-01）。

    python -m unittest tests.test_launcher_simulate -v

- 範囲の端は通り、外・nan・inf・bool・数でない値・整数でない steps は ValueError
- 同じ引数なら全値がビット単位で一致する
- y は常に 0 付近、x は非減少
- 低い打ち上げ（speed·sin(angle) <= 4）では z が 0 以上で、着地の 1000 歩後に z が 0.02 付近
"""
import math
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import launcher  # noqa: E402


def bits(result):
    return [struct.pack("<6d", *r["pos"], *r["vel"]) for r in result]


class RangeTests(unittest.TestCase):
    def test_edges_accepted(self):
        for angle in (5, 85, 5.0, 85.0):
            self.assertEqual(len(launcher.simulate(angle, 1, 3)), 3)
        for speed in (0.5, 10, 10.0):
            self.assertEqual(len(launcher.simulate(45, speed, 3)), 3)
        self.assertEqual(len(launcher.simulate(45, 1, 1)), 1)
        self.assertEqual(len(launcher.simulate(45, 1, 20000)), 20000)

    def test_outside_rejected(self):
        bad = [
            (4.999, 1, 5), (85.001, 1, 5), (45, 0.499, 5), (45, 10.001, 5),
            (45, 1, 0), (45, 1, 20001), (45, 1, -1),
        ]
        for args in bad:
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    launcher.simulate(*args)

    def test_nan_inf_rejected(self):
        for v in (math.nan, math.inf, -math.inf):
            with self.subTest(v=v):
                with self.assertRaises(ValueError):
                    launcher.simulate(v, 1, 5)
                with self.assertRaises(ValueError):
                    launcher.simulate(45, v, 5)

    def test_bool_rejected(self):
        for args in ((True, 1, 5), (45, True, 5), (45, 1, True), (45, 1, False)):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    launcher.simulate(*args)

    def test_non_number_rejected(self):
        for args in (("45", 1, 5), (45, "1", 5), (None, 1, 5), (45, None, 5), (45, 1, "5"), (45, 1, None)):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    launcher.simulate(*args)

    def test_steps_must_be_int(self):
        for steps in (5.0, 5.5, math.nan, math.inf):
            with self.subTest(steps=steps):
                with self.assertRaises(ValueError):
                    launcher.simulate(45, 1, steps)

    def test_huge_int_rejected(self):
        with self.assertRaises(ValueError):
            launcher.simulate(10 ** 400, 1, 5)


class ShapeTests(unittest.TestCase):
    def test_shape_and_finite(self):
        result = launcher.simulate(30, 5, 50)
        self.assertEqual(len(result), 50)
        for r in result:
            for key in ("pos", "vel"):
                self.assertEqual(len(r[key]), 3)
                for v in r[key]:
                    self.assertIsInstance(v, float)
                    self.assertTrue(math.isfinite(v))


class DeterminismTests(unittest.TestCase):
    def test_bitwise_identical(self):
        for args in ((30, 5, 500), (85, 10, 2000), (5, 0.5, 100)):
            with self.subTest(args=args):
                self.assertEqual(bits(launcher.simulate(*args)), bits(launcher.simulate(*args)))


GRID = [(a, s) for a in (5, 15, 30, 45, 60, 85) for s in (0.5, 2, 5, 10)]


class YAndXTests(unittest.TestCase):
    def test_y_pinned_and_x_monotone(self):
        for angle, speed in GRID:
            with self.subTest(angle=angle, speed=speed):
                result = launcher.simulate(angle, speed, 3000)
                prev_x = None
                for r in result:
                    self.assertLessEqual(abs(r["pos"][1]), 1e-9)
                    self.assertLessEqual(abs(r["vel"][1]), 1e-9)
                    if prev_x is not None:
                        self.assertGreaterEqual(r["pos"][0], prev_x)
                    prev_x = r["pos"][0]


def landing_index(result):
    """z > 0.02 の歩の次に初めて z <= 0.02 になる歩の添字（無ければ None）。"""
    was_above = False
    for i, r in enumerate(result):
        z = r["pos"][2]
        if z > 0.02:
            was_above = True
        elif was_above:
            return i
    return None


class FloorTests(unittest.TestCase):
    def test_stays_above_floor_and_rests(self):
        checked = 0
        for angle, speed in GRID:
            if speed * math.sin(math.radians(angle)) > 4:
                continue
            with self.subTest(angle=angle, speed=speed):
                result = launcher.simulate(angle, speed, 20000)
                for r in result:
                    self.assertGreaterEqual(r["pos"][2], 0.0)
                land = landing_index(result)
                if land is None:
                    continue
                after = land + 1000
                self.assertLess(after, len(result))
                self.assertTrue(0.015 <= result[after]["pos"][2] <= 0.025)
                checked += 1
        self.assertGreater(checked, 0)

    def test_short_run_stays_on_floor(self):
        result = launcher.simulate(5, 0.5, 5)
        for r in result:
            self.assertGreaterEqual(r["pos"][2], 0.0)


if __name__ == "__main__":
    unittest.main()
