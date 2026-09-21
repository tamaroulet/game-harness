"""不変条件テストの Outer 段（harness/invrun.py、docs/design/mechanical_barriers.md §5 手順 5b）。

    python -m unittest discover -s tests -v

**なぜ要るか**: 不変条件テストは実装役に見せない。サンドボックスの外に生成して、サンドボックスの
実装を外から参照してビルドする。破れたときに実装役へ返すのは反例の 1 行だけ。
この経路が本当に働くことを、わざと保存則を破る合成の実装で dotnet test まで通して確かめる。
"""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "tests"))

import invgen  # noqa: E402
import invrun  # noqa: E402
from test_invgen import DECL  # noqa: E402
from test_unit_schema import GDD, SPEC  # noqa: E402

# 保存則 INV-01 を破る合成の実装（Advance のたびにブロックだけが増える）。ゲームの実装ではない
BROKEN_CORE = """namespace Game.Core
{
    public enum GamePhase { Ready, Playing, GameOver }
    public sealed class Board { public const int Width = 10; }
    public sealed class GameState
    {
        public GamePhase Phase { get; private set; }
        public int LinesCleared { get; private set; }
        public int BlockCount { get; private set; }
        public int LockedCount { get; private set; }
        public void Advance() { BlockCount++; }
        public void InjectSeed(uint seed) { }
    }
}
"""
KEPT_CORE = BROKEN_CORE.replace("BlockCount++;", "")


class Pure(unittest.TestCase):
    def test_seed_is_deterministic_and_nonzero(self):
        self.assertEqual(invrun.seed_for("diff"), invrun.seed_for("diff"))
        self.assertNotEqual(invrun.seed_for("diff a"), invrun.seed_for("diff b"))
        self.assertGreater(invrun.seed_for(""), 0)

    def test_only_counterexample_lines_are_kept(self):
        out = ("  Failed Invariant_INV_01 [12 ms]\n  Error Message:\n"
               "   INVARIANT_FAIL id=INV-01 rule=LP-01 seed=77 ops=GameState.Advance()\n"
               "  Stack Trace:\n     at Game.Core.Tests.Generated.InvariantsCases.Fail(...)\n"
               "   INVARIANT_FAIL id=INV-01 rule=LP-01 seed=77 ops=GameState.Advance()\n")
        self.assertEqual(invrun.parse_failures(out),
                         ["INVARIANT_FAIL id=INV-01 rule=LP-01 seed=77 ops=GameState.Advance()"])

    def test_refuses_to_write_inside_the_sandbox(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                invrun.materialize(Path(d) / "sb" / "inv", Path(d) / "sb", "Core", {})

    def test_no_declaration_means_no_stage(self):
        c = SimpleNamespace(cfg={"project": {"id": "no-such-project"}}, tel={})
        self.assertIsNone(invrun.check(c))
        self.assertEqual(c.tel["invariants"], {"declared": False})


class EndToEnd(unittest.TestCase):
    """生成 → サンドボックスの外に配置 → dotnet test → 反例の行の読み取り。"""

    def run_against(self, core_text):
        with tempfile.TemporaryDirectory() as d:
            core = Path(d) / "sandbox" / "Game" / "Assets" / "Core"
            core.mkdir(parents=True)
            (core / ("GameState." + "cs")).write_text(core_text, encoding="utf-8")
            files = invgen.generate(DECL, SPEC, GDD)
            csproj = invrun.materialize(Path(d) / "out" / "invariants", Path(d) / "sandbox",
                                        "Game/Assets/Core", files)
            rc, out, err = invrun.run_tests(csproj, seed=12345, ttl=600)
            return rc, invrun.parse_failures(out + "\n" + err), (out + err)[-2000:]

    def test_broken_conservation_yields_one_line_counterexample(self):
        rc, failures, log = self.run_against(BROKEN_CORE)
        self.assertNotEqual(rc, 0, log)
        self.assertTrue(failures, log)
        self.assertTrue(all(f.startswith("INVARIANT_FAIL id=INV-01 rule=LP-01 seed=") for f in failures), failures)
        self.assertIn("ops=", failures[0])

    def test_kept_conservation_passes(self):
        rc, failures, log = self.run_against(KEPT_CORE)
        self.assertEqual((rc, failures), (0, []), log)


if __name__ == "__main__":
    unittest.main()
