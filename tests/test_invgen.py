"""不変条件テストの宣言の検査と生成（harness/invgen.py、docs/design/mechanical_barriers.md §4・§5 手順 5a）。

    python -m unittest discover -s tests -v

**なぜ要るか**: 不変条件は期待値を持たない性質テストで、実装役には見せない（§4.2）。
宣言に書けるものを門で絞り、生成物がコンパイルできることを dotnet build まで通して保証する。
テスト生成器（手順 3）と同じく、「通すべき 1 つ」と「規則ごとに拒絶すべき 1 つずつ」だけを置く。
"""
import copy
import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "tests"))

import invgen  # noqa: E402
import testgen  # noqa: E402
from test_testgen import build  # noqa: E402
from test_unit_schema import GDD, SPEC  # noqa: E402

DECL = {
    "schema": 1,
    "gdd_sha256": hashlib.sha256(GDD.encode("utf-8")).hexdigest(),
    "interface": {"types": [
        {"name": "GamePhase", "kind": "enum", "values": ["Ready", "Playing", "GameOver"]},
        {"name": "Board", "kind": "class", "members": [
            {"name": "Width", "kind": "const", "type": "int", "value": {"param": "PR-06"}},
        ]},
        {"name": "GameState", "kind": "class", "members": [
            {"name": "Phase", "kind": "property", "type": "GamePhase"},
            {"name": "LinesCleared", "kind": "property", "type": "int"},
            {"name": "BlockCount", "kind": "property", "type": "int"},
            {"name": "LockedCount", "kind": "property", "type": "int"},
            {"name": "Advance", "kind": "method", "type": "void"},
            {"name": "InjectSeed", "kind": "method", "type": "void", "params": [{"name": "seed", "type": "uint"}]},
        ]},
    ]},
    "subject": {"construct": "GameState"},
    "ops": [
        {"call": "GameState.Advance"},
        {"call": "GameState.InjectSeed", "args": {"seed": [0, 1, {"param": "PR-06"}]}},
    ],
    "steps": 50,
    "seeds": 4,
    "invariants": [
        {"id": "INV-01", "rule": "LP-01",
         "equation": "GameState.LinesCleared * 10 + GameState.BlockCount = 4 * GameState.LockedCount"},
        {"id": "INV-02", "rule": "PR-06", "equation": "Board.Width = 10"},
    ],
}


def mutated(fn):
    d = copy.deepcopy(DECL)
    fn(d)
    return d


class Validate(unittest.TestCase):
    def test_valid_passes(self):
        self.assertEqual(invgen.validate(DECL, SPEC, GDD), [])

    def assertRejected(self, decl, fragment):
        problems = invgen.validate(decl, SPEC, GDD)
        self.assertTrue(any(fragment in p for p in problems), f"{fragment!r} が問題に無い: {problems}")

    def test_operator_outside_grammar(self):
        self.assertRejected(mutated(lambda d: d["invariants"][0].update(
            equation="GameState.BlockCount / 2 = GameState.LockedCount")), "= はちょうど 1 つ")

    def test_two_equals(self):
        self.assertRejected(mutated(lambda d: d["invariants"][0].update(
            equation="GameState.BlockCount = GameState.LockedCount = 0")), "= はちょうど 1 つ")

    def test_non_integer_state(self):
        self.assertRejected(mutated(lambda d: d["invariants"][0].update(
            equation="GameState.Phase = 0")), "整数の状態ではありません")

    def test_rule_not_in_spec(self):
        self.assertRejected(mutated(lambda d: d["invariants"][0].update(rule="RL-99")), "構造化仕様に存在する ID")

    def test_op_not_on_subject(self):
        self.assertRejected(mutated(lambda d: d["ops"].append({"call": "Board.Width"})), "インスタンスメソッドではありません")

    def test_empty_argument_domain(self):
        self.assertRejected(mutated(lambda d: d["ops"][1].update(args={"seed": []})), "値の候補の列")

    def test_hand_computed_argument_must_be_literal(self):
        self.assertRejected(mutated(lambda d: d["ops"][1].update(args={"seed": ["x ^ 13"]})), "GDD から引けないリテラル")

    def test_stale_against_gdd(self):
        self.assertRejected(mutated(lambda d: d.update(gdd_sha256="0" * 64)), "gdd_sha256 が今の GDD と一致しません")

    def test_steps_out_of_range(self):
        self.assertRejected(mutated(lambda d: d.update(steps=0)), "steps は 1 以上")


class Generate(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual(invgen.generate(DECL, SPEC, GDD), invgen.generate(DECL, SPEC, GDD))

    def test_counterexample_line_is_emitted(self):
        text = invgen.generate(DECL, SPEC, GDD)["InvariantsCases.cs"]
        self.assertIn("INVARIANT_FAIL id=INV-01 rule=LP-01 seed=", text)
        self.assertIn(invgen.SEED_ENV, text)

    def test_invalid_declaration_is_not_generated(self):
        with self.assertRaises(invgen.InvariantError):
            invgen.generate(mutated(lambda d: d.update(seeds=0)), SPEC, GDD)


class Builds(unittest.TestCase):
    def files(self, mangle=lambda s: s):
        files = {k: mangle(v) for k, v in invgen.generate(DECL, SPEC, GDD).items()}
        files["Stub.cs"] = testgen.interface_stub(DECL, SPEC, GDD)
        return files

    def test_generated_invariants_compile(self):
        rc, log = build(self.files())
        self.assertEqual(rc, 0, log)

    def test_build_check_is_not_vacuous(self):
        rc, _ = build(self.files(lambda s: s.replace("Next(ref x)", "Nxt(ref x)")))
        self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
