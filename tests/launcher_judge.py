"""課題 B06（docs/bench/B06/spec.md）の判定役。総監督が書いた。保護パス。

公開の入口（REQ-01）だけを通して確かめる。決定論（REQ-03）と入口の形（REQ-01・REQ-02）のほかは、入力を走らせるたびに
変わる種の乱数で、仕様の範囲の中から選ぶ（前もって計算した値を埋め込む実装は通らない）。種は失敗の行に出る。
同じ種で再現するときだけ、環境変数 LAUNCHER_JUDGE_SEED に渡す（Gate 1 と照合は渡さない）。
名前が test_ で始まらないので CI の全件（harness.fastsuite）には入らず、本体が無い間も CI を赤にしない。
走らせるのは、台帳の W-21 の検証コマンドと、受信箱の照合 checks/r3a-b06.json（report.md の「## 照合」）。
"""
import math
import os
import random
import unittest

G = 9.81
DT = 0.002
R0 = 0.02
SEED = int(os.environ.get("LAUNCHER_JUDGE_SEED") or random.SystemRandom().randrange(2 ** 32))


def lib():
    import launcher
    return launcher


def crossing(states):
    """着地の歩（0 歩目＝打ち出しの位置）と、そこで z について線形に補間した x。無ければ (None, None)。"""
    prev_x, prev_z = 0.0, R0
    for i, s in enumerate(states, start=1):
        x, z = s["pos"][0], s["pos"][2]
        if prev_z > R0 >= z:
            return i, prev_x + (x - prev_x) * (prev_z - R0) / (prev_z - z)
        prev_x, prev_z = x, z
    return None, None


def flight_steps(angle, speed):
    """着地を含むのに足りる歩数（解析の滞空時間の 1.2 倍と 50 歩）。"""
    return int(1.2 * 2 * speed * math.sin(math.radians(angle)) / G / DT) + 50


def analytic(angle, speed):
    return speed ** 2 * math.sin(math.radians(2 * angle)) / G


def rng(salt):
    return random.Random(SEED * 100 + salt)


def samples(n, angle, speed, salt):
    r = rng(salt)
    return [(r.uniform(*angle), r.uniform(*speed)) for _ in range(n)]


class Entry(unittest.TestCase):
    """REQ-01・REQ-02"""

    def test_simulate_returns_one_state_per_step(self):
        states = lib().simulate(45, 3, 7)
        self.assertEqual(len(states), 7)
        for s in states:
            self.assertEqual((len(s["pos"]), len(s["vel"])), (3, 3))
            for v in list(s["pos"]) + list(s["vel"]):
                self.assertIsInstance(v, float)

    def test_landing_and_aim_return_floats(self):
        self.assertIsInstance(lib().landing(45, 3), float)
        self.assertIsInstance(lib().aim(0.5, 3), float)

    def test_out_of_range_and_wrong_types_raise_value_error(self):
        m = lib()
        bad = [lambda: m.simulate(4.9, 3, 10), lambda: m.simulate(85.1, 3, 10), lambda: m.simulate(45, 0.4, 10),
               lambda: m.simulate(45, 10.1, 10), lambda: m.simulate(45, 3, 0), lambda: m.simulate(45, 3, 20001),
               lambda: m.simulate(45, 3, 1.5), lambda: m.simulate(45, 3, True), lambda: m.simulate("45", 3, 10),
               lambda: m.simulate(True, 3, 10), lambda: m.landing(45, float("nan")), lambda: m.landing(90, 3),
               lambda: m.aim(0, 3), lambda: m.aim(-1, 3), lambda: m.aim(float("inf"), 3), lambda: m.aim(True, 3),
               lambda: m.aim(1, 11)]
        for i, call in enumerate(bad):
            with self.subTest(i=i), self.assertRaises(ValueError):
                call()


class Determinism(unittest.TestCase):
    """REQ-03"""

    def test_two_calls_are_bit_identical(self):
        m = lib()
        for angle, speed in [(30, 3), (60, 7.5), (12, 0.8)]:
            with self.subTest(angle=angle, speed=speed):
                self.assertEqual(m.simulate(angle, speed, 300), m.simulate(angle, speed, 300))
                self.assertEqual(m.landing(angle, speed).hex(), m.landing(angle, speed).hex())
        self.assertEqual(m.aim(1.3, 5), m.aim(1.3, 5))


class Flight(unittest.TestCase):
    """REQ-04・REQ-05"""

    def test_the_flight_stays_in_the_plane_and_moves_forward(self):
        for angle, speed in samples(5, (5, 85), (0.5, 10), 2):
            states = lib().simulate(angle, speed, flight_steps(angle, speed))
            land, _ = crossing(states)
            with self.subTest(seed=SEED, angle=angle, speed=speed):
                self.assertIsNotNone(land)
                prev_x = 0.0
                for s in states[:land - 1]:
                    self.assertTrue(all(math.isfinite(v) for v in list(s["pos"]) + list(s["vel"])))
                    self.assertLessEqual(abs(s["pos"][1]), 1e-9)
                    self.assertLessEqual(abs(s["vel"][1]), 1e-9)
                    self.assertGreaterEqual(s["pos"][0], prev_x)
                    prev_x = s["pos"][0]

    def test_landing_is_the_interpolated_crossing_of_simulate(self):
        for angle, speed in samples(5, (5, 85), (0.5, 10), 3):
            _, x = crossing(lib().simulate(angle, speed, flight_steps(angle, speed)))
            with self.subTest(seed=SEED, angle=angle, speed=speed):
                self.assertAlmostEqual(lib().landing(angle, speed), x, delta=1e-9)

    def test_landing_matches_the_analytic_range(self):
        for angle, speed in samples(8, (15, 75), (1, 8), 4):
            r = analytic(angle, speed)
            tol = 0.01 * r + speed * math.cos(math.radians(angle)) * DT
            with self.subTest(seed=SEED, angle=angle, speed=speed):
                self.assertAlmostEqual(lib().landing(angle, speed), r, delta=tol)

    def test_complementary_angles_land_alike(self):
        for angle, speed in samples(4, (20, 40), (2, 8), 5):
            r = analytic(angle, speed)
            with self.subTest(seed=SEED, angle=angle, speed=speed):
                self.assertAlmostEqual(lib().landing(angle, speed), lib().landing(90 - angle, speed),
                                       delta=0.02 * r + 2 * speed * DT)

    def test_twice_the_speed_lands_four_times_as_far(self):
        for angle, speed in samples(4, (20, 70), (1, 4), 6):
            r = analytic(angle, speed)
            with self.subTest(seed=SEED, angle=angle, speed=speed):
                self.assertAlmostEqual(lib().landing(angle, 2 * speed), 4 * lib().landing(angle, speed),
                                       delta=0.04 * r + 4 * speed * DT)


class Floor(unittest.TestCase):
    """REQ-06"""

    def test_the_ball_never_goes_below_the_floor_and_stays_on_it(self):
        r, cases = rng(7), []
        for near_the_limit in (True, True, False, False):
            angle = r.uniform(5, 85)
            top = min(10, 4 / math.sin(math.radians(angle)))   # speed·sin(angle) ≤ 4 の上端
            cases.append((angle, r.uniform(0.9 * top, top) if near_the_limit else r.uniform(0.5, top)))
        for angle, speed in cases:
            states = lib().simulate(angle, speed, flight_steps(angle, speed) + 1000)
            land, _ = crossing(states)
            with self.subTest(seed=SEED, angle=angle, speed=speed):
                self.assertIsNotNone(land)
                self.assertGreaterEqual(min(s["pos"][2] for s in states), 0.0)
                z = states[land + 1000 - 1]["pos"][2]
                self.assertTrue(0.015 <= z <= 0.025, z)


class Aim(unittest.TestCase):
    """REQ-07・REQ-08"""

    def test_aim_hits_the_target_with_the_lower_angle(self):
        r = rng(8)
        for _ in range(6):
            speed, ratio = r.uniform(2, 10), r.uniform(0.25, 0.95)
            distance = ratio * speed ** 2 / G
            angle = lib().aim(distance, speed)
            with self.subTest(seed=SEED, speed=speed, distance=distance):
                self.assertIsNotNone(angle)
                self.assertTrue(5 <= angle <= 45, angle)
                self.assertAlmostEqual(lib().landing(angle, speed), distance, delta=0.02)

    def test_out_of_reach_is_none(self):
        r = rng(9)
        for speed, ratio in [(r.uniform(0.5, 10), r.uniform(1.1, 2.0)) for _ in range(3)]:
            with self.subTest(seed=SEED, speed=speed, ratio=ratio):
                self.assertIsNone(lib().aim(ratio * speed ** 2 / G, speed))


if __name__ == "__main__":
    unittest.main()
