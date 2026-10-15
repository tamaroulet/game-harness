"""課題 B09（docs/bench/B09/spec.md）の判定役。総監督が書いた。保護パス。

公開の入口（REQ-01）だけを通して確かめる。入口の形（REQ-01・REQ-02）のほかは、
種と操作を走らせるたびに変わる種の乱数で選ぶ（前もって計算した値を埋め込む実装は通らない）。
判定役の種は失敗の行に出る。同じ種で再現するときだけ、環境変数 CATCHER_JUDGE_SEED に渡す（Gate 1 と照合は渡さない）。
名前が test_ で始まらないので CI の全件（harness.fastsuite）には入らず、本体が無い間も CI を赤にしない。
"""
import math
import os
import random
import unittest

DT = 0.002
G = 9.81
SPACING = 375
SEED = int(os.environ.get("CATCHER_JUDGE_SEED") or random.SystemRandom().randrange(2 ** 32))


def lib():
    import catcher
    return catcher


def rng(salt):
    return random.Random(SEED * 100 + salt)


def positions(seed):
    r = random.Random(seed)
    return [-0.4 + 0.8 * r.random() for _ in range(10)]


def height(i, k):
    """k 歩目の後の i 番の玉の (z, vz)。"""
    j = k - SPACING * i
    if j <= 0:
        return 1.0, 0.0
    return 1.0 - G * DT * DT * j * (j + 1) / 2, -G * DT * j


class Recorder:
    """種の乱数で決めた速さ（[−1.5, 1.5]。切り詰めを確かめる）を返し、受け取った状態を残す。"""

    def __init__(self, r, keep=False):
        self.r, self.keep, self.states, self.speeds = r, keep, [], []

    def __call__(self, state):
        if self.keep:
            self.states.append(state)
        v = self.r.uniform(-1.5, 1.5) if self.r.random() < 0.9 else self.r.choice((-3.0, 3.0))
        self.speeds.append(v)
        return v


def replay(speeds):
    p, out = 0.0, []
    for v in speeds:
        p = min(0.5, max(-0.5, p + DT * min(1.0, max(-1.0, v))))
        out.append(p)
    return out


class Entry(unittest.TestCase):
    """REQ-01・REQ-02"""

    def test_play_returns_the_counts_events_and_paddle(self):
        result = lib().play(7, lambda s: 0.0, 300)
        self.assertEqual(set(result), {"caught", "missed", "events", "paddle"})
        self.assertEqual(len(result["paddle"]), 300)
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(set(result["events"][0]), {"ball", "step", "caught"})
        self.assertIsInstance(lib().auto_play(_first_state(7)), (int, float))

    def test_out_of_range_and_wrong_types_raise_value_error(self):
        m = lib()
        zero = lambda s: 0.0
        bad = [lambda: m.play(-1, zero, 5), lambda: m.play(2 ** 32, zero, 5), lambda: m.play(True, zero, 5),
               lambda: m.play(1.0, zero, 5), lambda: m.play(1, zero, 0), lambda: m.play(1, zero, 5001),
               lambda: m.play(1, zero, 5.0), lambda: m.play(1, None, 5), lambda: m.play(1, 0.5, 5),
               lambda: m.play(1, lambda s: float("nan"), 5), lambda: m.play(1, lambda s: None, 5),
               lambda: m.play(1, lambda s: True, 5), lambda: m.play(1, lambda s: float("inf"), 5),
               lambda: m.play(1, lambda s: "0.5", 5)]
        for i, call in enumerate(bad):
            with self.subTest(i=i), self.assertRaises(ValueError):
                call()


def _first_state(seed):
    seen = []
    lib().play(seed, lambda s: seen.append(s) or 0.0, 1)
    return seen[0]


class Determinism(unittest.TestCase):
    """REQ-03"""

    def test_two_plays_are_bit_identical(self):
        seed = rng(1).randrange(2 ** 32)
        a = lib().play(seed, Recorder(random.Random(seed)), 1200)
        b = lib().play(seed, Recorder(random.Random(seed)), 1200)
        self.assertEqual(a, b)


class Balls(unittest.TestCase):
    """REQ-04・REQ-05・REQ-06"""

    def test_balls_are_placed_by_the_seed_and_fall_freely(self):
        r = rng(2)
        seed = r.randrange(2 ** 32)
        xs = positions(seed)
        rec = Recorder(r, keep=True)
        lib().play(seed, rec, 1200)
        for k, state in enumerate(rec.states):   # k 歩目の後の状態
            with self.subTest(seed=SEED, game=seed, step=k):
                for i, b in enumerate(state["balls"]):
                    z, vz = height(i, k)
                    self.assertLessEqual(abs(b["x"] - xs[i]), 1e-12, i)
                    self.assertLessEqual(abs(b["z"] - z), 1e-9, (i, b["z"], z))
                    self.assertLessEqual(abs(b["vz"] - vz), 1e-9, i)

    def test_each_ball_is_judged_once_at_its_step(self):
        r = rng(3)
        for _ in range(2):
            seed = r.randrange(2 ** 32)
            xs = positions(seed)
            steps = r.randint(1500, 3700)
            rec = Recorder(r, keep=True)
            result = lib().play(seed, rec, steps)
            path = replay(rec.speeds)
            want = [(i, SPACING * i + 226, abs(xs[i] - path[SPACING * i + 225]) <= 0.1)
                    for i in range(10) if SPACING * i + 226 <= steps]
            with self.subTest(seed=SEED, game=seed, steps=steps):
                got = [(e["ball"], e["step"], e["caught"]) for e in result["events"]]
                self.assertEqual(got, want)
                self.assertEqual(result["caught"], sum(1 for w in want if w[2]))
                self.assertEqual(result["missed"], sum(1 for w in want if not w[2]))
                for k, state in enumerate(rec.states):
                    for i, b in enumerate(state["balls"]):
                        done = SPACING * i + 226 <= k
                        self.assertEqual(b["resolved"], done, (k, i))
                        self.assertEqual(b["caught"], want[i][2] if done else None, (k, i))


class Paddle(unittest.TestCase):
    """REQ-07・REQ-08"""

    def test_the_paddle_follows_the_clipped_speeds(self):
        r = rng(4)
        seed = r.randrange(2 ** 32)
        rec = Recorder(r)
        result = lib().play(seed, rec, 2000)
        for k, (got, want) in enumerate(zip(result["paddle"], replay(rec.speeds))):
            self.assertLessEqual(abs(got - want), 1e-12, k)

    def test_changing_the_given_state_does_not_change_the_game(self):
        seed = rng(5).randrange(2 ** 32)

        def vandal(state):
            v = math.sin(state["step"] / 50.0)
            state["paddle"] = 9.0
            for b in state["balls"]:
                b["x"], b["z"], b["resolved"] = 0.0, -5.0, True
            state["balls"].clear()
            return v

        calm = lib().play(seed, lambda s: math.sin(s["step"] / 50.0), 1500)
        self.assertEqual(lib().play(seed, vandal, 1500), calm)


class Auto(unittest.TestCase):
    """REQ-09"""

    def test_auto_play_catches_nine_or_more(self):
        r = rng(6)
        for _ in range(3):
            seed = r.randrange(2 ** 32)
            with self.subTest(seed=SEED, game=seed):
                self.assertGreaterEqual(lib().play(seed, lib().auto_play, 3700)["caught"], 9)


if __name__ == "__main__":
    unittest.main()
