"""改善ループの判定（harness/cycle_judge.py）の検査。L6 の公開の集計に当てて、手で出した結果（v3 §15）と同じになることも見る。

    python -m unittest tests.test_cycle_judge -v
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(ROOT / "harness")]
import bench_pair  # noqa: E402
import cycle_judge as cj  # noqa: E402

GUARD = cj.settings()["guard"]


def row(k=1, new=(2, 2), kept=(3, 3), invalid=False, tokens=100):
    return {"k": k, "new": list(new), "kept": list(kept), "invalid": invalid, "tokens": tokens}


OK, NG = row(), row(new=(1, 2))
KEY = ("B01", 1)


class L6Records(unittest.TestCase):
    """公開の集計は Git にあるので、CI でも当てられる。"""

    def rows(self, tag):
        return cj.public_rows(ROOT, tag)

    def test_the_stage_counts_match_the_hand_counts(self):
        for tag, passed, new, kept, tokens in (("v0-r1", 13, [97, 124], [351, 352], 32549105), ("v0-r2", 16, [121, 124], [402, 402], 27710799),
                                               ("l6n-dev", 16, [121, 124], [404, 404], 23739632)):
            got = cj.totals(self.rows(tag))
            self.assertEqual((got["passed"], got["new"], got["kept"], got["tokens"], got["stages"]), (passed, new, kept, tokens, 18), tag)

    def test_the_two_v0_runs_agree_as_counted_by_hand(self):
        classes = cj.classify(self.rows("v0-r1"), self.rows("v0-r2"))
        counts = {c: sum(1 for v in classes.values() if v == c) for c in (cj.PASS, cj.FAIL, cj.FLAP, cj.UNKNOWN)}
        self.assertEqual(counts, {cj.PASS: 13, cj.FAIL: 2, cj.FLAP: 3, cj.UNKNOWN: 0})   # v3 §7-7 の 3

    def test_the_redone_pair_counts_nothing_fixed_or_broken(self):
        classes = cj.classify(self.rows("v0-r1"), self.rows("v0-r2"))
        pairs = [(self.rows(f"{p}-old"), self.rows(f"{p}-new")) for p in ("l6p2-r1", "l6p2-r2")]
        picked = cj.targets({("B08", 4)}, classes)
        self.assertEqual(picked, [("B08", 4)])
        got = cj.pair_counts(picked, classes, pairs)
        self.assertEqual((got["fixed"], got["broke"], got["same"], got["ok"]), (0, 0, 2, False))   # v3 §15 の 11-06

    def test_the_new_version_is_inside_the_dev_line(self):
        base = [cj.totals(self.rows(t)) for t in ("v0-r1", "v0-r2")]
        got = cj.guard_dev(cj.totals(self.rows("l6n-dev")), base, GUARD)
        self.assertEqual((got["ok"], got["floor"]), (True, 12))


class Classify(unittest.TestCase):
    def one(self, a, b):
        return cj.classify({KEY: a} if a else {}, {KEY: b} if b else {})[KEY]

    def test_two_runs(self):
        self.assertEqual(self.one(OK, OK), cj.PASS)
        self.assertEqual(self.one(NG, NG), cj.FAIL)
        self.assertEqual(self.one(OK, NG), cj.FLAP)

    def test_a_stage_with_no_clean_result_is_not_agreed(self):
        tainted = row(invalid="tainted")
        self.assertEqual(self.one(tainted, OK), cj.UNKNOWN)   # 前のステージが無効の行は、自分の点が満点でも結果が分からない
        self.assertEqual(self.one(row(invalid="stray"), NG), cj.UNKNOWN)
        self.assertEqual(self.one(None, OK), cj.UNKNOWN)

    def test_a_row_without_kept_does_not_pass(self):
        self.assertEqual(cj.outcome({"k": 1, "new": [2, 2], "invalid": False}), cj.FAIL)


class Targets(unittest.TestCase):
    def test_only_agreed_stages_named_by_the_proposal(self):
        classes = {("B01", 2): cj.PASS, ("B01", 1): cj.FAIL, ("B02", 1): cj.FLAP, ("B03", 1): cj.UNKNOWN}
        named = {("B02", 1), ("B03", 1), ("B01", 2), ("B01", 1), ("B09", 9)}
        self.assertEqual(cj.targets(named, classes), [("B01", 1), ("B01", 2)])
        self.assertEqual(cj.targets({("B02", 1)}, classes), [])


class PairCounts(unittest.TestCase):
    def counts(self, want, old, new):
        return cj.pair_counts([KEY], {KEY: want}, [({KEY: old} if old else {}, {KEY: new} if new else {})])

    def test_fixed_needs_baseline_fail_old_fail_new_pass(self):
        got = self.counts(cj.FAIL, NG, OK)
        self.assertEqual((got["fixed"], got["broke"], got["ok"]), (1, 0, True))

    def test_broke_needs_baseline_pass_old_pass_new_fail(self):
        got = self.counts(cj.PASS, OK, NG)
        self.assertEqual((got["fixed"], got["broke"], got["ok"]), (0, 1, False))

    def test_old_different_from_the_baseline_is_not_counted(self):
        for want, old, new in ((cj.FAIL, OK, OK), (cj.PASS, NG, OK)):
            got = self.counts(want, old, new)
            self.assertEqual((got["fixed"], got["broke"], got["ok"]), (0, 0, False))
            self.assertEqual(got["skipped"][0]["why"], "旧版が基準と違う")

    def test_a_missing_or_invalid_row_is_not_counted(self):
        for old, new in ((None, OK), (NG, row(invalid="stray"))):
            got = self.counts(cj.FAIL, old, new)
            self.assertEqual((got["fixed"], got["same"]), (0, 0))
            self.assertEqual(got["skipped"][0]["why"], "対の行が欠けるか無効")

    def test_no_change_is_same_and_does_not_go_on(self):
        got = self.counts(cj.FAIL, NG, NG)
        self.assertEqual((got["same"], got["ok"]), (1, False))

    def test_fixed_must_outnumber_broke_over_all_pairs(self):
        k2 = ("B01", 2)
        classes = {KEY: cj.FAIL, k2: cj.PASS}
        pair = ({KEY: NG, k2: OK}, {KEY: OK, k2: NG})
        self.assertFalse(cj.pair_counts([KEY, k2], classes, [pair])["ok"])   # 1 対 1 は進まない
        self.assertTrue(cj.pair_counts([KEY], classes, [pair, pair])["ok"])
        self.assertEqual(cj.pair_counts([KEY], classes, [pair, pair])["fixed"], 2)   # 2 回ずつ走らせたら 2 回数える


class Guard(unittest.TestCase):
    def run_of(self, passed=16, new=(120, 124), kept=(400, 400), tokens=20_000_000):
        return {"passed": passed, "new": list(new), "kept": list(kept), "tokens": tokens, "stages": 18, "invalid": 0}

    def base(self, *passed, tokens=20_000_000):
        return [self.run_of(passed=p, tokens=tokens) for p in passed]

    def test_dev_allows_one_stage_below_the_lower_baseline(self):
        base = self.base(16, 14)
        self.assertTrue(cj.guard_dev(self.run_of(passed=13), base, GUARD)["ok"])
        got = cj.guard_dev(self.run_of(passed=12), base, GUARD)
        self.assertFalse(got["ok"])
        self.assertIn("通ったステージ 12", got["why"][0])

    def test_kept_below_98_percent_fails_on_both_sets(self):
        run = self.run_of(kept=(97, 100))
        self.assertFalse(cj.guard_dev(run, self.base(16, 16), GUARD)["ok"])
        self.assertFalse(cj.guard_held(self.run_of(passed=14, kept=(97, 100)), self.base(12, 14), GUARD)["ok"])
        self.assertTrue(cj.guard_dev(self.run_of(kept=(98, 100)), self.base(16, 16), GUARD)["ok"])

    def test_cost_is_measured_against_the_mean_of_the_baselines(self):
        base = [self.run_of(tokens=10_000_000), self.run_of(tokens=30_000_000)]
        self.assertTrue(cj.guard_dev(self.run_of(tokens=30_000_000), base, GUARD)["ok"])
        self.assertFalse(cj.guard_dev(self.run_of(tokens=30_000_001), base, GUARD)["ok"])

    def test_held_line_and_its_fallback(self):
        base = self.base(12, 14)
        self.assertTrue(cj.guard_held(self.run_of(passed=11, new=(70, 96)), base, GUARD)["ok"])
        self.assertTrue(cj.guard_held(self.run_of(passed=10, new=(80, 96), kept=(98, 100)), base, GUARD)["ok"])
        self.assertFalse(cj.guard_held(self.run_of(passed=10, new=(79, 96)), base, GUARD)["ok"])
        self.assertFalse(cj.guard_held(self.run_of(passed=9, new=(90, 96)), base, GUARD)["ok"])

    def test_an_empty_kept_is_not_a_pass(self):
        self.assertFalse(cj.guard_dev(self.run_of(kept=(0, 0)), self.base(16, 16), GUARD)["ok"])


class PrivateRows(unittest.TestCase):
    def test_held_rows_come_from_the_private_file(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        for base in ("B05", "B06"):
            d = tmp / "t1" / base
            d.mkdir(parents=True)
            (d / "public_full.json").write_text(json.dumps({"base": base, "stages": [row(k=1), row(k=2, new=(0, 2))]}), encoding="utf-8")
        rows = cj.private_rows(tmp, "t1")
        self.assertEqual(sorted(rows), [("B05", 1), ("B05", 2), ("B06", 1), ("B06", 2)])
        self.assertEqual(cj.totals(rows)["passed"], 2)

    def test_passed_is_the_one_rule_in_bench_pair(self):
        self.assertIs(cj.bench_pair.passed, bench_pair.passed)


class Settings(unittest.TestCase):
    def test_the_lines_are_the_approved_ones(self):
        cfg = cj.settings()
        self.assertEqual(cfg["guard"]["dev_margin_stages"], 1)
        self.assertEqual(cfg["guard"]["kept_min"], 0.98)
        self.assertEqual(cfg["guard"]["cost_ratio"], 1.5)
        self.assertEqual(cfg["guard"]["held_passed"], 11)
        self.assertEqual(cfg["guard"]["held_fallback"], {"passed": 10, "new": 80, "kept_min": 0.98})
        self.assertEqual(set(cfg["sets"]), {"dev", "held"})


if __name__ == "__main__":
    unittest.main()
