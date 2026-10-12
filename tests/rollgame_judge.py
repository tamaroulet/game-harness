"""課題 B01（docs/bench/B01/spec.md）の判定役。総監督が書いた。保護パス。

公開の入口（REQ-01）だけを通して確かめる。入口の形（REQ-01・REQ-02）と決まった入力の要件（REQ-04・REQ-10）のほかは、
入力を走らせるたびに変わる種の乱数で、仕様の範囲の中から選ぶ（前もって計算した値を埋め込む実装は通らない）。種は失敗の行に出る。
同じ種で再現するときだけ、環境変数 ROLLGAME_JUDGE_SEED に渡す（Gate 1 と照合は渡さない）。
名前が test_ で始まらないので CI の全件（harness.fastsuite）には入らず、本体が無い間も CI を赤にしない。
走らせるのは、台帳の W-32 の検証コマンドと、受信箱の照合（report.md の「## 照合」）。
"""
import math
import os
import random
import unittest

G = 9.81
DT = 0.002
START = (-0.35, -0.35)
GOAL = (0.35, 0.35, 0.05)
LIMIT = 5000
SEED = int(os.environ.get("ROLLGAME_JUDGE_SEED") or random.SystemRandom().randrange(2 ** 32))


def lib():
    import rollgame
    return rollgame


def rng(salt):
    return random.Random(SEED * 100 + salt)


def play(inputs):
    """入力の列で新しい遊びを進め、返った辞書の列。"""
    game = lib().Game()
    return [game.step(tx, ty) for tx, ty in inputs]


def segments(r, total, limit):
    """長さ 30〜150 歩の一定の傾きの区間をつないだ、長さ total の入力の列。傾きは各成分 [-limit, limit]。"""
    out = []
    while len(out) < total:
        tx, ty = r.uniform(-limit, limit), r.uniform(-limit, limit)
        out += [(tx, ty)] * r.randint(30, 150)
    return out[:total]


def expected(state, step):
    """返った pos と歩の数から、仕様（REQ-08〜REQ-10）が決める outcome。"""
    x, y, z = state["pos"]
    if z < -0.05:
        return "fell"
    if z >= 0 and (x - GOAL[0]) ** 2 + (y - GOAL[1]) ** 2 <= GOAL[2] ** 2:
        return "goal"
    return "timeout" if step >= LIMIT else None


class Entry(unittest.TestCase):
    """REQ-01・REQ-02"""

    def test_step_returns_the_state_and_the_outcome(self):
        s = lib().Game().step(0.0, 0.0)
        self.assertEqual((len(s["pos"]), len(s["vel"])), (3, 3))
        for v in list(s["pos"]) + list(s["vel"]):
            self.assertIsInstance(v, float)
        self.assertIsNone(s["outcome"])

    def test_out_of_range_and_wrong_types_raise_value_error(self):
        game = lib().Game()
        bad = [(0.21, 0), (-0.21, 0), (0, 0.21), (0, -0.21), (float("nan"), 0), (0, float("inf")), (True, 0),
               (0, False), ("0.1", 0), (None, 0)]
        for tx, ty in bad:
            with self.subTest(tx=tx, ty=ty), self.assertRaises(ValueError):
                game.step(tx, ty)


class Determinism(unittest.TestCase):
    """REQ-03"""

    def test_two_games_with_the_same_inputs_are_bit_identical(self):
        inputs = segments(rng(1), 800, 0.2)
        self.assertEqual(play(inputs), play(inputs))


class Rest(unittest.TestCase):
    """REQ-04"""

    def test_a_level_board_keeps_the_ball_in_place(self):
        states = play([(0.0, 0.0)] * 1000)
        for s in states:
            self.assertLessEqual(math.hypot(s["pos"][0] - START[0], s["pos"][1] - START[1]), 0.001)
            self.assertIsNone(s["outcome"])


class Rolling(unittest.TestCase):
    """REQ-05"""

    def test_the_ball_rolls_downhill_like_a_rolling_sphere(self):
        r = rng(2)
        for axis in (0, 1, 0, 1):
            t = r.choice((-1, 1)) * r.uniform(0.05, 0.2)
            n = r.randint(100, 180)
            tilt = (t, 0.0) if axis == 0 else (0.0, t)
            states = play([(0.0, 0.0)] * 100 + [tilt] * n)
            before, after = states[99]["pos"], states[-1]["pos"]
            moved, side = after[axis] - before[axis], after[1 - axis] - before[1 - axis]
            free = 0.5 * G * abs(math.sin(t)) * (DT * n) ** 2
            with self.subTest(seed=SEED, axis=axis, t=t, n=n):
                self.assertEqual(math.copysign(1, moved), math.copysign(1, t))
                self.assertTrue(0.64 * free <= abs(moved) <= 0.80 * free, (moved, free))
                self.assertLess(abs(side), 0.001)


class Symmetry(unittest.TestCase):
    """REQ-06"""

    def test_swapping_the_tilts_swaps_x_and_y(self):
        r = rng(3)
        for _ in range(2):
            inputs = segments(r, 600, 0.12)
            a, b = play(inputs), play([(ty, tx) for tx, ty in inputs])
            with self.subTest(seed=SEED):
                for i, (s, t) in enumerate(zip(a, b)):
                    for key in ("pos", "vel"):
                        self.assertAlmostEqual(s[key][0], t[key][1], delta=1e-4, msg=(i, key))
                        self.assertAlmostEqual(s[key][1], t[key][0], delta=1e-4, msg=(i, key))
                        self.assertAlmostEqual(s[key][2], t[key][2], delta=1e-4, msg=(i, key))
                    self.assertEqual(s["outcome"], t["outcome"], i)


class Board(unittest.TestCase):
    """REQ-07"""

    def test_the_ball_rides_on_the_board(self):
        r = rng(4)
        for _ in range(2):
            states = play(segments(r, 1500, 0.2))
            with self.subTest(seed=SEED):
                for i, s in enumerate(states):
                    x, y, z = s["pos"]
                    if s["outcome"] is None and abs(x) <= 0.45 and abs(y) <= 0.45:
                        self.assertTrue(0.015 <= z <= 0.025, (i, z))


class Outcomes(unittest.TestCase):
    """REQ-08・REQ-09・REQ-10・REQ-11"""

    def check_consistent(self, states):
        """outcome は、終わるまでの各歩で返った pos と歩の数から決まる（最初に条件を満たした歩で終わる）。"""
        for i, s in enumerate(states, start=1):
            want = expected(s, i)
            self.assertEqual(s["outcome"], want, (i, s["pos"]))
            if want is not None:
                return i
        return None

    def test_tilting_away_drops_the_ball(self):
        r = rng(5)
        for _ in range(2):
            t, s = r.uniform(-0.2, -0.1), r.uniform(-0.05, 0.05)
            states = play([(t, s)] * LIMIT)
            with self.subTest(seed=SEED, t=t, s=s):
                end = self.check_consistent(states)
                self.assertIsNotNone(end)
                self.assertEqual(states[end - 1]["outcome"], "fell")

    def test_tilting_toward_the_goal_reaches_it(self):
        r = rng(6)
        for _ in range(2):
            t = r.uniform(0.06, 0.15)
            states = play([(t, t)] * LIMIT)
            with self.subTest(seed=SEED, t=t):
                end = self.check_consistent(states)
                self.assertIsNotNone(end)
                self.assertEqual(states[end - 1]["outcome"], "goal")

    def test_a_level_board_times_out_at_the_limit(self):
        states = play([(0.0, 0.0)] * LIMIT)
        self.assertTrue(all(s["outcome"] is None for s in states[:-1]))
        self.assertEqual(states[-1]["outcome"], "timeout")

    def test_random_play_ends_where_the_rules_say(self):
        r = rng(7)
        for _ in range(2):
            states = play(segments(r, 3000, 0.2))
            with self.subTest(seed=SEED):
                self.check_consistent(states)

    def test_after_the_end_nothing_changes(self):
        r = rng(8)
        t = r.uniform(-0.2, -0.1)
        game = lib().Game()
        last = None
        for _ in range(LIMIT):
            last = game.step(t, 0.0)
            if last["outcome"] is not None:
                break
        with self.subTest(seed=SEED, t=t):
            self.assertEqual(last["outcome"], "fell")
            for _ in range(5):
                again = game.step(r.uniform(-0.2, 0.2), r.uniform(-0.2, 0.2))
                self.assertEqual(again, last)
            with self.assertRaises(ValueError):
                game.step(0.3, 0.0)


if __name__ == "__main__":
    unittest.main()
