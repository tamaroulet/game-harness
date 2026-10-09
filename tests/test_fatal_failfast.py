"""待っても直らない異常（Fatal）の即時の打ち切り。

    python -m unittest tests.test_fatal_failfast -v

**なぜ要るか**: 進捗の記録の拒否や push の拒否は、待って呼び直しても直らない。Infra と同じに扱うと、新しい作業ツリーで分解役・
実装役を呼び直して無駄にモデルを使い、infra_halt でラインまで止める。Fatal はその What だけを未収束にして理由を残し、他は続ける。
外に出る副作用（push・PR・モデルの呼び出し・sleep）は起こさない（proc.run・must・sleep を差し替える）。
"""
import contextlib
import io
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import hline_base  # noqa: E402
import hline_git  # noqa: E402
import hline_report  # noqa: E402
import hline_task  # noqa: E402
import infra_retry  # noqa: E402

GIT_CFG = {"ttl_seconds": {"gate": 1, "git": 1}, "commit_trailer": "T", "integration_branch": "hline/integration"}
REASON = "進捗の記録が拒まれました"


def quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


class IntegrateFatal(unittest.TestCase):
    def run_integrate(self, complete=(0, "", ""), push=(0, "", "")):
        runs, musts = [], []

        def fake_run(args, cwd, ttl, label, env=None, input=None):
            runs.append(args)
            return complete if "complete" in args else push

        def fake_must(args, cwd, ttl, label):
            musts.append(args)
            return ""

        with mock.patch.object(hline_git.proc, "run", side_effect=fake_run), mock.patch.object(hline_git, "must", side_effect=fake_must):
            try:
                hline_git.integrate(GIT_CFG, Path("."), "120-x", "題", "S9-1")
            except hline_base.Infra as e:
                return e, runs, musts
        return None, runs, musts

    def test_a_refused_record_is_fatal_with_the_id_and_the_tail_and_runs_no_git(self):
        e, runs, musts = self.run_integrate(complete=(1, "x" * 900 + "OUT", "ERR"))
        self.assertIsInstance(e, hline_base.Fatal)
        self.assertTrue(e.fatal)
        self.assertIn("S9-1", str(e))
        self.assertIn(REASON, str(e))
        self.assertIn("再試行しません", str(e))
        self.assertTrue(str(e).endswith("OUTERR"))
        self.assertLessEqual(len(str(e).split(": ", 1)[1]), 800)
        self.assertEqual(musts, [])
        self.assertFalse([r for r in runs if r[:1] == ["git"]])

    def test_a_push_refused_for_good_is_fatal(self):
        e, _, musts = self.run_integrate(push=(1, "", "remote: error: GH006: Protected branch update failed"))
        self.assertIsInstance(e, hline_base.Fatal)
        self.assertEqual([m[1] for m in musts], ["add", "commit"])

    def test_a_push_that_failed_for_another_reason_stays_a_plain_infra(self):
        e, _, _ = self.run_integrate(push=(124, "out", "timeout err"))
        self.assertIs(type(e), hline_base.Infra)
        self.assertEqual(str(e), "git push が失敗しました（終了コード 124）: timeout err")

    def test_a_non_fast_forward_push_stays_a_plain_infra(self):
        e, _, _ = self.run_integrate(push=(1, "", "! [rejected] (non-fast-forward)"))
        self.assertIs(type(e), hline_base.Infra)

    def test_a_successful_push_raises_nothing(self):
        e, runs, musts = self.run_integrate()
        self.assertIsNone(e)
        self.assertEqual([m[1] for m in musts], ["add", "commit"])
        self.assertEqual(runs[-1][1], "push")


class Host:
    def __init__(self, tmp, integrate_error):
        self.tmp = Path(tmp)
        self.calls = {k: 0 for k in ("run_task", "new_worktree")}
        self.marks, self.integrate_error = [], integrate_error
        (self.tmp / "w.md").write_text("# T\n", encoding="utf-8")

    def counted(self, key, value):
        def fn(*a, **kw):
            self.calls[key] += 1
            return value
        return fn

    def build(self):
        ns = types.SimpleNamespace

        def integrate(*a):
            raise self.integrate_error

        return ns(what_path=lambda cfg, n: self.tmp / "w.md", slug=lambda n: "w", mark=lambda *a, **kw: self.marks.append(a),
                  run_task=self.counted("run_task", (Path("."), "b", [])), changed_paths=lambda wt, cfg: ["harness/x.py"],
                  self_change=lambda paths: None, integrate=integrate, save_state=lambda cfg, st: None,
                  today=lambda: "2026-10-06", new_worktree=self.counted("new_worktree", (Path("."), "b")))


class ProcessFatal(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.cfg = {"out": str(self.tmp / "out"), "inbox": str(self.tmp), "respecs": 0,
                    "infra_retry": {"max_retries": 2, "wait_seconds": [5]}}
        self.st = {"items": {"w.md": {"title": "題", "task": "S9-1", "milestone": "M", "status": "waiting"}}}
        self.sleep = mock.Mock()
        patcher = mock.patch.object(infra_retry, "SLEEP", self.sleep)
        patcher.start()
        self.addCleanup(patcher.stop)

    def process(self, error):
        host = Host(self.tmp, error)
        return quiet(hline_task.process, host.build(), self.cfg, self.st, "w.md"), host

    def test_a_fatal_marks_the_item_unconverged_without_retrying_or_halting(self):
        done, host = self.process(hline_base.Fatal(REASON))
        item = self.st["items"]["w.md"]
        self.assertIs(done, False)
        self.assertEqual(item["status"], "unconverged")
        self.assertEqual(item["at"], "2026-10-06")
        self.assertIn(REASON, item["reason"])
        self.assertNotIn("infra_halt", self.st)
        self.assertNotIn("infra_retries", item)
        self.assertEqual((host.calls["run_task"], host.calls["new_worktree"]), (1, 1))
        self.sleep.assert_not_called()
        self.assertTrue(host.marks)

    def test_the_reason_reaches_the_todo(self):
        self.process(hline_base.Fatal(REASON))
        hline_report.write_todo(self.cfg, self.st)
        self.assertIn(REASON, (self.tmp / "TODO.md").read_text(encoding="utf-8"))

    def test_a_plain_infra_waits_and_retries_then_halts_the_line(self):
        done, host = self.process(hline_base.Infra("git push が失敗しました（終了コード 124）: TTL 超過"))
        self.assertIs(done, False)
        self.assertEqual(host.calls["run_task"], 3)
        self.assertEqual(self.sleep.call_count, 2)
        self.assertEqual(self.st["infra_halt"]["name"], "w.md")
        self.assertEqual(self.st["items"]["w.md"]["status"], "waiting")
        self.assertEqual(len(self.st["items"]["w.md"]["infra_retries"]), 3)


class RetryFatal(unittest.TestCase):
    def test_a_fatal_goes_out_at_once_without_a_record_a_wait_or_a_log(self):
        sleep, log, calls = mock.Mock(), mock.Mock(), []

        def attempt(n):
            calls.append(n)
            raise hline_base.Fatal("x")

        with self.assertRaises(hline_base.Fatal):
            infra_retry.retry(attempt, hline_base.Infra, 3, [1], sleep=sleep, log=log)
        self.assertEqual(calls, [1])
        sleep.assert_not_called()
        log.assert_not_called()

    def test_an_exception_without_the_attribute_is_waited_for_and_retried_as_before(self):
        sleep, calls = mock.Mock(), []

        def attempt(n):
            calls.append(n)
            if n < 3:
                raise hline_base.Infra("t")
            return "ok"

        out, records = infra_retry.retry(attempt, hline_base.Infra, 3, [4, 8], sleep=sleep, log=lambda m: None)
        self.assertEqual((out, calls), ("ok", [1, 2, 3]))
        self.assertEqual([r["wait"] for r in records], [4, 8])
        self.assertEqual([c.args for c in sleep.call_args_list], [(4,), (8,)])

    def test_the_retries_run_out_into_infra_exhausted(self):
        def attempt(n):
            raise hline_base.Infra("t")

        with self.assertRaises(infra_retry.InfraExhausted) as ctx:
            infra_retry.retry(attempt, hline_base.Infra, 1, [0], sleep=mock.Mock(), log=lambda m: None)
        self.assertEqual(len(ctx.exception.records), 2)


class FatalPushReason(unittest.TestCase):
    def test_refusals_that_wait_cannot_fix_have_a_reason(self):
        for err in ("! [remote rejected] main (protected branch hook declined)", "remote: error: GH006: Protected branch update failed",
                    "remote: error: pre-receive hook declined", "git@github.com: Permission denied (publickey).",
                    "remote: Permission to a/b.git denied to someone.", "ERROR: Repository not found.",
                    "remote: error: this repository is read-only"):
            with self.subTest(err=err):
                reason = infra_retry.fatal_push_reason(1, err)
                self.assertIsInstance(reason, str)
                self.assertIn("再試行しません", reason)

    def test_non_fast_forward_success_and_unreadable_output_have_none(self):
        for code, err in ((1, "! [rejected] main -> main (non-fast-forward)"), (1, "! [rejected] (fetch first)"),
                          (1, "Your branch is behind its remote counterpart. protected branch"), (0, "GH006 protected branch"),
                          (1, "fatal: unable to access: could not resolve host"), (1, ""), (1, None)):
            with self.subTest(code=code, err=err):
                self.assertIsNone(infra_retry.fatal_push_reason(code, err))


if __name__ == "__main__":
    unittest.main()
