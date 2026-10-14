"""課題 B04（docs/bench/B04/spec.md）の判定役。総監督が書いた。保護パス。

公開の入口（REQ-01）だけを通して確かめる。入口の形（REQ-01・REQ-02）のほかは、
枚数・間隔・始めの点・進む向きを走らせるたびに変わる種の乱数で、仕様の範囲の中から選ぶ（前もって計算した値を埋め込む実装は通らない）。
種は失敗の行に出る。同じ種で再現するときだけ、環境変数 DOMINO_JUDGE_SEED に渡す（Gate 1 と照合は渡さない）。
名前が test_ で始まらないので CI の全件（harness.fastsuite）には入らず、本体が無い間も CI を赤にしない。
"""
import math
import os
import random
import unittest

SEED = int(os.environ.get("DOMINO_JUDGE_SEED") or random.SystemRandom().randrange(2 ** 32))
DIRS = {"+x": (1.0, 0.0), "-x": (-1.0, 0.0), "+y": (0.0, 1.0), "-y": (0.0, -1.0)}
TILT0 = 0.2
FALLEN = 1.2
STEPS = 3000


def lib():
    import domino
    return domino


def rng(salt):
    return random.Random(SEED * 100 + salt)


def row(r, n_lo, n_hi, s_lo, s_hi):
    """(n, spacing, origin, direction) を仕様の範囲から選ぶ。"""
    return (r.randint(n_lo, n_hi), r.uniform(s_lo, s_hi), (r.uniform(-1.0, 1.0), r.uniform(-1.0, 1.0)),
            r.choice(sorted(DIRS)))


def placed(n, s, origin, direction):
    """置いた位置（中心）。0 番は前の下の辺を軸に TILT0 だけ前へ傾けた位置。"""
    ex, ey = DIRS[direction]
    out = [(origin[0] + i * s * ex, origin[1] + i * s * ey, 0.05) for i in range(n)]
    a = 0.005 - 0.005 * math.cos(TILT0) + 0.05 * math.sin(TILT0)
    out[0] = (origin[0] + a * ex, origin[1] + a * ey, 0.005 * math.sin(TILT0) + 0.05 * math.cos(TILT0))
    return out


class Entry(unittest.TestCase):
    """REQ-01・REQ-02"""

    def test_simulate_returns_n_boards_per_step(self):
        states = lib().simulate(3, 0.06, 4)
        self.assertEqual(len(states), 4)
        for boards in states:
            self.assertEqual(len(boards), 3)
            for b in boards:
                self.assertEqual(set(b), {"pos", "tilt"})
                self.assertEqual(len(b["pos"]), 3)
                for v in list(b["pos"]) + [b["tilt"]]:
                    self.assertIsInstance(v, float)

    def test_fall_steps_returns_one_entry_per_board(self):
        self.assertEqual(lib().fall_steps(3, 0.15, 5), [None, None, None])

    def test_out_of_range_and_wrong_types_raise_value_error(self):
        m = lib()
        bad = [lambda: m.simulate(1, 0.06, 5), lambda: m.simulate(13, 0.06, 5), lambda: m.simulate(3.0, 0.06, 5),
               lambda: m.simulate(True, 0.06, 5), lambda: m.simulate(3, 0.039, 5), lambda: m.simulate(3, 0.21, 5),
               lambda: m.simulate(3, float("nan"), 5), lambda: m.simulate(3, True, 5), lambda: m.simulate(3, 0.06, 0),
               lambda: m.simulate(3, 0.06, 20001), lambda: m.simulate(3, 0.06, 1.5),
               lambda: m.simulate(3, 0.06, 5, (1.1, 0.0)), lambda: m.simulate(3, 0.06, 5, (0.0,)),
               lambda: m.simulate(3, 0.06, 5, "00"), lambda: m.simulate(3, 0.06, 5, (float("nan"), 0.0)),
               lambda: m.simulate(3, 0.06, 5, (True, 0.0)), lambda: m.simulate(3, 0.06, 5, (0.0, 0.0), "x"),
               lambda: m.simulate(3, 0.06, 5, (0.0, 0.0), "+z"), lambda: m.simulate(3, 0.06, 5, (0.0, 0.0), None),
               lambda: m.fall_steps(3, 0.06, 0), lambda: m.fall_steps(3, 0.3), lambda: m.fall_steps(3, 0.06, 5, (0.0, 0.0), "-X")]
        for i, call in enumerate(bad):
            with self.subTest(i=i), self.assertRaises(ValueError):
                call()


class Determinism(unittest.TestCase):
    """REQ-03"""

    def test_two_calls_are_bit_identical(self):
        n, s, o, d = row(rng(2), 4, 6, 0.05, 0.065)
        self.assertEqual(lib().simulate(n, s, 1200, o, d), lib().simulate(n, s, 1200, o, d))
        self.assertEqual(lib().fall_steps(n, s, 1200, o, d), lib().fall_steps(n, s, 1200, o, d))


class Placement(unittest.TestCase):
    """REQ-04"""

    def test_the_boards_start_where_they_are_placed(self):
        r = rng(3)
        for _ in range(4):
            n, s, o, d = row(r, 2, 12, 0.04, 0.2)
            first = lib().simulate(n, s, 1, o, d)[0]
            want = placed(n, s, o, d)
            with self.subTest(seed=SEED, n=n, spacing=s, origin=o, direction=d):
                self.assertLessEqual(math.dist(first[0]["pos"], want[0]), 0.002)
                self.assertLessEqual(abs(first[0]["tilt"] - TILT0), 0.01)
                for i in range(1, n):
                    self.assertLessEqual(math.dist(first[i]["pos"], want[i]), 0.001, i)
                    self.assertLessEqual(first[i]["tilt"], 0.01, i)


class Apart(unittest.TestCase):
    """REQ-05・REQ-07"""

    def test_a_wide_row_lets_only_the_first_board_fall_forward(self):
        r = rng(4)
        for _ in range(2):
            n, s, o, d = row(r, 2, 6, 0.125, 0.2)
            states = lib().simulate(n, s, STEPS, o, d)
            want = placed(n, s, o, d)
            ex, ey = DIRS[d]
            with self.subTest(seed=SEED, n=n, spacing=s, origin=o, direction=d):
                for k, boards in enumerate(states):
                    for i, b in enumerate(boards):
                        self.assertGreaterEqual(b["pos"][2], 0.0, (k, i))
                        if i:
                            self.assertLessEqual(math.dist(b["pos"], want[i]), 0.002, (k, i))
                            self.assertLessEqual(b["tilt"], 0.02, (k, i))
                x, y, z = states[-1][0]["pos"]
                along, side = (x - o[0]) * ex + (y - o[1]) * ey, (x - o[0]) * -ey + (y - o[1]) * ex
                self.assertLessEqual(z, 0.01)
                self.assertTrue(0.04 <= along <= 0.07, along)
                self.assertLessEqual(abs(side), 0.01)
                falls = lib().fall_steps(n, s, STEPS, o, d)
                self.assertIsInstance(falls[0], int)
                self.assertLessEqual(falls[0], 1000)
                self.assertEqual(falls[1:], [None] * (n - 1))


class Chain(unittest.TestCase):
    """REQ-06・REQ-07"""

    def test_a_close_row_falls_board_after_board(self):
        r = rng(5)
        for n_lo in (10, 3):
            n, s, o, d = row(r, n_lo, 10, 0.05, 0.065)
            falls = lib().fall_steps(n, s, STEPS, o, d)
            states = lib().simulate(n, s, STEPS, o, d)
            with self.subTest(seed=SEED, n=n, spacing=s, origin=o, direction=d, falls=falls):
                for i, k in enumerate(falls):
                    self.assertIsInstance(k, int, i)
                for i in range(n - 1):
                    self.assertLessEqual(falls[i], falls[i + 1], i)
                for i, b in enumerate(states[-1]):
                    self.assertGreater(b["tilt"], FALLEN, i)
                for k, boards in enumerate(states):
                    for i, b in enumerate(boards):
                        self.assertGreaterEqual(b["pos"][2], 0.0, (k, i))


class Transforms(unittest.TestCase):
    """REQ-08"""

    def test_moving_and_turning_the_row_keeps_the_fall_steps(self):
        r = rng(6)
        n, s = r.randint(4, 8), r.uniform(0.05, 0.065)
        base = lib().fall_steps(n, s, STEPS)
        for d in ("-x", "+y", "-y"):
            o = (r.uniform(-1.0, 1.0), r.uniform(-1.0, 1.0))
            got = lib().fall_steps(n, s, STEPS, o, d)
            with self.subTest(seed=SEED, n=n, spacing=s, origin=o, direction=d, base=base, got=got):
                for i, (a, b) in enumerate(zip(base, got)):
                    self.assertIsInstance(a, int, i)
                    self.assertIsInstance(b, int, i)
                    self.assertLessEqual(abs(a - b), 10, i)


if __name__ == "__main__":
    unittest.main()
