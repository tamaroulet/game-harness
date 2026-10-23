"""外側の周期を暦の週から走行の周期に替える：周期の報告と、周期に 1 本の外側の統合 PR（W-118）。

    python -m unittest tests.test_hline_cycle_report -v

前の周期の報告の後に済みが下限（config/hline.json の cycle_min_done）に届き、外側の統合 PR が開いていないとき、次の番号の報告を書く。
外部は tests/test_hline_outer_branch.py と同じ差し替え（偽の git・gh・実装役）。一時ディレクトリはテストごとに作る。
"""
import datetime
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cycle_report  # noqa: E402
import hline  # noqa: E402
import weekly_report  # noqa: E402
from test_hline_outer_branch import OUTER, OuterWorld  # noqa: E402
from test_hline_queue import REPO  # noqa: E402

NOW = datetime.datetime(2026, 10, 22, 12, 0, 0, tzinfo=datetime.timezone.utc)
DAY = datetime.datetime(2026, 10, 10, 0, 0, 0, tzinfo=datetime.timezone.utc)


def done(k):
    """k 番目（時刻の順）に済んだ What。"""
    return {"status": "done", "done_at": (DAY + datetime.timedelta(hours=k)).isoformat(timespec="seconds"),
            "tries": [{"gate": True, "usage": {"num_turns": 3, "total_tokens": 100 + k, "output_tokens": 10}, "turns": 3}]}


class Cycles(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = {"out": str(self.tmp / "out"), "cycle_min_done": 10}
        self.records = self.tmp / "records"
        self.records.mkdir()

    def state(self, count, start=0, outer=None):
        return {"items": {f"w-{k:03d}": done(k) for k in range(start, start + count)}, "outer": outer or {}}

    def ensure(self, st):
        return cycle_report.ensure_next(self.cfg, st, records=self.records, now=NOW)

    def previous(self, n, end):
        cycle_report.write(self.cfg, {"cycle": n, "start": "2026-10-01T00:00:00+00:00", "end": end, "names": [], "metrics": {}, "notes": {}})

    def test_the_default_floor_is_ten(self):
        cfg = json.loads((REPO / "config" / "hline.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["cycle_min_done"], 10)
        self.assertEqual(cycle_report.minimum({}), 10)

    def test_the_next_numbered_report_is_written_with_the_weekly_metric_keys(self):
        self.previous(4, (DAY + datetime.timedelta(hours=2, minutes=30)).isoformat())   # w-000〜w-002 は前の周期の分
        st = self.state(15)   # 後に済んだのは w-003〜w-014 の 12 件
        path = self.ensure(st)
        self.assertEqual(path.name, "005.md")
        rec = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual(rec["cycle"], 5)
        self.assertEqual(rec["names"], [f"w-{k:03d}" for k in range(3, 15)])
        self.assertEqual((rec["start"], rec["end"]), ((DAY + datetime.timedelta(hours=2, minutes=30)).isoformat(), NOW.isoformat()))
        weekly_keys, _ = weekly_report.metrics({}, [], datetime.date(2026, 10, 5))
        self.assertEqual(set(rec["metrics"]), set(weekly_keys))
        self.assertEqual(rec["metrics"]["tokens_per_done"]["sum"], sum(100 + k for k in range(3, 15)))
        self.assertIn("w-014", path.read_text(encoding="utf-8"))

    def test_nothing_is_written_below_the_floor(self):
        self.previous(0, (DAY + datetime.timedelta(hours=0, minutes=30)).isoformat())
        self.assertIsNone(self.ensure(self.state(10)))   # 後に済んだのは 9 件
        self.assertEqual(cycle_report.latest(self.cfg)["cycle"], 0)

    def test_nothing_is_written_while_the_outer_pr_is_open(self):
        self.previous(0, (DAY - datetime.timedelta(hours=1)).isoformat())
        self.assertIsNone(self.ensure(self.state(12, outer={"awaiting_pr": "https://github.com/o/r/pull/9"})))
        self.assertEqual(cycle_report.latest(self.cfg)["cycle"], 0)

    def test_without_a_report_number_zero_is_written_from_the_latest_floor_count(self):
        path = self.ensure(self.state(13))
        rec = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual(rec["cycle"], 0)
        self.assertEqual(rec["names"], [f"w-{k:03d}" for k in range(3, 13)])
        self.assertEqual(rec["start"], done(3)["done_at"])
        self.assertEqual(cycle_report.state(self.cfg, self.state(13))["number"], 1)   # 周期 1 はそこから始まる
        self.assertEqual(cycle_report.state(self.cfg, self.state(13))["done"], 0)

    def test_without_a_report_and_below_the_floor_nothing_is_written(self):
        self.assertIsNone(self.ensure(self.state(9)))

    def test_a_report_is_not_written_twice_for_the_same_cycle(self):
        self.ensure(self.state(12))
        self.assertIsNone(self.ensure(self.state(12)))


class Flow(OuterWorld):
    def test_the_line_writes_the_report_and_the_report_md_shows_the_cycle(self):
        self.cfg["cycle_min_done"] = 2
        self.put("010-a", milestone="B8.1")
        self.put("020-b", milestone="B8.1")
        self.poll()
        self.assertFalse((self.out / "cycles").exists())   # 報告は走行の初めに書く。済みが届いた次の走行で書かれる
        self.poll()
        self.assertEqual(sorted(p.name for p in (self.out / "cycles").glob("*.json")), ["000.json"])
        self.assertIn("今の周期: 1（済み 0 件／下限 2 件）、この周期に外側の統合 PR をまだ出していない", self.report())

    def test_report_md_shows_the_number_the_count_and_the_floor(self):
        self.put("010-a", milestone="outer")
        self.poll()
        self.assertIn("今の周期: 0（済み 1 件／下限 10 件）、この周期に外側の統合 PR を出した", self.report())
        self.assertNotIn("今週", self.report())

    def test_the_outer_pr_is_made_once_per_cycle_even_when_the_calendar_week_changes(self):
        self.put("010-a", milestone="outer")
        self.poll()
        self.states[self.prs(OUTER)[0]] = "MERGED"
        self.put("020-b", milestone="outer")
        self.week_name = "2026-W44"
        self.poll()
        self.assertEqual(len(self.prs(OUTER)), 1)
        self.cfg["cycle_min_done"] = 2   # 済みが 2 件に届いて周期の報告が書かれると、次の周期の外側の統合 PR が出る
        self.poll()
        self.assertEqual(len(self.prs(OUTER)), 2)
        self.assertEqual(self.state()["outer"]["cycle"], 0)


if __name__ == "__main__":
    unittest.main()
