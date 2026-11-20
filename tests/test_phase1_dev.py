"""フェーズ 1 の公開の dev の並びと、dev の外を走らせない見張り（harness/phase1_dev.py）の試験。

    python -m unittest discover -s tests -v
"""
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import phase1_dev  # noqa: E402
import scb_run  # noqa: E402


def doc_list(marker):
    """実験と計画 §6-2 の dev の行（marker で始まる行の「：」の後）の名前。"""
    text = (ROOT / "docs" / "v4" / "experiments.md").read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines() if ln.strip().startswith(marker))
    names = line.split("）：", 1)[1].split("。", 1)[0]
    return sorted(names.split("・"))


class DevListTests(unittest.TestCase):
    def test_counts_match_the_split(self):
        dev = phase1_dev.load()
        self.assertEqual(len(dev["slopcodebench"]), 11)
        self.assertEqual(len(dev["swe_game_mode4"]), 12)
        self.assertEqual(sum(len(v) for v in dev["swe_game_mode4"].values()), 23)

    def test_lists_are_the_public_dev_of_the_docs(self):
        dev = phase1_dev.load()
        self.assertEqual(sorted(dev["slopcodebench"]), doc_list("- dev（11 問"))
        self.assertEqual(sorted(dev["swe_game_mode4"]), doc_list("- dev（12 ゲーム"))

    def test_case_ids_belong_to_their_game(self):
        for game, cases in phase1_dev.load()["swe_game_mode4"].items():
            for case in cases:
                self.assertTrue(re.fullmatch(re.escape(game.replace("_", "-")) + r"-[a-z0-9-]+-v\d+", case), case)


class GuardTests(unittest.TestCase):
    def test_scb_guard(self):
        self.assertEqual(phase1_dev.require_scb("etl_pipeline"), "etl_pipeline")
        with self.assertRaises(phase1_dev.NotDevError) as cm:
            phase1_dev.require_scb("not-a-dev-problem")
        self.assertNotIn("not-a-dev-problem", str(cm.exception))   # 渡された名前を画面に出さない

    def test_mode4_guard_needs_the_pair(self):
        self.assertEqual(phase1_dev.require_mode4("pixel_platformer", "pixel-platformer-phase-prop-seat-v1"),
                         ("pixel_platformer", "pixel-platformer-phase-prop-seat-v1"))
        for game, case in (("ballast_yard", "canopy-dash-hollow-phase-bind-v2"), ("not_a_dev_game", "x-v1"),
                           ("pixel_platformer", "not-a-dev-case-v1")):
            with self.assertRaises(phase1_dev.NotDevError):
                phase1_dev.require_mode4(game, case)

    def test_scb_run_stops_before_making_anything(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out"
            with self.assertRaises(phase1_dev.NotDevError):
                scb_run.run_problem({}, "not-a-dev-problem", "H0", out, fake=True)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
