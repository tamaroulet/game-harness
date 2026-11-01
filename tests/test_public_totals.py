"""dev でない組（保留・予備・不明）の公開の集計を合計だけにする試験（harness/bench_pair.py の publish・totals・passed と、
harness/chain_score.py・harness/bench_pair.py の finish）。一時ディレクトリの偽のクローン・束・記録だけで書き、工廠は走らせない。
"""
import argparse
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "harness")]
import bench_pair  # noqa: E402
import bench_run  # noqa: E402
import chain_score  # noqa: E402
import hline_base  # noqa: E402
from test_bench_pair import TestFinish, init_repo  # noqa: E402

SUMMARY_KEYS = {"tag", "set", "base", "rev", "stopped", "totals"}


def row(k, invalid=False, new=(2, 2), kept=(3, 3), tokens=10):
    r = {"k": k, "run_id": f"r{k}", "attempts": 1, "invalid": invalid, "reason": None, "stop": "done", "tries": 1,
         "tokens": tokens, "new": list(new), "old": [3, 3], "new_at_start": [0, 2], "violations": 0}
    if kept is not None:
        r["kept"] = list(kept)
    return r


def public_of(set_name, rows, **extra):
    doc = {"tag": "t", "base": "B07", "rev": "abc", "stopped": None, "s0": [1, 1], "stages": rows, **extra}
    if set_name is not ...:
        doc["set"] = set_name
    return doc


class Passed(unittest.TestCase):
    def test_only_a_valid_row_with_all_new_and_all_kept_passes(self):
        self.assertTrue(bench_pair.passed(row(1)))
        for name, r in (("invalid", row(1, invalid="REQ-22")), ("tainted", row(1, invalid="tainted")),
                        ("kept missing", row(1, kept=None)), ("kept partial", row(1, kept=(2, 3))),
                        ("new missing", {k: v for k, v in row(1).items() if k != "new"}), ("new partial", row(1, new=(1, 2))),
                        ("rate of a wrong shape", row(1, new=(2, 2, 2)))):
            with self.subTest(name):
                self.assertFalse(bench_pair.passed(r))

    def test_totals_count_stages_passed_invalid_and_tokens(self):
        rows = [row(1), row(2, kept=None), row(3, invalid="REQ-11", tokens=None), row(4, new=(0, 2), tokens=True),
                row(5, invalid="tainted", tokens=5)]
        self.assertEqual(bench_pair.totals(rows), {"stages": 5, "passed": 1, "invalid": 2, "tokens": 25})
        self.assertEqual(bench_pair.totals([]), {"stages": 0, "passed": 0, "invalid": 0, "tokens": 0})


class Publish(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.dest, self.priv = self.root / "pub" / "B07.json", self.root / "priv"
        self.dest.parent.mkdir()
        self.priv.mkdir()

    def test_held_reserve_unknown_and_missing_set_publish_only_totals(self):
        rows = [row(1), row(2, invalid="REQ-22")]
        for name in ("held", "reserve", "other", None, ...):
            with self.subTest(name):
                full = public_of(name, rows)
                bench_pair.publish(self.dest, full, self.priv)
                got = bench_run.read_json(self.dest, {})
                self.assertEqual(set(got), SUMMARY_KEYS)
                self.assertNotIn("stages", got)
                self.assertEqual(got["totals"], {"stages": 2, "passed": 1, "invalid": 1, "tokens": 20})
                self.assertEqual((got["tag"], got["base"], got["rev"], got["set"]), ("t", "B07", "abc", None if name is ... else name))
                self.assertEqual(bench_run.read_json(self.priv / "public_full.json", {}), full)

    def test_dev_is_written_as_before_and_nothing_goes_to_priv(self):
        full = public_of("dev", [row(1)])
        bench_pair.publish(self.dest, full, self.priv)
        expected = self.root / "expected.json"
        hline_base.write_json(expected, full)
        self.assertEqual(self.dest.read_bytes(), expected.read_bytes())
        self.assertFalse((self.priv / "public_full.json").exists())


class ChainScoreFinish(unittest.TestCase):
    """chain_score.finish の置き場と形（採点は finish_chain の差し替えで固める）。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo, self.out, self.chains = self.root / "clone", self.root / "out", self.root / "oracles" / "chains"
        self.hout = self.root / "hout"
        init_repo(self.repo, {"config/hline.json": "{}\n"})
        hline_base.write_json(self.repo / "config" / "hline.json", {"out": str(self.hout), "oracles": str(self.chains.parent)})
        self.rows = [row(1), row(2, kept=(0, 1)), row(3, invalid="REQ-22", tokens=7)]

    def finish(self, tag, set_name):
        rec = {"tag": tag, "base": "B07", "package": "pkg", "rev": "abc", "stages": [], "stopped": None}
        if set_name is not ...:
            rec["set"] = set_name
        (self.out / "chains" / tag).mkdir(parents=True)
        hline_base.write_json(self.out / "chains" / tag / "B07.json", rec)
        ns = argparse.Namespace(tag=tag, repo=str(self.repo), chains=str(self.chains), out=str(self.out))
        def fake(r, out, chains, cfg, priv):   # 本物の finish_chain と同じく、非公開の置き場を作ってから集計を返す
            priv.mkdir(parents=True, exist_ok=True)
            return public_of(r.get("set"), self.rows, tag=r["tag"])

        with patch.object(chain_score, "finish_chain", side_effect=fake):
            self.assertEqual(chain_score.finish(ns), 0)
        return bench_run.read_json(self.repo / "bench" / "records" / "chains" / tag / "B07.json", {})

    def test_non_dev_sets_publish_totals_and_keep_the_full_form_under_oracles(self):
        for tag, name in (("h1", "held"), ("r1", "reserve"), ("n1", ...)):
            with self.subTest(name):
                got = self.finish(tag, name)
                self.assertEqual(set(got), SUMMARY_KEYS)
                self.assertEqual(got["totals"], {"stages": 3, "passed": 1, "invalid": 1, "tokens": 27})
                kept = bench_run.read_json(self.chains / "runs" / tag / "B07" / "public_full.json", {})
                self.assertEqual([r["k"] for r in kept["stages"]], [1, 2, 3])
                self.assertFalse((self.hout / "chains" / tag).exists())

    def test_dev_keeps_the_stage_rows(self):
        got = self.finish("d1", "dev")
        self.assertEqual(got, public_of("dev", self.rows, tag="d1"))
        self.assertFalse((self.hout / "chains" / "d1" / "B07" / "public_full.json").exists())


class PairFinish(unittest.TestCase):
    """bench_pair.finish の本物の採点（偽の束）で、held は合計だけ・dev は段ごとの行。"""

    setUp, stage, write, finish, public = (TestFinish.setUp, TestFinish.stage, TestFinish.write, TestFinish.finish,
                                           TestFinish.public)

    def valid_stage(self, k, run_id):
        """呼び出しの記録（モデルの欄の無い result.json）を置き、無効（REQ-22）にならない段。先端は f()=1。"""
        st = self.stage(k, run_id, 1)
        call = bench_run.run_dir(self.out, "B07", run_id) / "calls" / "1-implementer" / "result.json"
        call.parent.mkdir(parents=True)
        call.write_text("{}", encoding="utf-8")
        return st

    def record(self, tag, set_name):
        hline_base.write_json(self.repo / "config" / "hline.json", {**self.cfg, "implementer": {"model": "m"}})
        hidden = self.chains / "B07" / "s1" / "hidden" / "test_chain_B07_s1.py"   # f()=1 の先端で段 1 も段 0 も通る組
        hidden.write_text("import unittest\nfrom pkg.mod import f\nclass T(unittest.TestCase):\n"
                          "    def test_s1(self): self.assertIn(f(), (1, 2))\n", encoding="utf-8")
        s1, s2 = self.valid_stage(1, f"{tag}-s1"), self.valid_stage(2, f"{tag}-s2")   # 段 2 は new（f()=3）が落ちる
        s3 = dict(self.valid_stage(3, f"{tag}-s3"), invalid="REQ-22")
        rec = {"tag": tag, "base": "B07", "package": "pkg", "rev": self.base_rev, "from": "ref1", "pairs": [1, 2, 3],
               "stages": [s1, s2, s3], "skipped": [{"k": 4, "reason": "x"}], "stopped": None}
        if set_name is not ...:
            rec["set"] = set_name
        self.write(tag, "B07", rec)
        self.assertEqual(self.finish(tag), 0)
        return self.public(tag)

    def test_held_and_missing_set_publish_only_totals_with_from(self):
        for tag, name in (("p-held", "held"), ("p-none", ...)):
            with self.subTest(name):
                got = self.record(tag, name)
                self.assertEqual(set(got), SUMMARY_KEYS | {"from"})
                self.assertEqual(got["from"], "ref1")
                self.assertEqual(got["totals"], {"stages": 3, "passed": 1, "invalid": 1, "tokens": 27})
                full = bench_run.read_json(self.chains / "runs" / tag / "B07" / "public_full.json", {})
                self.assertEqual([r["k"] for r in full["stages"]], [1, 2, 3])
                self.assertEqual(full["skipped"], [{"k": 4, "reason": "x"}])
                self.assertTrue(bench_pair.passed(full["stages"][0]))

    def test_dev_publishes_stage_rows_as_before(self):
        got = self.record("p-dev", "dev")
        self.assertEqual([r["k"] for r in got["stages"]], [1, 2, 3])
        self.assertNotIn("totals", got)
        self.assertFalse((self.hout / "chains" / "p-dev" / "B07" / "public_full.json").exists())


if __name__ == "__main__":
    unittest.main()
