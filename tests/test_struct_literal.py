"""構造体のリテラル（given・op.args）と入れ子の基準（B4-E2、docs/design/b4_ab_experiment.md §7）。

    python -m unittest discover -s tests -v

**なぜ要るか**: 左右移動（A/B 実験の T1）の受入データは、壁際にいるミノ（構造体）を given で作り、
「入力を載せた 1 回のティック」で動かす必要がある。1 行 1 操作（horizon = 1）を保ったまま書けること、
書けてはいけない形（入れ子の入れ子・合わないコンストラクタ・宣言の無い型）を拒むこと、生成した C# が
ビルドできることを確かめる。
"""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "tests"))

import testgen  # noqa: E402
import unit_schema  # noqa: E402
from test_testgen import build  # noqa: E402
from test_unit_schema import GDD, SPEC  # noqa: E402

UNIT = {
    "schema": 2, "id": "issue_t1", "title": "左右移動",
    "prompt": "`GameState.Tick` に入力を載せて 1 ティック進める。",
    "task_kind": "feature",
    "interface": {"types": [
        {"name": "TickInput", "kind": "struct", "members": [
            {"name": "TickInput", "kind": "ctor", "params": [{"name": "left", "type": "bool"},
                                                            {"name": "right", "type": "bool"}]},
            {"name": "Left", "kind": "property", "type": "bool"},
            {"name": "Right", "kind": "property", "type": "bool"}]},
        {"name": "ActiveMino", "kind": "struct", "members": [
            {"name": "ActiveMino", "kind": "ctor", "params": [{"name": "x", "type": "int"},
                                                             {"name": "y", "type": "int"}]},
            {"name": "X", "kind": "property", "type": "int"},
            {"name": "Y", "kind": "property", "type": "int"}]},
        {"name": "GameState", "kind": "class", "members": [
            {"name": "GameState", "kind": "ctor"},
            {"name": "GameState", "kind": "ctor", "params": [{"name": "activeMino", "type": "ActiveMino?"}]},
            {"name": "ActiveMino", "kind": "property", "type": "ActiveMino?"},
            {"name": "Tick", "kind": "method", "type": "void", "params": [{"name": "input", "type": "TickInput"}]}]},
    ]},
    "whitelist": ["Game/Assets/Core/GameState.cs"], "impl_files": ["Game/Assets/Core/GameState.cs"],
    "acceptance": {"cases": [
        {"id": "left-wall", "rule": "RL-07", "given": {"GameState.ActiveMino": {"x": 0, "y": 10}},
         "op": {"call": "GameState.Tick", "args": {"input": {"left": True, "right": False}}},
         "expect": {"GameState.ActiveMino.X": {"same": True}}},
        {"id": "left-open", "rule": "RL-07", "given": {"GameState.ActiveMino": {"x": 5, "y": 10}},
         "op": {"call": "GameState.Tick", "args": {"input": {"left": True, "right": False}}},
         "expect": {"GameState.ActiveMino.X": {"given": "GameState.ActiveMino.X", "add": -1},
                    "GameState.ActiveMino.Y": {"same": True}}},
    ]},
    "human_check_point": "不要", "playtest": "none",
}


def check(u):
    return unit_schema.validate(u, SPEC, GDD, {})


def mutated(fn):
    u = copy.deepcopy(UNIT)
    fn(u)
    return u


class Schema(unittest.TestCase):
    def assertRejected(self, u, fragment):
        p = check(u)
        self.assertTrue(any(fragment in x for x in p), f"{fragment!r} が問題に無い: {p}")

    def test_struct_literals_and_nested_base_pass(self):
        self.assertEqual(check(UNIT), [])

    def test_ctor_must_match_keys(self):
        self.assertRejected(mutated(lambda u: u["acceptance"]["cases"][0]["given"].update(
            {"GameState.ActiveMino": {"x": 0}})), "コンストラクタ")

    def test_no_literal_for_undeclared_types(self):
        # TickInput の宣言を消すと、op.args の構造体のリテラルを作れない
        self.assertRejected(mutated(lambda u: u["interface"]["types"].pop(0)), "interface で宣言した")

    def test_nesting_is_one_level(self):
        self.assertRejected(mutated(lambda u: u["acceptance"]["cases"][0]["given"].update(
            {"GameState.ActiveMino": {"x": {"a": 1}, "y": 10}})), "1 段まで")

    def test_nested_base_must_be_in_the_given_literal(self):
        self.assertRejected(mutated(lambda u: u["acceptance"]["cases"][1].update(given={})), "given にありません")

    def test_expectation_is_still_gdd_bound(self):
        # given は自由なリテラルだが、期待値に独自の数値は書けない
        self.assertRejected(mutated(lambda u: u["acceptance"]["cases"][1]["expect"].update(
            {"GameState.ActiveMino.X": 4})), "GDD から引けないリテラル")


class Generation(unittest.TestCase):
    def generated(self):
        return testgen.generate(UNIT, SPEC, GDD, json.dumps(UNIT).encode("utf-8"))

    def test_literals_become_constructor_calls(self):
        text = "".join(self.generated().values())
        self.assertIn("var sut = new GameState(new ActiveMino(0, 10));", text)
        self.assertIn("sut.Tick(new TickInput(true, false));", text)
        self.assertIn("var before0 = sut.ActiveMino?.X;", text)
        self.assertIn('Assert.That(sut.ActiveMino?.X, Is.EqualTo(before0 - 1), "GameState.ActiveMino.X");', text)

    def test_generated_tests_compile(self):
        files = dict(self.generated())
        files["Stub.cs"] = testgen.interface_stub(UNIT, SPEC, GDD)
        rc, log = build(files)
        self.assertEqual(rc, 0, log)


if __name__ == "__main__":
    unittest.main()
