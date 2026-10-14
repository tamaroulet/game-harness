"""課題 B03（docs/bench/B03/spec.md）の判定役。総監督が書いた。保護パス。

公開の入口（REQ-01）だけを通して確かめる。入口の形（REQ-01・REQ-02）のほかは、
球の位置・速さ・向きを走らせるたびに変わる種の乱数で、仕様の範囲の中から選ぶ（前もって計算した値を埋め込む実装は通らない）。
種は失敗の行に出る。同じ種で再現するときだけ、環境変数 BILLIARDS_JUDGE_SEED に渡す（Gate 1 と照合は渡さない）。
名前が test_ で始まらないので CI の全件（harness.fastsuite）には入らず、本体が無い間も CI を赤にしない。
"""
import math
import os
import random
import unittest

NAMES = ("cue", "target")
SEED = int(os.environ.get("BILLIARDS_JUDGE_SEED") or random.SystemRandom().randrange(2 ** 32))
BEHIND = (-0.4, 0.18)   # REQ-05 の的球：+x 寄りに打つ手球の道から離れた位置


def lib():
    import billiards
    return billiards


def rng(salt):
    return random.Random(SEED * 100 + salt)


def speed(vel):
    return math.hypot(*vel)


def pair(r):
    """台の範囲の中の、中心の距離が 0.08 以上の 2 点。"""
    while True:
        c = (r.uniform(-0.45, 0.45), r.uniform(-0.2, 0.2))
        t = (r.uniform(-0.45, 0.45), r.uniform(-0.2, 0.2))
        if math.dist(c, t) >= 0.08:
            return c, t


def aim(c, t):
    return math.degrees(math.atan2(t[1] - c[1], t[0] - c[0]))


def corner_line(r, lo, hi):
    """(隅 C, C へ向かう単位ベクトル, 距離 L)。直線は x 軸と 35°〜55° の角をなす（REQ-09）。"""
    sx, sy = r.choice((-1, 1)), r.choice((-1, 1))
    phi = math.radians(r.uniform(35.0, 55.0))
    return (0.5 * sx, 0.25 * sy), (sx * math.cos(phi), sy * math.sin(phi)), r.uniform(lo, hi)


def on_line(corner, u, dist):
    return corner[0] - dist * u[0], corner[1] - dist * u[1]


class Entry(unittest.TestCase):
    """REQ-01・REQ-02"""

    def test_simulate_returns_both_balls_per_step(self):
        states = lib().simulate((-0.2, 0.0), (0.2, 0.1), 1.0, 30.0, 4)
        self.assertEqual(len(states), 4)
        for s in states:
            self.assertEqual(set(s), set(NAMES))
            for name in NAMES:
                b = s[name]
                self.assertEqual(set(b), {"pos", "vel", "pocketed"})
                self.assertEqual((len(b["pos"]), len(b["vel"])), (2, 2))
                for v in list(b["pos"]) + list(b["vel"]):
                    self.assertIsInstance(v, float)
                self.assertIsInstance(b["pocketed"], bool)

    def test_shot_returns_steps_and_both_balls(self):
        out = lib().shot((-0.2, 0.0), (0.2, 0.1), 0.3, 90.0)
        self.assertEqual(set(out), {"steps", "cue", "target"})
        self.assertIsInstance(out["steps"], int)
        for name in NAMES:
            self.assertEqual(set(out[name]), {"pos", "vel", "pocketed"})

    def test_out_of_range_and_wrong_types_raise_value_error(self):
        m = lib()
        ok_c, ok_t = (-0.2, 0.0), (0.2, 0.1)
        bad = [lambda: m.simulate((0.46, 0.0), ok_t, 1.0, 0.0, 5), lambda: m.simulate((0.0, 0.21), ok_t, 1.0, 0.0, 5),
               lambda: m.simulate(ok_c, (-0.2, 0.05), 1.0, 0.0, 5), lambda: m.simulate((0.0,), ok_t, 1.0, 0.0, 5),
               lambda: m.simulate("00", ok_t, 1.0, 0.0, 5), lambda: m.simulate((True, 0.0), ok_t, 1.0, 0.0, 5),
               lambda: m.simulate((float("nan"), 0.0), ok_t, 1.0, 0.0, 5),
               lambda: m.simulate(ok_c, ok_t, 0.05, 0.0, 5), lambda: m.simulate(ok_c, ok_t, 2.1, 0.0, 5),
               lambda: m.simulate(ok_c, ok_t, True, 0.0, 5), lambda: m.simulate(ok_c, ok_t, float("nan"), 0.0, 5),
               lambda: m.simulate(ok_c, ok_t, 1.0, float("inf"), 5), lambda: m.simulate(ok_c, ok_t, 1.0, "0", 5),
               lambda: m.simulate(ok_c, ok_t, 1.0, True, 5), lambda: m.simulate(ok_c, ok_t, 1.0, 0.0, 0),
               lambda: m.simulate(ok_c, ok_t, 1.0, 0.0, 20001), lambda: m.simulate(ok_c, ok_t, 1.0, 0.0, 2.0),
               lambda: m.simulate(ok_c, ok_t, 1.0, 0.0, True), lambda: m.shot(ok_c, ok_t, 2.5, 0.0),
               lambda: m.shot((0.0, 0.0), (0.0, 0.0), 1.0, 0.0)]
        for i, call in enumerate(bad):
            with self.subTest(i=i), self.assertRaises(ValueError):
                call()


class Determinism(unittest.TestCase):
    """REQ-03"""

    def test_two_calls_are_bit_identical(self):
        r = rng(2)
        c, t = pair(r)
        v, a = r.uniform(1.0, 2.0), aim(c, t) + r.uniform(-10.0, 10.0)
        self.assertEqual(lib().simulate(c, t, v, a, 800), lib().simulate(c, t, v, a, 800))
        self.assertEqual(lib().shot(c, t, v, a), lib().shot(c, t, v, a))


class Start(unittest.TestCase):
    """REQ-04"""

    def test_the_first_state_is_the_shot(self):
        r = rng(3)
        for _ in range(3):
            c, t = pair(r)
            v, a = r.uniform(0.1, 2.0), r.uniform(-180.0, 180.0)
            s = lib().simulate(c, t, v, a, 1)[0]
            shot = (v * math.cos(math.radians(a)), v * math.sin(math.radians(a)))
            with self.subTest(seed=SEED, cue=c, target=t, speed=v, angle=a):
                self.assertLessEqual(math.dist(s["cue"]["pos"], c), 0.005)
                self.assertLessEqual(math.dist(s["cue"]["vel"], shot), 0.01 * v)
                self.assertLessEqual(math.dist(s["target"]["pos"], t), 1e-6)
                self.assertLessEqual(speed(s["target"]["vel"]), 1e-6)
                self.assertEqual((s["cue"]["pocketed"], s["target"]["pocketed"]), (False, False))


class Slowing(unittest.TestCase):
    """REQ-05"""

    def test_a_free_ball_stops_after_speed_over_1_5_on_a_straight_line(self):
        r = rng(4)
        for _ in range(3):
            c = (r.uniform(-0.25, -0.15), r.uniform(-0.05, 0.05))
            v, a = r.uniform(0.3, 0.5), r.uniform(-20.0, 20.0)
            out = lib().shot(c, BEHIND, v, a)
            end = out["cue"]["pos"]
            heading = math.atan2(end[1] - c[1], end[0] - c[0])
            with self.subTest(seed=SEED, cue=c, speed=v, angle=a):
                self.assertIs(out["cue"]["pocketed"], False)
                self.assertTrue(0.99 <= math.dist(end, c) / (v / 1.5) <= 1.01, end)
                self.assertLessEqual(abs(heading - math.radians(a)), 1e-3)


class Rest(unittest.TestCase):
    """REQ-06"""

    def test_every_shot_comes_to_rest_within_3000_steps(self):
        r = rng(5)
        for _ in range(4):
            c, t = pair(r)
            v, a = r.uniform(1.0, 2.0), aim(c, t) + r.uniform(-15.0, 15.0)
            out = lib().shot(c, t, v, a)
            with self.subTest(seed=SEED, cue=c, target=t, speed=v, angle=a):
                self.assertLessEqual(out["steps"], 3000)
                for name in NAMES:
                    if not out[name]["pocketed"]:
                        self.assertLess(speed(out[name]["vel"]), 0.001, name)


class HeadOn(unittest.TestCase):
    """REQ-07"""

    def test_a_head_on_hit_stops_the_cue_ball_and_sends_the_target_on(self):
        r = rng(6)
        for _ in range(3):
            x, y, d, v = r.uniform(-0.4, -0.2), r.uniform(-0.15, 0.15), r.uniform(0.1, 0.25), r.uniform(0.5, 1.5)
            states = lib().simulate((x, y), (x + d, y), v, 0.0, 700)
            gaps = [math.dist(s["cue"]["pos"], s["target"]["pos"]) for s in states]
            with self.subTest(seed=SEED, cue=(x, y), gap=d, speed=v):
                start = next((i for i, g in enumerate(gaps) if g <= 0.05), None)
                self.assertIsNotNone(start, "接触が無い")
                end = next((i for i in range(start + 1, len(gaps)) if gaps[i] > 0.05), None)
                self.assertIsNotNone(end, "離れない")
                before = speed(states[start]["cue"]["vel"])
                self.assertLessEqual(speed(states[end]["cue"]["vel"]) / before, 0.2)
                self.assertTrue(0.75 <= speed(states[end]["target"]["vel"]) / before <= 1.0)
                self.assertLessEqual(abs(states[end]["target"]["vel"][1]), 1e-6)


class Cushion(unittest.TestCase):
    """REQ-08"""

    def test_a_ball_bounces_back_from_a_cushion(self):
        r = rng(7)
        for _ in range(2):
            c, v = (r.uniform(0.0, 0.3), r.uniform(0.05, 0.15)), r.uniform(1.0, 1.5)
            states = lib().simulate(c, (-0.35, -0.15), v, 0.0, 800)
            xs = [s["cue"]["pos"][0] for s in states]
            with self.subTest(seed=SEED, cue=c, speed=v):
                k1 = next((i for i, x in enumerate(xs) if x >= 0.47), None)
                self.assertIsNotNone(k1, "縁に届かない")
                k2 = next((i for i in range(k1 + 1, len(xs)) if xs[i] < 0.47), None)
                self.assertIsNotNone(k2, "縁から戻らない")
                v1, v2 = states[k1 - 1]["cue"]["vel"][0], states[k2]["cue"]["vel"][0]
                self.assertTrue(0.6 <= -v2 / v1 <= 1.0, (v1, v2))
                for i in range(k2 + 1):
                    self.assertLessEqual(abs(states[i]["cue"]["pos"][1] - c[1]), 1e-6, i)
                    self.assertLessEqual(abs(xs[i]), 0.49, i)

    def test_fast_shots_stay_inside_the_cushions(self):
        r = rng(8)
        for _ in range(3):
            c, t = pair(r)
            a = r.uniform(-180.0, 180.0)
            states = lib().simulate(c, t, 2.0, a, 1500)
            with self.subTest(seed=SEED, cue=c, target=t, angle=a):
                for i, s in enumerate(states):
                    for name in NAMES:
                        if not s[name]["pocketed"]:
                            self.assertLessEqual(abs(s[name]["pos"][0]), 0.49, (i, name))
                            self.assertLessEqual(abs(s[name]["pos"][1]), 0.24, (i, name))


class Pocket(unittest.TestCase):
    """REQ-09"""

    def test_a_ball_shot_at_a_corner_is_pocketed_and_then_stays(self):
        r = rng(9)
        for _ in range(2):
            corner, u, length = corner_line(r, 0.2, 0.35)
            c = on_line(corner, u, length)
            t = (-0.3 * math.copysign(1, u[0]), -0.15 * math.copysign(1, u[1]))
            v, a = r.uniform(0.8, 1.5), math.degrees(math.atan2(u[1], u[0]))
            states = lib().simulate(c, t, v, a, 800)
            with self.subTest(seed=SEED, cue=c, corner=corner, speed=v):
                k = next((i for i, s in enumerate(states) if s["cue"]["pocketed"]), None)
                self.assertIsNotNone(k, "ポケットに入らない")
                self.assertLessEqual(math.dist(states[k]["cue"]["pos"], corner), 0.05 + 1e-12)
                if k > 0:
                    self.assertGreater(math.dist(states[k - 1]["cue"]["pos"], corner), 0.05)
                for s in states[k:]:
                    self.assertEqual(s["cue"], {"pos": states[k]["cue"]["pos"], "vel": [0.0, 0.0], "pocketed": True})
                self.assertIs(lib().shot(c, t, v, a)["cue"]["pocketed"], True)

    def test_a_head_on_push_toward_a_corner_pockets_the_target(self):
        r = rng(10)
        for _ in range(2):
            corner, u, length = corner_line(r, 0.15, 0.25)
            t, c = on_line(corner, u, length), on_line(corner, u, length + 0.1)
            v, a = r.uniform(1.0, 1.5), math.degrees(math.atan2(u[1], u[0]))
            with self.subTest(seed=SEED, cue=c, target=t, corner=corner, speed=v):
                self.assertIs(lib().shot(c, t, v, a)["target"]["pocketed"], True)


class Mirror(unittest.TestCase):
    """REQ-10"""

    def test_mirrored_shots_give_mirrored_states(self):
        r = rng(11)
        c, t = pair(r)
        v, a = r.uniform(0.5, 2.0), aim(c, t) + r.uniform(-15.0, 15.0)
        base = lib().simulate(c, t, v, a, 1500)
        flip_y = lib().simulate((c[0], -c[1]), (t[0], -t[1]), v, -a, 1500)
        flip_x = lib().simulate((-c[0], c[1]), (-t[0], t[1]), v, 180.0 - a, 1500)
        with self.subTest(seed=SEED, cue=c, target=t, speed=v, angle=a):
            for i, (b, my, mx) in enumerate(zip(base, flip_y, flip_x)):
                for name in NAMES:
                    self.assertEqual(my[name]["pocketed"], b[name]["pocketed"], (i, name))
                    self.assertEqual(mx[name]["pocketed"], b[name]["pocketed"], (i, name))
                    for k in ("pos", "vel"):
                        self.assertAlmostEqual(my[name][k][0], b[name][k][0], delta=1e-6, msg=(i, name, k))
                        self.assertAlmostEqual(my[name][k][1], -b[name][k][1], delta=1e-6, msg=(i, name, k))
                        self.assertAlmostEqual(mx[name][k][0], -b[name][k][0], delta=1e-6, msg=(i, name, k))
                        self.assertAlmostEqual(mx[name][k][1], b[name][k][1], delta=1e-6, msg=(i, name, k))


if __name__ == "__main__":
    unittest.main()
