"""統合 PR のチェックは、ワークフローごとに最新の走行だけで判定・表示する。

    python -m unittest tests.test_hline_latest_check_run -v

**なぜ要るか**: approval は PR を開いた時点（ラベルの前）に失敗し、ラベルの後に成功する。同じ head に古い失敗と新しい成功が並ぶ。
GitHub は最新の走行で決まるので、H ラインも最新だけを見ないと、緑なのにマージしない・古い失敗を FAILURE と出す。
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import hline_merge  # noqa: E402
import hline_probe as hp  # noqa: E402

OLD, NEW = "2026-10-11T02:52:00Z", "2026-10-11T03:01:00Z"


def run(workflow, conclusion, started, status="COMPLETED"):
    return {"workflowName": workflow, "name": workflow, "status": status, "conclusion": conclusion,
            "startedAt": started, "completedAt": started}


def info(*checks, labels=("ms4:approved",)):
    return {"statusCheckRollup": list(checks), "labels": [{"name": n} for n in labels]}


class Verdict(unittest.TestCase):
    def test_old_approval_failure_new_success_merges(self):
        for order in (1, -1):
            runs = [run("approval", "FAILURE", OLD), run("approval", "SUCCESS", NEW)][::order]
            self.assertEqual(hline_merge.verdict(info(run("harness-tests", "SUCCESS", NEW), *runs)), "merge")

    def test_old_tests_failure_new_success_is_not_ci_failed(self):
        for order in (1, -1):
            runs = [run("harness-tests", "FAILURE", OLD), run("harness-tests", "SUCCESS", NEW)][::order]
            self.assertEqual(hline_merge.verdict(info(run("approval", "SUCCESS", NEW), *runs)), "merge")

    def test_old_success_new_failure_is_ci_failed(self):
        v = hline_merge.verdict(info(run("harness-tests", "SUCCESS", OLD), run("harness-tests", "FAILURE", NEW),
                                     run("approval", "SUCCESS", NEW)))
        self.assertEqual(v, "ci_failed")

    def test_new_run_in_progress_waits(self):
        pending = {"workflowName": "harness-tests", "name": "test", "status": "IN_PROGRESS", "conclusion": "",
                   "startedAt": NEW, "completedAt": "0001-01-01T00:00:00Z"}
        v = hline_merge.verdict(info(run("harness-tests", "SUCCESS", OLD), pending, run("approval", "SUCCESS", NEW)))
        self.assertEqual(v, "wait")
        v = hline_merge.verdict(info(run("harness-tests", "SUCCESS", NEW), run("approval", "SUCCESS", OLD),
                                     dict(pending, workflowName="approval")))
        self.assertEqual(v, "wait")

    def test_same_started_at_uses_completed_at(self):
        a, b = run("approval", "FAILURE", OLD), run("approval", "SUCCESS", OLD)
        b["completedAt"] = NEW
        self.assertEqual(hline_merge.verdict(info(run("harness-tests", "SUCCESS", NEW), a, b)), "merge")

    def test_single_runs_unchanged(self):
        ok = [run("harness-tests", "SUCCESS", NEW), run("approval", "SUCCESS", NEW)]
        self.assertEqual(hline_merge.verdict(info(*ok)), "merge")
        self.assertEqual(hline_merge.verdict(info(*ok, labels=())), "wait")
        self.assertEqual(hline_merge.verdict(info(run("harness-tests", "FAILURE", NEW), ok[1])), "ci_failed")
        self.assertEqual(hline_merge.verdict(info(ok[0], run("approval", "FAILURE", NEW))), "wait")
        self.assertEqual(hline_merge.verdict(info(ok[0])), "wait")


class Report(unittest.TestCase):
    CFG = {"ttl_seconds": {"git": 5}}

    def fetch(self, *checks):
        payload = json.dumps(info(*checks))
        return hp.pr_fetch(self.CFG, lambda args, cwd, ttl, label: (0, payload, ""), "https://github.com/o/r/pull/7")

    def test_latest_run_is_shown(self):
        line = self.fetch(run("approval", "FAILURE", OLD), run("approval", "SUCCESS", NEW),
                          run("harness-tests", "SUCCESS", OLD), run("harness-tests", "FAILURE", NEW))
        self.assertIn("approval（approval）: 状態 COMPLETED／結論 SUCCESS", line)
        self.assertIn("harness-tests（test）: 状態 COMPLETED／結論 FAILURE", line)

    def test_in_progress_latest_is_shown(self):
        pending = dict(run("approval", "", NEW, status="IN_PROGRESS"), completedAt="0001-01-01T00:00:00Z")
        line = self.fetch(run("approval", "FAILURE", OLD), pending)
        self.assertIn("状態 IN_PROGRESS／結論 なし／所要 -", line)
        self.assertNotIn("FAILURE", line)

    def test_single_run_and_missing(self):
        line = self.fetch(run("approval", "SUCCESS", NEW))
        self.assertIn("harness-tests: チェックなし", line)
        self.assertIn("結論 SUCCESS", line)
        self.assertIn("ms4:approved あり", line)


if __name__ == "__main__":
    unittest.main()
