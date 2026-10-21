"""統合 PR の自動マージ（進捗のタスク W-12）。

    python -m unittest tests.test_hline_auto_merge -v

**なぜ要るか**: 統合 PR が承認済み（ms4:approved）で、approval と harness-tests が今の head で緑なら H ラインがマージする。
緑でないのにマージする・ラベルを付け外しする・CI の失敗で受信箱を止めたままにする、のいずれも事故になる。
gh は偽物（World の setUp が proc を差し替えた上で、ここでさらに gh だけ偽物にする）。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import test_hline_queue as tq  # noqa: E402
import hline  # noqa: E402

URL = "https://github.com/o/r/pull/7"


def run(workflow, conclusion="SUCCESS", status="COMPLETED"):
    return {"workflowName": workflow, "name": workflow, "status": status, "conclusion": conclusion}


GREEN = [run("harness-tests"), run("approval")]


class AutoMerge(tq.World):
    def setUp(self):
        super().setUp()
        self.calls, self.labels, self.checks, self.merge_code = [], ["ms4:approved"], list(GREEN), 0
        self.patch(hline.proc, "resolve_cli", side_effect=lambda name: [name])
        self.patch(hline.proc, "run", side_effect=self.fake_gh)

    def fake_gh(self, args, cwd, ttl, label):
        self.calls.append(list(args))
        if args[1:3] == ["pr", "view"]:
            import json
            return 0, json.dumps({"statusCheckRollup": self.checks, "labels": [{"name": n} for n in self.labels]}), ""
        if args[1:3] == ["pr", "merge"]:
            return self.merge_code, "", "merge conflict" if self.merge_code else ""
        return 0, "", ""

    def open_pr_waiting(self):
        self.put("010-a")
        self.poll()
        self.assertEqual(self.state()["awaiting_pr"], "https://github.com/o/r/pull/1")
        self.calls.clear()
        st = self.state()
        st["awaiting_pr"] = URL
        tq.hline_queue.save_state(self.cfg, st)

    def merges(self):
        return [c for c in self.calls if c[1:3] == ["pr", "merge"]]

    def assert_no_label_change(self):
        for c in self.calls:
            self.assertFalse(c[1:2] == ["pr"] and c[2:3] in (["edit"],), c)
            self.assertNotIn("--add-label", c)
            self.assertNotIn("--remove-label", c)
            self.assertNotIn("--force", c)

    def test_merges_once_when_approved_and_both_green_then_cleans_up_and_takes_inbox(self):
        self.open_pr_waiting()
        self.put("020-b")
        self.poll()
        self.assertEqual(self.merges(), [["gh", "pr", "merge", URL, "--merge"]])
        self.assertEqual(self.dropped, [1])
        self.assertIn("T-020-b", self.implemented)
        self.assert_no_label_change()

    def test_no_merge_without_label(self):
        self.open_pr_waiting()
        self.labels = []
        self.poll()
        self.assertEqual(self.merges(), [])
        self.assertIn(f"REVIEW_REQUIRED {URL}", self.report())
        self.assert_no_label_change()

    def test_no_merge_while_a_check_is_incomplete(self):
        self.open_pr_waiting()
        self.checks = [run("harness-tests"), run("approval", conclusion="", status="IN_PROGRESS")]
        self.poll()
        self.assertEqual(self.merges(), [])
        self.assertIn(f"REVIEW_REQUIRED {URL}", self.report())

    def test_no_merge_when_only_approval_is_red(self):
        self.open_pr_waiting()
        self.checks = [run("harness-tests"), run("approval", conclusion="FAILURE")]
        self.poll()
        self.assertEqual(self.merges(), [])
        self.assertIn(f"REVIEW_REQUIRED {URL}", self.report())

    def test_no_merge_when_a_check_is_missing(self):
        self.open_pr_waiting()
        self.checks = [run("harness-tests")]
        self.poll()
        self.assertEqual(self.merges(), [])

    def test_ci_failed_reports_and_resumes_the_inbox_and_keeps_the_same_pr(self):
        self.open_pr_waiting()
        self.checks = [run("harness-tests", conclusion="FAILURE"), run("approval")]
        self.open_pr = URL
        self.put("020-fix")
        self.poll()
        self.assertEqual(self.merges(), [])
        self.assertIn("T-020-fix", self.implemented)
        self.assertIn(f"CI_FAILED {URL}", self.report())
        self.assertEqual(len(self.created), 1)   # 開いている PR を使う（2 本目を作らない）
        self.assertEqual(self.state()["awaiting_pr"], URL)
        self.assert_no_label_change()

    def test_ci_failed_without_new_whats_keeps_waiting_on_the_open_pr(self):
        self.open_pr_waiting()
        self.checks = [run("harness-tests", conclusion="FAILURE"), run("approval")]
        self.poll()
        self.assertEqual(self.state()["awaiting_pr"], URL)
        self.assertIn(f"CI_FAILED {URL}", self.report())

    def test_merge_failure_reports_and_keeps_waiting_and_tries_again_next_run(self):
        self.open_pr_waiting()
        self.merge_code = 1
        self.put("020-b")
        self.poll()
        self.assertEqual(len(self.merges()), 1)
        self.assertIn(f"MERGE_FAILED {URL}：", self.report())
        self.assertEqual(self.state()["awaiting_pr"], URL)
        self.assertEqual(self.inbox_names(), [])   # 待ちの継続でも受信箱は取る（開いている PR の head は変えない）
        self.assertIn("T-020-b", self.implemented)
        self.merge_code = 0
        self.poll()
        self.assertEqual(len(self.merges()), 2)
        self.assertEqual(self.dropped[:1], [1])   # 統合ブランチを消す。保留の積みの後始末（rebuild）の削除がその後ろに続いてよい

    def test_closed_and_merged_prs_are_handled_as_before(self):
        self.open_pr_waiting()
        self.pr_state = "CLOSED"
        self.poll()
        self.assertEqual(self.dropped, [])
        self.assertIsNone(self.state()["awaiting_pr"])
        self.assertEqual(self.merges(), [])
        self.assert_no_label_change()


if __name__ == "__main__":
    unittest.main()
