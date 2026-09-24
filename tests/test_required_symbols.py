"""required_symbols の照合（harness/pipeline.py の symbol_found / missing_symbols）。

    python -m unittest discover -s tests -v

**なぜ要るか**: 分解役が `GameState.Width` のような修飾名で単位を書くと、宣言側のソースには
その並びが現れない（C# なら `class GameState` の中に `public int Width`）。生の部分一致だけで
照合していたため、実装が正しくても必ず落ちた。2026-09-18 の Issue #12 は 3 試行ともこれで
REJECT になったが、実装役が回した受入テストは 37 件すべて通っていた。

同時に、`required_symbols` には識別子だけでなく `class MetaPointRules` や
`[SerializeField] private int _turnBonusPerTurn` のようなコード片も入る（unity-2d の実運用）。
これらを正規表現として解釈すると `[SerializeField]` が文字クラスになって壊れる。
修飾名だけを特別扱いし、それ以外は今までどおり生の部分一致であることを固定する。
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import pipeline  # noqa: E402

# Issue #12 の単位定義（実測）。16 件のうち 11 件が修飾名。
ISSUE_12 = [
    "GamePhase", "MinoType", "Rotation", "ActiveMino", "GameState",
    "GameState.Width", "GameState.VisibleHeight", "GameState.Height",
    "GameState.Phase", "GameState.IsOccupied", "GameState.ActiveMino",
    "GameState.NextQueue", "GameState.Score", "GameState.ClearedLines",
    "GameState.ElapsedTicks", "GameState.InjectSeed",
]

# 実装役が書いたものと同じ形（宣言側。修飾名の並びはどこにも出てこない）。
CORE = """// SPDX-AI-Disclosure: ai-generated
using System.Collections.Generic;

namespace Game.Core
{
    public enum GamePhase { Ready, Playing, GameOver }

    public enum MinoType { I, O, T, S, Z, J, L }

    public enum Rotation { Spawn, Right, Two, Left }

    public readonly struct ActiveMino
    {
        public MinoType Type { get; }
        public int X { get; }
        public int Y { get; }
        public Rotation Rotation { get; }
    }

    public sealed class GameState
    {
        private readonly bool[] _cells = new bool[220];
        private uint _seed;

        public int Width => 10;
        public int VisibleHeight => 20;
        public int Height => 22;
        public GamePhase Phase { get; private set; }
        public ActiveMino? ActiveMino { get; private set; }
        public IReadOnlyList<MinoType> NextQueue { get; }
        public int Score { get; private set; }
        public int ClearedLines { get; private set; }
        public int ElapsedTicks { get; private set; }

        public bool IsOccupied(int x, int y) => x < 0 || x >= Width || y < 0 || y >= Height || _cells[y * Width + x];

        public void InjectSeed(uint seed) { if (Phase == GamePhase.Ready) _seed = seed == 0 ? 1u : seed; }
    }
}
"""


class SymbolFoundTests(unittest.TestCase):
    def test_qualified_names_pass_against_the_declaration(self):
        """これが退行そのもの。実装は正しいのに 11 件すべてが「揃っていません」になっていた。"""
        self.assertEqual(pipeline.missing_symbols(ISSUE_12, CORE), [])

    def test_a_member_that_is_really_absent_still_fails(self):
        """緩めただけで何も測らなくなっては意味が無い。"""
        without = CORE.replace("public int ClearedLines { get; private set; }", "")
        self.assertEqual(pipeline.missing_symbols(ISSUE_12, without), ["GameState.ClearedLines"])

    def test_a_type_that_is_really_absent_still_fails(self):
        text = "public sealed class GameState { public int Width => 10; }"
        self.assertEqual(pipeline.missing_symbols(["Rotation", "ActiveMino.Rotation"], text),
                         ["Rotation", "ActiveMino.Rotation"])

    def test_a_longer_identifier_does_not_satisfy_a_qualified_name(self):
        """語として見る。WidthOffset があるだけでは Width を満たさない。"""
        text = "public sealed class GameState { public int WidthOffset => 1; }"
        self.assertEqual(pipeline.missing_symbols(["GameState.Width"], text), ["GameState.Width"])

    def test_a_literal_qualified_name_still_passes(self):
        """静的アクセスなどで並びがそのまま出ている場合。"""
        self.assertEqual(pipeline.missing_symbols(["Board.Width"], "var w = Board.Width;"), [])

    def test_code_fragments_are_matched_raw(self):
        """unity-2d の実運用。正規表現として解釈すると [SerializeField] が文字クラスになって壊れる。"""
        so = ("public sealed class MetaPointResolverSO : ScriptableObject {\n"
              "    [SerializeField] private int _turnBonusPerTurn = 1;\n"
              "    public int TurnBonusPerTurn => _turnBonusPerTurn;\n}")
        required = ["class MetaPointResolverSO", "ScriptableObject",
                    "[SerializeField] private int _turnBonusPerTurn", "public int TurnBonusPerTurn"]
        self.assertEqual(pipeline.missing_symbols(required, so), [])

    def test_code_fragments_that_are_absent_still_fail(self):
        """コード片の側も緩めていないこと。"""
        self.assertEqual(pipeline.missing_symbols(["[SerializeField] private int _missing"], "class X {}"),
                         ["[SerializeField] private int _missing"])

    def test_a_dot_inside_a_fragment_is_not_a_qualified_name(self):
        """空白を含むものは修飾名と見なさない（生の部分一致のまま）。"""
        self.assertFalse(pipeline.symbol_found("private int x = Foo.Bar", "class X { int Foo; int Bar; }"))

    def test_it_does_not_check_which_type_declares_the_member(self):
        """**測っていないことの明文化**。正解を決めるのは分解役が書いた受入テストであって、この篩ではない。"""
        elsewhere = "public class GameState { } public class Other { public int Width => 10; }"
        self.assertEqual(pipeline.missing_symbols(["GameState.Width"], elsewhere), [])


class GateStaticTests(unittest.TestCase):
    """門の配線まで見る（照合関数だけ直しても呼ばれていなければ意味が無い）。"""

    def setUp(self):
        self.tmp = Path(__file__).resolve().parent / "fixtures"
        self.c = SimpleNamespace(
            test_driven=True,
            unit={"required_symbols": ISSUE_12, "impl_files": ["Game/Assets/Core/GameState.cs"]},
        )
        self.c.sb = lambda rel: SimpleNamespace(read_text=lambda **kw: CORE)

    def test_the_gate_passes_a_unit_written_with_qualified_names(self):
        with mock.patch.object(pipeline, "gate_static_common", return_value=None):
            self.assertIsNone(pipeline.gate_static(self.c))

    def test_test_driven_gate_leaves_signatures_to_the_compiler(self):
        """v2 §3.3・§5.1 の 3：テスト駆動の単位では、シグネチャを文字列で照合しない（形はコンパイラが決める）。

        v1 はここで impl_files の文字列だけを探し、whitelist の外にある宣言を見つけられずに誤爆した
        （game-harness#63）。無いメンバーを要求しても、門は禁止パターンと skip 属性の検査の結果だけを返す。
        """
        self.c.unit["required_symbols"] = ISSUE_12 + ["GameState.Nonexistent"]
        self.c.sb = lambda rel: (_ for _ in ()).throw(AssertionError("実装のファイルを読んではいけない"))
        with mock.patch.object(pipeline, "gate_static_common", return_value=None):
            self.assertIsNone(pipeline.gate_static(self.c))
        with mock.patch.object(pipeline, "gate_static_common", return_value="禁止パターン"):
            self.assertEqual(pipeline.gate_static(self.c), "禁止パターン")


if __name__ == "__main__":
    unittest.main()
