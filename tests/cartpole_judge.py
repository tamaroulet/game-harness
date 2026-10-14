"""課題 B02（docs/bench/B02/spec.md）の判定役。総監督が書いた。保護パス。

公開の入口（REQ-01）だけを通して確かめる。入口の形（REQ-01・REQ-02）のほかは、
始めの状態・力・歩数を走らせるたびに変わる種の乱数で、仕様の範囲の中から選ぶ（前もって計算した値を埋め込む実装は通らない）。
種は失敗の行に出る。同じ種で再現するときだけ、環境変数 CARTPOLE_JUDGE_SEED に渡す（Gate 1 と照合は渡さない）。
名前が test_ で始まらないので CI の全件（harness.fastsuite）には入らず、本体が無い間も CI を赤にしない。
"""
import math
import os
import random
import unittest

KEYS = ("x", "x_dot", "theta", "theta_dot")
SEED = int(os.environ.get("CARTPOLE_JUDGE_SEED") or random.SystemRandom().randrange(2 ** 32))


def lib():
    import cartpole
    return cartpole


def rng(salt):
    return random.Random(SEED * 100 + salt)


def signed(r, lo, hi):
    return r.choice((-1, 1)) * r.uniform(lo, hi)


def zero(state):
    return 0.0


def com(state):
    """系の重心の x（REQ-06）。"""
    return (1.0 * state["x"] + 0.1 * (state["x"] + 0.25 * math.sin(state["theta"]))) / 1.1


class Entry(unittest.TestCase):
    """REQ-01・REQ-02"""

    def test_simulate_returns_one_state_per_step(self):
        states = lib().simulate(0.05, [0.0, 1.0, -1.0], 0.1)
        self.assertEqual(len(states), 3)
        for s in states:
            self.assertEqual(set(s), set(KEYS))
            for k in KEYS:
                self.assertIsInstance(s[k], float)

    def test_episode_returns_status_steps_and_state(self):
        out = lib().episode(0.01, zero, 10)
        self.assertEqual(set(out), {"status", "steps", "state"})
        self.assertEqual((out["status"], out["steps"]), ("timeout", 10))
        self.assertEqual(set(out["state"]), set(KEYS))

    def test_balance_returns_a_bounded_float(self):
        r = rng(1)
        for _ in range(20):
            s = {"x": r.uniform(-1, 1), "x_dot": r.uniform(-3, 3), "theta": r.uniform(-0.5, 0.5),
                 "theta_dot": r.uniform(-5, 5)}
            f = lib().balance(s)
            with self.subTest(seed=SEED, state=s):
                self.assertIsInstance(f, float)
                self.assertLessEqual(abs(f), 10.0)

    def test_out_of_range_and_wrong_types_raise_value_error(self):
        m = lib()
        bad = [lambda: m.simulate(0.41, [0.0]), lambda: m.simulate(float("nan"), [0.0]),
               lambda: m.simulate(True, [0.0]), lambda: m.simulate(0.0, []), lambda: m.simulate(0.0, [10.5]),
               lambda: m.simulate(0.0, [0.0] * 20001), lambda: m.simulate(0.0, "0"), lambda: m.simulate(0.0, [False]),
               lambda: m.simulate(0.0, [0.0], 0.96), lambda: m.episode(0.0, zero, 0),
               lambda: m.episode(0.0, zero, 20001), lambda: m.episode(0.0, zero, 10.0),
               lambda: m.episode(0.0, zero, True), lambda: m.episode(0.0, "zero"),
               lambda: m.episode(0.0, lambda s: 11.0, 10), lambda: m.episode(0.0, lambda s: None, 10),
               lambda: m.episode(0.0, lambda s: float("inf"), 10), lambda: m.balance({"x": 0.0}),
               lambda: m.balance({"x": 0.0, "x_dot": 0.0, "theta": float("nan"), "theta_dot": 0.0}),
               lambda: m.balance(None)]
        for i, call in enumerate(bad):
            with self.subTest(i=i), self.assertRaises(ValueError):
                call()


class Determinism(unittest.TestCase):
    """REQ-03"""

    def test_two_calls_are_bit_identical(self):
        r = rng(2)
        theta0, x0 = r.uniform(-0.1, 0.1), r.uniform(-0.3, 0.3)
        forces = [r.uniform(-10, 10) for _ in range(400)]
        self.assertEqual(lib().simulate(theta0, forces, x0), lib().simulate(theta0, forces, x0))
        self.assertEqual(lib().episode(theta0, lib().balance, 1000, x0), lib().episode(theta0, lib().balance, 1000, x0))


class Start(unittest.TestCase):
    """REQ-04"""

    def test_the_first_state_is_the_start(self):
        r = rng(3)
        for _ in range(3):
            theta0, x0 = r.uniform(-0.4, 0.4), r.uniform(-0.95, 0.95)
            s = lib().simulate(theta0, [0.0], x0)[0]
            with self.subTest(seed=SEED, theta0=theta0, x0=x0):
                self.assertLessEqual(abs(s["x"] - x0), 1e-4)
                self.assertLessEqual(abs(s["theta"] - theta0), 1e-3)
                self.assertLessEqual(abs(s["x_dot"]), 0.1)
                self.assertLessEqual(abs(s["theta_dot"]), 0.1)


class Response(unittest.TestCase):
    """REQ-05"""

    def test_a_push_moves_the_cart_and_tips_the_pole_back(self):
        r = rng(4)
        for _ in range(3):
            f = signed(r, 3.0, 8.0)
            s = lib().simulate(0.0, [f] * 25)[-1]
            with self.subTest(seed=SEED, force=f):
                self.assertTrue(0.96 <= s["x_dot"] / (f * 0.05) <= 0.99, s)
                self.assertTrue(-3.1 <= s["theta_dot"] / (f * 0.05) <= -2.8, s)


class CenterOfMass(unittest.TestCase):
    """REQ-06"""

    def test_without_force_the_center_of_mass_stays(self):
        r = rng(5)
        theta0 = signed(r, 0.01, 0.02)
        n = lib().episode(theta0, zero)["steps"]
        start = com({"x": 0.0, "theta": theta0})
        states = lib().simulate(theta0, [0.0] * n)
        with self.subTest(seed=SEED, theta0=theta0, steps=n):
            for i, s in enumerate(states):
                self.assertLessEqual(abs(com(s) - start), 0.002, i)


class Falling(unittest.TestCase):
    """REQ-07"""

    def test_without_force_the_pole_falls_to_its_side(self):
        r = rng(6)
        for _ in range(3):
            theta0 = signed(r, 0.005, 0.02)
            out = lib().episode(theta0, zero)
            with self.subTest(seed=SEED, theta0=theta0):
                self.assertEqual(out["status"], "fallen")
                self.assertTrue(250 <= out["steps"] <= 1500, out["steps"])
                self.assertGreater(out["state"]["theta"] * theta0, 0)
                self.assertGreater(abs(out["state"]["theta"]), 0.5)


class Leaving(unittest.TestCase):
    """REQ-08"""

    def test_pushing_at_the_edge_leaves_the_rail(self):
        r = rng(7)
        for _ in range(2):
            x0 = signed(r, 0.9, 0.95)
            push = math.copysign(10.0, x0)
            out = lib().episode(0.0, lambda s: push, 5000, x0)
            with self.subTest(seed=SEED, x0=x0):
                self.assertEqual(out["status"], "out")
                self.assertLessEqual(out["steps"], 100)
                self.assertGreater(abs(out["state"]["x"]), 1.0)


class Timeout(unittest.TestCase):
    """REQ-09"""

    def test_a_short_episode_times_out(self):
        r = rng(8)
        for _ in range(3):
            theta0, limit = signed(r, 0.005, 0.02), r.randint(20, 200)
            out = lib().episode(theta0, zero, limit)
            with self.subTest(seed=SEED, theta0=theta0, max_steps=limit):
                self.assertEqual((out["status"], out["steps"]), ("timeout", limit))


class Mirror(unittest.TestCase):
    """REQ-10"""

    def test_negated_inputs_give_negated_states(self):
        r = rng(9)
        theta0, x0 = r.uniform(-0.2, 0.2), r.uniform(-0.5, 0.5)
        forces = [r.uniform(-10, 10) for _ in range(300)]
        base = lib().simulate(theta0, forces, x0)
        mirror = lib().simulate(-theta0, [-f for f in forces], -x0)
        with self.subTest(seed=SEED, theta0=theta0, x0=x0):
            for i, (a, b) in enumerate(zip(base, mirror)):
                for k in KEYS:
                    self.assertAlmostEqual(b[k], -a[k], delta=1e-6, msg=(i, k))


class Balance(unittest.TestCase):
    """REQ-11"""

    def test_balance_keeps_the_pole_up_and_returns_home(self):
        r = rng(10)
        for _ in range(3):
            theta0, x0 = r.uniform(-0.1, 0.1), r.uniform(-0.3, 0.3)
            out = lib().episode(theta0, lib().balance, 5000, x0)
            with self.subTest(seed=SEED, theta0=theta0, x0=x0):
                self.assertEqual((out["status"], out["steps"]), ("timeout", 5000))
                self.assertLessEqual(abs(out["state"]["theta"]), 0.01)
                self.assertLessEqual(abs(out["state"]["x"]), 0.05)


if __name__ == "__main__":
    unittest.main()
