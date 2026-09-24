"""propgen（V2-2）：性質の宣言から、性質テスト・参照モデル・1 行の反例を作る。

    python -m unittest tests.test_propgen

**なぜ要るか**: v2 は例示テストを捨て、性質テストと参照モデルに一元化する（docs/design/v2_contract_foundry.md §4）。
性質が弱いと「何もしない実装」が通るので、活性の性質の義務と、前提が一度も成り立たない性質の失敗を課す。
参照データ（形・キック表）を宣言に書き写すと写し間違いが入るので、構造化仕様 §5 から名前で引くことを確かめる。
C# の実行（本物のコンパイラと NUnit）は dotnet を使わないここでは確かめない。PR 本文を見ること。
"""
import copy
import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import propgen as g  # noqa: E402

GDD = "<!-- project: demo -->\n<!-- version: 1 -->\n# GDD\n盤面は幅 10、内部行数 22。\n"

# ミノは I と O の 2 種に絞った仕様（値は falling-blocks の GDD v10 と同じ書式）
PARAMS = [
    ("PR-06", "盤面幅", "10"), ("PR-08", "内部行数", "22"),
    ("PR-17", "形状 I 回転原点", "(1.5, 1.5)"),
    ("PR-18", "形状 I 向き 0", "(0, 2) (1, 2) (2, 2) (3, 2)"),
    ("PR-19", "形状 I 向き R", "(2, 3) (2, 2) (2, 1) (2, 0)"),
    ("PR-20", "形状 I 向き 2", "(0, 1) (1, 1) (2, 1) (3, 1)"),
    ("PR-21", "形状 I 向き L", "(1, 3) (1, 2) (1, 1) (1, 0)"),
    ("PR-22", "形状 O 回転原点", "(1.5, 1.5)"),
    ("PR-23", "形状 O 向き 0", "(1, 1) (2, 1) (1, 2) (2, 2)"),
    ("PR-24", "形状 O 向き R", "(1, 1) (2, 1) (1, 2) (2, 2)"),
    ("PR-25", "形状 O 向き 2", "(1, 1) (2, 1) (1, 2) (2, 2)"),
    ("PR-26", "形状 O 向き L", "(1, 1) (2, 1) (1, 2) (2, 2)"),
    ("PR-60", "回転補正候補 I 0 → R", "(0, 0) (-2, 0) (1, 0) (-2, -1) (1, 2)"),
    ("PR-61", "回転補正候補 I R → 0", "(0, 0) (2, 0) (-1, 0) (2, 1) (-1, -2)"),
    ("PR-62", "回転補正候補 I R → 2", "(0, 0) (-1, 0) (2, 0) (-1, 2) (2, -1)"),
    ("PR-63", "回転補正候補 I 2 → R", "(0, 0) (1, 0) (-2, 0) (1, -2) (-2, 1)"),
    ("PR-64", "回転補正候補 I 2 → L", "(0, 0) (2, 0) (-1, 0) (2, 1) (-1, -2)"),
    ("PR-65", "回転補正候補 I L → 2", "(0, 0) (-2, 0) (1, 0) (-2, -1) (1, 2)"),
    ("PR-66", "回転補正候補 I L → 0", "(0, 0) (1, 0) (-2, 0) (1, -2) (-2, 1)"),
    ("PR-67", "回転補正候補 I 0 → L", "(0, 0) (-1, 0) (2, 0) (-1, 2) (2, -1)"),
    ("PR-68", "回転補正候補 O 全向き遷移", "(0, 0)"),
]
RULES = ["RL-16", "RL-40", "RL-50"]


def spec_for(gdd_text, params=PARAMS):
    sha = hashlib.sha256(gdd_text.encode("utf-8")).hexdigest()
    return "\n".join([
        "<!-- project: demo -->", "<!-- gdd-version: 1 -->", f"<!-- gdd-sha256: {sha} -->",
        "# demo 構造化仕様", "",
        "## 1. ループと終了条件", "| ID | 内容 | 根拠 |", "|:--|:--|:--|", "| LP-01 | ループ | L4 |", "",
        "## 2. 状態遷移", "| ID | 状態 | 契機 | 遷移先 | 根拠 |", "|:--|:--|:--|:--|:--|",
        "| ST-06 | Ready | シードの注入 | Ready | L4 |", "",
        "## 3. 規則と計算式", "| ID | 規則・式 | 境界値 | 参照パラメーター | 根拠 |", "|:--|:--|:--|:--|:--|"]
        + [f"| {r} | 規則 | - | - | L4 |" for r in RULES] + ["",
        "## 4. 公開インターフェース", "| ID | 公開する状態・操作 | 型・範囲 | 根拠 |", "|:--|:--|:--|:--|",
        "| IF-08 | 経過ティック数 | 整数 | L4 |", "",
        "## 5. 外部パラメーター", "| ID | 名前 | 値 | 区分 | 根拠 |", "|:--|:--|:--|:--|:--|"]
        + [f"| {i} | {n} | {v} | 確定 | L4 |" for i, n, v in params] + ["",
        "## 6. 人間確認・演出", "| ID | 内容 | 根拠 |", "|:--|:--|:--|", "| （該当なし） | - | - |", ""])


SPEC = spec_for(GDD)


def ctor(name, *params):
    return {"name": name, "kind": "ctor", "params": [{"name": n, "type": t} for n, t in params]}


def prop(name, typ):
    return {"name": name, "kind": "property", "type": typ}


INTERFACE = {"types": [
    {"name": "GamePhase", "kind": "enum", "values": ["Ready", "Playing", "GameOver"]},
    {"name": "MinoType", "kind": "enum", "values": ["I", "O"]},
    {"name": "Rotation", "kind": "enum", "values": ["Spawn", "Right", "Two", "Left"]},
    {"name": "ActiveMino", "kind": "struct", "members": [
        ctor("ActiveMino", ("type", "MinoType"), ("x", "int"), ("y", "int"), ("rotation", "Rotation")),
        prop("Type", "MinoType"), prop("X", "int"), prop("Y", "int"), prop("Rotation", "Rotation")]},
    {"name": "TickInput", "kind": "struct", "members": [
        ctor("TickInput", *[(n, "bool") for n in ("left", "right", "rotateCw", "rotateCcw", "softDrop", "hardDrop")]),
        *[prop(n, "bool") for n in ("Left", "Right", "RotateCw", "RotateCcw", "SoftDrop", "HardDrop")]]},
    {"name": "Cell", "kind": "struct", "members": [ctor("Cell", ("x", "int"), ("y", "int")),
                                                   prop("X", "int"), prop("Y", "int")]},
    {"name": "GameState", "kind": "class", "members": [
        ctor("GameState", ("phase", "GamePhase"), ("activeMino", "ActiveMino?"),
             ("locked", "System.Collections.Generic.IReadOnlyList<Cell>")),
        prop("Phase", "GamePhase"), prop("ActiveMino", "ActiveMino?"),
        prop("NextQueue", "System.Collections.Generic.IReadOnlyList<MinoType>"),
        prop("LockedMinoCount", "int"),
        {"name": "IsOccupied", "kind": "method", "type": "bool",
         "params": [{"name": "x", "type": "int"}, {"name": "y", "type": "int"}]},
        {"name": "Tick", "kind": "method", "type": "void", "params": [{"name": "input", "type": "TickInput"}]}]},
]}

LEFT = "input.Left && !input.Right && before.ActiveMino != null && fits(moved(before.ActiveMino, -1, 0))"
DECL = {
    "schema": 1,
    "gdd_sha256": hashlib.sha256(GDD.encode("utf-8")).hexdigest(),
    "reference": {"board_width": "PR-06", "board_height": "PR-08", "shapes": "PR-17..PR-26", "kicks": "PR-60..PR-68"},
    "start": {"phase": "Playing", "locked_max": 12},
    "steps": 50,
    "seeds": {"public": 5, "hidden": 20},
    "properties": [
        {"id": "P-T1-01", "task": "T1", "rule": "RL-16", "given": LEFT,
         "then": "after.ActiveMino.X == before.ActiveMino.X - 1"},
        {"id": "P-T1-02", "task": "T1", "rule": "RL-40", "given": "after.ActiveMino != null",
         "then": "in_board(after.ActiveMino)"},
        {"id": "P-T5-01", "task": "T5", "rule": "RL-50",
         "given": "input.RotateCw && !input.RotateCcw && before.ActiveMino != null",
         "then": "after.ActiveMino == kick(before.ActiveMino, 1)"},
    ],
}


def mutated(fn):
    d = copy.deepcopy(DECL)
    fn(d)
    return d


class Validate(unittest.TestCase):
    def assertRejected(self, decl, needle, spec=SPEC, interface=INTERFACE):
        problems = g.validate(decl, spec, GDD, interface)
        self.assertTrue(any(needle in p for p in problems), problems)

    def test_valid_declaration(self):
        self.assertEqual(g.validate(DECL, SPEC, GDD, INTERFACE), [])

    def test_gdd_change_refuses_generation(self):
        self.assertRejected(mutated(lambda d: d.update(gdd_sha256="0" * 64)), "gdd_sha256")

    def test_every_task_needs_a_live_property(self):
        """「== で結果を一意に決める性質」が無いタスクは、何もしない実装を通しうる（§4.2 の 1）。"""
        def only_bool_then(d):
            d["properties"][2]["then"] = "in_board(after.ActiveMino)"
        self.assertRejected(mutated(only_bool_then), "T5: then が == の性質")

    def test_expression_type_errors(self):
        cases = [
            ("after.ActiveMino.X == true", "比べられません"),
            ("after.Nope == 1", "after に Nope はありません"),
            ("fits(1)", "fits の引数に int は渡せません"),
            ("teleport(after.ActiveMino)", "関数 teleport はありません"),
            ("after.ActiveMino.X + 1", "bool にしてください"),
            ("after.ActiveMino.X == before.ActiveMino.X - ", "式が途中で終わって"),
            ("after.ActiveMino.X @ 1", "読めない字"),
        ]
        for then, needle in cases:
            with self.subTest(then=then):
                self.assertRejected(mutated(lambda d: d["properties"][0].update(then=then)), needle)

    def test_rule_must_exist_in_spec_and_ids_are_unique(self):
        self.assertRejected(mutated(lambda d: d["properties"][0].update(rule="RL-99")), "rule は構造化仕様にある")
        self.assertRejected(mutated(lambda d: d["properties"][1].update(id="P-T1-01")), "重複しない")

    def test_reference_is_read_by_name_and_must_be_complete(self):
        without = [p for p in PARAMS if p[0] != "PR-63"]
        self.assertRejected(DECL, "PR-63 が構造化仕様にありません", spec=spec_for(GDD, without))
        # I の 2 → R を、R → 2 の重複にすり替える（その遷移の候補が欠ける）
        dup = [(i, "回転補正候補 I R → 2" if i == "PR-63" else n, v) for i, n, v in PARAMS]
        self.assertRejected(DECL, "候補がありません", spec=spec_for(GDD, dup))
        renamed = [(i, "形状 I 向き 3" if i == "PR-19" else n, v) for i, n, v in PARAMS]
        self.assertRejected(DECL, "名前を読めません", spec=spec_for(GDD, renamed))

    def test_contract_must_have_the_observation_api(self):
        iface = copy.deepcopy(INTERFACE)
        gs = iface["types"][-1]
        gs["members"] = [m for m in gs["members"] if m["name"] != "IsOccupied"]
        self.assertRejected(DECL, "IsOccupied", interface=iface)

    def test_seeds_and_start(self):
        self.assertRejected(mutated(lambda d: d.update(seeds={"public": 0, "hidden": 20})), "seeds は")
        self.assertRejected(mutated(lambda d: d["start"].update(phase="Paused")), "start.phase")


class Reference(unittest.TestCase):
    def test_shapes_and_kicks_follow_the_spec(self):
        ref = g.reference(DECL["reference"], g.param_rows(SPEC), g.Contract(INTERFACE))
        self.assertEqual((ref["width"], ref["height"]), (10, 22))
        self.assertEqual(ref["shapes"]["I"][1], [(2, 3), (2, 2), (2, 1), (2, 0)])
        self.assertEqual(ref["kicks"]["I"][(0, 1)][1], (-2, 0), "I の 0 → R の 2 番目の候補")
        self.assertEqual(ref["kicks"]["I"][(0, 3)][1], (-1, 0), "I の 0 → L")
        self.assertEqual(ref["kicks"]["O"][(2, 1)], [(0, 0)], "O は全向き遷移で (0, 0) だけ")


class Generate(unittest.TestCase):
    def setUp(self):
        self.files = g.generate(DECL, SPEC, GDD, INTERFACE, "tests")

    def test_files(self):
        self.assertEqual(sorted(self.files), ["tests/Properties/Checks.cs", "tests/Properties/PropertiesT1Cases.cs",
                                              "tests/Properties/PropertiesT5Cases.cs", "tests/Properties/PropertyModel.cs"])
        self.assertEqual(self.files, g.generate(DECL, SPEC, GDD, INTERFACE, "tests"), "決定論")

    def test_public_and_hidden_tests_per_property(self):
        t1 = self.files["tests/Properties/PropertiesT1Cases.cs"]
        self.assertIn("public void P_T1_01_Public() =>", t1)
        self.assertIn("public void P_T1_01_Hidden() =>", t1)
        self.assertIn("PropertyRunner.Public(5)", t1)
        self.assertIn("PropertyRunner.Hidden()", t1)
        self.assertNotIn("P_T5_01", t1, "タスクごとにクラスを分ける")

    def test_hidden_seeds_are_not_written_into_the_generated_code(self):
        model = self.files["tests/Properties/PropertyModel.cs"]
        self.assertIn(f"GetEnvironmentVariable(\"{g.HIDDEN_ENV}\")", model)
        self.assertIn("HIDDEN_SEEDS_ABSENT", model)

    def test_reference_tables_come_from_the_spec(self):
        model = self.files["tests/Properties/PropertyModel.cs"]
        self.assertIn("public const int Width = 10;", model)
        self.assertIn("public const int Height = 22;", model)
        self.assertIn("new[] { 2, 3 }, new[] { 2, 2 }, new[] { 2, 1 }, new[] { 2, 0 }", model)

    def test_counterexample_is_one_line_with_the_shortest_prefix(self):
        model = self.files["tests/Properties/PropertyModel.cs"]
        self.assertIn('"PROPERTY_FAIL id=" + id + " rule=" + rule + " ops=["', model)
        self.assertIn('"] expected=" + expected + " actual=" + actual', model)
        self.assertIn('"PROPERTY_VACUOUS id=" + id + " rule=" + rule + " given=0"', model)
        # 最初に破れた手で切り、同じ開始状態から手を 1 つずつ抜いても破れるなら抜く（反例の縮小）
        self.assertIn("Shrink(id, rule, seed, ops.GetRange(0, at + 1), check, expected, actual);", model)
        self.assertIn("cand.RemoveAt(j);", model)
        checks = self.files["tests/Properties/Checks.cs"]
        self.assertIn('expected = "ActiveMino.X:" + PropertyRunner.Fmt(r);', checks)
        self.assertIn('actual = "ActiveMino.X:" + PropertyRunner.Fmt(l);', checks)

    def test_nullable_member_access_is_guarded(self):
        checks = self.files["tests/Properties/Checks.cs"]
        self.assertIn("PropertyModel.Req(a.ActiveMino).X", checks)
        self.assertIn("PropertyModel.Fits(b, PropertyModel.Moved(PropertyModel.Req(b.ActiveMino), (-1), 0))", checks)
        self.assertIn("r = PropertyModel.Kick(b, PropertyModel.Req(b.ActiveMino), 1);", checks)

    def test_invalid_declaration_is_refused(self):
        with self.assertRaises(g.PropertyError):
            g.generate(mutated(lambda d: d.update(steps=0)), SPEC, GDD, INTERFACE, "tests")


if __name__ == "__main__":
    unittest.main()
