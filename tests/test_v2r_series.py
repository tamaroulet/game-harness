"""再実験 v2r のタスクの系列と入力（experiments/v2r、docs/design/v2r_protocol.md §11.3、U5）。

    python -m unittest tests.test_v2r_series

**なぜ要るか**: 系列・性質の宣言・契約・マニフェストは、承認の後に凍結する実験の入力。ここでは、凍結した入力どうしが
食い違っていないこと（マニフェストの sha256、要求文とタスクの対応、事前投資の記録と費用のモデル、モデルの記録、
自然言語の描画）を、ゲームのリポジトリを読まずに確かめる。
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import nlgen  # noqa: E402
from ab import common, v2r  # noqa: E402

V2R = ROOT / "experiments" / "v2r"
TASKS = [f"T{i}" for i in range(1, 11)]


class Series(unittest.TestCase):
    def setUp(self):
        self.m = common.load_manifest(V2R / "tasks.json")   # 凍結したファイルの sha256 を照合する
        self.decl = json.loads((V2R / "properties.json").read_text(encoding="utf-8"))

    def test_the_manifest_is_a_v2r_manifest_of_ten_tasks(self):
        self.assertTrue(common.is_v2r(self.m))
        self.assertEqual([t["id"] for t in self.m["tasks"]], TASKS)
        self.assertEqual(self.m["base_commit"], "87bdfdaf183cf220cf2b1e1f795f23bcca45d370")

    def test_every_task_has_a_requirement_and_two_or_more_properties(self):
        for t in TASKS:
            self.assertTrue((V2R / "requirements" / f"{t}.md").exists(), t)
            self.assertGreaterEqual(sum(p["task"] == t for p in self.decl["properties"]), 2, t)
        self.assertTrue((V2R / "requirements" / "common.md").exists())

    def test_the_first_five_requirements_are_the_v2_ones(self):
        for t in TASKS[:5]:
            self.assertEqual((V2R / "requirements" / f"{t}.md").read_bytes(),
                             (ROOT / "experiments" / "b4_ab" / "requirements" / f"{t}.md").read_bytes(), t)

    def test_line_clears_can_happen_from_the_start_state(self):
        self.assertGreaterEqual(self.decl["start"].get("gap_rows", 0), 1, "ライン消去の前提を起こす開始状態")

    def test_the_precost_records_the_pinned_model_and_matches_the_cost_model(self):
        runs = [json.loads((V2R / "results" / n).read_text(encoding="utf-8"))
                for n in ("precost_run1_failed.json", "precost.json")]
        for r in runs:
            self.assertEqual((r["model_requested"], r["models_used"]), ("claude-opus-5", ["claude-opus-5"]))
        self.assertFalse(runs[0]["ok"])
        self.assertTrue(runs[1]["ok"])
        cm = json.loads((V2R / "cost_model.json").read_text(encoding="utf-8"))
        self.assertAlmostEqual(cm["fixed"]["shared"]["usd"], sum(r["total"]["cost_usd"] for r in runs), places=6)
        self.assertAlmostEqual(cm["fixed"]["shared"]["seconds"], sum(r["total"]["seconds"] for r in runs), places=1)
        self.assertIsNone(cm["fixed"]["B_only"]["usd"], "F_B_only は測れないので 0 と書かない（感度の表で扱う）")

    def test_the_natural_language_spec_renders_for_every_task(self):
        contract = json.loads((V2R / "contract.json").read_text(encoding="utf-8"))
        self.assertEqual(nlgen.check_contract(contract), [])
        for t in self.m["tasks"]:
            unit = common.unit_of(self.m, t)
            self.assertEqual(unit["id"], f"v2r_{t['id'].lower()}")
            v2r.spec_text(self.m, t, unit, "nl")   # 描画の検査（1 対 1・定義の閉包）に落ちれば ABError


if __name__ == "__main__":
    unittest.main()
