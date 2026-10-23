"""上位の設計の決定（W-143、docs/design/adr-005.md）：部品ごとの決定・根拠の強さ・畳む案の比較・別系統のモデルの反論。

    python -m unittest tests.test_adr005 -v
"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import premises  # noqa: E402

ADR = ROOT / "docs" / "design" / "adr-005.md"
RECORDS = ROOT / "docs" / "research"
DECISIONS = ("残す", "置き換える", "捨てる")


def text():
    return ADR.read_text(encoding="utf-8")


def decision_rows():
    return [line for line in text().splitlines() if re.match(r"^\| D-\d+ \|", line)]


class Decisions(unittest.TestCase):
    def test_every_component_has_one_decision(self):
        comps = json.loads((ROOT / "config" / "arch_components.json").read_text(encoding="utf-8"))["components"]
        rows = decision_rows()
        for name in comps:
            mine = [r for r in rows if re.search(rf"[（(][^）)]*\b{name}\b", r)]
            self.assertTrue(mine, f"部品 {name} の決定が無い")
            for r in mine:
                cell = r.split("|")[3]
                self.assertTrue(any(d in cell for d in DECISIONS), f"{name}: {cell}")

    def test_each_decision_cites_a_premise_or_a_measurement(self):
        for r in decision_rows():
            self.assertRegex(r.split("|")[4], r"P-\d{3}|実測", r[:40])

    def test_cited_premises_exist_and_research_ones_are_strong(self):
        state = premises.load()
        known = {p["id"]: p for p in state["premises"]}
        cited = set(re.findall(r"P-\d{3}", text()))
        self.assertTrue(cited)
        self.assertEqual(cited - set(known), set(), "ADR-005 が引く前提が台帳に無い")
        weak = {pid for pid, _ in premises.weak(state, premises.load_claims(RECORDS))}
        self.assertEqual({c for c in cited if known[c].get("kind") == "research"} & weak, set())

    def test_cited_claims_exist(self):
        claims = premises.load_claims(RECORDS)
        for short, cid in re.findall(r"\b(r\d|c\d|m\d|v\d)#(C\d+)", text()):
            refs = [k for k in claims if re.match(rf"R-\d{{8}}-{short}-.*#{cid}$", k)]
            self.assertTrue(refs, f"{short}#{cid} が docs/research/ に無い")

    def test_folding_the_harness_was_compared(self):
        self.assertIn("独自のハーネスを畳み", text())


class Rebuttal(unittest.TestCase):
    def rebuttals(self):
        """ADR-005 への反証の記録（問いの名前に adr005 を含む refute）。"""
        recs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(RECORDS.glob("R-*adr005*.json"))]
        return [r for r in recs if r.get("stage") == "refute"]

    def test_the_adr_records_its_rebuttal_and_how_each_point_was_handled(self):
        self.assertTrue(self.rebuttals())
        section = text().split("## 4. 反論と扱い", 1)[1].split("## 5.", 1)[0]
        self.assertGreaterEqual(len([l for l in section.splitlines() if l.startswith("| ") and "r2" in l]), 3)

    def test_a_model_of_another_family_argued_against_it(self):
        """ADR を書いたのは Claude。反論には別系統のモデル（agy の Gemini など）を少なくとも 1 回使う（W-143 の受け入れの条件）。"""
        families = {m.split("-")[0] for rec in self.rebuttals() for m in rec.get("models") or []}
        self.assertTrue(families - {"claude"}, f"反論のモデルの系統: {sorted(families)}（agy の利用枠が戻ったら回す）")


if __name__ == "__main__":
    unittest.main()
