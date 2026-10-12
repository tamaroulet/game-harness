import unittest

try:
    import tower
except ImportError:  # mujoco が無い環境
    tower = None


@unittest.skipIf(tower is None, "mujoco が無い")
class TowerTest(unittest.TestCase):
    def test_simulate_shape(self):
        states = tower.simulate([0.0, 0.01], 3)
        self.assertEqual(len(states), 3)
        for boxes in states:
            self.assertEqual(len(boxes), 2)
            for b in boxes:
                self.assertEqual((len(b["pos"]), len(b["vel"])), (3, 3))

    def test_bad_inputs_raise(self):
        bad = [lambda: tower.simulate([], 5), lambda: tower.simulate([0.6], 5),
               lambda: tower.simulate([0.0, 0.11], 5), lambda: tower.simulate([True], 5),
               lambda: tower.simulate([0.0], 2.0), lambda: tower.simulate([0.0], 20001),
               lambda: tower.harmonic(7), lambda: tower.harmonic(True)]
        for i, call in enumerate(bad):
            with self.subTest(i=i), self.assertRaises(ValueError):
                call()

    def test_deterministic(self):
        self.assertEqual(tower.simulate([0.0, 0.07], 100), tower.simulate([0.0, 0.07], 100))

    def test_harmonic_steps(self):
        xs = tower.harmonic(5)
        self.assertEqual(xs[0], 0.0)
        for j in range(4):
            self.assertAlmostEqual(xs[j + 1] - xs[j], 0.04 / (4 - j), delta=1e-12)

    def test_supported_stands_and_overhang_falls(self):
        self.assertIs(tower.stands([0.0, 0.03]), True)
        self.assertIs(tower.stands([0.0, 0.07]), False)


if __name__ == "__main__":
    unittest.main()
