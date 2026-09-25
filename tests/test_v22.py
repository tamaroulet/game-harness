"""v2.2（docs/design/v2_b_efficiency.md §6）：本走の前に B の効率の改善を入れる。

    python -m unittest tests.test_v22

**なぜ要るか**: v2-smoke-04 では、出力の 85〜95% が思考で、B の費用の 57% を占めた。GddReference（約 1.3 万字）は
T1〜T4 では回転補正候補の表を使わないのに、毎回全部を埋め込んでいた。ここでは、agy を使わずに次を縛る。
- 参照データの契約は、単位定義の prompt が参照する PR と、書き換えてよいファイルがすでに使うものに絞った抜粋を埋め込む
- 抜粋でも形の表（Shape）と、残した表が引く定数は残す。外した表は宣言だけ残す
- 作業場所に置くファイルは全部のまま（コンパイルは全部に対して行う）
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import contractgen  # noqa: E402
import implementer_context  # noqa: E402

ENUMS = {"MinoType": ["O", "T"], "Rotation": ["Zero", "Right", "Two", "Left"]}


def rows():
    r = {"PR-01": ("盤面の幅", "10 マス"), "PR-02": ("落下の間隔", "20 ティック")}
    n = 3
    for t in ENUMS["MinoType"]:
        for rot in ("0", "R", "2", "L"):
            r[f"PR-{n:02d}"] = (f"形状 {t} 向き {rot}", "(0, 0) (1, 0) (0, 1) (1, 1)")
            n += 1
    r[f"PR-{n:02d}"] = ("回転補正候補 O 全向き遷移", "(0, 0)")
    r[f"PR-{n + 1:02d}"] = ("回転補正候補 T 全向き遷移", "(0, 0) (-1, 0)")
    return r


def reference():
    return next(iter(contractgen.reference_file(rows(), ENUMS, "Core", subdir="")[0].values()))


class PruneReference(unittest.TestCase):
    def test_class_name_matches_contractgen(self):
        self.assertEqual(implementer_context.REFERENCE_CLASS, contractgen.REFERENCE_CLASS)

    def test_keeps_referenced_constants_and_the_shape_table_but_drops_unused_kicks(self):
        text = reference()
        p = implementer_context.prune_reference(text, {"PR-02"})
        self.assertIn("PR_02 = 20", p)
        self.assertNotIn("PR_01", p, "参照しない定数は外す")
        for k in range(3, 11):
            self.assertIn(f"PR_{k:02d}", p, "形の表と、それが引く定数は必ず残す")
        self.assertNotIn("PR_11", p)
        self.assertNotIn("PR_12", p)
        self.assertIn("public static int[][] Kicks(", p, "外した表も宣言は残す")
        self.assertIn("抜粋では本体を省いた", p)
        self.assertEqual(p.count("{"), p.count("}"))
        self.assertLess(len(p), len(text))

    def test_keeps_kicks_when_a_kick_constant_is_referenced(self):
        p = implementer_context.prune_reference(reference(), {"PR-12"})
        self.assertIn("return PR_11;", p)
        self.assertIn("PR_11 =", p, "残した表が引く定数は、参照していなくても残す")
        self.assertNotIn("抜粋では本体を省いた", p)

    def test_everything_referenced_leaves_the_text_unchanged(self):
        text = reference()
        self.assertEqual(implementer_context.prune_reference(text, set(rows())), text)

    def test_unknown_shape_is_left_alone(self):
        self.assertEqual(implementer_context.prune_reference("class X {}\n", set()), "class X {}\n")

    def test_used_reference_reads_constants_and_tables_from_code(self):
        code = "var w = GddReference.PR_01; var k = GddReference.Kicks(t, a, b);"
        self.assertEqual(implementer_context.used_reference(code), {"PR-01", "Kicks"})

    def test_for_unit_embeds_an_excerpt_and_keeps_what_the_editable_file_already_uses(self):
        with tempfile.TemporaryDirectory() as d:
            core = Path(d) / "Core"
            core.mkdir()
            (core / "GddReference.cs").write_text(reference(), encoding="utf-8")
            (core / "GameState.cs").write_text("class GameState { int w = GddReference.PR_01; }", encoding="utf-8")
            unit = {"prompt": "PR-02 のティックごとに落ちる。GddReference.Kicks は回転補正候補の表",
                    "whitelist": ["Core/GameState.cs"], "interface": {"types": []}}
            text = implementer_context.for_unit(d, unit, "Core")
            full = (core / "GddReference.cs").read_text(encoding="utf-8")
        self.assertIn("この仕事に関わる定数と表の抜粋", text)
        self.assertIn("PR_01 = 10", text, "書き換えてよいファイルがすでに使う定数は残す")
        self.assertIn("PR_02 = 20", text)
        self.assertIn("抜粋では本体を省いた", text, "表の名前が文章に出るだけでは、表を残さない")
        self.assertIn("PR_11", full, "作業場所のファイルは全部のまま")

    def test_blocks_without_keep_embeds_the_whole_reference(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "Core").mkdir()
            (Path(d) / "Core" / "GddReference.cs").write_text(reference(), encoding="utf-8")
            text = implementer_context.blocks(d, [], ["Core/GddReference.cs"])
        self.assertNotIn("抜粋", text)
        self.assertIn("return PR_11;", text)


if __name__ == "__main__":
    unittest.main()
