"""v2r の記録のアーカイブと照合・変異の死滅率・集計（docs/design/v2r_protocol.md §7・§8・§11）。

    python -m unittest tests.test_v2r_tools

**なぜ要るか**: 第三者が Release から取得して、改ざん・欠け・余分が無いことを sha256 で確かめ、集計を再現できること。
受入の合格が甘いテストの見せかけでないことを、変異の死滅率で示すこと。効果と費用の差を、ばらつきと並べて判定すること。
"""
import gzip
import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "experiments" / "v2r" / "tools"))

import mutation  # noqa: E402
import pack  # noqa: E402
import propgen  # noqa: E402
import verify  # noqa: E402
from ab import v2r_report  # noqa: E402

PRICES = {"input_per_mtok": 0.75, "output_per_mtok": 3.75, "cache_read_per_mtok": 0.075}


class PackVerify(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        out = self.tmp / "out"
        for cond in ("A0", "B"):
            d = out / "v2r-run-01" / cond
            (d / "T1" / "bin").mkdir(parents=True)
            (d / "metrics.jsonl").write_text('{"task": "T1"}\n', encoding="utf-8")
            (d / "T1_a1.implementer.log").write_text("# prompt\n全文\n\n# stdout\n{}\n\n# stderr\n", encoding="utf-8")
            (d / "T1" / "T1.pipeline.json").write_text("{}", encoding="utf-8")
            (d / "T1" / "bin" / "x.json").write_text("{}", encoding="utf-8")
            (d / "T1" / "x.dll").write_bytes(b"MZ")
        (out / "v2r-run.env.json").write_text('{"problems": []}', encoding="utf-8")
        self.out = out

    def pack(self, dest):
        return pack.pack("v2r-run", self.out, ["v2r-run-01"], dest, [self.out / "v2r-run.env.json"])

    def test_round_trip_passes_and_extracts(self):
        archive, manifest, n = self.pack(self.tmp / "rel")
        self.assertEqual(n, 7, "3 ファイル × 2 条件と env.json。bin の下と .dll は入れない")
        found, count = verify.verify(archive, manifest, extract=self.tmp / "x")
        self.assertEqual((found, count), ([], 7))
        self.assertEqual((self.tmp / "x" / "v2r-run-01" / "A0" / "T1_a1.implementer.log").read_text(encoding="utf-8"),
                         "# prompt\n全文\n\n# stdout\n{}\n\n# stderr\n")

    def test_the_archive_is_deterministic(self):
        a1, m1, _ = self.pack(self.tmp / "r1")
        a2, m2, _ = self.pack(self.tmp / "r2")
        self.assertEqual(a1.read_bytes(), a2.read_bytes())
        self.assertEqual(m1.read_text(encoding="utf-8"), m2.read_text(encoding="utf-8"))

    def rebuild(self, archive, change):
        """中身を変えたアーカイブ（マニフェストはそのまま）。"""
        with tarfile.open(archive, "r:gz") as tar:
            items = [(m.name, tar.extractfile(m).read()) for m in tar.getmembers()]
        items = change(items)
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w") as tar:
            for name, data in items:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        archive.write_bytes(gzip.compress(raw.getvalue()))

    def test_tampering_missing_and_extra_files_are_found(self):
        archive, manifest, _ = self.pack(self.tmp / "rel")
        cases = {
            "改ざん": lambda items: [(n, b"x" if n.endswith("metrics.jsonl") else d) for n, d in items],
            "欠け": lambda items: [(n, d) for n, d in items if not n.startswith("env/")],
            "余分": lambda items: items + [("v2r-run-01/B/extra.log", b"?")],
            "危ないパス": lambda items: items + [("../escape.log", b"?")],
        }
        original = archive.read_bytes()
        for label, change in cases.items():
            with self.subTest(label):
                archive.write_bytes(original)
                self.rebuild(archive, change)
                found, _ = verify.verify(archive, manifest)
                self.assertTrue(found)
                self.assertTrue(any("アーカイブの sha256" in f for f in found), "アーカイブ自身の sha256 も違う")
        archive.write_bytes(original)
        broken = manifest.read_text(encoding="utf-8").replace("  v2r-run-01/A0/metrics.jsonl", "  v2r-run-01/A0/other.jsonl")
        manifest.write_text(broken, encoding="utf-8")
        found, _ = verify.verify(archive, manifest)
        self.assertTrue(any("マニフェストに無い" in f for f in found) and any("アーカイブに無い" in f for f in found))

    def test_nothing_is_extracted_when_verification_fails(self):
        archive, manifest, _ = self.pack(self.tmp / "rel")
        self.rebuild(archive, lambda items: [(n, b"x") for n, _ in items])
        found, _ = verify.verify(archive, manifest, extract=self.tmp / "x")
        self.assertTrue(found)
        self.assertFalse((self.tmp / "x").exists())


def report_with(statuses):
    return {"schemaVersion": "1", "files": {"GameState.cs": {"language": "cs", "source": "コード（数えない）",
                                                                "mutants": [{"id": str(i), "mutatorName": m, "status": s}
                                                                            for i, (m, s) in enumerate(statuses)]}}}


class Mutation(unittest.TestCase):
    def test_score_follows_the_stryker_definition(self):
        st = {"Killed": 7, "Timeout": 1, "Survived": 1, "NoCoverage": 1, "CompileError": 5, "Ignored": 3}
        self.assertEqual(mutation.score(st), 0.8, "（Killed＋Timeout）÷（それ＋Survived＋NoCoverage）")
        self.assertIsNone(mutation.score({"CompileError": 2}), "有効な変異が 0 なら測れていない")

    def test_tasks_below_the_threshold_are_flagged(self):
        good = report_with([("Equality", "Killed")] * 9 + [("Arithmetic", "Survived")])
        bad = report_with([("Equality", "Killed")] * 7 + [("Arithmetic", "Survived")] * 3)
        rows = mutation.summarize({"T2": bad, "T1": good, "T3": report_with([("Block", "CompileError")])})
        self.assertEqual([(r["task"], r["score"], r["oracle_ok"]) for r in rows],
                         [("T1", 0.9, True), ("T2", 0.7, False), ("T3", None, False)])
        self.assertEqual(rows[0]["mutators"], {"Arithmetic": 1, "Equality": 9})
        text = mutation.table(rows)
        self.assertIn("オラクル不合格", text)
        self.assertNotIn("コード（数えない）", text, "コードは出さない")

    def test_an_unknown_status_is_not_silently_counted(self):
        rows = mutation.summarize({"T1": report_with([("E", "Killed")] * 9 + [("E", "Mystery")])})
        self.assertFalse(rows[0]["oracle_ok"])
        self.assertEqual(rows[0]["unknown_status"], ["Mystery"])

    def test_the_runner_mutates_only_the_given_files_and_passes_hidden_seeds(self):
        seen = {}

        def runner(args, **kw):
            seen["args"], seen["env"] = args, kw["env"]
            out = Path(args[args.index("--output") + 1]) / "reports"
            out.mkdir(parents=True)
            (out / "mutation-report.json").write_text("{}", encoding="utf-8")
            return type("R", (), {"returncode": 0})()
        with tempfile.TemporaryDirectory() as d:
            path = mutation.run(d, "t.csproj", ["Game/Assets/Core/GameState.cs"], Path(d) / "out", seeds=[3, 5],
                                runner=runner, project="Core.csproj")
            self.assertTrue(path and path.name == "mutation-report.json")
        self.assertEqual(seen["args"][:2], ["dotnet", "stryker"])
        self.assertIn("--project", seen["args"])
        self.assertEqual(seen["args"][seen["args"].index("--project") + 1], "Core.csproj")
        self.assertIn("**/GameState.cs", seen["args"])
        self.assertEqual(seen["env"][mutation.HIDDEN_ENV], "3,5")
        self.assertEqual(mutation.HIDDEN_ENV, propgen.HIDDEN_ENV)


def row(run, cond, task, idx, accepted=True, p2p=0, inp=1000):
    return {"run_id": run, "condition": cond, "task": task, "index": idx, "accepted": accepted, "p2p_broken": p2p,
            "invariants": {"failures": 0}, "tokens": {"input": inp, "output": 0, "cache_read": 0}}


class Report(unittest.TestCase):
    def test_bootstrap_is_deterministic_and_reports_no_difference_when_zero_is_inside(self):
        ps = [(1, 0), (0, 1), (1, 1), (0, 0)] * 5
        a, b = v2r_report.bootstrap(ps), v2r_report.bootstrap(ps)
        self.assertEqual(a, b, "乱数の種を固定")
        self.assertFalse(a["significant"])
        self.assertIn("差があるとは言えない", v2r_report.verdict(a))

    def test_a_clear_difference_is_significant(self):
        e = v2r_report.bootstrap([(0, 1)] * 18 + [(1, 1)] * 2, stat="mean")
        self.assertTrue(e["significant"])
        self.assertLess(e["hi"], 0)
        self.assertAlmostEqual(e["point"], -0.9)

    def test_no_pairs(self):
        self.assertEqual(v2r_report.bootstrap([])["n"], 0)

    def test_pairs_match_the_same_run_and_task(self):
        rows = [row("r1", "A1", "T1", 1), row("r1", "A0", "T1", 1, accepted=False), row("r1", "A1", "T2", 2),
                row("r2", "A0", "T2", 2)]
        self.assertEqual(v2r_report.pairs(rows, "A1", "A0", v2r_report.defect), [(0, 1)])

    def test_summary_has_the_effects_and_the_sensitivity_table(self):
        rows = []
        for r in ("r1", "r2", "r3"):
            for i, t in enumerate(("T1", "T2", "T3"), start=1):
                rows += [row(r, "A0", t, i, accepted=False, p2p=1, inp=1000), row(r, "A1", t, i, inp=3000),
                         row(r, "B-G", t, i, accepted=False, inp=900), row(r, "B", t, i, inp=2500)]
        text = v2r_report.summarize(rows, {"prices": PRICES, "fixed": {"shared": {"usd": 1.0}}}, (0, 0.01, 1000),
                                    stat="mean")
        self.assertIn("| H1 門 | A1 − A0、B − B-G | defect | 18 | -1.0000 |", text)
        self.assertIn("差がある", text)
        self.assertIn("| 1000 | — |", text, "大きな F_B_only では回収しない")
        self.assertIn("F_B_only", text)

    def test_default_statistics_are_mean_for_defects_and_median_for_cost(self):
        """§11.2 の改定（game-harness#111 で提案、承認）：欠陥と P2P は差の平均（発生率の差）、費用は差の中央値。"""
        self.assertEqual(v2r_report.STATS, {"defect": "mean", "p2p": "mean", "usd": "median"})
        rows = []
        for r in ("r1", "r2"):
            for i, t in enumerate(("T1", "T2", "T3", "T4"), start=1):
                bad = i == 1   # 4 タスクのうち 1 つだけ、門なしで欠陥
                rows += [row(r, "A0", t, i, accepted=not bad), row(r, "A1", t, i),
                         row(r, "B-G", t, i, accepted=not bad), row(r, "B", t, i)]
        text = v2r_report.summarize(rows, {"prices": PRICES, "fixed": {"shared": {"usd": 1.0}}}, (0,))
        self.assertIn("| H1 門 | A1 − A0、B − B-G | defect | 16 | -0.2500 |", text, "平均なら発生率の差 −0.25")
        self.assertIn("defect は差の平均", text)
        self.assertIn("usd は差の中央値", text)

    def test_sensitivity_break_even_grows_with_f_b_only(self):
        rows = [row("r1", "A0", f"T{i}", i, inp=4000 * i) for i in range(1, 6)] + \
               [row("r1", "B", f"T{i}", i, inp=4000) for i in range(1, 6)]
        table = v2r_report.sensitivity(rows, PRICES, 1.0, (0, 0.005, 0.02))
        ns = [n for _, n, _ in table]
        self.assertEqual(ns[0], 1, "F_B_only = 0 なら最初のタスクから追いつく（同額）")
        self.assertTrue(ns[1] is not None and ns[1] > ns[0])
        self.assertTrue(ns[2] is None or ns[2] >= ns[1])


if __name__ == "__main__":
    unittest.main()
