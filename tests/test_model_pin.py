"""エージェントのモデルの固定と記録（harness/model_pin.py、2026-09-26 の是正）。

    python -m unittest tests.test_model_pin

**なぜ要るか**: v2 の報告書で、仕様分解役のモデル名が「記録なし（CLI の既定）」になった。ここでは次を縛る。
- すべてのエージェント設定が model_flag と正確なモデルの ID を持つ（CLI の既定に頼らない）
- CLI が報告したモデルが無い・設定と違う実行は止める
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import model_pin  # noqa: E402

CLAUDE = {"cli": "claude", "model_flag": "--model", "model": "claude-opus-5", "auxiliary_models": ["claude-haiku-"]}
AGY = {"cli": "agy", "model_flag": "--model", "model_name": "gemini-3.8-flash-medium",
       "output_format_args": ["--output-format", "stream-json"]}


class Require(unittest.TestCase):
    def test_the_model_must_be_written_in_the_setting(self):
        self.assertEqual(model_pin.require(CLAUDE, "x"), "claude-opus-5")
        for broken in ({k: v for k, v in CLAUDE.items() if k != "model"}, {**CLAUDE, "model": " "},
                       {k: v for k, v in CLAUDE.items() if k != "model_flag"}):
            with self.assertRaises(model_pin.ModelPinError):
                model_pin.require(broken, "x")

    def test_an_agy_implementer_must_use_stream_json(self):
        """agy の json の出力にはモデルが無い。記録できない形式では起動させない。"""
        self.assertEqual(model_pin.require_implementer(AGY, "x"), "gemini-3.8-flash-medium")
        with self.assertRaises(model_pin.ModelPinError) as ctx:
            model_pin.require_implementer({**AGY, "output_format_args": ["--output-format", "json"]}, "x")
        self.assertIn("stream-json", str(ctx.exception))


class Check(unittest.TestCase):
    def test_claude_the_pinned_model_is_used_and_recorded(self):
        used = {"claude-opus-5": {}, "claude-haiku-4-5-20251001": {}}
        self.assertEqual(model_pin.check_claude(CLAUDE, used, None, "x"),
                         ["claude-haiku-4-5-20251001", "claude-opus-5"])

    def test_claude_unreported_missing_or_stray_models_stop(self):
        for used, why in ((None, "modelUsage がありません"), ({}, None), ({"claude-haiku-4-5": {}}, None),
                          ({"claude-opus-5": {}, "claude-sonnet-5": {}}, None)):
            with self.subTest(used=used), self.assertRaises(model_pin.ModelPinError):
                model_pin.check_claude(CLAUDE, used, why, "x")

    def test_claude_models_reads_model_usage_from_the_envelope(self):
        self.assertEqual(model_pin.claude_models(json.dumps({"modelUsage": {"m": {}}})), ({"m": {}}, None))
        self.assertIsNone(model_pin.claude_models(json.dumps({"result": "x"}))[0])
        self.assertIsNone(model_pin.claude_models("not json")[0])

    def test_agy_reported_model_must_equal_the_setting(self):
        self.assertEqual(model_pin.check_reported("m", "m", "x"), "m")
        for reported in (None, "", "m-high"):
            with self.subTest(reported=reported), self.assertRaises(model_pin.ModelPinError):
                model_pin.check_reported("m", reported, "x")


class Configs(unittest.TestCase):
    def test_every_agent_setting_in_the_repository_pins_its_model(self):
        """unity-2d の実装役は json の出力で、使ったモデルを記録できない（移行するまで起動時に止まる）。
        それ以外の不備が増えたら、ここで落ちる。"""
        places = [where for where, _, _ in model_pin.agent_configs()]
        for expected in ("config/decompose.json", "config/spec.json",
                         "projects/falling-blocks/pipeline.json の implementer"):
            self.assertIn(expected, places)
        found = model_pin.problems()
        self.assertEqual([f for f in found if "projects/unity-2d/" not in f], [])
        self.assertEqual(len(found), 1, found)

    def test_a_setting_without_a_model_is_found(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "config").mkdir()
            (Path(d) / "config" / "decompose.json").write_text(json.dumps({"cli": "claude"}), encoding="utf-8")
            found = model_pin.problems(d)
        self.assertEqual(len(found), 1)
        self.assertIn("model_flag", found[0])


if __name__ == "__main__":
    unittest.main()
