"""引数付き問い合わせの受入データ（docs/design/mechanical_barriers.md §5 手順 5.5）。

    python -m unittest discover -s tests -v

**なぜ要るか**: v2 の期待値は状態にしか書けず、`Board.InBounds(x, y)` のような問い合わせを表せなかった。
query の行を足すにあたり、生成テストには戻り値だけでなく次の 2 つを必ず入れる（2026-09-23 裁定）。
- 冪等：2 回呼んで同じ値
- 状態の不変（CQS）：公開状態を控えて、呼んだ後に変わっていない

それが本当に働くことを、わざと状態を書き換える問い合わせ・呼ぶたびに値が変わる問い合わせの
合成の実装で、dotnet test まで通して確かめる。
"""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "tests"))

import invrun  # noqa: E402
import testgen  # noqa: E402
import unit_schema  # noqa: E402
from test_testgen import build  # noqa: E402
from test_unit_schema import GDD, SPEC  # noqa: E402

QUNIT = {
    "schema": 2,
    "id": "issue_q",
    "title": "盤面の問い合わせ",
    "prompt": "`Board.InBounds` と `GameState.IsOccupied` を問い合わせとして作る。状態は変えない。",
    "interface": {"types": [
        {"name": "Board", "kind": "class", "members": [
            {"name": "Width", "kind": "const", "type": "int", "value": {"param": "PR-06"}},
            {"name": "InBounds", "kind": "method", "type": "bool", "static": True,
             "params": [{"name": "x", "type": "int"}, {"name": "y", "type": "int"}]},
        ]},
        {"name": "GameState", "kind": "class", "members": [
            {"name": "GameState", "kind": "ctor"},
            {"name": "GameState", "kind": "ctor", "params": [{"name": "tickCount", "type": "int"}]},
            {"name": "TickCount", "kind": "property", "type": "int"},
            {"name": "NextQueue", "kind": "property", "type": "System.Collections.Generic.IReadOnlyList<int>"},
            {"name": "IsOccupied", "kind": "method", "type": "bool",
             "params": [{"name": "x", "type": "int"}, {"name": "y", "type": "int"}]},
        ]},
    ]},
    "whitelist": ["Game/Assets/Core/Board.cs"],
    "impl_files": ["Game/Assets/Core/Board.cs"],
    "acceptance": {"cases": [
        {"id": "inbounds-origin", "rule": "PR-06", "given": {},
         "op": {"query": "Board.InBounds", "args": {"x": 0, "y": 0}}, "expect": {"return": True}},
        {"id": "inbounds-min", "rule": "PR-06", "given": {},
         "op": {"query": "Board.InBounds", "args": {"x": -2147483648, "y": 0}}, "expect": {"return": False}},
        {"id": "occupied-empty", "rule": "LP-01", "given": {"GameState.TickCount": 5},
         "op": {"query": "GameState.IsOccupied", "args": {"x": 0, "y": 0}}, "expect": {"return": False}},
    ]},
    "human_check_point": "不要",
    "playtest": "none",
}
BODY = json.dumps(QUNIT, ensure_ascii=False).encode("utf-8")

# 合成の実装（ゲームの実装ではない）。PURE は問い合わせを正しく作ったもの
PURE = """namespace Game.Core
{
    public sealed class Board
    {
        public const int Width = 10;
        public static bool InBounds(int x, int y) { return x >= 0 && x <= 9 && y >= 0 && y <= 21; }
    }
    public sealed class GameState
    {
        private readonly System.Collections.Generic.List<int> _queue = new System.Collections.Generic.List<int>();
        public GameState() { }
        public GameState(int tickCount) { TickCount = tickCount; }
        public int TickCount { get; private set; }
        public System.Collections.Generic.IReadOnlyList<int> NextQueue { get { return _queue; } }
        public bool IsOccupied(int x, int y) { return false; }
    }
}
"""
# 問い合わせの中で、公開している列をその場で書き換える（CQS 違反）
MUTATING = PURE.replace("public bool IsOccupied(int x, int y) { return false; }",
                        "public bool IsOccupied(int x, int y) { _queue.Add(1); return false; }")
# 呼ぶたびに戻り値が変わる（冪等でない）。公開状態は変えない
FLAKY = PURE.replace("public static bool InBounds(int x, int y) { return x >= 0 && x <= 9 && y >= 0 && y <= 21; }",
                     "private static int _calls;\n"
                     "        public static bool InBounds(int x, int y) { _calls++; return _calls % 2 == 1 && x >= 0 && x <= 9 && y >= 0 && y <= 21; }")


def mutated(fn):
    u = copy.deepcopy(QUNIT)
    fn(u)
    return u


class Schema(unittest.TestCase):
    def test_query_unit_passes(self):
        self.assertEqual(unit_schema.validate(QUNIT, SPEC, GDD), [])

    def assertRejected(self, unit, fragment):
        problems = unit_schema.validate(unit, SPEC, GDD)
        self.assertTrue(any(fragment in p for p in problems), f"{fragment!r} が問題に無い: {problems}")

    def test_query_needs_a_return_value(self):
        self.assertRejected(mutated(lambda u: u["interface"]["types"][0]["members"][1].update(type="void")),
                            "戻り値のあるメソッドだけ")

    def test_query_expects_only_return(self):
        self.assertRejected(mutated(lambda u: u["acceptance"]["cases"][2]["expect"].update(
            {"GameState.TickCount": {"same": True}})), "query の行の期待値は")

    def test_static_query_has_no_given(self):
        self.assertRejected(mutated(lambda u: u["acceptance"]["cases"][0].update(given={"GameState.TickCount": 1})),
                            "static な query の行は given を持てません")

    def test_return_must_be_bound_to_gdd(self):
        self.assertRejected(mutated(lambda u: u["acceptance"]["cases"][0]["expect"].update({"return": 42})),
                            "GDD から引けないリテラル")


class Generated(unittest.TestCase):
    def text(self):
        return testgen.generate(QUNIT, SPEC, GDD, BODY)["IssueQCases.cs"]

    def test_every_query_checks_idempotency_and_every_public_state(self):
        text = self.text()
        self.assertEqual(text.count("IDEMPOTENT: "), 3)
        self.assertIn("CQS: GameState.IsOccupied が GameState.TickCount を変えた", text)
        self.assertIn("CQS: GameState.IsOccupied が GameState.NextQueue を変えた", text)
        self.assertIn("System.Linq.Enumerable.ToArray(sut.NextQueue)", text)

    def test_generated_queries_compile(self):
        files = testgen.generate(QUNIT, SPEC, GDD, BODY)
        files["Stub.cs"] = testgen.interface_stub(QUNIT, SPEC, GDD)
        rc, log = build(files)
        self.assertEqual(rc, 0, log)


class EndToEnd(unittest.TestCase):
    """生成したテストを、合成の実装に対して dotnet test まで回す。"""

    def run_against(self, core_text):
        with tempfile.TemporaryDirectory() as d:
            core = Path(d) / "sandbox" / "Game" / "Assets" / "Core"
            core.mkdir(parents=True)
            (core / ("Board." + "cs")).write_text(core_text, encoding="utf-8")
            files = testgen.generate(QUNIT, SPEC, GDD, BODY)
            csproj = invrun.materialize(Path(d) / "out" / "q", Path(d) / "sandbox", "Game/Assets/Core", files)
            rc, out, err = invrun.run_tests(csproj, seed=1, ttl=600)
            return rc, out + err

    def test_pure_queries_pass(self):
        rc, log = self.run_against(PURE)
        self.assertEqual(rc, 0, log[-2000:])

    def test_state_changing_query_is_caught(self):
        rc, log = self.run_against(MUTATING)
        self.assertNotEqual(rc, 0)
        self.assertIn("CQS: ", log)

    def test_non_idempotent_query_is_caught(self):
        rc, log = self.run_against(FLAKY)
        self.assertNotEqual(rc, 0)
        self.assertIn("IDEMPOTENT: ", log)


if __name__ == "__main__":
    unittest.main()
