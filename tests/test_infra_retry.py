"""インフラの例外の呼び直し（進捗のタスク S2-1）の検査。World（test_hline_queue）で外部呼び出しを差し替え、SLEEP も差し替える。"""
import itertools
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "harness")]
import hline  # noqa: E402
import infra_retry  # noqa: E402
import test_hline_queue as base  # noqa: E402

CFG = base.CFG
LIMIT = CFG["infra_retry"]["max_retries"]


def flaky(calls, fails):
    def attempt(n):
        calls.append(n)
        if n <= fails:
            raise ValueError(f"失敗{n}")
        return "ok"
    return attempt


class Pure(unittest.TestCase):
    def test_classify_exit(self):
        self.assertEqual(sorted(infra_retry.INFRA_EXIT_CODES), [124, 127])
        self.assertIn("タイムアウト", infra_retry.classify_exit(124, "TTL超過"))
        self.assertIn("見つかりません", infra_retry.classify_exit(127, ""))
        for text in ("Could not resolve host: x", "Connection reset", "Connection timed out", "The remote end hung up",
                     "early EOF", "Unable to create '.git/index.lock'", "HTTP 502: Bad Gateway", "HTTP 5xx",
                     "Service Unavailable", "Authentication failed", "Bad credentials", "run: gh auth login", "Rate limit"):
            self.assertTrue(infra_retry.classify_exit(1, text), text)
        for code, text in ((0, "connection reset"), (1, "AssertionError"), (1, "HTTP 404: Not Found"), (2, "")):
            self.assertIsNone(infra_retry.classify_exit(code, text))
        self.assertEqual(infra_retry.wait_for([], 1), 0)
        self.assertEqual([infra_retry.wait_for([60, 300], k) for k in (1, 2, 3, 9)], [60, 300, 300, 300])

    def test_retry_records_waits_and_logs_each_failure(self):
        calls, slept, logged = [], [], []
        value, records = infra_retry.retry(flaky(calls, 2), ValueError, 2, [60, 300], sleep=slept.append, log=logged.append)
        self.assertEqual((value, calls, slept, len(logged)), ("ok", [1, 2, 3], [60, 300], 2))
        self.assertEqual(records, [{"attempt": 1, "reason": "失敗1", "wait": 60}, {"attempt": 2, "reason": "失敗2", "wait": 300}])
        self.assertTrue(all(x in logged[0] for x in ("失敗1", "1/2", "60")))
        self.assertEqual(infra_retry.retry(flaky([], 0), ValueError, 2, [60], sleep=slept.append, log=print), ("ok", []))

    def test_retry_gives_up_after_the_limit_with_every_record(self):
        calls, slept = [], []
        with self.assertRaises(infra_retry.InfraExhausted) as cm:
            infra_retry.retry(flaky(calls, 99), ValueError, 2, [60, 300], sleep=slept.append, log=lambda m: None)
        records = cm.exception.records
        self.assertEqual((calls, slept, [r["wait"] for r in records]), ([1, 2, 3], [60, 300], [60, 300, None]))
        self.assertTrue("3" in str(cm.exception) and "失敗3" in str(cm.exception))

    def test_other_exceptions_pass_through_and_the_module_sleep_is_the_default(self):
        with self.assertRaises(KeyError):
            infra_retry.retry(lambda n: {}[n], ValueError, 2, [0], sleep=lambda s: None, log=lambda m: None)
        slept = []
        with mock.patch.object(infra_retry, "SLEEP", new=slept.append):
            infra_retry.retry(flaky([], 1), ValueError, 1, [7], log=lambda m: None)
        self.assertEqual(slept, [7])
        text = (HERE.parent / "harness" / "infra_retry.py").read_text(encoding="utf-8")
        for name in ("hline", "hline_base", "hline_queue", "hline_report", "pipeline", "scheduler", "proc", "transient"):
            self.assertNotRegex(text, rf"(import|from) {name}\b")
        self.assertEqual(CFG["infra_retry"], {"max_retries": 2, "wait_seconds": [60, 300]})
        self.assertIn("max_attempts", CFG["_infra_retry_note"])


class Implementer(base.World):
    def patch_agents(self):   # 実装役の CLI は本物の経路を通し、proc.run だけを差し替える
        self.codes, self.calls = [], []

        def fake_run(args, cwd, ttl, label, env=None, input=None):
            if label != "実装役":
                return 0, "", ""   # 前の走行の残骸の掃除（git worktree prune など）
            self.calls.append(label)
            code = self.codes.pop(0) if self.codes else 0
            return (code, "", "TTL超過") if code else (0, base.claude_json("できた", "claude-sonnet-5-5"), "")

        self.patch(hline.proc, "run", side_effect=fake_run)
        self.patch(hline.proc, "resolve_cli", return_value=["claude"])
        self.patch(hline, "changed_paths", return_value=["harness/textnorm.py"])
        self.patch(hline, "gate", side_effect=lambda c, w, p, spec=None, task=None, **kw: (True, "ok"))

    def test_a_timeout_is_retried_and_does_not_count_as_a_try(self):
        self.codes = [124]
        self.put("010-a")
        self.assertEqual(self.poll(), 0)
        item = self.state()["items"]["010-a"]
        self.assertEqual((item["status"], self.integrated, len(self.calls), len(item["tries"]), hline.gate.call_count,
                          len(item["infra_retries"])), ("done", ["010-a"], 2, 1, 1, 1))   # 試行・Gate 1 は 1 回だけ
        self.assertIn("タイムアウト", item["infra_retries"][0]["reason"])


class Line(base.World):
    def failing_run_task(self, times):
        """実装の試行に入るところで Infra を上げる偽物。使われた作業ツリーを控える（段は 1 つ。C5）。"""
        used = []

        def fake(cfg, tid, what, outdir, first=None, task=None, on_stage=None):
            used.append(first[0] if first else None)
            if len(used) <= times:
                raise hline.Infra(f"gh が落ちた{len(used)}")
            return self.fake_run_task(cfg, tid, what, outdir, first, task)

        return used, fake

    def test_every_retry_uses_a_different_new_worktree(self):
        used, fake = self.failing_run_task(2)
        count = itertools.count(1)
        self.put("010-a")
        with mock.patch.object(hline, "run_task", side_effect=fake), mock.patch.object(
                hline, "new_worktree", side_effect=lambda c, t: (self.wt / f"w{next(count)}", "b")) as nw:
            self.assertEqual(self.poll(), 0)
        self.assertEqual((nw.call_count, len(set(used)), self.slept), (3, 3, [60, 300]))

    def test_faults_beyond_the_limit_halt_the_line_and_the_next_run_takes_the_same_what(self):
        used, fake = self.failing_run_task(99)
        self.put("010-a")
        with mock.patch.object(hline, "run_task", side_effect=fake):
            self.assertEqual(self.poll(), 2)
        self.assertEqual((len(used), self.status("010-a")), (1 + LIMIT, "waiting"))
        self.assertTrue((self.out / "queue" / "010-a.md").exists())
        self.assertEqual((self.created, self.integrated), ([], []))
        halt = self.state()["infra_halt"]
        self.assertEqual((halt["name"], halt["attempts"]), ("010-a", 1 + LIMIT))
        self.assertRegex(self.report(), r"- 人間作業: INFRA_HALTED 010-a.*gh が落ちた3")
        self.assertIn("- 状態: インフラ例外で停止", self.report())
        self.assertEqual(self.poll(), 0)
        self.assertIsNone(self.state()["infra_halt"])
        self.assertEqual((self.implemented, self.status("010-a")), (["T-010-a"], "done"))

    def test_a_gate_1_failure_is_not_retried(self):
        self.failing = {"T-010-a"}
        self.put("010-a")
        with mock.patch.object(hline, "new_worktree", side_effect=lambda c, t: (self.wt, "b")) as nw:
            self.assertEqual(self.poll(), 1)
        item = self.state()["items"]["010-a"]
        self.assertEqual(item["status"], "unconverged")
        self.assertFalse(item.get("infra_retries"))
        self.assertEqual((self.state()["infra_halt"], nw.call_count, self.slept), (None, 1, []))


if __name__ == "__main__":
    unittest.main()
