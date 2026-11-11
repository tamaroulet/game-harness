"""比べの本番の集計（harness/chain_compare.py。v3 §15 の 11-08 その 4 の決め 5・6）：累積の完了と保ちの通過の違い、予定の段と
記録の無い段、置き場の 2 つの形、項目の差、符号の入れ替えの検定（厳密と無作為）、Holm、4 分割表、落ちの種類、終えたと言った段、
費用の和、命令の入口。"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import bench_pair  # noqa: E402
import chain_compare as cc  # noqa: E402
import hline_base  # noqa: E402


def row(k, ok=True, **kw):
    """段の行。ok なら new と old が全部通る。"""
    base = {"k": k, "invalid": False, "new": [2, 2] if ok else [1, 2], "old": [5, 5], "kept": [5, 5], "stop": "done"}
    return {**base, **kw}


def put(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    hline_base.write_json(path, doc)


def run(tag, rows_by_base, arm=None, planned=None, set_="dev"):
    return {"tag": tag, "set": set_, "arm": arm, "rev": "r", "path": tag,
            "bases": {b: {"planned": planned, "stopped": None, "rows": {r["k"]: r for r in rows}} for b, rows in rows_by_base.items()}}


class Complete(unittest.TestCase):
    def test_an_old_requirement_broken_earlier_still_counts(self):
        broken = row(3, old=[4, 5], kept=[4, 4])   # 前の段で壊れた 1 件は kept の分母から消えている
        self.assertTrue(bench_pair.passed(broken))
        self.assertFalse(cc.complete(broken))

    def test_invalid_missing_and_partial_rows_are_not_complete(self):
        for r in (row(1, invalid="REQ-22"), None, row(1, ok=False), row(1, old=None)):
            self.assertFalse(cc.complete(r))
        self.assertTrue(cc.complete(row(1)))


class Plan(unittest.TestCase):
    def test_planned_stages_win_and_otherwise_the_highest_seen_stage(self):
        a = run("a", {"B07": [row(1), row(2)]}, planned=6)
        b = run("b", {"B07": [row(1)], "B08": [row(1), row(2), row(3)]})
        self.assertEqual(cc.plan([a, b]), [("B07", k) for k in range(1, 7)] + [("B08", k) for k in (1, 2, 3)])

    def test_missing_stages_count_as_not_complete(self):
        a = run("a", {"B07": [row(1)]}, planned=3)
        doc = cc.compare([a], [run("b", {"B07": [row(1), row(2), row(3)]})], draws=10)
        self.assertEqual((doc["a"]["quality"]["complete"], doc["b"]["quality"]["complete"], doc["a"]["health"]["missing"]), (1, 3, 2))
        self.assertEqual(doc["a"]["health"]["failures"]["missing"], 2)


class Load(unittest.TestCase):
    def test_the_dev_and_the_private_layouts_and_bad_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dev, held, mixed = root / "dev", root / "held", root / "mixed"
            put(dev / "B07.json", {"tag": "t", "set": "dev", "base": "B07", "planned": 6, "stages": [row(1)]})
            put(held / "B05.json", {"tag": "h", "set": "held", "base": "B05", "totals": {}})   # 公開の合計だけ
            put(held / "B05" / "public_full.json", {"tag": "h", "set": "held", "base": "B05", "stages": [row(1), row(2)]})
            put(mixed / "B07.json", {"tag": "x", "base": "B07", "stages": []})
            put(mixed / "B08.json", {"tag": "y", "base": "B08", "stages": []})
            got = cc.load_run(dev)
            self.assertEqual((got["tag"], got["bases"]["B07"]["planned"], list(got["bases"]["B07"]["rows"])), ("t", 6, [1]))
            self.assertEqual(list(cc.load_run(held)["bases"]["B05"]["rows"]), [1, 2])
            for bad in (mixed, root / "nothing"):
                with self.assertRaises(ValueError):
                    cc.load_run(bad)


class Tests(unittest.TestCase):
    def test_exact_sign_flips(self):
        self.assertEqual(cc.signflip([1, 1, 1])["greater"], 1 / 8)
        self.assertEqual({k: cc.signflip([1, -1])[k] for k in ("greater", "less", "two")}, {"greater": 0.75, "less": 0.75, "two": 1.0})
        self.assertEqual(cc.signflip([0, 0, 1])["greater"], 0.5)   # 差 0 は入れ替えに数えない
        got = cc.signflip([Fraction(1, 2), 1])
        self.assertEqual((got["greater"], got["mean"], got["method"]), (0.25, 0.75, "exact"))
        self.assertIsNone(cc.signflip([]))

    def test_large_differences_are_sampled_with_the_seed(self):
        got = cc.signflip([10 ** 6] * 25, draws=200, seed=3)
        self.assertEqual((got["greater"], got["method"]), (1 / 201, "sampled 200（seed 3）"))
        self.assertEqual(got, cc.signflip([10 ** 6] * 25, draws=200, seed=3))

    def test_holm(self):
        self.assertEqual(cc.holm({"quality": 0.01, "cost": 0.04}), {"quality": 0.02, "cost": 0.04})
        self.assertEqual(cc.holm({"quality": 0.03, "cost": 0.02}), {"cost": 0.04, "quality": 0.04})
        self.assertEqual(cc.holm({"quality": None, "cost": 0.5}), {"cost": 0.5})


class Compare(unittest.TestCase):
    def setUp(self):
        cost = lambda n: {"non_cache": n, "seconds": 100, "agent_seconds": 60, "wait_seconds": 10}  # noqa: E731
        self.a = run("a1", {"B07": [row(1, cost=cost(10), gate=None), row(2, ok=False, cost=cost(20), gate=None)]}, arm="solo")
        self.b = [run(f"b{i}", {"B07": [row(1, cost=cost(30), gate={"passed": True, "first": True, "tries": 1}),
                                        row(2, ok=i == 1, cost=cost(40), gate={"passed": True, "first": False, "tries": 2})]})
                  for i in (1, 2)]

    def test_pairs_tables_bases_and_costs(self):
        doc = cc.compare([self.a], self.b, draws=10)
        self.assertEqual([(p["k"], p["quality"], p["cost"]) for p in doc["pairs"]], [(1, 0.0, 20.0), (2, 0.5, 20.0)])
        self.assertEqual([t["a"] + t["b"] for t in doc["four_way"]["pairs"]], ["a1b1", "a1b2"])   # 走りの数が違えば全部の組み合わせ
        self.assertEqual(doc["four_way"]["sum"], {"both": 2, "b_only": 1, "a_only": 0, "neither": 1})
        self.assertEqual(doc["per_base"]["B07"], {"a": {"complete": 1, "of": 2, "non_cache": 30}, "b": {"complete": 3, "of": 4, "non_cache": 140}})
        self.assertEqual(doc["b"]["cost"]["sums"]["harness_seconds"], 120.0)
        self.assertEqual((doc["a"]["cost"]["non_cache_per_stage"], doc["b"]["stability"]), (15.0, {"all": 1, "none": 0, "mixed": 1}))
        self.assertEqual(doc["verdicts"]["cost_both_complete"], {"items": 1, "mean": 20.0})
        self.assertEqual(doc["b"]["mechanism"], {"stages": 4, "passed": 4, "first": 2, "retried": 2, "extended": 0, "full_suite": 0})

    def test_claimed_but_broken_and_the_failure_kinds(self):
        doc = cc.compare([self.a], self.b, draws=10)
        self.assertEqual({k: doc["b"]["health"][k] for k in ("claimed", "claimed_broken", "claimed_unknown")},
                         {"claimed": 4, "claimed_broken": 1, "claimed_unknown": 0})
        self.assertEqual((doc["a"]["health"]["claimed"], doc["a"]["health"]["claimed_broken"]), (2, 1))
        kinds = [cc.failure(r) for r in (None, row(1, invalid="REQ-11"), row(1, violations=1), row(1, stop="time"),
                                         row(1, gate={"passed": False}), row(1, ok=False), row(1, old=[4, 5]))]
        self.assertEqual(kinds, ["missing", "invalid", "invalid", "limit", "gate", "new", "old"])
        self.assertIsNone(cc.claimed({"k": 1, "stop": "done"}))   # 門の欄の無い古い行

    def test_equal_run_counts_pair_by_position_and_warn_on_arm_names(self):
        doc = cc.compare(self.b, self.b, draws=10)
        self.assertEqual([t["a"] + t["b"] for t in doc["four_way"]["pairs"]], ["b1b1", "b2b2"])
        self.assertTrue(any("腕の名" in w for w in doc["warnings"]))
        self.assertEqual(cc.compare([self.a], self.b, draws=10)["warnings"], [])


class Main(unittest.TestCase):
    def test_writes_the_json_and_prints_the_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for tag, arm in (("a1", "solo"), ("b1", None)):
                put(root / tag / "B07.json", {"tag": tag, "set": "dev", "base": "B07", **({"arm": arm} if arm else {}),
                                                                 "stages": [row(1), row(2, ok=tag == "b1")]})
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out):
                code = cc.main(["--a", str(root / "a1"), "--b", str(root / "b1"), "--json", str(root / "cmp.json")])
            self.assertEqual(code, 0)
            self.assertIn("累積の完了：A 1/2（50.0%）・B 2/2（100.0%）", out.getvalue())
            self.assertEqual(json.loads((root / "cmp.json").read_text(encoding="utf-8"))["four_way"]["sum"]["b_only"], 1)
            with redirect_stderr(err):
                self.assertEqual(cc.main(["--a", str(root / "none"), "--b", str(root / "b1")]), 2)
            self.assertIn("ABORT", err.getvalue())


if __name__ == "__main__":
    unittest.main()
