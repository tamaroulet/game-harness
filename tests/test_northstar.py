"""北極星計器（harness/northstar.py）：queue.json から日ごとの「済み 1 件あたりのトークン」を出す（進捗のタスク G0-5）。

    python -m unittest tests.test_northstar -v

合成の queue.json で、済みと未収束が混ざる日・済みが 0 件の日・日付の無い項目・利用量の無い試行を確かめる。
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import hline_report  # noqa: E402
import northstar  # noqa: E402


def try_(tokens=None):
    t = {"run": 1, "attempt": 1}
    if tokens is not None:
        t.update(turns=3, usage={"total_tokens": tokens, "output_tokens": 10})
    return t


def done(day, *tokens):
    return {"status": "done", "done_at": f"{day}T09:00:00+09:00", "tries": [try_(t) for t in tokens]}


def stuck(day, *tokens):
    return {"status": "unconverged", "at": day, "tries": [try_(t) for t in tokens]}


ITEMS = {
    "a": done("2026-10-10", 100),                 # 1 回で済んだ
    "b": done("2026-10-10", 50, 251),             # 2 回
    "c": stuck("2026-10-10", 70),
    "d": stuck("2026-10-08", 40, None),           # 済みが 0 件の日・利用量の無い試行
    "e": {"status": "done", "tries": [try_(999)]},   # 日付なし
    "f": {"status": "unconverged", "tries": []},     # 日付なし
    "g": done("2026-10-03", 10),                  # 窓の外（最後の日から 7 日より前）
    "h": {"status": "waiting", "tries": [try_(5)]},
}


def cells(text, day):
    row = next(x for x in text.splitlines() if x.startswith(f"| {day} "))
    return [c.strip() for c in row.strip("|").split("|")]


class Northstar(unittest.TestCase):
    def test_a_day_with_done_and_unconverged_items(self):
        out = northstar.render(ITEMS)
        # トークン 100+50+251+70 = 471、済み 2 件 → 235、1 回で済んだのは 1/2 → 50%
        self.assertEqual(cells(out, "2026-10-10"), ["2026-10-10", "2", "1", "471", "235", "50%"])

    def test_a_day_without_done_shows_a_dash(self):
        out = northstar.render(ITEMS)
        self.assertEqual(cells(out, "2026-10-08"), ["2026-10-08", "0", "1", "40", "—", "—"])
        self.assertEqual(cells(out, "2026-10-09"), ["2026-10-09", "0", "0", "0", "—", "—"])

    def test_the_table_is_seven_days_ending_at_the_last_date_in_the_data(self):
        out = northstar.render(ITEMS)
        days = [x.split("|")[1].strip() for x in out.splitlines() if x.startswith("| 2026")]
        self.assertEqual(days, [f"2026-10-{d:02d}" for d in range(4, 11)])

    def test_undated_items_and_unmeasured_tries_are_counted_under_the_table(self):
        out = northstar.render(ITEMS)
        self.assertIn("日付の無い項目: 2 件", out)
        self.assertIn("利用量の無い試行: 1 件", out)

    def test_floor_division(self):
        out = northstar.render({"a": done("2026-10-10", 10), "b": done("2026-10-10", 10), "c": done("2026-10-10", 11, 1, 1)})
        self.assertEqual(cells(out, "2026-10-10")[4:], ["11", "66%"])   # 33 // 3, 2 // 3 → 66

    def test_no_dated_items_gives_an_empty_table(self):
        out = northstar.render({"a": {"status": "done", "tries": []}})
        self.assertNotIn("| 2026", out)
        self.assertIn("日付の無い項目: 1 件", out)

    def test_the_same_queue_gives_the_same_output_and_is_not_modified(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "queue.json"
            p.write_text(json.dumps({"items": ITEMS}), encoding="utf-8")
            before = p.read_bytes()
            outs = []
            for _ in range(2):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    self.assertEqual(northstar.main(["--queue", str(p)]), 0)
                outs.append(buf.getvalue())
            self.assertEqual(outs[0], outs[1])
            self.assertEqual(outs[0], northstar.render(ITEMS))
            self.assertEqual(p.read_bytes(), before)


class InReport(unittest.TestCase):
    def state(self):
        return {"items": {n: {"title": n, "milestone": "G0", "deps": [], **i} for n, i in ITEMS.items()}}

    def test_the_section_follows_the_queue_section(self):
        text = hline_report.h_section({"integration_branch": "x"}, {**self.state(), "awaiting_pr": None, "skipped": [],
                                                                      "infra_halt": None, "quota_wait": None})
        self.assertLess(text.index("## キュー"), text.index("## 計器"))
        self.assertIn("| 2026-10-10 | 2 | 1 | 471 | 235 | 50% |", text)

    def test_a_broken_meter_becomes_one_line_and_does_not_stop_the_report(self):
        with mock.patch.object(northstar, "render", side_effect=ValueError("boom")):
            text = hline_report.northstar_section(ITEMS)
        self.assertEqual(text, "## 計器\n- 計器を出せませんでした: ValueError: boom\n")


if __name__ == "__main__":
    unittest.main()
