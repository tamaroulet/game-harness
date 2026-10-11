import unittest

import launcher


class LauncherEntryTest(unittest.TestCase):
    def test_simulate_length(self):
        self.assertEqual(len(launcher.simulate(45, 3, 5)), 5)

    def test_invalid_inputs(self):
        for call in (lambda: launcher.simulate(45, 3, True),
                     lambda: launcher.simulate(4, 3, 5),
                     lambda: launcher.aim(0, 3)):
            with self.assertRaises(ValueError):
                call()

    def test_out_of_reach_is_none(self):
        self.assertIsNone(launcher.aim(100.0, 3))

    def test_deterministic(self):
        self.assertEqual(launcher.landing(30, 3).hex(), launcher.landing(30, 3).hex())


if __name__ == "__main__":
    unittest.main()
