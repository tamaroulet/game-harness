"""課題 B10（docs/bench/B10/spec.md）の判定役。総監督が書いた。保護パス。

公開の入口（REQ-01）だけを通して確かめる。入口の形（REQ-01・REQ-02）と静止（REQ-04）のほかは、
迷路・傾きを走らせるたびに変わる種の乱数で選ぶ（前もって計算した値を埋め込む実装は通らない）。
種は失敗の行に出る。同じ種で再現するときだけ、環境変数 MAZE_JUDGE_SEED に渡す（Gate 1 と照合は渡さない）。
名前が test_ で始まらないので CI の全件（harness.fastsuite）には入らず、本体が無い間も CI を赤にしない。
"""
import math
import os
import random
import unittest

DT = 0.002
ROLL = 5.0 / 7.0 * 9.81
SEED = int(os.environ.get("MAZE_JUDGE_SEED") or random.SystemRandom().randrange(2 ** 32))
OPEN = ["#########", "#.......#", "#.......#", "#.......#", "#...S...#", "#.......#", "#.......#", "#......G#", "#########"]


def lib():
    import maze
    return maze


def rng(salt):
    return random.Random(SEED * 100 + salt)


def center(grid, r, c):
    return 0.1 * c + 0.05, 0.1 * (len(grid) - 1 - r) + 0.05


def find(grid, ch):
    return next((r, row.index(ch)) for r, row in enumerate(grid) if ch in row)


def cell_of(grid, s):
    """玉の中心のあるセル（行, 列）。"""
    return len(grid) - 1 - math.floor(s["y"] / 0.1), math.floor(s["x"] / 0.1)


def carve(r, rows, cols):
    """奇数の座標のセルをつなぐ完全な迷路（種の乱数で掘る）に、S と G を置く。"""
    grid = [["#"] * cols for _ in range(rows)]
    nodes = [(i, j) for i in range(1, rows, 2) for j in range(1, cols, 2)]
    start = r.choice(nodes)
    grid[start[0]][start[1]] = "."
    stack, seen = [start], {start}
    while stack:
        i, j = stack[-1]
        nxt = [(i + di, j + dj) for di, dj in ((2, 0), (-2, 0), (0, 2), (0, -2))
               if 0 < i + di < rows - 1 and 0 < j + dj < cols - 1 and (i + di, j + dj) not in seen]
        if not nxt:
            stack.pop()
            continue
        a, b = r.choice(nxt)
        grid[(i + a) // 2][(j + b) // 2] = "."
        grid[a][b] = "."
        seen.add((a, b))
        stack.append((a, b))
    s, g = r.sample(nodes, 2)
    grid[s[0]][s[1]], grid[g[0]][g[1]] = "S", "G"
    return ["".join(row) for row in grid]


def maze_of(r):
    return carve(r, r.choice((5, 7, 9)), r.choice((5, 7, 9)))


def sealed(r):
    """G のまわりの 4 つを壁にして、解けなくした迷路。"""
    while True:
        grid = [list(row) for row in maze_of(r)]
        gr, gc = find(grid, "G")
        for nr, nc in ((gr - 1, gc), (gr + 1, gc), (gr, gc - 1), (gr, gc + 1)):
            if grid[nr][nc] == ".":
                grid[nr][nc] = "#"
        if any("S" in row for row in grid):
            return ["".join(row) for row in grid]


def wander(r, n, hold=100):
    tilts = []
    while len(tilts) < n:
        t = (r.uniform(-0.1, 0.1), r.uniform(-0.1, 0.1))
        tilts += [t] * hold
    return tilts[:n]


class Entry(unittest.TestCase):
    """REQ-01・REQ-02"""

    def test_simulate_returns_one_state_per_tilt(self):
        states = lib().simulate(OPEN, [(0.01, -0.02)] * 4)
        self.assertEqual(len(states), 4)
        for s in states:
            self.assertEqual(set(s), {"x", "y", "z"})
            for v in s.values():
                self.assertIsInstance(v, float)

    def test_bad_grids_and_tilts_raise_value_error(self):
        m = lib()
        ok = [(0.0, 0.0)]
        grids = [OPEN[:4], OPEN + ["#########"] * 2, [row[:4] for row in OPEN], OPEN[:-1] + ["########"],
                 [OPEN[0]] + ["#..x...G#"] + OPEN[2:], [row.replace("G", ".") for row in OPEN],
                 [row.replace("G", "S") for row in OPEN], ["#.#######"] + OPEN[1:], "\n".join(OPEN),
                 [list(row) for row in OPEN]]
        for i, g in enumerate(grids):
            with self.subTest(grid=i):
                self.assertRaises(ValueError, m.simulate, g, ok)
                self.assertRaises(ValueError, m.solve, g)
        tilts = [[], [(0.0, 0.0)] * 20001, [(0.11, 0.0)], [(0.0, -0.1001)], [(0.0,)], [(0.0, 0.0, 0.0)],
                 [(True, 0.0)], [(float("nan"), 0.0)], [0.0], ((0.0, 0.0),)]
        for i, t in enumerate(tilts):
            with self.subTest(tilts=i), self.assertRaises(ValueError):
                m.simulate(OPEN, t)


class Determinism(unittest.TestCase):
    """REQ-03"""

    def test_two_calls_are_bit_identical(self):
        r = rng(1)
        grid, tilts = maze_of(r), wander(r, 600)
        self.assertEqual(lib().simulate(grid, tilts), lib().simulate(grid, tilts))
        self.assertEqual(lib().solve(grid), lib().solve(grid))


class Rolling(unittest.TestCase):
    """REQ-04・REQ-05"""

    def test_without_tilt_the_ball_stays(self):
        sx, sy = center(OPEN, *find(OPEN, "S"))
        for k, s in enumerate(lib().simulate(OPEN, [(0.0, 0.0)] * 500), 1):
            self.assertLessEqual(abs(s["x"] - sx), 1e-6, k)
            self.assertLessEqual(abs(s["y"] - sy), 1e-6, k)

    def test_a_constant_tilt_rolls_the_ball_with_five_sevenths_of_it(self):
        r = rng(2)
        sx, sy = center(OPEN, *find(OPEN, "S"))
        for _ in range(3):
            ax, ay = r.uniform(-0.1, 0.1), r.uniform(-0.1, 0.1)
            states = lib().simulate(OPEN, [(ax, ay)] * 400)
            with self.subTest(seed=SEED, ax=ax, ay=ay):
                for k, s in enumerate(states, 1):
                    t = DT * k
                    for got, a in ((s["x"] - sx, ax), (s["y"] - sy, ay)):
                        want = 0.5 * ROLL * a * t * t
                        self.assertLessEqual(abs(got - want), 0.03 * abs(want) + 0.002, (k, got, want))

    def test_mirroring_the_grid_and_the_tilt_mirrors_the_ball(self):
        r = rng(3)
        mirrored = [row[::-1] for row in OPEN]
        tilts = [(r.uniform(-0.1, 0.1), r.uniform(-0.1, 0.1))] * 400
        base = lib().simulate(OPEN, tilts)
        mirror = lib().simulate(mirrored, [(-ax, ay) for ax, ay in tilts])
        with self.subTest(seed=SEED, tilt=tilts[0]):
            for k, (a, b) in enumerate(zip(base, mirror), 1):
                self.assertLessEqual(abs(b["x"] - (0.9 - a["x"])), 1e-6, k)
                self.assertLessEqual(abs(b["y"] - a["y"]), 1e-6, k)
                self.assertLessEqual(abs(b["z"] - a["z"]), 1e-6, k)


def outside_walls(test, grid, states):
    for k, s in enumerate(states, 1):
        r, c = cell_of(grid, s)
        test.assertNotEqual(grid[r][c], "#", (k, s))
        test.assertTrue(0.0 < s["z"] < 0.1, (k, s))


class Walls(unittest.TestCase):
    """REQ-06"""

    def test_the_ball_never_enters_a_wall(self):
        r = rng(4)
        for _ in range(2):
            grid = maze_of(r)
            with self.subTest(seed=SEED, grid=grid):
                outside_walls(self, grid, lib().simulate(grid, wander(r, 3000)))


class Solve(unittest.TestCase):
    """REQ-07・REQ-08"""

    def test_solve_brings_the_ball_to_the_goal(self):
        r = rng(5)
        for _ in range(2):
            grid = maze_of(r)
            plan = lib().solve(grid)
            with self.subTest(seed=SEED, grid=grid):
                self.assertIsInstance(plan, list)
                self.assertTrue(1 <= len(plan) <= 20000, len(plan))
                states = lib().simulate(grid, plan)
                outside_walls(self, grid, states)
                gr, gc = find(grid, "G")
                gx, gy = center(grid, gr, gc)
                self.assertTrue(any(abs(s["x"] - gx) < 0.05 and abs(s["y"] - gy) < 0.05 for s in states))

    def test_an_unreachable_goal_gives_none(self):
        r = rng(6)
        for _ in range(2):
            grid = sealed(r)
            with self.subTest(seed=SEED, grid=grid):
                self.assertIsNone(lib().solve(grid))


if __name__ == "__main__":
    unittest.main()
