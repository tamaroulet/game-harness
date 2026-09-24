"""v2 の A/B 実験の配管（V2-5、docs/design/v2_contract_foundry.md §5.3）。

    python -m unittest tests.test_v2_pipe

**なぜ要るか**: v1 の配管（例示テストの凍結ファイル、feature の単位）のまま v2 の生成物を流すと、
次の 4 か所で実装に関係なく止まる。それぞれを dotnet も LLM も使わずに縛る。
1. base には契約を満たす実装が無いので、探針と性質テストがビルドできない → 基準の確立で生成物も除く
2. まだ実装していないタスクの性質テストを置くと、それが落ちて受入を汚す → T1〜Tk のクラスだけ置く
3. base に既にある型（GamePhase など）を契約として生成すると、同じ型が 2 つになる → 生成しない
4. 例示の受入データを持たない単位を、スキーマの門が通さない → task_kind に property を足す
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "tests"))

import contractgen  # noqa: E402
import pipeline  # noqa: E402
import propgen  # noqa: E402
import unit_schema  # noqa: E402
from ab import common, driver, measure  # noqa: E402
from adapters import dotnet  # noqa: E402
from test_propgen import DECL, GDD, INTERFACE, SPEC  # noqa: E402

V2 = ROOT / "experiments" / "v2"


class ContractExisting(unittest.TestCase):
    def test_existing_data_types_are_not_generated_but_behavior_interfaces_are(self):
        files = contractgen.generate(INTERFACE, "Core", "Tests", existing=["GamePhase", "ActiveMino", "GameState"],
                                     subdir="")
        self.assertNotIn("Core/GamePhase.cs", files)
        self.assertNotIn("Core/ActiveMino.cs", files)
        self.assertIn("Core/TickInput.cs", files, "base に無いデータ型は生成する")
        self.assertIn("Core/IGameState.cs", files, "振る舞いの型の interface は、既にあっても生成する")
        self.assertIn("Tests/Contracts/ContractProbe.cs", files)


class PropertyTasks(unittest.TestCase):
    def test_only_the_named_tasks_get_test_classes(self):
        files = propgen.generate(DECL, SPEC, GDD, INTERFACE, "tests", tasks={"T1"})
        self.assertIn("tests/Properties/PropertiesT1Cases.cs", files)
        self.assertNotIn("tests/Properties/PropertiesT5Cases.cs", files)
        self.assertIn("P_T5_01", files["tests/Properties/Checks.cs"], "判定は全部出す（クラスだけ絞る）")


class PropertyKindUnit(unittest.TestCase):
    def test_real_v2_units_pass_the_schema_gate_shape(self):
        for k in range(1, 6):
            unit = json.loads((V2 / "units" / f"T{k}.json").read_text(encoding="utf-8"))
            self.assertEqual(unit["task_kind"], "property")
            self.assertEqual(unit["acceptance"]["cases"], [])
            self.assertTrue(unit["acceptance"]["required_tests"])
            self.assertTrue(all(unit_schema.PROPERTY_TEST_RE.match(t) for t in unit["acceptance"]["required_tests"]))
            # 実装役が書くのは GameState だけ。base に無いファイル（v1 の MinoShape.cs）を入れると、
            # 在ることを要求する静的な門が必ず落ちる（v2-dry-01 の B）
            self.assertEqual(unit["whitelist"], ["Game/Assets/Core/GameState.cs"])
            self.assertEqual(unit["impl_files"], unit["whitelist"])

    def test_property_kind_rules(self):
        problems = []
        ok = {"cases": [], "required_tests": ["PropertiesT1Cases.P_T1_01_Public"]}
        unit_schema._cases(ok, {}, problems, prop=True)
        self.assertEqual(problems, [])
        for bad in ({"cases": [], "required_tests": []},
                    {"cases": [], "required_tests": ["PropertiesT1Cases.P_T1_01_Hidden"]},
                    {"cases": [], "required_tests": ["PropertiesT1Cases.P_T2_01_Public"]},
                    {"cases": [{"id": "x"}], "required_tests": ["PropertiesT1Cases.P_T1_01_Public"]}):
            problems = []
            unit_schema._cases(bad, {}, problems, prop=True)
            self.assertTrue(problems, bad)


class BaseIsolation(unittest.TestCase):
    """実装前（base）は生成物がビルドできないので、基準の確立で受入テストと一緒に除く。"""

    def test_generated_test_side_files_are_found_by_their_header(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "tests" / "Core.Tests"
            (root / "Properties").mkdir(parents=True)
            (root / "Contracts").mkdir()
            (root / "Properties" / "Checks.cs").write_text(propgen.HEADER + "\n", encoding="utf-8")
            (root / "Contracts" / "ContractProbe.cs").write_text(contractgen.HEADER + "\n", encoding="utf-8")
            (root / "OldTests.cs").write_text("// 人が書いた既存のテスト\n", encoding="utf-8")
            c = SimpleNamespace(unit={"fast_test_project": "tests/Core.Tests/Core.Tests.csproj",
                                      "acceptance": {"required_tests": ["PropertiesT1Cases.P_T1_01_Public"]}},
                                sb=lambda rel: Path(d) / rel, sandbox=Path(d), fast=dotnet)
            found = [p.name for p in pipeline.new_test_files(c)]
        self.assertEqual(sorted(found), ["Checks.cs", "ContractProbe.cs"])


class Manifest(unittest.TestCase):
    def test_real_v2_manifest_loads_and_is_v2(self):
        m = common.load_manifest(V2 / "tasks.json")
        self.assertTrue(common.is_v2(m))
        self.assertEqual([t["id"] for t in m["tasks"]], ["T1", "T2", "T3", "T4", "T5"])
        self.assertIn("Game/Assets/Core/TickInput.cs", m["generated_sha256"])
        self.assertNotIn("Game/Assets/Core/GamePhase.cs", m["generated_sha256"], "base にある型は生成しない")

    def test_regenerated_files_must_match_the_frozen_sha256(self):
        m = common.load_manifest(V2 / "tasks.json")
        with mock.patch("ab.v2prep.generated", return_value={"Game/Assets/Core/TickInput.cs": "changed"}), \
                mock.patch.object(common.unit_schema, "git_reader", return_value=lambda p: ""), \
                mock.patch.object(common.project, "config", return_value={"spec_path": "s", "gdd_path": "g"}):
            with self.assertRaises(common.ABError):
                common.v2_files(m, 1)

    def test_placement_writes_meta_for_unity_assets_and_reports_tampering(self):
        m = {"kind": "v2", "_v2_files": {1: {"Game/Assets/Core/TickInput.cs": "A", "tests/Core.Tests/Properties/X.cs": "B"}}}
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(measure.place_generated(m, d, 1), [])
            self.assertTrue((Path(d) / "Game/Assets/Core/TickInput.cs.meta").exists())
            self.assertFalse((Path(d) / "tests/Core.Tests/Properties/X.cs.meta").exists())
            (Path(d) / "tests/Core.Tests/Properties/X.cs").write_text("tampered", encoding="utf-8")
            self.assertEqual(measure.place_generated(m, d, 1), ["tests/Core.Tests/Properties/X.cs"])
            self.assertEqual((Path(d) / "tests/Core.Tests/Properties/X.cs").read_text(encoding="utf-8"), "B")


class Measure(unittest.TestCase):
    CLASSES = {"PropertiesT1Cases": "T1"}

    def test_public_only_ignores_hidden(self):
        r = {"G.PropertiesT1Cases.P_T1_01_Public": "Passed", "G.PropertiesT1Cases.P_T1_01_Hidden": "NotExecuted"}
        self.assertEqual(measure.acceptance(r, self.CLASSES, "T1", public_only=True), (1, 1))
        self.assertEqual(measure.acceptance(r, self.CLASSES, "T1"), (1, 2), "最後の測定は非公開も数える")

    def test_v2_classes_and_hidden_seeds(self):
        m = common.load_manifest(V2 / "tasks.json")
        self.assertEqual(measure.class_to_task(m, []), {f"PropertiesT{k}Cases": f"T{k}" for k in range(1, 6)})
        a = common.hidden_seeds("r1", "T1", 20)
        self.assertEqual(a, common.hidden_seeds("r1", "T1", 20))
        self.assertNotEqual(a, common.hidden_seeds("r2", "T1", 20))
        self.assertEqual(len(a), 20)

    def test_retry_feedback_for_a_is_the_property_line(self):
        trx = ('<?xml version="1.0" encoding="utf-8"?><TestRun xmlns="http://microsoft.com/schemas/VisualStudio/'
               'TeamTest/2010"><Results><UnitTestResult testId="1" testName="P_T1_01_Public" outcome="Failed">'
               '<Output><ErrorInfo><Message>PROPERTY_FAIL id=P-T1-01 rule=RL-16 ops=[Left] expected=ActiveMino.X:3 '
               'actual=ActiveMino.X:4\n   at X.Y()</Message></ErrorInfo></Output></UnitTestResult></Results>'
               '<TestDefinitions><UnitTest name="P_T1_01_Public" id="1"><TestMethod className="G.PropertiesT1Cases" '
               'name="P_T1_01_Public" /></UnitTest></TestDefinitions></TestRun>')
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.trx"
            p.write_text(trx, encoding="utf-8")
            text = driver.failure_list({}, self.CLASSES, "T1", p)
        self.assertEqual(text, "PROPERTY_FAIL id=P-T1-01 rule=RL-16 ops=[Left] expected=ActiveMino.X:3 "
                               "actual=ActiveMino.X:4")


if __name__ == "__main__":
    unittest.main()
