"""週次の報告（harness/weekly_report.py）の検査（W-54）。

    python -m unittest tests.test_weekly_report -v

一時ディレクトリに作った queue.json と工廠の走行の記録だけを使う（本物の queue.json・bench・claude・gh は使わない）。
"""
import contextlib
import datetime
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import hline  # noqa: E402
import weekly_report  # noqa: E402

TODAY = datetime.date(2026, 10, 19)   # 月曜。前の週は 2026-W42（10/12〜10/18）


def try_(tokens, ok, cls=None):
    t = {"gate": ok, "turns": 1, "usage": {"total_tokens": tokens, "output_tokens": 1}}
    if cls:
        t["failure"] = {"class": cls}
    return t


def done(day, *tries):
    return {"status": "done", "done_at": f"{day}T09:00:00+09:00", "tries": list(tries)}


def stuck(day, cls, *tries):
    return {"status": "unconverged", "at": day, "failure_class": cls, "tries": list(tries)}


ITEMS = {
    # 2026-W42
    "a": done("2026-10-12", try_(100, True)),
    "b": done("2026-10-15", try_(50, False, "full_suite"), try_(251, True)),
    "c": done("2026-10-18", try_(200, True)),
    "d": stuck("2026-10-14", "test", try_(30, False, "test"), try_(30, False, "cutoff")),
    "e": stuck("2026-10-19", "other", try_(5, False, "other")),   # W43 なので数えない
    "w": {"status": "waiting", "tries": [try_(999, False, "other")]},
    # 2026-W41
    "f": done("2026-10-06", try_(10, False, "abandon"), try_(20, True)),
    # 2026-W40
    "g": stuck("2026-10-01", "timeout", try_(7, False, "timeout")),
}
RUNS = {
    "B01/factory-1.json": {"started_at": "2026-10-13T10:00:00+09:00", "intake_revised": 2},
    "B02/factory-base-1.json": {"started_at": "2026-10-14T10:00:00+09:00", "intake_revised": 1},
    "B01/factory-2.json": {"started_at": "2026-10-07T10:00:00+09:00", "intake_revised": 0},
    "B01/floor.json": {"started_at": "2026-10-13T10:00:00+09:00", "intake_revised": 50},   # 走行の記録ではない
}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.queue, self.records, self.out = self.tmp / "queue.json", self.tmp / "records", self.tmp / "weekly"
        self.queue.write_text(json.dumps({"items": ITEMS}), encoding="utf-8")
        for name, rec in RUNS.items():
            p = self.records / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(rec), encoding="utf-8")
        bad = self.records / "B03" / "factory-bad.json"
        bad.parent.mkdir()
        bad.write_text("{壊れた", encoding="utf-8")

    def run_main(self, *extra):
        args = ["--queue", str(self.queue), "--records", str(self.records), "--out", str(self.out), *extra]
        with contextlib.redirect_stdout(io.StringIO()):
            return weekly_report.main(args, today=TODAY)


class Report(Base):
    def report(self, week="2026-W42"):
        self.assertEqual(self.run_main("--week", week), 0)
        return json.loads((self.out / f"{week}.json").read_text(encoding="utf-8"))

    def test_the_week_and_the_two_before_it_match_the_hand_count(self):
        rep = self.report()
        m, p1, p2 = rep["metrics"], rep["previous"][0], rep["previous"][1]
        self.assertEqual((rep["week"], rep["start"], rep["end"]), ("2026-W42", "2026-10-12", "2026-10-18"))
        self.assertEqual((p1["week"], p2["week"]), ("2026-W41", "2026-W40"))
        # W42: 済み a・b・c。1 回で済んだのは a・c → 2/3。トークン 100・301・200 → 中央値 200・和 601
        self.assertAlmostEqual(m["first_try_rate"], 2 / 3)
        self.assertEqual(m["tokens_per_done"], {"median": 200, "sum": 601})
        self.assertEqual(m["bottleneck"], [{"name": "b", "tokens": 301}, {"name": "c", "tokens": 200}, {"name": "a", "tokens": 100}])
        self.assertEqual(m["unconverged"], 1)
        self.assertEqual({k: v for k, v in m.items() if k.startswith("failure.") and v}, {"failure.full_suite": 1, "failure.test": 1, "failure.cutoff": 1})
        self.assertEqual(m["failure.abandon"], 0)
        self.assertEqual(m["failure.不明"], 0)
        self.assertAlmostEqual(m["audit.revise_rate"], 1.5)   # (2+1)/2 走行。floor.json と壊れた記録は数えない
        # W41
        self.assertEqual(p1["metrics"]["first_try_rate"], 0)
        self.assertEqual(p1["metrics"]["tokens_per_done"], {"median": 30, "sum": 30})
        self.assertEqual(p1["metrics"]["bottleneck"], [{"name": "f", "tokens": 30}])
        self.assertEqual((p1["metrics"]["unconverged"], p1["metrics"]["failure.abandon"]), (0, 1))
        self.assertEqual(p1["metrics"]["audit.revise_rate"], 0)
        # W40
        self.assertEqual(p2["metrics"]["unconverged"], 1)
        self.assertEqual(p2["metrics"]["failure.timeout"], 1)

    def test_a_week_without_runs_or_done_has_null_with_a_reason_and_does_not_raise(self):
        rep = self.report()
        empty = rep["previous"][1]   # W40：済みも走行も無い
        for key in ("first_try_rate", "tokens_per_done", "bottleneck", "audit.revise_rate"):
            self.assertIsNone(empty["metrics"][key], key)
            self.assertIn(key, empty["notes"])
        text = (self.out / "2026-W42.md").read_text(encoding="utf-8")
        self.assertIn("2026-W40", text)
        self.assertIn("audit.revise_rate: null", text)
        self.assertEqual(self.report("2026-W30")["metrics"]["audit.revise_rate"], None)

    def test_the_markdown_lists_every_metric_with_the_three_weeks(self):
        self.report()
        lines = (self.out / "2026-W42.md").read_text(encoding="utf-8").splitlines()
        self.assertIn("| 計器 | 2026-W42 | 2026-W41 | 2026-W40 |", lines)
        row = next(x for x in lines if x.startswith("| first_try_rate "))
        self.assertEqual([c.strip() for c in row.strip("|").split("|")], ["first_try_rate", "0.667", "0", "null"])

    def test_the_default_week_is_the_previous_one_and_missing_inputs_do_not_stop_it(self):
        self.queue.unlink()
        self.assertEqual(self.run_main(), 0)
        self.assertTrue((self.out / "2026-W42.json").exists())


class LatestWithinDays(Base):
    def run_check(self, days, now):
        before = sorted(p.name for p in self.out.glob("*")) if self.out.exists() else []
        with contextlib.redirect_stdout(io.StringIO()):
            code = weekly_report.main(["--out", str(self.out), "--latest-within-days", str(days)], now=now)
        self.assertEqual(sorted(p.name for p in self.out.glob("*")) if self.out.exists() else [], before)   # 報告は書かない
        return code

    def test_exit_code_follows_the_age_of_the_newest_report(self):
        self.assertEqual(self.run_check(7, 1_000_000), 1)   # 書き先が無い
        self.out.mkdir()
        self.assertEqual(self.run_check(7, 1_000_000), 1)   # 空
        report = self.out / "2026-W41.json"
        report.write_text("{}", encoding="utf-8")
        os.utime(report, (1_000_000 - 2 * 86400, 1_000_000 - 2 * 86400))
        self.assertEqual(self.run_check(3, 1_000_000), 0)
        self.assertEqual(self.run_check(1, 1_000_000), 1)


class HLine(Base):
    def setUp(self):
        super().setUp()
        self.cfg = {"out": str(self.tmp / "hline-out"), "open_pr": False}
        self.expected = Path(self.cfg["out"]) / "weekly" / f"{weekly_report.week_name(weekly_report.previous_week(datetime.date.today()))}.json"

    def run_line(self):
        fakes = dict(fetch=lambda c: None, code_shas=lambda c: ("a" * 40, "a" * 40), recover=lambda *a: None, intake=lambda *a, **k: None,
                     save_state=lambda *a: None, write_report=lambda *a: None, sweep=lambda c: {},
                     load_state=lambda c: {"awaiting_pr": None, "items": {}})
        out = io.StringIO()
        with mock.patch.multiple(hline, **{k: mock.Mock(side_effect=v) for k, v in fakes.items()}), \
                mock.patch.object(weekly_report, "ROOT", self.tmp), contextlib.redirect_stdout(out):
            return hline.run_line(self.cfg), out.getvalue()

    def test_the_previous_week_is_written_once_and_not_rewritten(self):
        self.assertEqual(self.run_line()[0], 0)
        self.assertTrue(self.expected.exists())
        self.assertTrue(self.expected.with_suffix(".md").exists())
        self.expected.write_text("sentinel", encoding="utf-8")
        self.assertEqual(self.run_line()[0], 0)
        self.assertEqual(self.expected.read_text(encoding="utf-8"), "sentinel")

    def test_a_failure_to_make_the_report_does_not_stop_the_run(self):
        with mock.patch.object(weekly_report, "ensure_previous", side_effect=RuntimeError("壊れた")):
            code, out = self.run_line()
        self.assertEqual(code, 0)
        self.assertIn("週次の報告を作れませんでした: RuntimeError: 壊れた", out)
        self.assertFalse(self.expected.exists())


if __name__ == "__main__":
    unittest.main()
