"""開示ゴールデンのステージング（harness/pipeline.py の stage_golden）。

    python -m unittest discover -s tests -v

単位には 2 種類ある。リファクタリング（既存実装から採ったゴールデンが正解）と、
テスト駆動（分解役が先に書いたテストが正解。ゴールデンは無い）。
**ゴールデンが 1 本も無いことは、前者では異常、後者では正常。**

ここで確かめたい要点は 2 つ。

1. テスト駆動の単位で ABORT しないこと（falling-blocks の Issue #12 が実測でここで止まった）
2. **それでも staging の掃除は行われること。** 「テスト駆動なら何もせず戻る」と書くと、
   前のランのゴールデンが staging に残り、エンジンのゴールデンランナーがそれを拾う。
   ステージング先は 1 つで全ランナーが見るので、消し忘れがそのまま汚染になる
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import pipeline  # noqa: E402


class StageGoldenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sandbox = self.tmp / "sandbox"
        self.stage = self.tmp / "stage"
        (self.sandbox / "tests" / "golden").mkdir(parents=True)

    def ctx(self, test_driven):
        return SimpleNamespace(stage=self.stage, test_driven=test_driven,
                               sb=lambda rel: self.sandbox / rel)

    def put_golden(self, name, body="{}"):
        (self.sandbox / "tests" / "golden" / name).write_text(body, encoding="utf-8")

    def leftover(self, name="golden_leftover.json"):
        """前のランが staging に残したゴールデン。"""
        self.stage.mkdir(parents=True, exist_ok=True)
        p = self.stage / name
        p.write_text("{}", encoding="utf-8")
        return p

    # ---- テスト駆動の単位（ゴールデンが無いのが正常）
    def test_no_golden_is_not_an_abort_for_a_test_driven_unit(self):
        self.assertEqual(pipeline.stage_golden(self.ctx(True), with_holdout=False), [])

    def test_a_missing_golden_directory_is_not_an_abort_either(self):
        shutil.rmtree(self.sandbox / "tests" / "golden")
        self.assertEqual(pipeline.stage_golden(self.ctx(True), with_holdout=False), [])

    def test_the_staging_area_is_still_cleaned_for_a_test_driven_unit(self):
        """ここが要点。早期 return にすると前のランのゴールデンが残り、全ランナーが拾う。"""
        left = self.leftover()
        pipeline.stage_golden(self.ctx(True), with_holdout=False)
        self.assertFalse(left.exists(), "staging に前のランのゴールデンが残っている")

    # ---- リファクタリングの単位（ゴールデンが無いのは異常）
    def test_no_golden_still_aborts_for_a_refactoring_unit(self):
        with self.assertRaises(SystemExit) as ctx:
            pipeline.stage_golden(self.ctx(False), with_holdout=False)
        self.assertIn("開示ゴールデンが 1 本もありません", str(ctx.exception))

    def test_golden_files_are_staged_for_a_refactoring_unit(self):
        self.put_golden("golden_a.json")
        self.put_golden("golden_b.json")
        left = self.leftover()
        staged = pipeline.stage_golden(self.ctx(False), with_holdout=False)
        self.assertEqual(staged, ["golden_a.json", "golden_b.json"])
        self.assertTrue((self.stage / "golden_a.json").exists())
        self.assertFalse(left.exists(), "古いゴールデンは消してから置き直す")

    def test_golden_files_are_staged_for_a_test_driven_unit_too(self):
        """テスト駆動でも、置かれていれば運ぶ（見なかったことにはしない）。"""
        self.put_golden("golden_a.json")
        self.assertEqual(pipeline.stage_golden(self.ctx(True), with_holdout=False), ["golden_a.json"])
        self.assertTrue((self.stage / "golden_a.json").exists())


if __name__ == "__main__":
    unittest.main()
