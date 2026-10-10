"""H ライン：済みにした項目に、その時刻（done_at）が残る（進捗のタスク W-4）。

    python -m unittest tests.test_hline_done_time -v

**なぜ要るか**: 済みの項目がいつ済んだかをキューに残さないと、統合 PR のあとで経緯をたどれない。通常の済み・強制終了からの復旧の済みに付き、
既存の値は書き換えず、未収束・待ち・凍結には付かないことを確かめる。実際の push・PR・モデルの呼び出しは起こさない。
"""
import datetime
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hline_queue  # noqa: E402
from test_hline_queue import World  # noqa: E402

ISO = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$"
FIXED = datetime.datetime(2026, 10, 11, 9, 30, 5, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))


class DoneTime(World):
    def item(self, name):
        return self.state()["items"][name]

    def test_a_done_what_gets_the_time_to_the_second_with_the_utc_offset(self):
        self.put("010-a")
        with mock.patch.object(hline_queue, "now", return_value=FIXED):
            self.poll()
        self.assertEqual(self.item("010-a")["status"], "done")
        self.assertEqual(self.item("010-a")["done_at"], "2026-10-11T09:30:05+09:00")

    def test_the_real_clock_gives_an_iso_time_with_an_offset(self):
        self.put("010-a")
        self.poll()
        self.assertRegex(self.item("010-a")["done_at"], ISO)

    def test_a_what_recovered_as_done_gets_the_time_of_the_recovery(self):
        self.put("010-a")
        self.poll()
        st = self.state()
        st["awaiting_pr"] = None
        st["items"]["010-a"]["status"] = "processing"   # push の後、済みの記録の前に落ちた
        st["items"]["010-a"].pop("done_at", None)
        hline_queue.save_state(self.cfg, st)
        self.integrated.append("010-a")
        with mock.patch.object(hline_queue, "now", return_value=FIXED):
            hline_queue.recover(self.cfg, st, lambda n: n in self.integrated)
        self.assertEqual(self.item("010-a")["status"], "done")
        self.assertEqual(self.item("010-a")["done_at"], "2026-10-11T09:30:05+09:00")

    def test_an_existing_done_at_is_not_rewritten(self):
        item = {"status": "processing", "done_at": "2026-01-02T03:04:05+00:00"}
        with mock.patch.object(hline_queue, "now", return_value=FIXED):
            hline_queue.mark_done(item)
        self.assertEqual((item["status"], item["done_at"]), ("done", "2026-01-02T03:04:05+00:00"))

    def test_a_recovered_what_that_was_not_integrated_goes_back_to_waiting_without_a_time(self):
        st = {"items": {"a": {"status": "processing", "deps": []}}}
        hline_queue.recover(self.cfg, st, lambda n: False)
        self.assertEqual(st["items"]["a"]["status"], "waiting")
        self.assertNotIn("done_at", st["items"]["a"])

    def test_an_unconverged_what_and_the_ones_frozen_behind_it_get_no_time(self):
        self.failing = {"T-010-a"}
        self.put("010-a")
        self.put("020-b", deps=["010-a"])
        self.poll()
        for name in ("010-a", "020-b"):
            self.assertNotIn("done_at", self.item(name))
        self.assertEqual((self.item("010-a")["status"], self.item("020-b")["status"]), ("unconverged", "frozen"))

    def test_a_waiting_what_gets_no_time(self):
        self.put("010-a")
        self.put("020-b", deps=["030-missing"])
        self.poll()
        self.assertEqual(self.item("020-b")["status"], "waiting")
        self.assertNotIn("done_at", self.item("020-b"))

    def test_an_old_item_without_done_at_is_still_read(self):
        st = {"items": {"a": {"status": "done", "title": "t", "milestone": "B8.1", "task": None, "deps": []}}}
        hline_queue.save_state(self.cfg, st)
        loaded = hline_queue.load_state(self.cfg)
        self.assertEqual(hline_queue.by_status(loaded, "done"), ["a"])
        self.assertNotIn("done_at", loaded["items"]["a"])

    def test_the_time_is_not_in_the_report(self):
        self.put("010-a")
        self.poll()
        self.assertNotRegex(self.report(), r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


if __name__ == "__main__":
    unittest.main()
