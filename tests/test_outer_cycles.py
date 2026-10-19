"""外側の周期の記録の集計（W-59）。

    python -m unittest tests.test_outer_cycles -v

**なぜ要るか**: 週次の周期が回った週と、改善が計器を良くした件数を数える outer_cycles が、3 つの条件・続いた週・計器の向きを正しく数えるため。
一時ディレクトリに queue.json・What の写し・週次の報告・非劣性の記録を作る。gh・ネットワーク・ベンチは使わない。
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
import hline_outer  # noqa: E402
import outer_cycles  # noqa: E402

URL = "https://github.com/o/r/pull/9"
HEAD, OTHER = "a" * 40, "b" * 40
CFG = {"integration_branch": "hline/integration", "outer_integration_branch": "hline/outer"}


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "out"
        self.records = Path(self.tmp.name) / "root"
        self.items = {}

    def report(self, week, **metrics):
        d = self.out / "weekly"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{week}.json").write_text(json.dumps({"week": week, "metrics": metrics}), encoding="utf-8")

    def regress(self, head, passed=True, rev=None):
        d = self.records / "bench" / "records" / "regress"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{head}.json").write_text(json.dumps({"rev": rev or head, "passed": passed}), encoding="utf-8")

    def merged(self, name, day, head=HEAD, body="# 題\n", milestone="outer"):
        self.items[name] = {"status": "done", "milestone": milestone, "merged": True, "merged_at": f"{day}T10:00:00+09:00",
                            "merged_head": head}
        (self.out / "queue").mkdir(parents=True, exist_ok=True)
        (self.out / "queue" / f"{name}.md").write_text(body, encoding="utf-8")

    def run_main(self, *args):
        if self.items:
            self.out.mkdir(parents=True, exist_ok=True)
            (self.out / "queue.json").write_text(json.dumps({"items": self.items}), encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = outer_cycles.main(["--out", str(self.out), "--records", str(self.records), *args])
        return code, buf.getvalue()

    def cycle(self, name, day, week_before, head=HEAD, **kw):
        """前の週の報告・非劣性の記録・その週のマージがそろった週。"""
        self.report(week_before)
        self.regress(head)
        self.merged(name, day, head, **kw)


class Turned(Env):
    def test_only_weeks_with_all_three_conditions_count(self):
        self.cycle("ok", "2026-10-20", "2026-W42")                                   # W43 回った
        self.merged("noreport", "2026-10-27")                                         # W44 前の週の報告が無い
        self.report("2026-W44")                                                       # W45 に外側のマージが無い（報告だけ）
        self.merged("noregress", "2026-11-10", head=OTHER)                            # W46 非劣性が無い
        self.report("2026-W45")
        self.merged("failed", "2026-11-17", head="c" * 40)                            # W47 不合格
        self.report("2026-W46")
        self.regress("c" * 40, passed=False)
        self.merged("wrongrev", "2026-11-24", head="d" * 40)                          # W48 rev が違う
        self.report("2026-W47")
        self.regress("d" * 40, rev=OTHER)
        self.merged("inner", "2026-12-01", milestone="issues")                        # W49 内側のマージは数えない
        self.report("2026-W48")
        self.regress(HEAD)
        code, text = self.run_main("--min", "1")
        self.assertEqual(code, 0)
        self.assertIn("回った週 1、続いた週 1", text)
        self.assertEqual(self.run_main("--min", "2")[0], 1)

    def test_every_merged_head_in_the_week_must_pass(self):
        self.cycle("a", "2026-10-20", "2026-W42")
        self.merged("b", "2026-10-22", head=OTHER)   # 同じ週の 2 本目は記録が無い
        code, text = self.run_main("--min", "1")
        self.assertEqual(code, 1)
        self.assertIn("回った週 0", text)

    def test_consecutive_weeks(self):
        self.cycle("w43", "2026-10-20", "2026-W42")
        self.cycle("w44", "2026-10-27", "2026-W43")
        self.assertEqual(self.run_main("--min", "2")[0], 0)
        self.assertEqual(self.run_main("--min", "3")[0], 1)

    def test_a_missing_week_breaks_the_run(self):
        self.cycle("w43", "2026-10-20", "2026-W42")
        self.cycle("w44", "2026-10-27", "2026-W43")
        self.cycle("w46", "2026-11-10", "2026-W45")   # W45 が抜けている
        self.assertEqual(self.run_main("--min", "2")[0], 0)
        self.assertEqual(self.run_main("--min", "3")[0], 1)

    def test_iso_weeks_across_the_year_boundary_are_consecutive(self):
        self.cycle("w53", "2026-12-29", "2026-W52")
        self.cycle("w01", "2027-01-05", "2026-W53")
        self.assertEqual(self.run_main("--min", "2")[0], 0)
        self.assertEqual(self.run_main("--min", "3")[0], 1)

    def test_empty_storage_exits_1_without_raising(self):
        code, text = self.run_main("--min", "1")
        self.assertEqual(code, 1)
        self.assertIn("回った週 0", text)

    def test_missing_out_directory_exits_1(self):
        self.out = Path(self.tmp.name) / "nowhere"
        self.assertEqual(self.run_main("--min", "1")[0], 1)


class Improved(Env):
    def setUp(self):
        super().setUp()
        self.report("2026-W41", first_try_rate=0.5, failure={"full_suite": 4}, tokens_per_done={"median": 100}, nothing=None)
        self.report("2026-W42", first_try_rate=0.7, failure={"full_suite": 2}, tokens_per_done={"median": 300}, nothing=None)
        self.report("2026-W44", first_try_rate=0.8, failure={"full_suite": 1}, tokens_per_done={"median": 150}, nothing=None)
        self.regress(HEAD)

    def improved(self, *args):
        code, text = self.run_main("--min", "0", *args)
        return code, text

    def test_up_and_down_and_dotted_identifiers(self):
        self.merged("up", "2026-10-20", body="# 題\n計器: first_try_rate 上\n")
        self.merged("down", "2026-10-21", body="計器: failure.full_suite 下\n")
        self.merged("dotted", "2026-10-22", body="計器: tokens_per_done.median 下\n")   # 150 < 平均 200
        self.assertEqual(self.improved("--improved", "3")[0], 0)
        self.assertEqual(self.improved("--improved", "4")[0], 1)

    def test_wrong_direction_is_not_an_improvement(self):
        self.merged("up", "2026-10-20", body="計器: first_try_rate 下\n")   # 0.8 は 0.6 より大きい
        self.merged("down", "2026-10-21", body="計器: failure.full_suite 上\n")
        code, text = self.improved("--improved", "1")
        self.assertEqual(code, 1)
        self.assertIn("改善に数えない", text)

    def test_null_metric_missing_line_and_unreadable_line_do_not_count(self):
        self.merged("null", "2026-10-20", body="計器: nothing 上\n")
        self.merged("noline", "2026-10-21", body="# 題\n")
        self.merged("broken", "2026-10-22", body="計器: first_try_rate 横\n")
        self.merged("unknown", "2026-10-23", body="計器: no_such 上\n")
        code, text = self.improved("--improved", "1")
        self.assertEqual(code, 1)
        self.assertIn("値が数でない", text)
        self.assertIn("計器の行を読めない", text)
        self.assertNotIn("noline", text)
        self.assertEqual(self.improved("--improved", "0")[0], 0)

    def test_missing_report_for_the_week_after_is_not_an_improvement(self):
        self.merged("late", "2026-11-03", body="計器: first_try_rate 上\n")   # W45。W46 の報告が無い
        code, text = self.improved("--improved", "1")
        self.assertEqual(code, 1)
        self.assertIn("報告が足りない", text)


class MergeRecord(unittest.TestCase):
    class Host:
        def __init__(self, state="MERGED", info=None):
            self.state, self.info, self.saved = state, info, 0

        def pr_state(self, cfg, url):
            return self.state

        def pr_checks(self, cfg, url):
            if self.info is None:
                raise RuntimeError("gh down")
            return self.info

        def drop_merged_branch(self, cfg):
            pass

        def save_state(self, cfg, st):
            self.saved += 1

    def state(self):
        return {"items": {"o": {"status": "done", "milestone": "outer", "pr": URL},
                          "i": {"status": "done", "milestone": "issues", "pr": URL}}, "outer": {"awaiting_pr": URL}}

    def test_outer_items_get_the_merge_time_and_head(self):
        st = self.state()
        hline_outer.settle(self.Host(info={"headRefOid": HEAD}), CFG, st)
        o = st["items"]["o"]
        self.assertTrue(o["merged"])
        self.assertEqual(o["merged_head"], HEAD)
        datetime.datetime.fromisoformat(o["merged_at"])
        self.assertNotIn("merged_head", st["items"]["i"])   # 内側の What には残さない
        self.assertNotIn("merged_at", st["items"]["i"])

    def test_unreadable_head_leaves_nothing_and_does_not_stop(self):
        for info in (None, {}, {"headRefOid": "abc"}):
            with self.subTest(info=info):
                st = self.state()
                host = self.Host(info=info)
                hline_outer.settle(host, CFG, st)
                self.assertTrue(st["items"]["o"]["merged"])
                self.assertNotIn("merged_head", st["items"]["o"])
                self.assertNotIn("merged_at", st["items"]["o"])
                self.assertIsNone(st["outer"]["awaiting_pr"])


if __name__ == "__main__":
    unittest.main()
