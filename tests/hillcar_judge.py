"""課題 B07（docs/bench/B07/spec.md）の判定役。総監督が書いた。保護パス。

公開の入口（REQ-01）だけを通して確かめる。入口の形（REQ-01・REQ-02）と静止（REQ-05）のほかは、
坂の角・トルクを走らせるたびに変わる種の乱数で、仕様の範囲の中から選ぶ（前もって計算した値を埋め込む実装は通らない）。
種は失敗の行に出る。同じ種で再現するときだけ、環境変数 HILLCAR_JUDGE_SEED に渡す（Gate 1 と照合は渡さない）。
名前が test_ で始まらないので CI の全件（harness.fastsuite）には入らず、本体が無い間も CI を赤にしない。
"""
import math
import os
import random
import unittest

DT = 0.002
G = 9.81
MASS = 1.5
EFFECTIVE = 1.7
RADIUS = 0.1
SEED = int(os.environ.get("HILLCAR_JUDGE_SEED") or random.SystemRandom().randrange(2 ** 32))


def lib():
    import hillcar
    return hillcar


def rng(salt):
    return random.Random(SEED * 100 + salt)


def lift(slope):
    """浮き上がりのトルク τ*(α)。"""
    return 0.05 * MASS * G * math.cos(math.radians(slope))


def accel(slope, torque):
    return (torque / RADIUS - MASS * G * math.sin(math.radians(slope))) / EFFECTIVE


class Entry(unittest.TestCase):
    """REQ-01・REQ-02"""

    def test_simulate_returns_one_state_per_step(self):
        states = lib().simulate(5.0, 0.05, 4)
        self.assertEqual(len(states), 4)
        for s in states:
            self.assertEqual(set(s), {"x", "z", "pitch", "flipped"})
            for key in ("x", "z", "pitch"):
                self.assertIsInstance(s[key], float)
            self.assertIsInstance(s["flipped"], bool)

    def test_out_of_range_and_wrong_types_raise_value_error(self):
        m = lib()
        bad = [lambda: m.simulate(-40.1, 0.0, 5), lambda: m.simulate(40.1, 0.0, 5),
               lambda: m.simulate(float("nan"), 0.0, 5), lambda: m.simulate(True, 0.0, 5),
               lambda: m.simulate("0", 0.0, 5), lambda: m.simulate(0.0, 1.21, 5),
               lambda: m.simulate(0.0, -1.21, 5), lambda: m.simulate(0.0, float("inf"), 5),
               lambda: m.simulate(0.0, False, 5), lambda: m.simulate(0.0, 0.0, 0),
               lambda: m.simulate(0.0, 0.0, 20001), lambda: m.simulate(0.0, 0.0, 5.0),
               lambda: m.simulate(0.0, 0.0, True), lambda: m.simulate(None, 0.0, 5)]
        for i, call in enumerate(bad):
            with self.subTest(i=i), self.assertRaises(ValueError):
                call()


class Determinism(unittest.TestCase):
    """REQ-03"""

    def test_two_calls_are_bit_identical(self):
        r = rng(1)
        slope, torque = r.uniform(-40.0, 40.0), r.uniform(-1.2, 1.2)
        self.assertEqual(lib().simulate(slope, torque, 800), lib().simulate(slope, torque, 800))


class Start(unittest.TestCase):
    """REQ-04・REQ-05"""

    def test_the_first_state_is_the_placed_car(self):
        r = rng(2)
        for _ in range(3):
            slope, torque = r.uniform(-40.0, 40.0), r.uniform(-1.2, 1.2)
            s = lib().simulate(slope, torque, 1)[0]
            with self.subTest(seed=SEED, slope=slope, torque=torque):
                self.assertLessEqual(abs(s["x"]), 0.001)
                self.assertLessEqual(abs(s["z"] - 0.1), 0.001)
                self.assertLessEqual(abs(s["pitch"]), 0.01)
                self.assertFalse(s["flipped"])

    def test_a_car_on_flat_ground_without_torque_stays(self):
        for i, s in enumerate(lib().simulate(0.0, 0.0, 1000)):
            self.assertLessEqual(abs(s["x"]), 1e-6, i)
            self.assertLessEqual(abs(s["pitch"]), 1e-6, i)
            self.assertFalse(s["flipped"], i)


class Rolling(unittest.TestCase):
    """REQ-06"""

    def roll(self, slope, torque):
        a = accel(slope, torque)
        states = lib().simulate(slope, torque, 1000)
        with self.subTest(seed=SEED, slope=slope, torque=torque):
            for k, s in enumerate(states, 1):
                t = DT * k
                want = 0.5 * a * t * t
                self.assertLessEqual(abs(s["x"] - want), 0.03 * abs(want) + 0.1, (k, s["x"], want))
                self.assertLessEqual(abs(s["pitch"]), 0.02, k)
                self.assertFalse(s["flipped"], k)
        return states

    def test_the_car_rolls_with_the_analytic_acceleration(self):
        r = rng(3)
        for _ in range(3):
            slope = r.uniform(-20.0, 40.0)
            self.roll(slope, r.uniform(-0.1, 0.8 * lift(slope)))

    def test_a_slope_of_30_degrees_or_more_is_not_climbed(self):
        r = rng(4)
        for _ in range(2):
            slope = r.uniform(30.0, 40.0)
            states = self.roll(slope, r.uniform(0.0, 0.8 * lift(slope)))
            self.assertLess(states[-1]["x"], 0.0)


class Tipping(unittest.TestCase):
    """REQ-07"""

    def test_above_the_lift_torque_the_car_flips_toward_the_torque(self):
        r = rng(6)
        for sign in (1, -1):
            slope = r.uniform(-20.0, 20.0)
            torque = sign * r.uniform(1.5 * lift(slope), 1.2)
            states = lib().simulate(slope, torque, 600)
            with self.subTest(seed=SEED, slope=slope, torque=torque):
                self.assertTrue(states[-1]["flipped"])
                first = next(k for k, s in enumerate(states) if s["flipped"])
                self.assertTrue(all(s["flipped"] for s in states[first:]))
                tilted = next(s["pitch"] for s in states if abs(s["pitch"]) > 0.5)
                self.assertGreater(sign * tilted, 0.0)


class Mirror(unittest.TestCase):
    """REQ-08"""

    def test_reversing_the_slope_and_the_torque_mirrors_the_motion(self):
        r = rng(7)
        slope, torque = r.uniform(-10.0, 10.0), r.uniform(-0.1, 0.1)
        base = lib().simulate(slope, torque, 500)
        mirror = lib().simulate(-slope, -torque, 500)
        with self.subTest(seed=SEED, slope=slope, torque=torque):
            for k, (a, b) in enumerate(zip(base, mirror), 1):
                self.assertLessEqual(abs(b["x"] + a["x"]), 0.01 * abs(a["x"]) + 0.02, k)
                self.assertLessEqual(abs(b["pitch"] + a["pitch"]), 0.001, k)
                self.assertLessEqual(abs(b["z"] - a["z"]), 0.0001, k)


if __name__ == "__main__":
    unittest.main()
