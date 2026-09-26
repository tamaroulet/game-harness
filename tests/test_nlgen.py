"""性質の宣言の決定論的な自然言語の描画（harness/nlgen.py、docs/design/v2r_protocol.md §2・§3）。

    python -m unittest tests.test_nlgen

**なぜ要るか**: v2r の A0・A1 に渡す自然言語の仕様を、人や LLM を介さずに作る。ここでは次を縛る。
- 語彙の対応表は全単射（語句が重ならない）で、契約の全メンバーと propgen の全関数を覆う
- 対応表に無い識別子・関数・構文は、飛ばさずに止める
- 同じ入力からは常に同じ文（決定論）。対応表を変えれば sha256 が変わる
- 性質と文は 1 対 1。使った関数の意味の説明が仕様の中にある（定義の閉包）
- A1 の知らせは、B と同じ行を変えずに、性質の文を添えるだけ。単位定義に nl_properties が無ければ何もしない
"""
import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import nlgen  # noqa: E402
import pipeline  # noqa: E402
import propgen  # noqa: E402
import unit_schema  # noqa: E402

V2 = ROOT / "experiments" / "v2"


def props():
    return json.loads((V2 / "properties.json").read_text(encoding="utf-8"))["properties"]


class Vocabulary(unittest.TestCase):
    def test_the_table_is_bijective_and_covers_the_functions(self):
        self.assertEqual(nlgen.check_vocabulary(), [])

    def test_the_table_is_frozen(self):
        """U4：人間が査読した対応表の sha256（docs/design/v2r_nlgen_table.md）。変えるときは本書と同じ PR で改めて査読する。"""
        self.assertEqual(nlgen.table_sha256(), "959aedeb8eec960a0ec56b813c77d1af0d55709ceef30c512a27088836f60047")
        doc = (Path(__file__).resolve().parent.parent / "docs" / "design" / "v2r_nlgen_table.md").read_text(
            encoding="utf-8")
        self.assertIn(nlgen.table_sha256(), doc)
        self.assertIn(json.dumps(nlgen.table(), ensure_ascii=False, indent=2, sort_keys=True), doc)

    def test_the_table_covers_every_member_of_the_contract(self):
        contract = json.loads((V2 / "contract.json").read_text(encoding="utf-8"))
        self.assertEqual(nlgen.check_contract(contract), [])

    def test_a_clash_between_phrases_is_found(self):
        with mock.patch.dict(nlgen.ENUMS["Rotation"], {"Two": "右向き"}):
            problems = nlgen.check_vocabulary()
        self.assertTrue(any("右向き" in p for p in problems), problems)
        with mock.patch.dict(nlgen.STATE, {"Score": "局面"}):
            self.assertTrue(nlgen.check_vocabulary())

    def test_every_function_template_has_one_hole_per_argument(self):
        with mock.patch.dict(nlgen.VALUE_FUNCS, {"moved": "{0}を x に {1} 動かしたもの"}):
            self.assertTrue(any("moved" in p for p in nlgen.check_vocabulary()))

    def test_a_missing_function_is_found(self):
        with mock.patch.dict(propgen.FUNCS, {"teleport": (("mino",), "mino", lambda a: "")}):
            self.assertTrue(any("teleport" in p for p in nlgen.check_vocabulary()))

    def test_the_rendered_spec_can_go_into_a_unit_prompt(self):
        """単位定義の prompt の門（コードの記法の禁止）を通る字だけで書く。"""
        text = nlgen.render_spec(props())
        for tok in unit_schema.PROMPT_FORBIDDEN:
            self.assertNotIn(tok, text)


class Rendering(unittest.TestCase):
    def test_every_v2_property_renders_to_one_sentence_without_formal_syntax(self):
        ps = props()
        spec = nlgen.render_spec(ps[:4], ps[4:])
        self.assertEqual(nlgen.check_rendering(ps, spec), [])
        body = spec.split("## 性質の文の読み方")[0]
        for formal in ("&&", "||", "==", "!=", "before.", "after.", "input.", "fits(", "moved("):
            self.assertNotIn(formal, body, "文の部分に形式の記法を出さない")

    def test_the_same_input_gives_the_same_text(self):
        ps = props()
        self.assertEqual(nlgen.render_spec(ps), nlgen.render_spec(copy.deepcopy(ps)))
        self.assertEqual(nlgen.table_sha256(), nlgen.table_sha256())

    def test_the_table_hash_changes_with_the_table(self):
        before = nlgen.table_sha256()
        with mock.patch.dict(nlgen.STATE, {"Score": "点数"}):
            self.assertNotEqual(nlgen.table_sha256(), before)

    def test_examples(self):
        cases = {
            "input.Left && !input.Right": "左の入力がある、かつ右の入力が無い",
            "before.ActiveMino != null": "前の操作中のミノがある",
            "after.ActiveMino == null": "後の操作中のミノが無い",
            "after.ActiveMino.X == before.ActiveMino.X - 1": "後の操作中のミノのX 座標が前の操作中のミノのX 座標から1を引いた値と等しい",
            "!fits(moved(before.ActiveMino, -1, 0))": "前の操作中のミノを x に マイナス1、y に 0 動かしたものが置けない",
            "before.Phase == GamePhase.Playing || before.Phase == Ready":
                "前の局面がプレイ中と等しい、または前の局面が開始待機と等しい",
            "input.Left && (input.Right || input.HardDrop)":
                "左の入力がある、かつ（右の入力がある、またはハードドロップの入力がある）",
            "!(after.Score > before.Score)": "（後の得点が前の得点より大きい）ではない",
        }
        for expr, want in cases.items():
            with self.subTest(expr=expr):
                self.assertEqual(nlgen.render_expr(expr)[0], want)

    def test_unknown_syntax_stops(self):
        for expr in ("before.Nope == 1", "before.ActiveMino.Color == 1", "teleport(before.ActiveMino)",
                     "input.Jump", "input.Left == 1", "fits(before.ActiveMino) == true", "Purple == 1",
                     "before == 1", "moved(before.ActiveMino, 1, 0)", "before.Score >"):
            with self.subTest(expr=expr), self.assertRaises(nlgen.NLGenError):
                nlgen.render_expr(expr)

    def test_one_to_one_is_checked(self):
        ps = props()[:2]
        spec = nlgen.render_spec(ps)
        line = next(l for l in spec.splitlines() if l.startswith("- P-T1-01"))
        self.assertTrue(nlgen.check_rendering(ps, spec.replace(line, "")), "文が欠けた")
        self.assertTrue(nlgen.check_rendering(ps, spec + "\n" + line), "文が 2 つ")
        self.assertTrue(nlgen.check_rendering(ps[:1], spec), "性質に無い文")

    def test_definitions_of_the_used_functions_are_in_the_spec(self):
        ps = props()
        spec = nlgen.render_spec(ps)
        for f in ("fits", "moved", "rotated", "kick"):
            self.assertIn(nlgen.FUNC_DOCS[f], spec)
        self.assertNotIn(nlgen.FUNC_DOCS["drop"], spec, "使わない関数の説明は出さない")
        self.assertTrue(nlgen.check_rendering(ps, spec.replace(nlgen.FUNC_DOCS["kick"], "")))
        self.assertIn("「逆向き」：Rotation.Two", spec, "関数の説明に出る語句も対応に載せる")


class Feedback(unittest.TestCase):
    SENTENCES = {"P-T1-01": "- P-T1-01（RL-16）：左の入力があるとき、X 座標が1減る"}
    LINE = "PROPERTY_FAIL id=P-T1-01 rule=RL-16 ops=[Left] expected=ActiveMino.X:3 actual=ActiveMino.X:4"

    def test_the_same_lines_with_the_sentence_added(self):
        text = "\n".join(["コンパイルの診断", self.LINE, "PROPERTY_VACUOUS id=P-T1-01 rule=RL-16"])
        out = nlgen.annotate_feedback(text, self.SENTENCES)
        lines = out.splitlines()
        self.assertEqual([l for l in lines if not l.startswith("  （この性質")], text.splitlines(), "元の行は変えない")
        self.assertEqual(sum(l.startswith("  （この性質：P-T1-01（RL-16）") for l in lines), 2)

    def test_an_unknown_property_in_the_feedback_stops(self):
        with self.assertRaises(nlgen.NLGenError):
            nlgen.annotate_feedback(self.LINE.replace("P-T1-01", "P-T9-01"), self.SENTENCES)

    def call(self, unit):
        seen = []
        c = SimpleNamespace(unit=unit, sandbox=Path("sb"), sb=lambda rel: Path("sb") / rel,
                            cfg={"implementer": {"cli": "agy", "headless_flag": "-p", "auto_approve_flag": "-y",
                                                 "model_flag": "-m", "model_name": "x"}},
                            ttl={"implementer": 300}, metrics={}, cur=None)

        def fake_run(args, cwd, ttl, label):
            seen.append(args[2])
            return 0, "", ""
        with mock.patch.object(pipeline, "run", side_effect=fake_run), \
                mock.patch.object(pipeline, "resolve_cli", side_effect=lambda n: [n]), \
                mock.patch.object(pipeline, "write_implementer_log"):
            pipeline.call_implementer(c, feedback=self.LINE)
        return seen[0]

    def test_condition_b_is_unchanged_and_a1_gets_the_sentence(self):
        b = self.call({"prompt": "作る", "whitelist": ["a.cs"]})
        self.assertIn(self.LINE, b)
        self.assertNotIn("この性質", b, "nl_properties の無い単位（条件 B）は、知らせを変えない")
        a1 = self.call({"prompt": "作る", "whitelist": ["a.cs"], "nl_properties": self.SENTENCES})
        self.assertIn(self.LINE + "\n  （この性質：P-T1-01（RL-16）：左の入力があるとき、X 座標が1減る）", a1)


if __name__ == "__main__":
    unittest.main()
