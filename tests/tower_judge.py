"""課題 B05（docs/bench/B05/spec.md）の判定役。総監督が書いた。保護パス。

公開の入口（REQ-01）だけを通して確かめる。入口の形（REQ-01・REQ-02）と調和の積み方（REQ-07）のほかは、
積み方を走らせるたびに変わる種の乱数で、仕様の範囲の中から選ぶ（前もって計算した値を埋め込む実装は通らない）。種は失敗の行に出る。
同じ種で再現するときだけ、環境変数 TOWER_JUDGE_SEED に渡す（Gate 1 と照合は渡さない）。
名前が test_ で始まらないので CI の全件（harness.fastsuite）には入らず、本体が無い間も CI を赤にしない。
走らせるのは、台帳の W-33 の検証コマンドと、受信箱の照合（report.md の「## 照合」）。
"""
import math
import os
import random
import unittest

H = 0.05
STEPS = 2000
SEED = int(os.environ.get("TOWER_JUDGE_SEED") or random.SystemRandom().randrange(2 ** 32))


def lib():
    import tower
    return tower


def rng(salt):
    return random.Random(SEED * 100 + salt)


def margins(xs):
    """各 j の d_j ＝ |箱 j より上の箱の x の平均 − x_j|（仕様の「支えの余裕」）。"""
    return [abs(sum(xs[j + 1:]) / (len(xs) - j - 1) - xs[j]) for j in range(len(xs) - 1)]


def standing(r, lo=2, hi=6):
    """すべての d_j ≤ 0.04 の積み方（REQ-05）。隣の差は ±0.04、全体は x の範囲の内。"""
    while True:
        xs = [r.uniform(-0.1, 0.1)]
        for _ in range(r.randint(lo, hi) - 1):
            xs.append(xs[-1] + r.uniform(-0.04, 0.04))
        if all(d <= 0.04 for d in margins(xs)) and all(abs(x) <= 0.4 for x in xs):
            return xs


def collapsing(r):
    """ある j で d_j ≥ 0.06 の積み方（REQ-06）。立つ積み方の箱 j より上を、まとめて横にずらして作る。"""
    while True:
        xs = standing(r)
        j = r.randrange(len(xs) - 1)
        c = sum(xs[j + 1:]) / (len(xs) - j - 1)
        shift = r.choice((-1, 1)) * r.uniform(0.065, 0.085) - (c - xs[j])
        ys = xs[:j + 1] + [x + shift for x in xs[j + 1:]]
        if abs(ys[j + 1] - ys[j]) <= 0.1 and all(abs(x) <= 0.5 for x in ys) and margins(ys)[j] >= 0.06:
            return ys


def placed(xs):
    return [(x, 0.0, 0.025 + H * j) for j, x in enumerate(xs)]


class Entry(unittest.TestCase):
    """REQ-01・REQ-02"""

    def test_simulate_returns_one_list_of_boxes_per_step(self):
        states = lib().simulate([0.0, 0.01], 5)
        self.assertEqual(len(states), 5)
        for boxes in states:
            self.assertEqual(len(boxes), 2)
            for b in boxes:
                self.assertEqual((len(b["pos"]), len(b["vel"])), (3, 3))
                for v in list(b["pos"]) + list(b["vel"]):
                    self.assertIsInstance(v, float)

    def test_stands_is_a_bool_and_harmonic_is_a_list_of_floats(self):
        self.assertIsInstance(lib().stands([0.0]), bool)
        xs = lib().harmonic(3)
        self.assertEqual(len(xs), 3)
        for v in xs:
            self.assertIsInstance(v, float)

    def test_out_of_range_and_wrong_types_raise_value_error(self):
        m = lib()
        bad = [lambda: m.simulate([], 5), lambda: m.simulate([0.0] * 7, 5), lambda: m.simulate([0.6], 5),
               lambda: m.simulate([0.0, 0.11], 5), lambda: m.simulate([float("nan")], 5),
               lambda: m.simulate([True], 5), lambda: m.simulate("0.0", 5), lambda: m.simulate([0.0], 0),
               lambda: m.simulate([0.0], 20001), lambda: m.simulate([0.0], 2.0), lambda: m.simulate([0.0], True),
               lambda: m.stands([0.0, 0.2]), lambda: m.harmonic(0), lambda: m.harmonic(7),
               lambda: m.harmonic(2.0), lambda: m.harmonic(True)]
        for i, call in enumerate(bad):
            with self.subTest(i=i), self.assertRaises(ValueError):
                call()


class Determinism(unittest.TestCase):
    """REQ-03"""

    def test_two_calls_are_bit_identical(self):
        xs = collapsing(rng(1))
        self.assertEqual(lib().simulate(xs, 600), lib().simulate(xs, 600))
        self.assertEqual(lib().stands(xs), lib().stands(xs))
        self.assertEqual(lib().harmonic(5), lib().harmonic(5))


class Placement(unittest.TestCase):
    """REQ-04・REQ-09"""

    def test_the_boxes_start_where_they_are_placed_and_stay_above_the_floor(self):
        r = rng(2)
        for xs in (standing(r), collapsing(r)):
            states = lib().simulate(xs, STEPS)
            with self.subTest(seed=SEED, offsets=xs):
                for b, p in zip(states[0], placed(xs)):
                    self.assertLessEqual(math.dist(b["pos"], p), 0.001)
                for i, boxes in enumerate(states):
                    for b in boxes:
                        self.assertGreaterEqual(b["pos"][2], 0.015, i)


class Standing(unittest.TestCase):
    """REQ-05・REQ-10"""

    def test_a_supported_stack_stands_still(self):
        r = rng(3)
        for _ in range(3):
            xs = standing(r)
            states = lib().simulate(xs, STEPS)
            with self.subTest(seed=SEED, offsets=xs):
                self.assertIs(lib().stands(xs), True)
                for i, boxes in enumerate(states):
                    for b, p in zip(boxes, placed(xs)):
                        self.assertLessEqual(math.dist(b["pos"], p), 0.005, i)

    def test_stands_follows_the_height_of_the_top_box(self):
        r = rng(4)
        for xs in (standing(r), collapsing(r), collapsing(r)):
            top = lib().simulate(xs, STEPS)[-1][-1]["pos"][2]
            with self.subTest(seed=SEED, offsets=xs):
                self.assertEqual(lib().stands(xs), top >= 0.95 * (0.025 + H * (len(xs) - 1)))


class Collapsing(unittest.TestCase):
    """REQ-06"""

    def test_an_unsupported_stack_falls(self):
        r = rng(5)
        for _ in range(4):
            xs = collapsing(r)
            with self.subTest(seed=SEED, offsets=xs, margins=margins(xs)):
                self.assertIs(lib().stands(xs), False)


class Harmonic(unittest.TestCase):
    """REQ-07"""

    def test_the_harmonic_stack_has_the_given_steps_stands_and_overhangs(self):
        for k in range(1, 7):
            xs = lib().harmonic(k)
            with self.subTest(k=k):
                self.assertAlmostEqual(xs[0], 0.0, delta=1e-12)
                for j in range(k - 1):
                    self.assertAlmostEqual(xs[j + 1] - xs[j], 0.8 * 0.05 / (k - 1 - j), delta=1e-12)
                self.assertIs(lib().stands(xs), True)
                if k >= 4:
                    self.assertGreater(xs[-1] - xs[0], 0.05)


class Transforms(unittest.TestCase):
    """REQ-08"""

    def test_mirror_and_shift_move_a_standing_stack_alike(self):
        r = rng(6)
        for _ in range(2):
            xs = standing(r)
            c = r.uniform(-0.05, 0.05)
            base = lib().simulate(xs, STEPS)
            mirror = lib().simulate([-x for x in xs], STEPS)
            shifted = lib().simulate([x + c for x in xs], STEPS)
            with self.subTest(seed=SEED, offsets=xs, c=c):
                for i in range(0, STEPS, 50):
                    for a, m, s in zip(base[i], mirror[i], shifted[i]):
                        self.assertAlmostEqual(m["pos"][0], -a["pos"][0], delta=1e-3)
                        self.assertAlmostEqual(s["pos"][0], a["pos"][0] + c, delta=1e-3)
                self.assertEqual(lib().stands([-x for x in xs]), lib().stands(xs))
                self.assertEqual(lib().stands([x + c for x in xs]), lib().stands(xs))

    def test_mirror_keeps_a_collapse(self):
        xs = collapsing(rng(7))
        self.assertIs(lib().stands([-x for x in xs]), False)


if __name__ == "__main__":
    unittest.main()
