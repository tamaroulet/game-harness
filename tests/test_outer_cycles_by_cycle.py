"""外側の周期の集計を、周期の報告で数える（W-119）。

    python -m unittest tests.test_outer_cycles_by_cycle -v

**なぜ要るか**: outer_cycles が暦の週でなく周期の報告（cycles/<番号>.json）で、回った周期・続いた周期・改善を数えるため。
一時ディレクトリに queue.json・What の写し・周期の報告・非劣性の記録を作る。gh・ネットワーク・ベンチは使わない。
"""
import contextlib
import datetime
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import outer_cycles  # noqa: E402

HEAD, OTHER = "a" * 40, "b" * 40
BASE = datetime.datetime(2026, 10, 20, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))


def at(hours):
    return (BASE + datetime.timedelta(hours=hours)).isoformat()


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "out"
        self.records = Path(self.tmp.name) / "root"
        self.items = {}

    def report(self, n, **metrics):
        """周期の報告 n。終わりは 2n 時間後（全部が同じ暦の週の中に収まる）。"""
        d = self.out / "cycles"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{n:03d}.json").write_text(json.dumps({"cycle": n, "end": at(2 * n), "metrics": metrics}), encoding="utf-8")

    def regress(self, head, passed=True):
        d = self.records / "bench" / "records" / "regress"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{head}.json").write_text(json.dumps({"rev": head, "passed": passed}), encoding="utf-8")

    def merged(self, name, hours, head=HEAD, body="# 題\n", milestone="outer"):
        self.items[name] = {"status": "done", "milestone": milestone, "merged": True, "merged_at": at(hours), "merged_head": head}
        (self.out / "queue").mkdir(parents=True, exist_ok=True)
        (self.out / "queue" / f"{name}.md").write_text(body, encoding="utf-8")

    def in_cycle(self, name, k, head=HEAD, **kw):
        """周期 k の中（報告 k−1 の終わりの 1 時間後）にマージする。"""
        self.merged(name, 2 * (k - 1) + 1, head, **kw)

    def run_main(self, *args):
        if self.items:
            self.out.mkdir(parents=True, exist_ok=True)
            (self.out / "queue.json").write_text(json.dumps({"items": self.items}), encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = outer_cycles.main(["--out", str(self.out), "--records", str(self.records), *args])
        return code, buf.getvalue()

    def turn(self, k, head=HEAD):
        self.in_cycle(f"c{k}", k, head)
        self.regress(head)


class Turned(Env):
    def test_four_cycles_inside_one_calendar_week_count(self):
        for n in range(5):
            self.report(n)
        for k in range(1, 5):
            self.turn(k)
        code, text = self.run_main("--min", "4")
        self.assertEqual(code, 0)
        self.assertIn("回った周期 4、続いた周期 4", text)
        self.assertEqual(self.run_main("--min", "5")[0], 1)

    def test_cycle_without_merge_breaks_the_run(self):
        for n in range(5):
            self.report(n)
        for k in (1, 2, 4):   # 周期 3 に外側のマージが無い
            self.turn(k)
        self.assertEqual(self.run_main("--min", "2")[0], 0)
        self.assertEqual(self.run_main("--min", "3")[0], 1)

    def test_failed_or_missing_regress_breaks_the_run(self):
        for n in range(5):
            self.report(n)
        self.turn(1)
        self.turn(2)
        self.in_cycle("c3", 3, head=OTHER)
        self.regress(OTHER, passed=False)
        self.in_cycle("c4", 4, head="c" * 40)   # 周期 4 は記録が無い
        code, text = self.run_main("--min", "3")
        self.assertEqual(code, 1)
        self.assertIn("回った周期 2、続いた周期 2", text)

    def test_every_merged_head_in_the_cycle_must_pass(self):
        for n in range(2):
            self.report(n)
        self.turn(1)
        self.merged("second", 1.5, head=OTHER)   # 同じ周期の 2 本目は記録が無い
        code, text = self.run_main("--min", "1")
        self.assertEqual(code, 1)
        self.assertIn("回った周期 0", text)

    def test_merge_before_report_zero_is_not_counted(self):
        self.report(0)
        self.report(1)
        self.merged("early", -5)
        self.regress(HEAD)
        code, text = self.run_main("--min", "1")
        self.assertEqual(code, 1)
        self.assertIn("回った周期 0", text)

    def test_open_cycle_after_the_latest_report_counts_when_the_report_exists(self):
        self.report(0)
        self.report(1)
        self.merged("now", 100)   # 周期 2 はまだ報告が無い（今まで）。報告 1 があるので回る
        self.regress(HEAD)
        self.assertEqual(self.run_main("--min", "1")[0], 0)

    def test_weekly_reports_are_not_used(self):
        d = self.out / "weekly"
        d.mkdir(parents=True)
        (d / "2026-W42.json").write_text(json.dumps({"week": "2026-W42", "metrics": {}}), encoding="utf-8")
        self.merged("m", 1)
        self.regress(HEAD)
        self.assertEqual(self.run_main("--min", "1")[0], 1)

    def test_empty_storage_and_missing_out_exit_1(self):
        code, text = self.run_main("--min", "1")
        self.assertEqual(code, 1)
        self.assertIn("回った周期 0", text)
        self.out = Path(self.tmp.name) / "nowhere"
        self.assertEqual(self.run_main("--min", "1")[0], 1)


class Improved(Env):
    def setUp(self):
        super().setUp()
        self.regress(HEAD)

    def test_improved_when_next_report_beats_mean_of_previous_two(self):
        self.report(0, first_try_rate=0.5)
        self.report(1, first_try_rate=0.7)
        self.in_cycle("up", 2, body="# 題\n計器: first_try_rate 上\n")
        self.report(2, first_try_rate=0.1)
        self.report(3, first_try_rate=0.8)   # 0.8 > 平均 0.6
        code, text = self.run_main("--min", "0", "--improved", "1")
        self.assertEqual(code, 0)
        self.assertIn("改善 1", text)

    def test_not_improved_when_value_is_not_better(self):
        self.report(0, first_try_rate=0.5)
        self.report(1, first_try_rate=0.7)
        self.in_cycle("up", 2, body="計器: first_try_rate 上\n")
        self.report(2)
        self.report(3, first_try_rate=0.6)   # 平均と同じ
        code, text = self.run_main("--min", "0", "--improved", "1")
        self.assertEqual(code, 1)
        self.assertIn("改善に数えない", text)

    def test_missing_reports_are_not_counted_and_reason_is_shown(self):
        self.report(0, first_try_rate=0.5)
        self.report(1, first_try_rate=0.7)
        self.in_cycle("up", 2, body="計器: first_try_rate 上\n")
        self.report(2)   # 報告 3 が無い
        code, text = self.run_main("--min", "0", "--improved", "1")
        self.assertEqual(code, 1)
        self.assertIn("報告が足りない", text)

    def test_cycle_one_lacks_the_report_before_zero(self):
        self.report(0, first_try_rate=0.5)
        self.report(1, first_try_rate=0.7)
        self.in_cycle("up", 1, body="計器: first_try_rate 上\n")
        self.report(2, first_try_rate=0.9)   # 報告 −1 が無い
        code, text = self.run_main("--min", "0", "--improved", "1")
        self.assertEqual(code, 1)
        self.assertIn("報告が足りない", text)


if __name__ == "__main__":
    unittest.main()
