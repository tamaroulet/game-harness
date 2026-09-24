"""v2.1c（docs/design/v2_1c_structure_review.md）：言葉ではなく契約と作業場所で実装役を縛る。

    python -m unittest tests.test_v21c

**なぜ要るか**: v2.1b は、単位定義の prompt から正規表現で仕様 ID を拾って構造化仕様の文章を埋め込み、
「テストや仕様書を読まないでください」と頼んで探索を止めていた。T5 の prompt の「PR-52〜PR-68」は両端しか拾えず、
キック表 17 行のうち 15 行が欠けるはずだった。ここでは、agy も dotnet も使わずに次を縛る。
- 参照データは contractgen が C# の定数（GddReference）にする。値が読めない行・欠けた表は黙って落とさず失敗する
- 実装役は細い作業場所（書き換えてよいファイルと契約と既存の型だけ）で動き、変わったものだけが書き戻される
- T1〜T5 の単位定義が、参照する参照データと、それまでのタスクの性質をすべて持ち、埋め込みが上限に収まる
（GddReference が本物のコンパイラで通ることは、PR 本文に記録した。ここでは dotnet を使わない）
"""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import contractgen  # noqa: E402
import implementer_context  # noqa: E402
import narrow_dir  # noqa: E402
import pipeline  # noqa: E402
import tool_policy  # noqa: E402

V2 = ROOT / "experiments" / "v2"
ENUMS = {"MinoType": ["I", "O"], "Rotation": ["Spawn", "Right", "Two", "Left"]}
I_KICKS = [("0", "R"), ("R", "0"), ("R", "2"), ("2", "R"), ("2", "L"), ("L", "2"), ("L", "0"), ("0", "L")]


def rows(**drop):
    r = {"PR-03": ("ロック猶予", "30 ティック"), "PR-09": ("出現位置", "(3, 19)"),
         "PR-10": ("7-Bag 初期配列", "I, O"), "PR-17": ("形状 I 回転原点", "(1.5, 1.5)"),
         "PR-69": ("ハードドロップ得点係数", "1 マスにつき 2 点")}
    n = 18
    for t in ("I", "O"):
        for lab in ("0", "R", "2", "L"):
            r[f"PR-{n:02d}"] = (f"形状 {t} 向き {lab}", "(0, 0) (1, 0) (2, 0) (9, 9)")
            n += 1
    for i, (f, to) in enumerate(I_KICKS):
        r[f"PR-{60 + i}"] = (f"回転補正候補 I {f} → {to}", f"(0, 0) ({i}, 0)")
    r["PR-68"] = ("回転補正候補 O 全向き遷移", "(0, 0)")
    for k in drop:
        r.pop(k.replace("_", "-"))
    return r


class ReferenceConstants(unittest.TestCase):
    """裁定 1：参照データは言葉ではなく C# の定数として渡す。"""

    def setUp(self):
        self.files, self.ids = contractgen.reference_file(rows(), ENUMS, "impl")
        self.text = self.files["impl/Contracts/GddReference.cs"]

    def test_every_row_becomes_a_constant_named_by_its_id(self):
        self.assertEqual(sorted(self.ids, key=lambda x: int(x[3:])), self.ids)
        self.assertEqual(set(self.ids), set(rows()))
        self.assertTrue(self.text.startswith(contractgen.HEADER), "埋め込みと作業場所が契約として見分ける")
        self.assertIn("public const int PR_03 = 30;", self.text)
        self.assertIn("// PR-03 ロック猶予：30 ティック", self.text, "単位は整数の行の注記に残す")
        self.assertIn("public const int PR_69 = 2;", self.text, "「1 マスにつき 2 点」は 2")
        self.assertIn("public static readonly int[] PR_09 = { 3, 19 };", self.text)
        self.assertIn("public static readonly MinoType[] PR_10 = { MinoType.I, MinoType.O };", self.text)
        self.assertIn("public static readonly double[] PR_17 = { 1.5, 1.5 };", self.text)

    def test_tables_are_looked_up_by_name_and_hold_each_value_once(self):
        self.assertIn("public static readonly int[][] PR_68 = { new[] { 0, 0 } };", self.text,
                      "表の行は座標が 1 つでも int[][]（Kicks が同じ型で返す）")
        self.assertIn("if (type == MinoType.I && rotation == Rotation.Right) return PR_19;", self.text)
        self.assertIn("if (from == Rotation.Two && to == Rotation.Left) return PR_64;", self.text)
        self.assertIn("if (type == MinoType.O) return PR_68;", self.text, "全向き遷移は 1 行にまとめる")
        self.assertEqual(self.text.count("new[] { 9, 9 }"), 8, "形の値は定数に 1 回ずつだけ書く")

    def test_deterministic(self):
        self.assertEqual(contractgen.reference_file(rows(), ENUMS, "impl"), (self.files, self.ids))

    def test_unreadable_values_and_missing_table_rows_fail_loudly(self):
        bad = rows()
        bad["PR-99"] = ("謎", "たくさん")
        with self.assertRaisesRegex(contractgen.ContractError, "PR-99"):
            contractgen.reference_file(bad, ENUMS, "impl")
        with self.assertRaisesRegex(contractgen.ContractError, "回転補正候補 I 2 → L"):
            contractgen.reference_file(rows(PR_64=1), ENUMS, "impl")
        with self.assertRaisesRegex(contractgen.ContractError, "形 O 向き L"):
            contractgen.reference_file(rows(PR_25=1), ENUMS, "impl")


class ReferencedParams(unittest.TestCase):
    def test_ranges_are_expanded(self):
        ids = implementer_context.referenced_params("補正（RL-47、PR-52〜PR-68）と PR-03 と PR-17..PR-18")
        self.assertEqual(len(ids), 17 + 1 + 2)
        self.assertIn("PR-60", ids, "v2.1b の正規表現は範囲の中を落としていた")


def v2_template(k):
    return json.loads((V2 / "tasks.json").read_text(encoding="utf-8"))["templates"][k]


class NarrowDir(unittest.TestCase):
    """裁定 2：読めるものを、頼みではなく置き場所で絞る。"""

    def test_only_listed_files_are_placed_and_changes_are_written_back(self):
        with tempfile.TemporaryDirectory() as origin, tempfile.TemporaryDirectory() as tmp:
            o = Path(origin)
            for rel, body in (("Core/GameState.cs", "a"), ("Core/IGameState.cs", "c"), ("Core/Keep.cs", "k"),
                              ("tests/T.cs", "t"), ("docs/spec.md", "s")):
                (o / rel).parent.mkdir(parents=True, exist_ok=True)
                (o / rel).write_text(body, encoding="utf-8")
            dst = Path(tmp) / narrow_dir.ROOT_NAME / "x"
            dst, placed = narrow_dir.populate(o, ["Core/GameState.cs", "Core/IGameState.cs", "Core/Keep.cs",
                                                  "Core/Board.cs"], dst)
            self.assertEqual(sorted(placed), ["Core/GameState.cs", "Core/IGameState.cs", "Core/Keep.cs"],
                             "テスト・仕様書は置かない。無いファイルは置かない")
            (dst / "Core/GameState.cs").write_text("edited", encoding="utf-8")
            (dst / "Core/Board.cs").write_text("new", encoding="utf-8")
            (dst / "Core/IGameState.cs").unlink()
            changed = narrow_dir.write_back(dst, o, placed)
            self.assertEqual(changed, ["Core/Board.cs", "Core/GameState.cs", "Core/IGameState.cs"])
            self.assertEqual((o / "Core/GameState.cs").read_text(encoding="utf-8"), "edited")
            self.assertEqual((o / "Core/Board.cs").read_text(encoding="utf-8"), "new")
            self.assertFalse((o / "Core/IGameState.cs").exists(), "消したことも書き戻し、門に判定させる")
            self.assertEqual((o / "tests/T.cs").read_text(encoding="utf-8"), "t")

            # 2 回目は空にしてから置き直す（前の呼び出しの残りを持ち越さない）
            dst2, placed2 = narrow_dir.populate(o, ["Core/GameState.cs"], dst)
            self.assertEqual(sorted(placed2), ["Core/GameState.cs"])
            self.assertFalse((dst2 / "Core/Board.cs").exists())

    def test_path_is_stable_per_origin_and_never_wipes_other_dirs(self):
        self.assertEqual(narrow_dir.path_for("a"), narrow_dir.path_for("a"))
        self.assertNotEqual(narrow_dir.path_for("a"), narrow_dir.path_for("b"))
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                narrow_dir.populate(d, [], Path(d))

    def test_pipeline_runs_the_implementer_in_the_narrow_dir(self):
        seen = {}
        imp = {"cli": "agy", "headless_flag": "-p", "auto_approve_flag": "-y", "model_flag": "--model",
               "model_name": "m", "output_format_args": ["--output-format", "stream-json"]}
        with tempfile.TemporaryDirectory() as d:
            sb = Path(d)
            (sb / "Core").mkdir()
            (sb / "Core" / "GameState.cs").write_text("class GameState {}", encoding="utf-8")
            (sb / "Core" / "IGameState.cs").write_text(contractgen.HEADER + "\ninterface IGameState {}",
                                                       encoding="utf-8")
            (sb / "tests").mkdir()
            (sb / "tests" / "T.cs").write_text("test", encoding="utf-8")
            c = SimpleNamespace(unit={"prompt": "作る", "whitelist": ["Core/GameState.cs"]}, sandbox=sb,
                                sb=lambda rel: sb / rel, cfg={"implementer": imp, "project": {"impl_dir": "Core"}},
                                ttl={"implementer": 300}, metrics={}, cur={})

            def fake_run(args, cwd, ttl, label, input=None):
                seen["cwd"], seen["prompt"] = Path(cwd), json.loads(input)["message"]["content"]
                seen["listing"] = sorted(p.relative_to(cwd).as_posix() for p in Path(cwd).rglob("*") if p.is_file())
                (Path(cwd) / "Core" / "GameState.cs").write_text("class GameState : IGameState {}", encoding="utf-8")
                return 0, "", ""
            with mock.patch.object(pipeline, "run", side_effect=fake_run), \
                    mock.patch.object(pipeline, "resolve_cli", side_effect=lambda n: [n]), \
                    mock.patch.object(pipeline, "write_implementer_log"):
                ok, _ = pipeline.call_implementer(c)
            self.assertTrue(ok)
            self.assertEqual(seen["cwd"], narrow_dir.path_for(sb))
            self.assertEqual(seen["listing"], ["Core/GameState.cs", "Core/IGameState.cs"])
            self.assertNotIn(str(sb), seen["prompt"], "サンドボックスのパスは出さない")
            self.assertEqual((sb / "Core" / "GameState.cs").read_text(encoding="utf-8"),
                             "class GameState : IGameState {}")
            self.assertEqual(c.cur["implementer"]["narrow_written"], ["Core/GameState.cs"])
        self.assertIn(tool_policy.EDIT_ONLY, seen["prompt"])
        self.assertNotIn("読まないで", seen["prompt"])
        self.assertLess(seen["prompt"].index("interface IGameState {}"), seen["prompt"].index("class GameState {}"),
                        "契約（変わらない前置き）を先に、書き換えてよいファイルを後に")

    def test_condition_a_runs_in_the_same_narrow_dir_and_writes_back(self):
        import shutil
        from ab import common, driver
        MANIFEST = ROOT / "experiments" / "b4_ab" / "tasks.json"
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        m = common.load_manifest(MANIFEST)
        unit = dict(common.unit_of(m, m["tasks"][0]), whitelist=["Core/GameState.cs"])
        wt = tmp / "wt"
        (wt / "Core").mkdir(parents=True)
        (wt / "Core" / "GameState.cs").write_text("class GameState {}", encoding="utf-8")
        ctx = {"m": m, "wt": wt, "out": tmp / "out", "ttl": 60, "impl_dir": "Core",
               "imp": {"output_format_args": ["--output-format", "stream-json"]},
               "test_project": "t.csproj", "index": 1, "classes": {"B4abT1Cases": "T1"},
               "templates": {k: (V2 / v2_template(k)).read_text(encoding="utf-8") for k in ("initial", "retry")}}
        ctx["out"].mkdir(parents=True)
        cwds = []

        def call(imp, prompt, cwd, cid, ttl):
            cwds.append(Path(cwd))
            (Path(cwd) / "Core" / "GameState.cs").write_text(f"edit {len(cwds)}", encoding="utf-8")
            return {"rc": 0, "seconds": 1.0, "conversation_id": "conv-1", "usage": {}, "out": "", "err": ""}

        def fast(wt_, proj, out, tag):
            return {"G.B4abT1Cases.Case_x": "Passed" if len(cwds) >= 2 else "Failed"}, "tail"
        rec = driver.run_task_a(ctx, m["tasks"][0], unit, {}, call=call, fast=fast)
        self.assertEqual(cwds, [narrow_dir.path_for(wt)] * 2, "会話を積むので、作業場所のパスは毎回同じ")
        self.assertEqual((wt / "Core" / "GameState.cs").read_text(encoding="utf-8"), "edit 2")
        self.assertEqual(rec["calls"][0]["narrow_written"], ["Core/GameState.cs"])

    def test_context_over_the_limit_fails_instead_of_inviting_reads(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.cs").write_text("x" * (implementer_context.MAX_CHARS + 1), encoding="utf-8")
            with self.assertRaises(implementer_context.ContextError):
                implementer_context.blocks(d, ["a.cs"], [])


class UnitsT1toT5(unittest.TestCase):
    """§3 の 4：T1〜T5 の単位定義を、走らせる前に静的に確かめる（凍結した入力だけを読む）。"""

    @classmethod
    def setUpClass(cls):
        cls.m = json.loads((V2 / "tasks.json").read_text(encoding="utf-8"))
        cls.props = json.loads((V2 / "properties.json").read_text(encoding="utf-8"))
        cls.units = {t["id"]: json.loads((V2 / t["unit"]).read_text(encoding="utf-8")) for t in cls.m["tasks"]}

    def test_reference_ranges_of_the_declarations_are_all_constants(self):
        ref = set(self.m["reference_ids"])
        for key, value in self.props["reference"].items():
            self.assertLessEqual(implementer_context.referenced_params(value), ref, key)

    def test_every_param_a_unit_mentions_is_a_constant(self):
        ref = set(self.m["reference_ids"])
        for tid, unit in self.units.items():
            self.assertLessEqual(implementer_context.referenced_params(unit["prompt"]), ref, tid)

    def test_each_unit_carries_its_own_and_all_earlier_properties(self):
        order = [t["id"] for t in self.m["tasks"]]
        for tid, unit in self.units.items():
            upto = set(order[:order.index(tid) + 1])
            want = {p["id"] for p in self.props["properties"] if p["task"] in upto}
            have = set(re.findall(r"P-T\d+-\d{2,}", unit["prompt"]))
            self.assertEqual(have, want, tid)

    def test_no_unit_or_template_points_at_documents_or_asks_not_to_read(self):
        texts = {tid: u["prompt"] for tid, u in self.units.items()}
        texts.update({k: (V2 / self.m["templates"][k]).read_text(encoding="utf-8") for k in ("initial", "retry")})
        for name, text in texts.items():
            for phrase in ("spec.md", "構造化仕様に従う", "GDD v10", "読まないで", "読み書きしないで", "{test_dir}"):
                self.assertNotIn(phrase, text, f"{name}: {phrase}")

    def test_contract_leaves_room_for_the_editable_files(self):
        self.assertLessEqual(self.m["contract_chars"], implementer_context.MAX_CHARS // 2)
        self.assertIn("Game/Assets/Core/GddReference.cs", self.m["generated_sha256"])


if __name__ == "__main__":
    unittest.main()
