"""harness-tests のジョブが 2 つ（test と test-linux）になったときの、統合 PR の自動マージの判定（hline_merge.verdict）。
ワークフローで走行を 1 件だけ選ぶと、開始が同じ秒のとき、もう一方のジョブが走っている途中でも赤でもマージしていた（10-31 の実測）。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import hline_merge  # noqa: E402
import hline_probe  # noqa: E402

T0, T1 = "2026-10-31T12:00:00Z", "2026-10-31T12:05:00Z"
NOT_YET = "0001-01-01T00:00:00Z"   # gh は、終わっていない走行の completedAt をこの値で返す


def job(name, status="COMPLETED", conclusion="SUCCESS", started=T0, completed=T1, workflow=hline_merge.TESTS):
    return {"workflowName": workflow, "name": name, "status": status, "conclusion": conclusion,
            "startedAt": started, "completedAt": completed}


def verdict(*tests):
    approval = job("approval", workflow=hline_merge.APPROVAL)
    return hline_merge.verdict({"statusCheckRollup": [*tests, approval], "labels": [{"name": hline_merge.LABEL}]})


class Verdict(unittest.TestCase):
    def test_waits_while_the_other_job_is_running(self):
        """開始が同じ秒で、Linux が緑・Windows が走っている途中なら、待つ（前はマージしていた）。"""
        self.assertEqual(verdict(job("test-linux"), job("test", "IN_PROGRESS", "", completed=NOT_YET)), "wait")

    def test_red_job_that_finished_first_is_not_overlooked(self):
        """開始が同じ秒で、先に終わった Linux が赤・後に終わった Windows が緑なら、CI の失敗（前はマージしていた）。"""
        linux = job("test-linux", conclusion="FAILURE", completed="2026-10-31T12:01:00Z")
        self.assertEqual(verdict(linux, job("test")), "ci_failed")

    def test_merges_when_both_jobs_are_green(self):
        self.assertEqual(verdict(job("test"), job("test-linux", completed="2026-10-31T12:02:00Z")), "merge")

    def test_a_rerun_of_one_job_replaces_its_older_run(self):
        """同じジョブの走り直しは、新しい方だけを見る（古い赤は数えない）。"""
        old = job("test-linux", conclusion="FAILURE", started=T0, completed=T0)
        self.assertEqual(verdict(old, job("test-linux", started=T1, completed=T1), job("test")), "merge")

    def test_one_job_workflow_is_as_before(self):
        """ジョブが 1 つのワークフローでは、前と同じく最新の 1 件だけを返す。"""
        runs = hline_merge._runs([job("approval", conclusion="FAILURE", started=T0, workflow="approval"),
                                  job("approval", started=T1, workflow="approval")], "approval")
        self.assertEqual([(r["name"], r["conclusion"]) for r in runs], [("approval", "SUCCESS")])


class Probe(unittest.TestCase):
    def test_probe_reports_the_named_job(self):
        """report の点検の行は、名指したジョブ（test）の走行を出す。並びの先に test-linux があっても取り違えない。"""
        rollup = [job("test-linux", "IN_PROGRESS", "", completed=NOT_YET), job("test", conclusion="FAILURE")]
        line = hline_probe._check(rollup, hline_merge.TESTS, "test")
        self.assertIn("状態 COMPLETED", line)
        self.assertIn("結論 FAILURE", line)

    def test_pr_line_shows_both_jobs(self):
        """統合 PR の CI の行は、test と test-linux の両方を出す（Linux だけが赤でも、なぜ止まったかが見える）。"""
        import json
        payload = json.dumps({"statusCheckRollup": [job("test"), job("test-linux", conclusion="FAILURE"),
                                                    job("approval", workflow=hline_merge.APPROVAL)], "labels": []})
        line = hline_probe.pr_fetch({"ttl_seconds": {"git": 5}}, lambda args, cwd, ttl, label: (0, payload, ""), "https://github.com/o/r/pull/7")
        self.assertIn("harness-tests（test）: 状態 COMPLETED／結論 SUCCESS", line)
        self.assertIn("harness-tests（test-linux）: 状態 COMPLETED／結論 FAILURE", line)
        self.assertLess(line.index("（test）"), line.index("（test-linux）"))


if __name__ == "__main__":
    unittest.main()
