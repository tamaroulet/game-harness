"""課題 B08（docs/bench/B08/spec.md）の判定役。総監督が書いた。保護パス。

公開の入口（REQ-01）だけを通して確かめる。入口の形（REQ-01・REQ-02）のほかは、
枚数・質量・ばね定数・減衰・始めの状態を走らせるたびに変わる種の乱数で、仕様の範囲の中から選ぶ（前もって計算した値を埋め込む実装は通らない）。
種は失敗の行に出る。同じ種で再現するときだけ、環境変数 SPRINGCHAIN_JUDGE_SEED に渡す（Gate 1 と照合は渡さない）。
名前が test_ で始まらないので CI の全件（harness.fastsuite）には入らず、本体が無い間も CI を赤にしない。
"""
import math
import os
import random
import unittest

DT = 0.002
SEED = int(os.environ.get("SPRINGCHAIN_JUDGE_SEED") or random.SystemRandom().randrange(2 ** 32))


def lib():
    import springchain
    return springchain


def rng(salt):
    return random.Random(SEED * 100 + salt)


def energy(mass, stiffness, x, v):
    n = len(x)
    stretch = x[0] ** 2 + sum((x[i + 1] - x[i]) ** 2 for i in range(n - 1)) + x[-1] ** 2
    return 0.5 * mass * sum(u * u for u in v) + 0.5 * stiffness * stretch


def shape(n, p):
    return [math.sin((i + 1) * p * math.pi / (n + 1)) for i in range(n)]


def omega(n, mass, stiffness, p):
    return 2 * math.sqrt(stiffness / mass) * math.sin(p * math.pi / (2 * (n + 1)))


def chain(r, n_lo=2, n_hi=12, m_lo=0.05, m_hi=0.5, ratio_lo=4.0, ratio_hi=25.0):
    """(n, mass, stiffness)。ratio は stiffness/mass。"""
    m = r.uniform(m_lo, m_hi)
    return r.randint(n_lo, n_hi), m, m * r.uniform(ratio_lo, ratio_hi)


def start(r, n):
    return [r.uniform(-0.03, 0.03) for _ in range(n)], [r.uniform(-1.0, 1.0) for _ in range(n)]


class Entry(unittest.TestCase):
    """REQ-01・REQ-02"""

    def test_simulate_returns_one_state_per_step(self):
        states = lib().simulate(3, 0.1, 1.0, 0.0, 5, [0.01, 0.0, -0.01], (0.0, 0.2, 0.0))
        self.assertEqual(len(states), 5)
        for s in states:
            self.assertEqual(set(s), {"x", "v"})
            self.assertEqual((len(s["x"]), len(s["v"])), (3, 3))
            for u in s["x"] + s["v"]:
                self.assertIsInstance(u, float)

    def test_out_of_range_and_wrong_types_raise_value_error(self):
        m = lib()
        bad = [lambda: m.simulate(1, 0.1, 1.0, 0.0, 5), lambda: m.simulate(13, 0.1, 1.0, 0.0, 5),
               lambda: m.simulate(3.0, 0.1, 1.0, 0.0, 5), lambda: m.simulate(True, 0.1, 1.0, 0.0, 5),
               lambda: m.simulate(3, 0.04, 0.5, 0.0, 5), lambda: m.simulate(3, 0.6, 5.0, 0.0, 5),
               lambda: m.simulate(3, 0.1, 0.3, 0.0, 5), lambda: m.simulate(3, 0.1, 2.6, 0.0, 5),
               lambda: m.simulate(3, 0.1, -1.0, 0.0, 5), lambda: m.simulate(3, 0.1, 1.0, -0.01, 5),
               lambda: m.simulate(3, 0.1, 1.0, 0.21, 5), lambda: m.simulate(3, 0.1, 1.0, 0.0, 0),
               lambda: m.simulate(3, 0.1, 1.0, 0.0, 20001), lambda: m.simulate(3, 0.1, 1.0, 0.0, 5.0),
               lambda: m.simulate(3, float("nan"), 1.0, 0.0, 5), lambda: m.simulate(3, 0.1, 1.0, 0.0, 5, [0.0, 0.0]),
               lambda: m.simulate(3, 0.1, 1.0, 0.0, 5, [0.0, 0.031, 0.0]),
               lambda: m.simulate(3, 0.1, 1.0, 0.0, 5, None, [0.0, 1.1, 0.0]),
               lambda: m.simulate(3, 0.1, 1.0, 0.0, 5, [0.0, True, 0.0]), lambda: m.simulate(3, 0.1, 1.0, 0.0, 5, "000")]
        for i, call in enumerate(bad):
            with self.subTest(i=i), self.assertRaises(ValueError):
                call()


class Determinism(unittest.TestCase):
    """REQ-03"""

    def test_two_calls_are_bit_identical(self):
        r = rng(1)
        n, m, k = chain(r)
        x0, v0 = start(r, n)
        c = r.uniform(0.0, 0.2)
        self.assertEqual(lib().simulate(n, m, k, c, 800, x0, v0), lib().simulate(n, m, k, c, 800, x0, v0))


class Start(unittest.TestCase):
    """REQ-04・REQ-05"""

    def test_the_first_state_follows_the_start(self):
        r = rng(2)
        for _ in range(3):
            n, m, k = chain(r)
            x0, v0 = start(r, n)
            s = lib().simulate(n, m, k, r.uniform(0.0, 0.2), 1, x0, v0)[0]
            with self.subTest(seed=SEED, n=n, mass=m, stiffness=k):
                for i in range(n):
                    self.assertLessEqual(abs(s["x"][i] - (x0[i] + DT * v0[i])), 0.001, i)
                    self.assertLessEqual(abs(s["v"][i] - v0[i]), 0.05, i)

    def test_a_chain_at_rest_stays_at_rest(self):
        r = rng(3)
        n, m, k = chain(r)
        for s in lib().simulate(n, m, k, r.uniform(0.0, 0.2), 500):
            for u in s["x"] + s["v"]:
                self.assertLessEqual(abs(u), 1e-12)


class Energy(unittest.TestCase):
    """REQ-06・REQ-07"""

    def test_without_damping_the_energy_is_kept(self):
        r = rng(4)
        for _ in range(2):
            n, m, k = chain(r)
            x0, v0 = start(r, n)
            e0 = energy(m, k, x0, v0)
            states = lib().simulate(n, m, k, 0.0, 5000, x0, v0)
            with self.subTest(seed=SEED, n=n, mass=m, stiffness=k):
                for i, s in enumerate(states):
                    self.assertTrue(0.97 * e0 <= energy(m, k, s["x"], s["v"]) <= 1.03 * e0, i)

    def test_damping_drains_the_energy(self):
        r = rng(5)
        for _ in range(2):
            n, m, k = chain(r, 2, 6, 0.05, 0.1)
            x0, v0 = start(r, n)
            c = r.uniform(0.1, 0.2)
            e0 = energy(m, k, x0, v0)
            states = lib().simulate(n, m, k, c, 5000, x0, v0)
            with self.subTest(seed=SEED, n=n, mass=m, stiffness=k, damping=c):
                for i, s in enumerate(states):
                    self.assertLessEqual(energy(m, k, s["x"], s["v"]), 1.03 * e0, i)
                self.assertLessEqual(energy(m, k, states[-1]["x"], states[-1]["v"]), 0.5 * e0)


class Modes(unittest.TestCase):
    """REQ-08・REQ-09"""

    def test_a_normal_mode_comes_back_after_one_period(self):
        r = rng(6)
        for _ in range(3):
            n, m, k = chain(r)
            p, a = r.randint(1, n), r.uniform(0.005, 0.03)
            x0 = [a * s for s in shape(n, p)]
            period = 2 * math.pi / omega(n, m, k, p)
            full, half = round(period / DT), round(period / (2 * DT))
            states = lib().simulate(n, m, k, 0.0, full, x0)
            with self.subTest(seed=SEED, n=n, mass=m, stiffness=k, p=p, amplitude=a):
                for i in range(n):
                    self.assertLessEqual(abs(states[full - 1]["x"][i] - x0[i]), 0.02 * a, i)
                    self.assertLessEqual(abs(states[half - 1]["x"][i] + x0[i]), 0.02 * a, i)

    def test_the_damping_grows_with_the_square_of_the_mode_frequency(self):
        r = rng(7)
        for low in (True, False):
            n, m, k = chain(r, 3, 12, 0.1, 0.5, 16.0, 25.0)
            p = 1 if low else n
            a, c = r.uniform(0.005, 0.03), r.uniform(0.005, 0.02)
            x0 = [a * s for s in shape(n, p)]
            want = math.exp(-(c / m) * (omega(n, m, k, p) / math.sqrt(k / m)) ** 2 * 2.0)
            s = lib().simulate(n, m, k, c, 1000, x0)[-1]
            ratio = energy(m, k, s["x"], s["v"]) / energy(m, k, x0, [0.0] * n)
            with self.subTest(seed=SEED, n=n, mass=m, stiffness=k, damping=c, p=p):
                self.assertTrue(0.9 * want <= ratio <= 1.1 * want, (ratio, want))


class Transforms(unittest.TestCase):
    """REQ-10・REQ-11"""

    def test_doubling_mass_stiffness_and_damping_keeps_the_motion(self):
        r = rng(8)
        n, m, k = chain(r, 2, 12, 0.05, 0.25)
        x0, v0 = start(r, n)
        c = r.uniform(0.0, 0.1)
        base = lib().simulate(n, m, k, c, 1500, x0, v0)
        twice = lib().simulate(n, 2 * m, 2 * k, 2 * c, 1500, x0, v0)
        for i, (a, b) in enumerate(zip(base, twice)):
            for key in ("x", "v"):
                for j in range(n):
                    self.assertAlmostEqual(a[key][j], b[key][j], delta=1e-9, msg=(i, key, j))

    def test_doubling_and_mirroring_the_start(self):
        r = rng(9)
        n, m, k = chain(r)
        x0 = [r.uniform(-0.015, 0.015) for _ in range(n)]
        v0 = [r.uniform(-0.5, 0.5) for _ in range(n)]
        c = r.uniform(0.0, 0.2)
        base = lib().simulate(n, m, k, c, 1500, x0, v0)
        double = lib().simulate(n, m, k, c, 1500, [2 * u for u in x0], [2 * u for u in v0])
        mirror = lib().simulate(n, m, k, c, 1500, [-u for u in reversed(x0)], [-u for u in reversed(v0)])
        for i, (a, d, mr) in enumerate(zip(base, double, mirror)):
            for key in ("x", "v"):
                for j in range(n):
                    self.assertAlmostEqual(d[key][j], 2 * a[key][j], delta=1e-9, msg=(i, key, j))
                    self.assertAlmostEqual(mr[key][j], -a[key][n - 1 - j], delta=1e-9, msg=(i, key, j))


if __name__ == "__main__":
    unittest.main()
