"""非劣性が不合格の外側の統合 PR を H ラインが閉じ、その周期を回らないとして次の周期へ進む（W-120）。

    python -m unittest tests.test_hline_regress_fail -v

**なぜ要るか**: 不合格の PR が開いたまま待ちになると、周期の報告が書かれず、人が閉じるまで周期が止まるため。gh・git・日時は偽物。
"""
import datetime
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import cycle_report  # noqa: E402
import hline_merge  # noqa: E402
import hline_outer  # noqa: E402

URL = "https://github.com/o/r/pull/9"
HEAD, OLD = "a" * 40, "b" * 40
GREEN = [{"workflowName": w, "name": w, "status": "COMPLETED", "conclusion": "SUCCESS"} for w in ("harness-tests", "approval")]
NOW = datetime.datetime(2026, 10, 22, 12, 0, 0, tzinfo=datetime.timezone.utc)


class Host:
    def __init__(self):
        self.info = {"statusCheckRollup": GREEN, "labels": [{"name": "ms4:approved"}], "headRefOid": HEAD}
        self.merged, self.closed, self.dropped, self.saved = [], [], [], 0

    def pr_state(self, cfg, url):
        return "OPEN"

    def pr_checks(self, cfg, url):
        return self.info

    def merge_pr(self, cfg, url, head=None):
        self.merged.append((url, head))

    def close_pr(self, cfg, url, comment):
        self.closed.append((url, comment))

    def drop_merged_branch(self, cfg):
        self.dropped.append(cfg["integration_branch"])

    def save_state(self, cfg, st):
        self.saved += 1


class RegressFail(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        saved = hline_merge.RECORDS
        hline_merge.RECORDS = self.tmp
        self.addCleanup(setattr, hline_merge, "RECORDS", saved)
        self.cfg = {"integration_branch": "hline/integration", "outer_integration_branch": "hline/outer",
                    "out": str(self.tmp / "out"), "cycle_min_done": 1}
        self.records = self.tmp / "records"
        self.records.mkdir()
        self.st = {"items": {
            "w-001": {"status": "done", "milestone": "outer", "pr": URL, "done_at": "2026-10-10T00:00:00+00:00"},
            "w-002": {"status": "done", "milestone": "tools", "done_at": "2026-10-11T00:00:00+00:00"},
        }, "outer": {"awaiting_pr": URL, "cycle": cycle_report.NONE, "opened_at": 1.0}}

    def record(self, rev, **fields):
        d = self.tmp / "bench" / "records" / "regress"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{rev}.json").write_text(json.dumps({"rev": rev, "passed": True, **fields}), encoding="utf-8")

    def settle(self, host):
        hline_outer.settle(host, self.cfg, self.st)

    def assert_waiting(self, host):
        self.assertEqual((host.closed, host.merged, host.dropped), ([], [], []))
        self.assertEqual(self.st["outer"]["awaiting_pr"], URL)
        self.assertEqual(self.st["items"]["w-001"]["status"], "done")

    def test_failed_record_closes_without_merging(self):
        self.record(HEAD, passed=False)
        host = Host()
        self.settle(host)
        self.assertEqual([u for u, _ in host.closed], [URL])
        self.assertIn(f"{HEAD}.json", host.closed[0][1])
        self.assertEqual(host.merged, [])
        self.assertEqual(host.dropped, ["hline/outer"])
        self.assertIsNone(self.st["outer"]["awaiting_pr"])
        item = self.st["items"]["w-001"]
        self.assertEqual(item["status"], "unconverged")
        self.assertIn("非劣性の不合格", item["reason"])
        self.assertEqual(self.st["items"]["w-002"]["status"], "done")

    def test_next_cycle_report_is_written_after_closing(self):
        self.record(HEAD, passed=False)
        self.assertIsNone(cycle_report.ensure_next(self.cfg, self.st, records=self.records, now=NOW))
        self.settle(Host())
        path = cycle_report.ensure_next(self.cfg, self.st, records=self.records, now=NOW)
        self.assertTrue(path and Path(path).exists())
        rec = cycle_report.latest(self.cfg)
        self.assertEqual(rec["names"], ["w-002"])

    def test_no_record_waits(self):
        host = Host()
        self.settle(host)
        self.assert_waiting(host)

    def test_record_for_another_rev_waits(self):
        d = self.tmp / "bench" / "records" / "regress"
        d.mkdir(parents=True)
        (d / f"{HEAD}.json").write_text(json.dumps({"rev": OLD, "passed": False}), encoding="utf-8")
        host = Host()
        self.settle(host)
        self.assert_waiting(host)

    def test_unreadable_head_waits(self):
        host = Host()
        host.info = {**host.info, "headRefOid": None}
        self.settle(host)
        self.assert_waiting(host)

    def test_gh_close_failure_waits(self):
        self.record(HEAD, passed=False)
        host = Host()
        host.close_pr = lambda cfg, url, comment: (_ for _ in ()).throw(RuntimeError("gh down"))
        self.settle(host)
        self.assert_waiting(host)

    def test_passing_record_still_merges(self):
        self.record(HEAD)
        host = Host()
        self.settle(host)
        self.assertEqual(host.merged, [(URL, HEAD)])
        self.assertEqual(host.closed, [])
        self.assertIsNone(self.st["outer"]["awaiting_pr"])

    def test_report_shows_closed_url_and_reason(self):
        self.record(HEAD, passed=False)
        self.settle(Host())
        text = "\n".join(hline_outer.section(self.cfg, self.st))
        self.assertIn(URL, text)
        self.assertIn("非劣性の不合格", text)


if __name__ == "__main__":
    unittest.main()
