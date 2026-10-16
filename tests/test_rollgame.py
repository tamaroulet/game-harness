import unittest

try:
    import rollgame
except ImportError:  # mujoco が無い環境
    rollgame = None


class RollgameTest(unittest.TestCase):
    def test_step_shape(self):
        s = rollgame.Game().step(0.0, 0.0)
        self.assertEqual((len(s["pos"]), len(s["vel"])), (3, 3))
        self.assertIsNone(s["outcome"])

    def test_bad_inputs_raise(self):
        game = rollgame.Game()
        for tx, ty in [(0.21, 0), (float("nan"), 0), (True, 0), ("0.1", 0), (None, 0)]:
            with self.subTest(tx=tx, ty=ty), self.assertRaises(ValueError):
                game.step(tx, ty)

    def test_deterministic(self):
        inputs = [(0.1, -0.05)] * 50 + [(-0.1, 0.1)] * 50
        a, b = rollgame.Game(), rollgame.Game()
        self.assertEqual([a.step(*i) for i in inputs], [b.step(*i) for i in inputs])

    def test_rolls_downhill(self):
        game = rollgame.Game()
        for _ in range(100):
            s = game.step(0.1, 0.0)
        self.assertGreater(s["pos"][0], -0.35)

    def test_fell_is_final(self):
        game = rollgame.Game()
        last = None
        for _ in range(5000):
            last = game.step(-0.2, 0.0)
            if last["outcome"] is not None:
                break
        self.assertEqual(last["outcome"], "fell")
        self.assertEqual(game.step(0.1, 0.1), last)


if __name__ == "__main__":
    unittest.main()
