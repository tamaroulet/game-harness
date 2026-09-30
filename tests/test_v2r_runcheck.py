"""v2r の R＝1 の 4 走行の健全性の検査（harness/ab/v2r_runcheck.py。V2R-R の検証コマンド）。

    python -m unittest tests.test_v2r_runcheck

**なぜ要るか**: R＝1 の 4 走行は、門の効果ではなく、基盤の健全性と追試に要る記録を示すためのもの（§15.13）。
欠陥はデータとして数えるだけにし、記録の欠け・空虚な性質・モデルの不一致・環境のずれ・来歴の欠けで落とす。
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

from ab import v2r_runcheck  # noqa: E402

TASKS = [f"T{i}" for i in range(1, 11)]
MODEL = {"requested": "gemini-3.8-flash-medium", "reported": ["gemini-3.8-flash-medium"]}


def row(task, accepted=True, vacuous=(), model=MODEL, cli="1.2.14"):
    return {"task": task, "accepted": accepted, "p2p_broken": 0, "invariants": {"failures": 0}, "attempts": 1,
            "vacuous_properties": list(vacuous), "model": model, "calls": [{"attempt": 1, "cli_version": cli}]}


class RunCheck(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        for cond in ("A0", "A1", "B-G", "B"):
            self.write(cond, [row(t) for t in TASKS])
        (self.root / "p.env.json").write_text(json.dumps({"problems": [], "pinned": {"cli": {"agy": "1.2.14"}}}),
                                              encoding="utf-8")
        self.prov({"harness": {"commit": "abc", "dirty": []}, "agy_global": {"GEMINI.md": "f" * 64}})

    def write(self, cond, rows):
        d = self.root / "p-01" / cond
        d.mkdir(parents=True, exist_ok=True)
        (d / "metrics.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def prov(self, rec):
        (self.root / "p.provenance.json").write_text(json.dumps(rec), encoding="utf-8")

    def check(self):
        return v2r_runcheck.check(self.root, "p", TASKS)

    def test_a_healthy_run_passes_even_with_defects(self):
        self.write("A0", [row(t, accepted=(t != "T3")) for t in TASKS])
        (self.root / "p-01" / "B" / "fault.json").write_text("{}", encoding="utf-8")
        res = self.check()
        self.assertTrue(res["ok"], res["problems"])
        self.assertEqual(res["table"][0]["defects"], 1, "欠陥はデータとして数えるだけ")
        self.assertTrue(res["table"][3]["resumed"])
        self.assertIn("あり", v2r_runcheck.render(res))

    def test_missing_rows_vacuous_properties_and_model_mismatch_fail(self):
        cases = {
            "行の無い": lambda: self.write("A1", [row(t) for t in TASKS[:9]]),
            "空虚な性質": lambda: self.write("B-G", [row(t, vacuous=("P-T6-01",) if t == "T6" else ()) for t in TASKS]),
            "モデル": lambda: self.write("B", [row(t, model={"requested": "m", "reported": ["n"]} if t == "T2" else MODEL)
                                             for t in TASKS]),
        }
        for word, breaks in cases.items():
            with self.subTest(word=word):
                self.setUp()
                breaks()
                res = self.check()
                self.assertFalse(res["ok"])
                self.assertTrue(any(word in p for p in res["problems"]), res["problems"])

    def test_a_task_stopped_by_the_gate_before_calling_fails(self):
        """v2r-r1-01 の A1・B：T2 を 0 回の試行で飛ばした行（detail.stopped）を、健全な走行として数えない。"""
        rows = [row(t) for t in TASKS]
        rows[1] = dict(rows[1], attempts=0, detail={"stopped": "ABORT: 検査系故障（base）"})
        self.write("A1", rows)
        res = self.check()
        self.assertFalse(res["ok"])
        self.assertTrue(any("門が止めた" in p and "T2" in p for p in res["problems"]), res["problems"])

    def test_a_call_on_another_or_unrecorded_cli_version_fails(self):
        """v2r-r1-01：起動時は 1.2.12 だったが、38 回のうち 37 回が 1.2.14 で走った。"""
        for cli in ("1.2.12", None):
            with self.subTest(cli=cli):
                self.setUp()
                self.write("B-G", [row(t, cli=cli if t == "T4" else "1.2.14") for t in TASKS])
                res = self.check()
                self.assertFalse(res["ok"])
                self.assertTrue(any("B-G" in p and "T4" in p and "版" in p for p in res["problems"]), res["problems"])

    def test_an_empty_model_report_fails(self):
        self.write("A0", [row(t, model={"requested": "m", "reported": []}) for t in TASKS])
        self.assertFalse(self.check()["ok"])

    def test_environment_and_provenance_are_required(self):
        (self.root / "p.env.json").write_text(json.dumps({"problems": ["cli.agy"]}), encoding="utf-8")
        self.assertFalse(self.check()["ok"])
        self.setUp()
        self.prov({"harness": {"commit": "abc", "dirty": ["harness/x.py"]}, "agy_global": {"GEMINI.md": "f"}})
        self.assertFalse(self.check()["ok"])
        self.setUp()
        self.prov({"harness": {"commit": "abc", "dirty": []}, "agy_global": {"GEMINI.md": None}})
        self.assertFalse(self.check()["ok"], "GEMINI.md の sha256 が無ければ公開できない")
        self.setUp()
        (self.root / "p.provenance.json").unlink()
        self.assertFalse(self.check()["ok"])

    def test_the_cli(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = v2r_runcheck.main(["--prefix", "p", "--out-root", str(self.root)])
        self.assertEqual(rc, 0)
        self.assertTrue(out.getvalue().rstrip().endswith("合格"))


if __name__ == "__main__":
    unittest.main()
