"""単位定義のスキーマ門（harness/unit_schema.py、docs/design/mechanical_barriers.md §3）。

    python -m unittest discover -s tests -v

**なぜ要るか**: 分解役に「手計算の期待値や C# を書くな」とプロンプトで頼んでも、LLM は混ぜる。
形として書けないことを門で固定する。ここでは「通すべきもの 1 つ」と「規則ごとに拒絶すべきもの 1 つずつ」
だけを置く（変異を網羅しない。過剰テストに戻らない）。
"""
import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import unit_schema  # noqa: E402

GDD = "<!-- project: demo -->\n<!-- version: 1 -->\n# GDD\n盤面は幅 10。出現位置は (3, 19)。\n"


def spec_for(gdd_text):
    sha = hashlib.sha256(gdd_text.encode("utf-8")).hexdigest()
    return "\n".join([
        "<!-- project: demo -->", "<!-- gdd-version: 1 -->", f"<!-- gdd-sha256: {sha} -->",
        "# demo 構造化仕様", "",
        "## 1. ループと終了条件", "| ID | 内容 | 根拠 |", "|:--|:--|:--|", "| LP-01 | ループ | L4 |", "",
        "## 2. 状態遷移", "| ID | 状態 | 契機 | 遷移先 | 根拠 |", "|:--|:--|:--|:--|:--|",
        "| ST-06 | Ready | シードの注入 | Ready | L4 |", "",
        "## 3. 規則と計算式", "| ID | 規則・式 | 境界値 | 参照パラメーター | 根拠 |", "|:--|:--|:--|:--|:--|",
        "| RL-07 | シード 0 は 1 に置き換える | 0 → 1 | - | L4 |", "",
        "## 4. 公開インターフェース", "| ID | 公開する状態・操作 | 型・範囲 | 根拠 |", "|:--|:--|:--|:--|",
        "| IF-08 | 経過ティック数 | 整数 | L4 |", "",
        "## 5. 外部パラメーター", "| ID | 名前 | 値 | 区分 | 根拠 |", "|:--|:--|:--|:--|:--|",
        "| PR-06 | 盤面幅 | 10 | 確定 | L4 |", "| PR-09 | 出現基準位置 | (3, 19) | 確定 | L4 |", "",
        "## 6. 人間確認・演出", "| ID | 内容 | 根拠 |", "|:--|:--|:--|", "| （該当なし） | - | - |", "",
    ])


SPEC = spec_for(GDD)

# Issue 12 を v2 で書いた形（抜粋）
VALID = {
    "schema": 2,
    "id": "issue_12",
    "title": "Ready 初期状態",
    "prompt": "`GameState` を公開コンストラクタで作ると Ready になる。`Board.Width` は盤面幅。"
              "書いてよいのは `Game/Assets/Core/GameState.cs` だけ。根拠は `RL-07`。",
    "interface": {"types": [
        {"name": "GamePhase", "kind": "enum", "values": ["Ready", "Playing", "GameOver"]},
        {"name": "Board", "kind": "class", "members": [
            {"name": "Width", "kind": "const", "type": "int", "value": {"param": "PR-06"}},
        ]},
        {"name": "GameState", "kind": "class", "members": [
            {"name": "GameState", "kind": "ctor"},
            {"name": "Phase", "kind": "property", "type": "GamePhase"},
            {"name": "TickCount", "kind": "property", "type": "int"},
            {"name": "SpawnX", "kind": "property", "type": "int"},
            {"name": "NextQueue", "kind": "property", "type": "System.Collections.Generic.IReadOnlyList<GamePhase>"},
            {"name": "InjectSeed", "kind": "method", "type": "void", "params": [{"name": "seed", "type": "uint"}]},
            {"name": "Advance", "kind": "method", "type": "void"},
        ]},
    ]},
    "whitelist": ["Game/Assets/Core/GameState.cs"],
    "impl_files": ["Game/Assets/Core/GameState.cs"],
    "acceptance": {
        "required_tests": ["InitialStateTests"],
        "cases": [
            {"id": "ready-initial", "rule": "ST-06", "given": {}, "op": {"construct": "GameState"},
             "expect": {"GameState.Phase": "Ready", "GameState.TickCount": 0, "GameState.NextQueue": []}},
            {"id": "seed-zero", "rule": "RL-07", "given": {"GameState.Phase": "Ready"},
             "op": {"call": "GameState.InjectSeed", "args": {"seed": 0}},
             "expect": {"GameState.Phase": {"same": True}}},
            {"id": "tick", "rule": "IF-08", "given": {"GameState.TickCount": 41},
             "op": {"call": "GameState.Advance"},
             "expect": {"GameState.TickCount": {"given": "GameState.TickCount", "add": 1},
                        "GameState.SpawnX": {"param": "PR-09", "index": 0}}},
        ],
    },
    "human_check_point": "不要",
    "playtest": "none",
}


def check(unit, spec=SPEC, gdd=GDD):
    return unit_schema.validate(unit, spec, gdd)


def mutated(fn):
    u = copy.deepcopy(VALID)
    fn(u)
    return u


class ValidUnit(unittest.TestCase):
    def test_passes(self):
        self.assertEqual(check(VALID), [])


class Rejects(unittest.TestCase):
    """規則ごとに 1 つ。どの規則で落ちたかを文言の一部で確かめる。"""

    def assertRejected(self, unit, fragment, spec=SPEC):
        problems = check(unit, spec)
        self.assertTrue(any(fragment in p for p in problems), f"{fragment!r} が問題に無い: {problems}")

    def test_unknown_top_key(self):
        self.assertRejected(mutated(lambda u: u.update(tests="[Test] public void X() {}")), "知らないキー")

    def test_csharp_in_type(self):
        self.assertRejected(mutated(lambda u: u["interface"]["types"][2]["members"][2].update(
            type="int TickCount { get; } = 0")), "type は型名だけ")

    def test_code_in_prompt(self):
        self.assertRejected(mutated(lambda u: u.update(prompt="Score は `Score = a + b;` で求める")), "';'")

    def test_unknown_backtick_token(self):
        self.assertRejected(mutated(lambda u: u.update(prompt="`x ^= x << 13` を使う")), "interface の識別子")

    def test_hand_computed_literal(self):
        self.assertRejected(mutated(lambda u: u["acceptance"]["cases"][0]["expect"].update(
            {"GameState.TickCount": 42})), "GDD から引けないリテラル")

    def test_const_literal_not_from_gdd(self):
        self.assertRejected(mutated(lambda u: u["interface"]["types"][1]["members"][0].update(value=10)),
                            "GDD から引けないリテラル")

    def test_missing_param(self):
        self.assertRejected(mutated(lambda u: u["acceptance"]["cases"][2]["expect"].update(
            {"GameState.SpawnX": {"param": "PR-99"}})), "PR-99 がありません")

    def test_delta_more_than_one(self):
        self.assertRejected(mutated(lambda u: u["acceptance"]["cases"][2]["expect"].update(
            {"GameState.TickCount": {"given": "GameState.TickCount", "add": 2}})), "±1 だけ")

    def test_rule_not_in_spec(self):
        self.assertRejected(mutated(lambda u: u["acceptance"]["cases"][0].update(rule="RL-99")), "構造化仕様に存在する ID")

    def test_multi_step_op(self):
        self.assertRejected(mutated(lambda u: u["acceptance"]["cases"][2].update(
            op=[{"call": "GameState.Advance"}, {"call": "GameState.Advance"}])), "操作は 1 つだけ")

    def test_undeclared_state(self):
        self.assertRejected(mutated(lambda u: u["acceptance"]["cases"][0]["expect"].update(
            {"GameState.Score": 0})), "宣言した状態")

    def test_stale_spec(self):
        self.assertRejected(VALID, "gdd-sha256", spec=spec_for(GDD + "変更\n"))


TICK = {"id": "fall", "rule": "IF-08", "given": {"GameState.TickCount": 41},
        "op": {"call": "GameState.Advance", "repeat": {"param": "PR-06"}},
        "expect": {"GameState.TickCount": {"given": "GameState.TickCount", "add": 1, "at_step": {"param": "PR-06"}}}}


def with_tick(fn=None):
    u = copy.deepcopy(VALID)
    u["acceptance"]["cases"].append(copy.deepcopy(TICK))
    if fn:
        fn(u["acceptance"]["cases"][-1])
    return u


class TickPredicates(unittest.TestCase):
    """ティック進行の述語（ADR-003 §3.9）。回数は GDD から引き、上限を超えられない。"""

    def assertRejected(self, unit, fragment, spec=SPEC):
        problems = check(unit, spec)
        self.assertTrue(any(fragment in p for p in problems), f"{fragment!r} が問題に無い: {problems}")

    def test_repeat_with_at_step_passes(self):
        self.assertEqual(check(with_tick()), [])

    def test_repeat_minus_one_passes(self):
        def minus_one(c):
            c["op"]["repeat"]["add"] = -1
            c["expect"]["GameState.TickCount"]["at_step"]["add"] = -1
        self.assertEqual(check(with_tick(minus_one)), [])

    def test_repeat_count_must_come_from_gdd(self):
        self.assertRejected(with_tick(lambda c: c["op"].update(repeat=60)), "回数は")

    def test_repeat_only_on_call(self):
        self.assertRejected(mutated(lambda u: u["acceptance"]["cases"][0]["op"].update(repeat={"param": "PR-06"})),
                            "repeat は call")

    def test_at_step_needs_repeat(self):
        self.assertRejected(with_tick(lambda c: c["op"].pop("repeat")), "repeat のある行にだけ")

    def test_at_step_cannot_exceed_repeat(self):
        self.assertRejected(with_tick(lambda c: c["op"]["repeat"].update(add=-1)), "超えています")

    def test_horizon_is_capped(self):
        big = SPEC.replace("| PR-06 | 盤面幅 | 10 |", "| PR-06 | 盤面幅 | 5000 |")
        self.assertRejected(with_tick(), "horizon の上限", spec=big)


TIMED = {"timed_constraints": {"exempt_units": ["issue_12"], "allowed_calls": ["GameState.Advance"],
                               "whitelist_must_exist": True}}


class TimedConstraints(unittest.TestCase):
    """時限制約（ADR-003 §3.3・§3.6）。免除した単位以外は、操作と whitelist が限られる。"""

    def test_exempt_unit_is_not_restricted(self):
        self.assertEqual(unit_schema.validate(VALID, SPEC, GDD, TIMED), [])
        self.assertEqual(unit_schema.whitelist_problems(VALID, lambda p: False, TIMED), [])

    def test_other_ops_are_rejected_for_new_units(self):
        u = mutated(lambda u: u.update(id="issue_99"))
        problems = unit_schema.validate(u, SPEC, GDD, TIMED)
        # construct と InjectSeed の 2 行が落ち、Advance の行は通る
        self.assertEqual(sum("時限制約" in p for p in problems), 2, problems)

    def test_new_file_in_whitelist_is_rejected(self):
        u = mutated(lambda u: u.update(id="issue_99"))
        self.assertEqual(len(unit_schema.whitelist_problems(u, lambda p: False, TIMED)), 1)
        self.assertEqual(unit_schema.whitelist_problems(u, lambda p: True, TIMED), [])

    def test_no_constraints_when_config_has_none(self):
        u = mutated(lambda u: u.update(id="issue_99"))
        self.assertEqual(unit_schema.validate(u, SPEC, GDD, {}), [])


def with_active_mino(case):
    """入れ子の状態（GameState.ActiveMino.Y）を持つ interface に、case の行を足したもの。"""
    u = copy.deepcopy(VALID)
    u["interface"]["types"].insert(1, {"name": "ActiveMino", "kind": "struct", "members": [
        {"name": "X", "kind": "property", "type": "int"}, {"name": "Y", "kind": "property", "type": "int"}]})
    u["interface"]["types"][-1]["members"].append({"name": "ActiveMino", "kind": "property", "type": "ActiveMino?"})
    u["acceptance"]["cases"].append(case)
    return u


FALL = {"id": "fall-one", "rule": "IF-08", "given": {},
        "op": {"call": "GameState.Advance", "repeat": {"param": "PR-06", "add": 1}},
        "expect": {"GameState.ActiveMino.Y": {"param": "PR-09", "index": 1, "add": -1},
                   "GameState.ActiveMino.X": {"param": "PR-09", "index": 0}}}


class NestedStateAndParamDelta(unittest.TestCase):
    """入れ子の状態の期待値と、仕様の値からの ±1（falling-blocks のスモーク、2026-09-23 オーナー裁定）。"""

    def problems(self, fn=None):
        case = copy.deepcopy(FALL)
        if fn:
            fn(case)
        return check(with_active_mino(case))

    def test_nested_expect_and_param_delta_pass(self):
        self.assertEqual(self.problems(), [])

    def test_nested_path_must_exist(self):
        p = self.problems(lambda c: c["expect"].update({"GameState.ActiveMino.Z": 0}))
        self.assertTrue(any("宣言した状態" in x for x in p), p)

    def test_nested_path_not_allowed_in_given(self):
        p = self.problems(lambda c: c.update(given={"GameState.ActiveMino.Y": 5}))
        self.assertTrue(any("宣言した状態" in x for x in p), p)

    def test_param_delta_is_one_at_most(self):
        p = self.problems(lambda c: c["expect"]["GameState.ActiveMino.Y"].update(add=2))
        self.assertTrue(any("±1 だけ" in x for x in p), p)


class Legacy(unittest.TestCase):
    CFG = {"legacy_v1_sha256": {}, "spec_path": "s", "gdd_path": "g"}

    def reader(self, path):
        return SPEC if path == "s" else GDD

    def test_frozen_v1_passes_only_byte_identical(self):
        body = json.dumps({"id": "issue_12", "prompt": "public sealed class GameState"}).encode("utf-8")
        cfg = dict(self.CFG, legacy_v1_sha256={hashlib.sha256(body).hexdigest(): "canary"})
        self.assertEqual(unit_schema.check(body, self.reader, cfg), ("v1-legacy", []))
        kind, problems = unit_schema.check(body + b" ", self.reader, cfg)
        self.assertEqual(kind, "v1")
        self.assertTrue(problems)

    def test_v2_goes_through_validate(self):
        body = json.dumps(VALID).encode("utf-8")
        self.assertEqual(unit_schema.check(body, self.reader, self.CFG), ("v2", []))


if __name__ == "__main__":
    unittest.main()
