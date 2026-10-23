"""地図と前提の照合（W-124、docs/design/research_protocol.md）：公開した記録が規則どおりに集められ、ADR-005 が強い根拠に依ること。

    python -m unittest tests.test_research_coverage -v

docs/research/ の記録（調べ役の出力を機械で照合したもの）を読むだけで、ネットワークにも CLI にも出ない。
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import premises  # noqa: E402
import research  # noqa: E402

RECORDS = ROOT / "docs" / "research"
# 分野の名前と、unknowns で名指しに使ってよい日本語（docs/design/research_protocol.md §3）
LABELS = {"frontier_labs": "最前線", "practitioners_at_scale": "大規模", "academia": "学術", "standards_tools": "標準",
          "practitioners": "実務家"}


def records(stage=None):
    out = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(RECORDS.glob("R-*.json"))]
    return [r for r in out if stage is None or r.get("stage") == stage]


class Map(unittest.TestCase):
    def test_there_are_map_records(self):
        self.assertTrue(records("map"))

    def test_each_listed_sector_has_claims_or_is_named_as_a_gap(self):
        for rec in records("map"):
            have = {c.get("sector") for c in rec["claims"]}
            gaps = " ".join(rec.get("unknowns") or [])
            for sector in rec["sectors"]:
                self.assertTrue(sector in have or sector in gaps or LABELS[sector] in gaps,
                                f"{rec['id']} の分野 {sector} に主張も unknowns の名指しも無い")

    def test_the_maps_together_reach_every_sector(self):
        reached = {c.get("sector") for rec in records("map") for c in rec["claims"]}
        self.assertEqual(reached, set(research.SECTORS))

    def test_records_keep_their_queries_and_the_question_shape(self):
        for rec in records():
            if rec.get("stage"):
                self.assertTrue(rec["queries"], rec["id"])
                self.assertEqual(research.question_problems(research.sections(rec["question"])), [], rec["id"])


class Premises(unittest.TestCase):
    def test_every_research_premise_points_at_published_claims(self):
        state, claims = premises.load(), premises.load_claims(RECORDS)
        missing = [(pid, why) for pid, why in premises.weak(state, claims) if "記録に無い" in why]
        self.assertEqual(missing, [])

    def test_premises_used_by_adr_005_are_strong(self):
        state = premises.load()
        used = {p["id"] for p in state["premises"] if any("ADR-005" in u for u in p.get("used_by") or [])}
        research_used = {p["id"] for p in state["premises"] if p["id"] in used and p.get("kind") == "research"}
        self.assertTrue(research_used, "ADR-005 が依る research の前提が無い")
        weak = {pid for pid, _ in premises.weak(state, premises.load_claims(RECORDS))}
        self.assertEqual(research_used & weak, set(), "弱い前提で方向を決めない（research_protocol.md §6）")

    def test_the_adopted_direction_was_refuted_at_least_once(self):
        self.assertTrue(records("refute"), "採る案に反する証拠を探した記録（refute）が要る")


if __name__ == "__main__":
    unittest.main()
