"""試行のトークンの内訳（入力・書き・読み・出力）：試行の記録・キューの行・日ごとの表・直近 20 件の行（進捗のタスク W-67）。

    python -m unittest tests.test_hline_usage_breakdown -v
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import hline_usage  # noqa: E402
import northstar  # noqa: E402
import telemetry  # noqa: E402


def split(a, b, c, d):
    return {"input_tokens": a, "cache_creation_tokens": b, "cache_read_tokens": c, "output_tokens": d,
            "total_tokens": a + b + c + d, "num_turns": 2}


def try_(u=None):
    t = {"run": 1, "attempt": 1}
    if u is not None:
        t["usage"] = u
    return t


def item(status, stamp, *tries):
    key = "done_at" if status == "done" else "at"
    return {"status": status, key: stamp, "tries": list(tries)}


def cells(text, day):
    row = next(x for x in text.splitlines() if x.startswith(f"| {day} "))
    return [c.strip() for c in row.strip("|").split("|")]


class Record(unittest.TestCase):
    def test_cli_usage_puts_the_four_numbers_in_the_record(self):
        out = json.dumps({"num_turns": 2, "total_cost_usd": 0.1,
                          "usage": {"input_tokens": 1, "cache_creation_input_tokens": 20, "cache_read_input_tokens": 300, "output_tokens": 4}})
        u = telemetry.cli_usage(out, "claude")
        self.assertEqual([u[k] for k in hline_usage.KEYS], [1, 20, 300, 4])


class QueueLine(unittest.TestCase):
    def test_with_breakdown_sums_the_tries(self):
        it = item("done", "2026-10-10", try_(split(1, 2, 3, 4)), try_(split(10, 20, 30, 40)))
        self.assertTrue(hline_usage.note(it).endswith("／内訳 入力 11・書き 22・読み 33・出力 44"))

    def test_partly_missing_adds_the_count(self):
        old = {"input_tokens": 5, "output_tokens": 1, "total_tokens": 6}   # 書き・読みの欄が無い古い記録
        it = item("done", "2026-10-10", try_(split(1, 2, 3, 4)), try_(old), try_())
        self.assertTrue(hline_usage.note(it).endswith("／内訳 入力 1・書き 2・読み 3・出力 4／内訳なし 2 件"))

    def test_none_at_all_says_only_that(self):
        it = item("done", "2026-10-10", try_({"input_tokens": 5, "output_tokens": 1}), try_())
        self.assertTrue(hline_usage.note(it).endswith("）／内訳なし"))
        self.assertNotIn("内訳なし 2 件", hline_usage.note(it))

    def test_a_non_integer_part_makes_the_try_lack_a_breakdown(self):
        u = split(1, 2, 3, 4)
        u["cache_read_tokens"] = None
        self.assertEqual(hline_usage.breakdown(item("done", "2026-10-10", try_(u)))[1:], (0, 1))

    def test_the_existing_wording_is_kept(self):
        it = item("done", "2026-10-10", try_(dict(split(1, 2, 3, 4), total_tokens=10)))
        it["tries"][0]["turns"] = 3
        self.assertTrue(hline_usage.note(it).startswith("（試行 1 回／ターン 3／トークン 10（うち出力 4）／利用量なしの試行 0 件）"))


class DailyTable(unittest.TestCase):
    ITEMS = {
        "a": item("done", "2026-10-10T09:00:00+09:00", try_(split(10, 0, 30, 10))),
        "b": item("unconverged", "2026-10-10", try_(split(10, 0, 30, 10)), try_()),   # 内訳の無い試行 1 件
        "c": item("done", "2026-10-08", try_()),                                       # この日は内訳なし
    }

    def test_sums_and_read_share(self):
        out = northstar.render_split(self.ITEMS)
        # 読み 60 ÷ 合計 100 = 60.0%
        self.assertEqual(cells(out, "2026-10-10"), ["2026-10-10", "20", "0", "60", "20", "60.0%", "1"])

    def test_a_day_without_breakdown(self):
        out = northstar.render_split(self.ITEMS)
        self.assertEqual(cells(out, "2026-10-08"), ["2026-10-08", *["内訳なし"] * 5, "1"])
        self.assertEqual(cells(out, "2026-10-09"), ["2026-10-09", *["内訳なし"] * 5, "0"])

    def test_the_rows_are_the_days_of_the_existing_table(self):
        days = lambda t: [x.split("|")[1].strip() for x in t.splitlines() if x.startswith("| 2026")]   # noqa: E731
        self.assertEqual(days(northstar.render_split(self.ITEMS)), days(northstar.render(self.ITEMS)))

    def test_the_section_keeps_the_old_table_then_the_split_table(self):
        text = northstar.section(self.ITEMS)
        self.assertEqual(text.index("## 計器"), 0)
        self.assertIn(northstar.render(self.ITEMS), text)
        self.assertLess(text.index("利用量の無い試行"), text.index("### トークンの内訳"))
        self.assertLess(text.index("- 直近"), text.index("### トークンの内訳"))


class Recent(unittest.TestCase):
    def test_denominator_and_rates(self):
        items = {
            "a": item("done", "2026-10-10T01:00:00", try_(split(1, 1, 1, 1))),
            "b": item("done", "2026-10-10T02:00:00", try_(split(1, 1, 1, 1)), try_(split(1, 1, 1, 1))),
            "c": item("unconverged", "2026-10-10T03:00:00", try_(split(1, 1, 1, 1))),
            "d": item("done", "2026-10-10T04:00:00", try_(split(1, 1, 1, 1))),
            "w": {"status": "waiting", "tries": []},
        }
        line = northstar.render_recent(items)
        self.assertIn("直近 4 件（済み 3・未収束 1）", line)
        self.assertIn("未収束の率 25.0%（1/4）", line)
        self.assertIn("1 回で済んだ率 50.0%（2/4）", line)

    def test_fewer_than_twenty_shows_the_count_found(self):
        self.assertIn("直近 1 件", northstar.render_recent({"a": item("done", "2026-10-10", try_(split(1, 1, 1, 1)))}))
        self.assertIn("直近 0 件", northstar.render_recent({}))

    def test_only_the_newest_twenty(self):
        items = {f"n{i:02d}": item("done", f"2026-09-{i + 1:02d}", try_(split(i, 0, 0, 0))) for i in range(25)}
        items["old-stuck"] = item("unconverged", "2026-08-01", try_())
        line = northstar.render_recent(items)
        self.assertIn("直近 20 件（済み 20・未収束 0）", line)
        # 新しい 20 件は i = 5〜24。合計の中央値は (14 + 15) / 2 = 14.5
        self.assertIn("中央値）14.5（入力 14.5・書き 0・読み 0・出力 0）", line)

    def test_whats_with_a_try_lacking_a_breakdown_are_left_out_of_the_median(self):
        items = {
            "a": item("done", "2026-10-10T01:00:00", try_(split(10, 0, 0, 0))),
            "b": item("done", "2026-10-10T02:00:00", try_(split(1000, 0, 0, 0)), try_()),   # 除く
            "c": item("done", "2026-10-10T03:00:00", try_(split(30, 0, 0, 0))),
            "s": item("unconverged", "2026-10-10T04:00:00", try_(split(7, 0, 0, 0))),        # 済みでないので中央値に入れない
        }
        line = northstar.render_recent(items)
        self.assertIn("直近 4 件（済み 3・未収束 1）", line)
        self.assertIn("中央値）20（入力 20", line)
        self.assertIn("内訳の無い試行を含む済みの What 1 件を除く", line)

    def test_no_eligible_what_gives_none(self):
        line = northstar.render_recent({"a": item("done", "2026-10-10", try_())})
        self.assertIn("中央値）なし", line)
        self.assertIn("1 件を除く", line)


if __name__ == "__main__":
    unittest.main()
