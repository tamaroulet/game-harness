"""ビルド診断の射影（harness/diagproj.py、ADR-003 §3.11、B3.2）。

    python -m unittest discover -s tests -v

**なぜ要るか**: 生成テスト側に出たコンパイルエラーを生のまま渡すと、実装役は「テストが壊れている」と
取り違える（因果盲目）。契約の不整合として interface の識別子に射影し、生のメッセージは渡さないこと、
連鎖したエラーを根の 1 行に畳むことを確かめる。入力は合成した診断の行で、実物のログは使わない。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import diagproj  # noqa: E402

UNIT = {"interface": {"types": [
    {"name": "GamePhase", "kind": "enum", "values": ["Ready", "Playing"]},
    {"name": "GameState", "kind": "class", "members": [
        {"name": "GameState", "kind": "ctor"},
        {"name": "Tick", "kind": "method", "type": "void"},
        {"name": "BlockCount", "kind": "property", "type": "int"}]},
]}}
SB = r"C:\wt\sb"
TEST = SB + r"\tests\Core.Tests\Generated\Issue17Cases" + ".cs"
IMPL = SB + r"\Game\Assets\Core\GameState" + ".cs"
OUT = "\n".join([
    f"  {TEST}(30,17): error CS1061: 'GameState' does not contain a definition for 'Tick' and no accessible "
    f"extension method 'Tick' accepting a first argument of type 'GameState' could be found [{SB}\t.csproj]",
    f"  {TEST}(41,17): error CS1061: 'GameState' does not contain a definition for 'Tick' and no accessible "
    f"extension method 'Tick' accepting a first argument of type 'GameState' could be found [{SB}\t.csproj]",
    f"  {TEST}(52,30): error CS0019: Operator '+' cannot be applied to operands of type 'int?' and 'bool' [{SB}\t.csproj]",
    f"  {IMPL}(12,9): error CS0103: The name 'counter' does not exist in the current context [{SB}\t.csproj]",
    "Build FAILED.",
])


class Projection(unittest.TestCase):
    def test_contract_errors_fold_into_one_line_per_member(self):
        lines = diagproj.project(OUT, UNIT, "Game/Assets/Core")
        self.assertEqual(lines, [
            "BUILD_DIAG kind=contract symbol=GameState.Tick code=CS1061",
            "BUILD_DIAG kind=impl file=Game/Assets/Core/GameState.cs line=12 code=CS0103",
        ])

    def test_raw_messages_are_not_passed(self):
        text = diagproj.feedback(OUT, UNIT, "Game/Assets/Core")
        self.assertNotIn("does not contain", text)
        self.assertNotIn("counter", text)
        self.assertNotIn(SB, text)
        self.assertIn("検査系の故障ではなく", text)

    def test_no_compile_error_means_no_projection(self):
        self.assertIsNone(diagproj.feedback("Build succeeded.\n  1 Warning(s)", UNIT, "Game/Assets/Core"))

    def test_output_is_capped(self):
        many = "\n".join(f"{IMPL}({i},1): error CS1002: ; expected [x]" for i in range(1, 40))
        self.assertEqual(len(diagproj.project(many, UNIT, "Game/Assets/Core")), diagproj.MAX_LINES)


if __name__ == "__main__":
    unittest.main()
