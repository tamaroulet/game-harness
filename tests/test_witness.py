"""証拠の状態（witness）の注入（docs/design/v2r_instrument_redesign.md の原則 P1、§6 の 1）。

    python -m unittest tests.test_witness

**なぜ要るか**: 希な前提（ライン消去など）を無作為な系列で起きるのを待つと、空虚が「実装の欠陥」か「運」かを見分け
られない（v2r-dry-02 の B の T6）。証拠の状態で前提が成り立つことを、生成の時点に実装に依らず確かめ、成り立たない宣言は
生成しない。テストの時点では、復元の欠陥（実装）と評価の食い違い（測定器）を別の行で出す。
"""
import copy
import sys
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "tests"))

import propgen as g  # noqa: E402
import test_propgen as tp  # noqa: E402
import witness  # noqa: E402
from ab import driver  # noqa: E402

# 一番下の行の右端（x = 9）だけが空き。縦の I（向き Right、x = 7 で列 9）を落とすとその穴に入る
GAP = "#########."
CLEAR_GIVEN = ("before.Phase == Playing && input.HardDrop && before.ActiveMino != null && !occupied_before(9, 0)"
               " && drop(before.ActiveMino) == moved(before.ActiveMino, 0, -2) && full_rows(drop(before.ActiveMino)) == 1")
WITNESS = {"board": [GAP], "mino": {"type": "I", "x": 7, "y": 2, "rotation": "Right"}, "input": ["HardDrop"]}


def decl_with(given=CLEAR_GIVEN, ws=None):
    d = copy.deepcopy(tp.DECL)
    d["properties"].append({"id": "P-T5-02", "task": "T5", "rule": "RL-50", "given": given,
                            "then": "after.LockedMinoCount == before.LockedMinoCount + 1",
                            "witness": copy.deepcopy(ws if ws is not None else [WITNESS])})
    return d


def problems(d):
    return g.validate(d, tp.SPEC, tp.GDD, tp.INTERFACE)


class Declaration(unittest.TestCase):
    def assertRejected(self, d, needle):
        ps = problems(d)
        self.assertTrue(any(needle in p for p in ps), ps)

    def test_the_input_order_matches_the_generator(self):
        self.assertEqual(witness.INPUT_ORDER, g.INPUT_ORDER)

    def test_a_witness_that_satisfies_the_given_is_accepted(self):
        self.assertEqual(problems(decl_with()), [])

    def test_a_witness_that_does_not_satisfy_the_given_stops_generation(self):
        """生成の時点で、参照モデルで前提を評価する（実装に依らない）。"""
        moved = copy.deepcopy(WITNESS)
        moved["mino"]["x"] = 5   # 列 7 に落ちるので、落とした位置は 2 段下ではない
        self.assertRejected(decl_with(ws=[moved]), "前提が成り立ちません")
        no_drop = dict(WITNESS, input=[])
        self.assertRejected(decl_with(ws=[no_drop]), "前提が成り立ちません")
        with self.assertRaises(g.PropertyError):
            g.generate(decl_with(ws=[moved]), tp.SPEC, tp.GDD, tp.INTERFACE, "tests")

    def test_the_given_of_a_witnessed_property_cannot_use_after(self):
        self.assertRejected(decl_with(given="after.LockedMinoCount == before.LockedMinoCount + 1"), "after は使えません")

    def test_counts_are_not_constructible_with_this_contract(self):
        self.assertRejected(decl_with(given="before.LockedMinoCount == 0"), "復元用コンストラクタが受け取らない")
        self.assertRejected(decl_with(ws=[dict(WITNESS, counters={"LockedMinoCount": 3})]), "数を受け取らない")

    def test_malformed_witnesses_are_refused(self):
        cases = [
            (dict(WITNESS, board=["##########"]), "埋まりきった行"),
            (dict(WITNESS, board=["###"]), "幅 10"),
            (dict(WITNESS, board=["#########x"]), "幅 10"),
            (dict(WITNESS, mino={"type": "I", "x": 7, "y": -1, "rotation": "Right"}), "置けません"),
            (dict(WITNESS, mino={"type": "Q", "x": 7, "y": 2, "rotation": "Right"}), "MinoType"),
            (dict(WITNESS, input=["Jump"]), "TickInput の名前"),
            (dict(WITNESS, phase="Paused"), "GamePhase"),
            ({"board": [GAP]}, "キーは board・mino"),
        ]
        for w, needle in cases:
            with self.subTest(needle=needle):
                self.assertRejected(decl_with(ws=[w]), needle)
        self.assertRejected(decl_with(ws=[]), "1〜10 個の列")

    def test_a_null_mino_makes_member_access_false_like_the_generated_code(self):
        ws = [{"board": [], "mino": None, "input": []}]
        self.assertRejected(decl_with(given="before.ActiveMino.X == 0", ws=ws), "前提が成り立ちません")
        self.assertEqual(problems(decl_with(given="before.ActiveMino == null", ws=ws)), [])


class Evaluator(unittest.TestCase):
    def setUp(self):
        self.c = g.Contract(tp.INTERFACE)
        self.ref = g.reference(tp.DECL["reference"], g.param_rows(tp.SPEC), self.c)

    def ev(self, occ=(), mino=("I", 7, 2, 1)):
        return witness.Evaluator(self.c, self.ref, set(occ), mino, {n: False for n in g.INPUT_ORDER}, "Playing")

    def test_drop_and_fits_follow_the_reference_model(self):
        e = self.ev(occ=[(9, 0)])
        self.assertEqual(e.drop(("I", 7, 5, 1)), ("I", 7, 1, 1), "列 9 の y = 0 が埋まっているので y = 1 で止まる")
        self.assertFalse(e.fits(("I", 8, 0, 1)), "列 10 は盤面の外")

    def test_full_rows_counts_the_rows_completed_by_the_mino(self):
        gap = [(x, 0) for x in range(9)] + [(x, 1) for x in range(9)]
        self.assertEqual(self.ev(occ=gap).full_rows(("I", 7, 0, 1)), 2, "列 9 の y = 0..3 を足すと 2 行が埋まる")
        self.assertEqual(self.ev(occ=gap).full_rows(("I", 7, 1, 1)), 1)
        self.assertEqual(self.ev(occ=gap).full_rows(("I", 7, 5, 1)), 0)
        self.assertEqual(self.ev(occ=[(x, 0) for x in range(8)]).full_rows(("I", 7, -1, 1)), 0,
                         "盤面の外のマスは数えない（列 9 の y = -1 は足さない）")

    def test_kick_tries_the_candidates_in_the_table_order(self):
        """I の 0 → R の候補は (0,0) (-2,0) (1,0)…。最初の候補が塞がれていれば 2 番目。"""
        e = self.ev(occ=[(5, 3)])
        self.assertEqual(e.kick(("I", 3, 1, 0), 1), ("I", 1, 1, 1))
        self.assertEqual(self.ev().kick(("I", 3, 1, 0), 1), ("I", 3, 1, 1))
        self.assertIsNone(self.ev().kick(("I", 3, 1, 0), 2))


class Generation(unittest.TestCase):
    def test_the_generated_runner_restores_the_witness_and_passes_it_to_run(self):
        files = g.generate(decl_with(), tp.SPEC, tp.GDD, tp.INTERFACE, "tests")
        model, checks = files["tests/Properties/PropertyModel.cs"], files["tests/Properties/Checks.cs"]
        cases = files["tests/Properties/PropertiesT5Cases.cs"]
        self.assertIn("static void RunWitnesses(", model)
        self.assertIn("if (witnesses != null) RunWitnesses(id, rule, witnesses, check, ref given);", model)
        self.assertIn("PROPERTY_WITNESS_RESTORE", model)
        self.assertIn("PROPERTY_WITNESS_INVALID", model)
        self.assertIn("public static PropertyRunner.Witness[] Witnesses_P_T5_02 =>", checks)
        self.assertIn("MinoType.I, 7, 2, global::", checks)
        self.assertIn(".Cell(8, 0) }", checks)
        self.assertIn("witnesses: global::", cases)
        self.assertEqual(cases.count("Witnesses_P_T5_02"), 2, "公開と非公開の両方")

    def test_declarations_without_witnesses_generate_the_same_bytes_as_before(self):
        files = g.generate(tp.DECL, tp.SPEC, tp.GDD, tp.INTERFACE, "tests")
        self.assertTrue(all("Witness" not in text for text in files.values()))


class Faults(unittest.TestCase):
    def test_witness_invalid_is_an_instrument_fault_and_restore_is_not(self):
        props = decl_with()["properties"]
        self.assertEqual(driver.instrument_faults("PROPERTY_WITNESS_INVALID id=P-T5-02 rule=RL-50 witness=0", props),
                         ["WITNESS_INVALID P-T5-02"])
        self.assertEqual(driver.instrument_faults("PROPERTY_WITNESS_RESTORE id=P-T5-02 rule=RL-50 witness=0", props), [])


class SelfTest(unittest.TestCase):
    def test_the_selftest_project_is_deterministic_and_carries_the_witness(self):
        import witness_selftest
        a = witness_selftest.files()
        self.assertEqual(a, witness_selftest.files())
        self.assertEqual(sorted(k for k in a if not k.startswith("gen/")), ["Stub.cs", "W.csproj"])
        self.assertIn("Witnesses_P_T5_02", a["gen/Properties/Checks.cs"])
        self.assertIn('Include="NUnit" Version="3.14.0"', a["W.csproj"], "U1 の固定の版と同じ")

    def test_the_selftest_refuses_a_non_empty_destination(self):
        import tempfile
        import witness_selftest
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "x").write_text("x", encoding="utf-8")
            with unittest.mock.patch("builtins.print"):
                self.assertEqual(witness_selftest.main([d]), 2)


class LockedFiles(unittest.TestCase):
    """原則 P5：作業場所の 1 ファイルの書き戻し・削除も再試行し、消せない作業場所は黙って残さない。"""

    def test_write_back_retries_a_locked_file(self):
        import errno
        import tempfile
        import narrow_dir
        with tempfile.TemporaryDirectory() as d:
            origin, dst = Path(d) / "o", Path(d) / narrow_dir.ROOT_NAME / "k"
            origin.mkdir()
            (origin / "A.cs").write_text("a", encoding="utf-8")
            dst, placed = narrow_dir.populate(origin, ["A.cs"], dst)
            (dst / "A.cs").write_text("b", encoding="utf-8")
            real, calls = Path.write_bytes, []

            def flaky(self_, data):
                calls.append(self_)
                if len(calls) == 1:
                    raise PermissionError(errno.EACCES, "locked")
                return real(self_, data)
            with unittest.mock.patch.object(Path, "write_bytes", flaky),                     unittest.mock.patch("fileops.time.sleep"):
                self.assertEqual(narrow_dir.write_back(dst, origin, placed), ["A.cs"])
            self.assertEqual((origin / "A.cs").read_text(encoding="utf-8"), "b")
            self.assertEqual(len(calls), 2)

    def test_discard_does_not_leave_a_locked_workspace_silently(self):
        import errno
        import tempfile
        import fileops
        import narrow_dir
        with tempfile.TemporaryDirectory() as d:
            dst = Path(d) / narrow_dir.ROOT_NAME / "k"
            dst.mkdir(parents=True)

            def locked(p):
                raise PermissionError(errno.EACCES, "locked")
            with unittest.mock.patch.object(fileops.shutil, "rmtree", locked),                     unittest.mock.patch("fileops.time.sleep"):
                with self.assertRaises(fileops.FileLockError):
                    narrow_dir.discard(dst)

    def test_the_guard_says_which_workspaces_were_left(self):
        import tempfile
        from ab import guard
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "aaa").mkdir()
            (Path(d) / "aaa" / "GameState.cs").write_text("x", encoding="utf-8")
            (Path(d) / "bbb").mkdir()
            lines = guard.describe_dirs(d)
        self.assertEqual([l.split(" mtime=")[0] for l in lines], ["aaa files=1", "bbb files=0"])


if __name__ == "__main__":
    unittest.main()
