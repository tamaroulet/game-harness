"""v2r の天井の検査（docs/design/v2r_protocol.md §11.3、§13 の U5）。

    python -m unittest tests.test_v2r_ceiling

**なぜ要るか**: A0 で欠陥が 1 件も出ない系列では、門の効果を測れない（V2-RUN の天井効果の繰り返し）。途中で止まった
走行を「欠陥 0 件」や「欠陥あり」と取り違えないよう、全タスクの行がそろっていることも確かめる。
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

from ab import v2r_ceiling  # noqa: E402

TASKS = [f"T{i}" for i in range(1, 11)]


def row(task, accepted=True, p2p=0, inv=0, vacuous=()):
    return {"task": task, "accepted": accepted, "acceptance": {"passed": 3 if accepted else 2, "total": 3},
            "p2p_broken": p2p, "invariants": {"failures": inv}, "vacuous_properties": list(vacuous)}


class Ceiling(unittest.TestCase):
    def test_the_manifest_has_ten_tasks(self):
        self.assertEqual(v2r_ceiling.task_ids(v2r_ceiling.MANIFEST), TASKS)

    def test_one_defect_passes(self):
        for bad in (row("T4", accepted=False), row("T4", p2p=1), row("T4", inv=2)):
            rows = [bad if t == "T4" else row(t) for t in TASKS]
            res = v2r_ceiling.check(rows, TASKS)
            self.assertTrue(res["ok"], res["problems"])
            self.assertEqual(res["defects"], 1)

    def test_no_defect_fails(self):
        res = v2r_ceiling.check([row(t) for t in TASKS], TASKS)
        self.assertFalse(res["ok"])
        self.assertIn("易しすぎる", res["problems"][0])

    def test_an_incomplete_run_fails_even_with_defects(self):
        rows = [row(t, accepted=False) for t in TASKS[:5]]
        res = v2r_ceiling.check(rows, TASKS)
        self.assertFalse(res["ok"])
        self.assertTrue(any("T6" in p for p in res["problems"]))
        res = v2r_ceiling.check([row(t, accepted=False) for t in TASKS] + [row("T3")], TASKS)
        self.assertFalse(res["ok"])
        self.assertTrue(any("重なった" in p for p in res["problems"]))

    def test_a_vacuous_property_blocks_the_verdict(self):
        """空虚は測定器の故障の疑いなので、欠陥として数えて合格にしない（v2r-dry-01 の誤り。原則 P2）。"""
        rows = [row(t, accepted=(t != "T1"), vacuous=("P-T6-01",) if t == "T6" else ()) for t in TASKS]
        res = v2r_ceiling.check(rows, TASKS)
        self.assertFalse(res["ok"])
        self.assertIn("空虚な性質があるので天井を判定しない", res["problems"][0])
        self.assertIn("P-T6-01", v2r_ceiling.render(res, "v2r-dry-03"))

    def test_an_accepted_ceiling_passes_only_a_healthy_run(self):
        """§15.13（2026-09-30 の操縦士の決定）：天井は難易度のベースラインとして記録する。測定器の健全性は緩めない。"""
        res = v2r_ceiling.check([row(t) for t in TASKS], TASKS, accept_ceiling=True)
        self.assertTrue(res["ok"])
        self.assertTrue(res["ceiling"])
        self.assertIn("ベースライン", v2r_ceiling.render(res, "v2r-dry-03"))
        rows = [row(t, vacuous=("P-T6-01",) if t == "T6" else ()) for t in TASKS]
        self.assertFalse(v2r_ceiling.check(rows, TASKS, accept_ceiling=True)["ok"], "空虚な性質は不合格のまま")
        self.assertFalse(v2r_ceiling.check([row(t) for t in TASKS[:9]], TASKS, accept_ceiling=True)["ok"],
                         "行の欠けは不合格のまま")
        res = v2r_ceiling.check([row(t, accepted=(t != "T2")) for t in TASKS], TASKS, accept_ceiling=True)
        self.assertTrue(res["ok"] and not res["ceiling"], "欠陥があれば天井ではない")

    def test_the_cli_reads_the_a0_metrics(self):
        with tempfile.TemporaryDirectory() as d:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(v2r_ceiling.main(["--run-id", "v2r-dry-01", "--out-root", d]), 1)
            p = Path(d) / "v2r-dry-01" / "A0"
            p.mkdir(parents=True)
            (p / "metrics.jsonl").write_text(
                "".join(json.dumps(row(t, accepted=(t != "T7"))) + "\n" for t in TASKS), encoding="utf-8")
            with contextlib.redirect_stdout(out):
                self.assertEqual(v2r_ceiling.main(["--run-id", "v2r-dry-01", "--out-root", d]), 0)
        self.assertIn("metrics.jsonl がありません", out.getvalue())
        self.assertTrue(out.getvalue().rstrip().endswith("合格"))


if __name__ == "__main__":
    unittest.main()
