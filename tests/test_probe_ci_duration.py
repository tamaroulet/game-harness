"""CI の所要と統合 PR の待ち：probe の 2 行・統合 PR の時刻の記録と待ちの行・fastsuite の「遅いモジュール:」行。"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import fastsuite as fs  # noqa: E402
import hline_probe as hp  # noqa: E402
import hline_queue as hq  # noqa: E402
import hline_report as hr  # noqa: E402

PR = "https://github.com/o/r/pull/7"


def run_row(status, start, end):
    return {"status": status, "conclusion": "success" if status == "completed" else "", "startedAt": start, "updatedAt": end}


# 完了 3 回（60 秒・120 秒・300 秒）と未完了 2 回
RUNS = [run_row("completed", "2026-10-01T00:00:00Z", "2026-10-01T00:01:00Z"),
        run_row("in_progress", "2026-10-01T01:00:00Z", "2026-10-01T01:09:00Z"),
        run_row("completed", "2026-10-01T02:00:00Z", "2026-10-01T02:05:00Z"),
        run_row("queued", "2026-10-01T03:00:00Z", "2026-10-01T03:00:01Z"),
        run_row("completed", "2026-10-01T04:00:00Z", "2026-10-01T04:02:00Z")]


def check(workflow, job, status, conclusion, start="2026-10-01T00:00:00Z", end="2026-10-01T00:03:05Z"):
    return {"workflowName": workflow, "name": job, "status": status, "conclusion": conclusion,
            "startedAt": start, "completedAt": end}


class Runner:
    def __init__(self, runs=RUNS, view=None, fail=()):
        self.calls, self.runs, self.fail = [], runs, fail
        self.view = view if view is not None else {"statusCheckRollup": [], "labels": []}

    def __call__(self, args, cwd, ttl, label):
        self.calls.append(args)
        if args[0] == "gh" and args[1] in self.fail:
            return 1, "", "boom: 理由"
        if args[0] == "gh" and args[1] == "run":
            return 0, json.dumps(self.runs), ""
        if args[0] == "gh" and args[1] == "pr":
            return 0, json.dumps(self.view), ""
        return 0, "abc1234 件名\n", ""

    def gh(self, sub):
        return [c for c in self.calls if c[0] == "gh" and c[1] == sub]


class Waits(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cache = self.tmp / "probe_ci.json"
        self.cfg = {"base": "origin/main", "integration_branch": "hline/integration", "out": str(self.tmp),
                    "ttl_seconds": {"git": 5}}

    def lines(self, runner, pr=None, clock=1_000_000.0):
        return hp.wait_lines(self.cfg, runner, clock, self.cache, pr)

    def test_median_max_and_count_over_the_completed_runs_only(self):
        (line,) = self.lines(Runner())
        self.assertIn("中央値 2 分 0 秒", line)
        self.assertIn("最大 5 分 0 秒", line)
        self.assertIn("数えた走行 3 回", line)
        self.assertIn("出典:", line)
        self.assertIn("取得:", line)

    def test_no_integration_pr_means_no_pr_line_and_no_pr_call(self):
        runner = Runner()
        self.assertEqual(len(self.lines(runner)), 1)
        self.assertEqual(runner.gh("pr"), [])

    def test_unfinished_checks_and_a_label(self):
        view = {"statusCheckRollup": [check("harness-tests", "test", "IN_PROGRESS", ""),
                                      check("approval", "approval", "COMPLETED", "FAILURE")],
                "labels": [{"name": "ms4:approved"}]}
        text = self.lines(Runner(view=view), pr=PR)[1]
        self.assertIn("harness-tests（test）: 状態 IN_PROGRESS", text)
        self.assertIn("approval（approval）: 状態 COMPLETED／結論 FAILURE／所要 3 分 5 秒", text)
        self.assertIn("ms4:approved あり", text)

    def test_no_label_and_missing_checks(self):
        text = self.lines(Runner(), pr=PR)[1]
        self.assertIn("harness-tests: チェックなし", text)
        self.assertIn("ms4:approved なし", text)

    def test_failing_gh_is_reported_as_failure_for_both_lines(self):
        lines = self.lines(Runner(fail=("run", "pr")), pr=PR)
        self.assertEqual(len(lines), 2)
        for line in lines:
            self.assertIn("取得失敗：", line)

    def test_gh_is_not_called_again_within_ttl_but_is_after(self):
        runner = Runner()
        self.lines(runner, pr=PR, clock=1000.0)
        self.lines(runner, pr=PR, clock=1000.0 + hp.CI_TTL - 1)
        self.assertEqual((len(runner.gh("run")), len(runner.gh("pr"))), (1, 1))
        self.lines(runner, pr=PR, clock=1000.0 + hp.CI_TTL + 1)
        self.assertEqual((len(runner.gh("run")), len(runner.gh("pr"))), (2, 2))

    def test_section_adds_the_lines_only_when_asked_and_survives_gh_failure(self):
        plain = hp.section(self.cfg, runner=Runner(), clock=1.0, cache=self.cache)
        self.assertEqual(len(plain.splitlines()), 6)
        full = hp.section(self.cfg, runner=Runner(fail=("run", "pr")), clock=1.0, cache=self.tmp / "other.json", pr=PR, waits=True)
        self.assertEqual(len(full.splitlines()), 8)
        self.assertIn("取得失敗：", full)


class PrTimes(unittest.TestCase):
    def test_state_keeps_the_times(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {"out": tmp}
            st = hq.load_state(cfg)
            self.assertIsNone(st["pr_times"])
            st["pr_times"] = {"url": PR, "opened_at": 100.0, "closed_at": None}
            hq.save_state(cfg, st)
            self.assertEqual(hq.load_state(cfg)["pr_times"], st["pr_times"])

    def test_wait_line_closed_open_and_missing(self):
        closed = hr.pr_wait_line({"opened_at": 1000.0, "closed_at": 1000.0 + 3725})
        self.assertEqual(len(closed), 1)
        self.assertIn("直近の統合 PR の待ち", closed[0])
        self.assertIn("1 時間 2 分 5 秒", closed[0])
        opened = hr.pr_wait_line({"opened_at": 1000.0, "closed_at": None}, at=1000.0 + 90)
        self.assertIn("開いている", opened[0])
        self.assertIn("1 分 30 秒", opened[0])
        self.assertEqual(hr.pr_wait_line(None), [])


class SlowModules(unittest.TestCase):
    def results(self, n):
        return [{"module": f"tests.test_{i}", "code": 0, "output": "", "ran": 1, "seconds": float(i)} for i in range(n)]

    def test_five_slowest_in_descending_order_before_the_wall_clock_line(self):
        code, body = fs.report(self.results(7), 9.0)
        self.assertEqual(code, 0)
        lines = body.splitlines()
        slow = [x for x in lines if x.startswith("遅いモジュール: ")]
        self.assertEqual(len(slow), 1)
        self.assertEqual(slow[0], "遅いモジュール: " + ", ".join(f"tests.test_{i} {i:.1f} 秒" for i in (6, 5, 4, 3, 2)))
        self.assertEqual(lines.index(slow[0]) + 1, lines.index("壁時計: 9.0 秒"))

    def test_fewer_than_five_lists_them_all_and_the_exit_code_is_unchanged(self):
        results = self.results(3)
        results[1]["code"] = 1
        code, body = fs.report(results, 1.0)
        self.assertEqual(code, 1)
        slow = next(x for x in body.splitlines() if x.startswith("遅いモジュール: "))
        self.assertEqual(slow.count(" 秒"), 3)


if __name__ == "__main__":
    unittest.main()
