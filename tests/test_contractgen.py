"""contractgen（V2-1）：単位定義の interface から、読み取り専用の契約（C#）を作る。

    python -m unittest tests.test_contractgen

**なぜ要るか**: v1 は、シグネチャを文字列で照合する静的な門が、whitelist の外のファイル（T1 で作った
TickInput.cs）を見られずに B の試行を 6 回とも落とした（game-harness#63）。v2 はデータ型を生成物にして
実装役の仕事から外し、振る舞いの型の形はコンパイラ（契約の探針）で縛る。ここでは、分類・生成の形・決定論・
探針が宣言どおりの型で呼んでいることを、dotnet を使わずに確かめる。本物のコンパイラでの確認は PR 本文。
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import contractgen as g  # noqa: E402

T5 = ROOT / "experiments" / "b4_ab" / "units" / "T5.json"


def struct(name, params, props=None, extra=()):
    members = [{"name": name, "kind": "ctor", "params": [{"name": n, "type": t} for n, t in params]}]
    members += [{"name": n, "kind": "property", "type": t}
                for n, t in (props if props is not None else [(n[:1].upper() + n[1:], t) for n, t in params])]
    return {"name": name, "kind": "struct", "members": members + list(extra)}


class Classify(unittest.TestCase):
    def test_enum_data_and_behavior(self):
        self.assertEqual(g.classify({"name": "E", "kind": "enum", "values": ["A"]}), "enum")
        self.assertEqual(g.classify(struct("P", [("x", "int"), ("y", "int")])), "data")
        self.assertEqual(g.classify({"name": "C", "kind": "class", "members": []}), "behavior")
        method = {"name": "Move", "kind": "method", "type": "void"}
        self.assertEqual(g.classify(struct("P", [("x", "int")], extra=[method])), "behavior")

    def test_data_struct_must_pair_ctor_params_with_properties(self):
        with self.assertRaises(g.ContractError):
            g.classify(struct("P", [("x", "int")], props=[("X", "int"), ("Y", "int")]))
        with self.assertRaises(g.ContractError):
            g.classify(struct("P", [("x", "int")], props=[("X", "long")]))

    def test_t5_interface_leaves_only_game_state_to_the_implementer(self):
        """v1 の T2・T3 を止めた TickInput は、実装役の仕事から外れる。"""
        self.assertEqual(g.implementer_owned(g.load_interface(T5)), ["GameState"])


class Generate(unittest.TestCase):
    def setUp(self):
        self.files = g.generate(g.load_interface(T5), "impl", "tests")

    def test_file_layout(self):
        self.assertEqual(sorted(self.files), [
            "impl/Contracts/ActiveMino.cs", "impl/Contracts/GamePhase.cs", "impl/Contracts/IGameState.cs",
            "impl/Contracts/MinoType.cs", "impl/Contracts/Rotation.cs", "impl/Contracts/TickInput.cs",
            "tests/Contracts/ContractProbe.cs"])
        for text in self.files.values():
            self.assertTrue(text.startswith(g.HEADER))

    def test_data_struct_assigns_every_property_and_has_value_equality(self):
        text = self.files["impl/Contracts/TickInput.cs"]
        self.assertIn("public TickInput(bool left, bool right, bool rotateCw, bool rotateCcw, bool softDrop, "
                      "bool hardDrop)", text)
        for prop in ("Left", "Right", "RotateCw", "RotateCcw", "SoftDrop", "HardDrop"):
            self.assertIn(f"{prop} = {prop[:1].lower() + prop[1:]};", text)
        self.assertIn("global::System.IEquatable<TickInput>", text)
        self.assertIn("public static bool operator ==(TickInput a, TickInput b)", text)

    def test_declared_type_names_are_fully_qualified(self):
        """ActiveMino.Rotation のように property の名前が型名と同じでも曖昧にしない。"""
        self.assertIn("public global::Game.Core.Rotation Rotation { get; }", self.files["impl/Contracts/ActiveMino.cs"])

    def test_interface_has_instance_members_but_no_ctor(self):
        text = self.files["impl/Contracts/IGameState.cs"]
        self.assertIn("public interface IGameState", text)
        self.assertIn("void Tick(global::Game.Core.TickInput input);", text)
        self.assertIn("global::System.Collections.Generic.IReadOnlyList<global::Game.Core.MinoType> NextQueue { get; }",
                      text)
        self.assertNotIn("GameState(", text)

    def test_probe_calls_ctor_and_members_with_declared_types(self):
        text = self.files["tests/Contracts/ContractProbe.cs"]
        self.assertIn("Is<global::Game.Core.IGameState>(x);", text)
        self.assertIn("new global::Game.Core.GameState(default(global::Game.Core.GamePhase), "
                      "default(global::Game.Core.ActiveMino?))", text)
        self.assertIn("Is<int>(x.BlockCount);", text)
        self.assertIn("x.InjectSeed(default(uint));", text)

    def test_static_and_const_members_are_bound_by_the_probe(self):
        t = {"name": "Board", "kind": "class", "members": [
            {"name": "Width", "kind": "const", "type": "int", "value": 10},
            {"name": "Create", "kind": "method", "type": "Board", "static": True}]}
        files = g.generate({"types": [t]}, "impl", "tests")
        self.assertNotIn("Width", files["impl/Contracts/IBoard.cs"])
        probe = files["tests/Contracts/ContractProbe.cs"]
        self.assertIn("Is<int>(global::Game.Core.Board.Width);", probe)
        self.assertIn("Is<global::Game.Core.Board>(global::Game.Core.Board.Create());", probe)

    def test_deterministic_and_locked(self):
        again = g.generate(g.load_interface(T5), "impl", "tests")
        self.assertEqual(self.files, again)
        lock = g.lock(self.files)
        self.assertEqual(sorted(lock), sorted(self.files))
        self.assertTrue(all(len(h) == 64 for h in lock.values()))

    def test_malformed_interface_is_refused(self):
        for bad in ({}, {"types": []}, {"types": [{"name": "1x", "kind": "class"}]},
                    {"types": [{"name": "A", "kind": "enum", "values": []}]},
                    {"types": [{"name": "A", "kind": "class"}, {"name": "A", "kind": "class"}]}):
            with self.subTest(bad=json.dumps(bad)), self.assertRaises(g.ContractError):
                g.generate(bad, "impl", "tests")


if __name__ == "__main__":
    unittest.main()
