"""外側の統合 PR は、非劣性の検査（W-57）に合格したときだけ H ラインがマージする（W-58）。

    python -m unittest tests.test_hline_outer_merge -v

**なぜ要るか**: 非劣性に通らない基盤の改善を main に入れないため。gh と記録の置き場は偽物（実際の gh・ネットワーク・ベンチは使わない）。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import hline_merge  # noqa: E402
import hline_outer  # noqa: E402

URL = "https://github.com/o/r/pull/9"
HEAD, OLD = "a" * 40, "b" * 40
GREEN = [{"workflowName": w, "name": w, "status": "COMPLETED", "conclusion": "SUCCESS"} for w in ("harness-tests", "approval")]
CFG = {"integration_branch": "hline/integration", "outer_integration_branch": "hline/outer"}


class Host:
    def __init__(self, labels=("ms4:approved",), checks=GREEN, head=HEAD):
        self.info = {"statusCheckRollup": checks, "labels": [{"name": n} for n in labels], "headRefOid": head}
        self.merged, self.saved = [], 0

    def pr_state(self, cfg, url):
        return "OPEN"

    def pr_checks(self, cfg, url):
        return self.info

    def merge_pr(self, cfg, url, head=None):
        self.merged.append((url, head))

    def drop_merged_branch(self, cfg):
        pass

    def save_state(self, cfg, st):
        self.saved += 1


class OuterMerge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        saved = hline_merge.RECORDS
        hline_merge.RECORDS = Path(self.tmp.name)
        self.addCleanup(setattr, hline_merge, "RECORDS", saved)
        self.st = {"items": {}, "outer": {"awaiting_pr": URL}}

    def record(self, rev, text=None, **fields):
        d = Path(self.tmp.name) / "bench" / "records" / "regress"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{rev}.json").write_text(text if text is not None else json.dumps({"rev": rev, "passed": True, **fields}), encoding="utf-8")

    def settle(self, host):
        hline_outer.settle(host, CFG, self.st)

    def assert_waiting(self, host):
        self.assertEqual(host.merged, [])
        self.assertEqual(self.st["outer"]["awaiting_pr"], URL)
        self.assertTrue(self.st["outer"].get("merge_note"))

    def test_passing_record_for_the_head_merges_once_pinned_to_the_head(self):
        self.record(HEAD)
        host = Host()
        self.settle(host)
        self.assertEqual(host.merged, [(URL, HEAD)])
        self.assertIsNone(self.st["outer"]["awaiting_pr"])

    def test_no_record_waits_and_leaves_the_reason(self):
        host = Host()
        self.settle(host)
        self.assert_waiting(host)
        self.assertIn(f"{HEAD}.json", self.st["outer"]["merge_note"])

    def test_failed_record_waits(self):
        self.record(HEAD, json.dumps({"rev": HEAD, "passed": False}))
        host = Host()
        self.settle(host)
        self.assert_waiting(host)

    def test_broken_records_wait(self):
        for text in ("{not json", json.dumps({"rev": HEAD, "passed": "yes"}), json.dumps({"rev": HEAD}), "[]"):
            with self.subTest(text=text):
                self.record(HEAD, text)
                host = Host()
                self.settle(host)
                self.assert_waiting(host)

    def test_record_whose_rev_differs_from_the_head_waits(self):
        self.record(HEAD, json.dumps({"rev": OLD, "passed": True}))
        host = Host()
        self.settle(host)
        self.assert_waiting(host)

    def test_only_an_old_head_record_does_not_merge_a_new_head(self):
        self.record(OLD)
        host = Host(head=HEAD)
        self.settle(host)
        self.assert_waiting(host)

    def test_passing_record_does_not_override_missing_label_or_red_checks(self):
        self.record(HEAD)
        red = [dict(GREEN[0], conclusion="FAILURE"), GREEN[1]]
        pending = [GREEN[0], dict(GREEN[1], status="IN_PROGRESS", conclusion=None)]
        for host in (Host(labels=()), Host(checks=red), Host(checks=pending)):
            self.st["outer"] = {"awaiting_pr": URL}
            self.settle(host)
            self.assertEqual(host.merged, [])
            self.assertEqual(self.st["outer"]["awaiting_pr"], URL)

    def test_ci_failure_is_still_reported(self):
        red = [dict(GREEN[0], conclusion="FAILURE"), GREEN[1]]
        self.settle(Host(checks=red))
        self.assertEqual(self.st["auto_merge"]["kind"], "CI_FAILED")

    def test_a_head_that_moved_makes_the_merge_fail_and_the_pr_waits(self):
        self.record(HEAD)
        host = Host()
        host.merge_pr = lambda cfg, url, head=None: (_ for _ in ()).throw(hline_merge.Infra("head moved"))
        self.settle(host)
        self.assertEqual(self.st["outer"]["awaiting_pr"], URL)
        self.assertEqual(self.st["auto_merge"]["kind"], "MERGE_FAILED")

    def test_gh_failure_waits_without_raising(self):
        host = Host()
        host.pr_checks = lambda cfg, url: (_ for _ in ()).throw(RuntimeError("gh down"))
        self.settle(host)
        self.assertEqual(self.st["outer"]["awaiting_pr"], URL)

    def test_the_report_section_shows_the_reason(self):
        self.settle(Host())
        text = "\n".join(hline_outer.section(CFG, self.st))
        self.assertIn("非劣性", text)
        self.assertIn(f"{HEAD}.json", text)


class InnerMerge(unittest.TestCase):
    def test_inner_pr_merges_without_a_record(self):
        host = Host()
        st = {"items": {}}
        self.assertEqual(hline_merge.settle(host, CFG, st, URL), "merged")
        self.assertEqual(host.merged, [(URL, None)])
        self.assertNotIn("outer", st)


if __name__ == "__main__":
    unittest.main()
