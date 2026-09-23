"""interface の実装役への展開と、単位定義からの書き出し（docs/design/mechanical_barriers.md 手順 5.6）。

    python -m unittest discover -s tests -v

**なぜ要るか**
- v2 の interface（作る形）は、パイプラインから実装役に渡っていなかった。実装役は prompt の文章と、
  既存の受入テストから名前を推し量るしかなかった（2026-09-23 に手順 6 の準備で発覚）
- 既存の単位を v2 に移すとき、生成テストをテスト置き場に書く正規の経路が無かった。総監督の deny 設定を
  すり抜けずに済むよう、分解役に「LLM を呼ばず、単位定義から書き出す」モードを持たせる
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "tests"))

import decompose  # noqa: E402
import pipeline  # noqa: E402
import testgen  # noqa: E402
from test_query import QUNIT  # noqa: E402
from test_testgen import UNIT  # noqa: E402
from test_unit_schema import GDD, SPEC  # noqa: E402


class Render(unittest.TestCase):
    def test_signatures_are_listed(self):
        text = testgen.render_interface(QUNIT)
        self.assertIn("- class Board", text)
        self.assertIn("  - public const int Width = 構造化仕様 PR-06 の値", text)
        self.assertIn("  - public static method bool InBounds(int x, int y)", text)
        self.assertIn("  - public method bool IsOccupied(int x, int y)", text)
        self.assertIn("  - public コンストラクタ (int tickCount)", text)
        self.assertIn("System.Collections.Generic.IReadOnlyList<int> NextQueue", text)

    def test_enum_values_in_order(self):
        self.assertIn("- enum GamePhase: Ready, Playing, GameOver", testgen.render_interface(UNIT))


class ImplementerPrompt(unittest.TestCase):
    def prompt_for(self, unit):
        c = SimpleNamespace(unit=unit, sandbox=Path("C:/sb"), sb=lambda rel: Path("C:/sb") / rel,
                            cfg={"implementer": {"cli": "agy", "headless_flag": "-p", "auto_approve_flag": "-y",
                                                 "model_flag": "--model", "model_name": "m"}},
                            ttl={"implementer": 1}, metrics={}, cur=None)
        with mock.patch.object(pipeline, "resolve_cli", return_value=["agy"]), \
                mock.patch.object(pipeline, "write_implementer_log"), \
                mock.patch.object(pipeline, "run", return_value=(0, "", "")) as run:
            pipeline.call_implementer(c)
        return run.call_args.args[0][2]

    def test_v2_unit_carries_its_interface(self):
        prompt = self.prompt_for(UNIT)
        self.assertIn(UNIT["prompt"], prompt)
        self.assertIn("## 作る型とメンバー", prompt)
        self.assertIn("public method void InjectSeed(uint seed)", prompt)

    def test_v1_unit_is_unchanged(self):
        prompt = self.prompt_for({"prompt": "v1 の指示", "whitelist": ["a"]})
        self.assertNotIn("## 作る型とメンバー", prompt)


class FromUnit(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, decompose, "CFG", decompose.CFG)
        self.addCleanup(setattr, decompose, "ROOT", decompose.ROOT)
        reader = lambda which: SPEC if which == "spec" else GDD
        self.enterContext(mock.patch.object(decompose, "base_reader", return_value=reader))
        # LLM を呼んだら失敗にする
        self.enterContext(mock.patch.object(decompose, "call_claude", side_effect=AssertionError("LLM を呼んだ")))

    def args(self, unit):
        path = self.tmp / "unit.json"
        path.write_text(json.dumps(unit, ensure_ascii=False), encoding="utf-8")
        return SimpleNamespace(project="falling-blocks", repo_dir=str(self.tmp / "repo"),
                               issue=None, file=None, id=None, from_unit=str(path))

    def test_writes_unit_and_generated_tests_without_llm(self):
        self.assertEqual(decompose.decompose(self.args(UNIT)), 0)
        repo = self.tmp / "repo"
        written = sorted(str(p.relative_to(repo)).replace("\\", "/") for p in repo.rglob("*") if p.is_file())
        self.assertEqual(written, ["tests/Core.Tests/Generated/Issue12Cases." + "cs", "tools/units/issue_12.json"])

    def test_v1_unit_is_refused(self):
        with self.assertRaises(SystemExit) as cm:
            decompose.decompose(self.args({"id": "issue_12", "prompt": "v1"}))
        self.assertEqual(cm.exception.code, 1)

    def test_unit_failing_the_gate_writes_nothing(self):
        bad = json.loads(json.dumps(UNIT))
        bad["acceptance"]["cases"][0]["expect"]["GameState.TickCount"] = 42
        self.assertEqual(decompose.decompose(self.args(bad)), 1)
        self.assertFalse((self.tmp / "repo").exists())


if __name__ == "__main__":
    unittest.main()
