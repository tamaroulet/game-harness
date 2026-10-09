"""並列で落ちて単独で通るテスト（揺れ）の扱いの検査。fastsuite.run・subprocess.run・hline の各関数を差し替え、push・PR・モデルの呼び出しは起こさない。"""
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import fastsuite as fs  # noqa: E402
import hline  # noqa: E402
import hline_report as hr  # noqa: E402

CFG = hline.load_config()
CANARY = hline.base_whitelist.unittest_command(CFG["gate_command"], CFG["canary_modules"])   # 影響テストが無いときだけ走る束（C3）
NAME = "test_x (tests.test_agy_pinned.X)"
TRACE = "Traceback (most recent call last):\n  File \"x.py\", line 1, in test_x\nAssertionError: boom"
PARALLEL_FAIL = f"FAIL: {NAME}\n{TRACE}\nFAILED (failures=1)"
ALONE_FAIL = f"FAIL: {NAME}\n{TRACE}\nalone-run"


def result(module, code=0, output="", ran=1):
    return {"module": module, "code": code, "output": output, "ran": ran, "seconds": 0.1}


def run_main(first, second):
    """main を、run の 1 回目に first・2 回目に second を返させて走らせる。(終了コード, 本文, run のモック)。"""
    with mock.patch.object(fs, "run", side_effect=[first, second]) as run, mock.patch.object(Path, "cwd", return_value=ROOT), \
            mock.patch("sys.stdout", new_callable=io.StringIO) as out:
        code = fs.main(["tests.test_a", "tests.test_agy_pinned"])
    return code, out.getvalue(), run


class Pure(unittest.TestCase):
    def test_failed_tests_takes_the_names_in_order_without_duplicates(self):
        text = "FAIL: a (m.X)  \nERROR: b (m.Y)\r\nFAIL: a (m.X)\nFAILED (failures=1)\n  FAIL: indented\n"
        self.assertEqual(fs.failed_tests(text), ("a (m.X)", "b (m.Y)"))
        self.assertEqual((fs.failed_tests(""), fs.failed_tests("OK")), ((), ()))

    def test_flaky_names_reads_the_prefix_line_even_from_a_tail(self):
        _, body = fs.report([result("tests.test_a")], 1.0, ("a (m.X)", "b (m.Y)", "a (m.X)"))
        self.assertEqual(fs.flaky_names(body), ("a (m.X)", "b (m.Y)"))
        self.assertEqual(fs.flaky_names(body[body.index(fs.FLAKY_PREFIX) - 5:]), ("a (m.X)", "b (m.Y)"))
        self.assertEqual(fs.flaky_names(f"x\n{fs.FLAKY_PREFIX}a,  b, c, a\n"), ("a", "b", "c"))
        self.assertEqual((fs.flaky_names(""), fs.flaky_names("落ちたモジュール: なし")), ((), ()))

    def test_report_without_flaky_is_unchanged_and_flaky_never_changes_the_exit_code(self):
        results = [result("tests.test_a"), result("tests.test_b", 1, "FAIL: q")]
        self.assertEqual(fs.report(results, 2.0), fs.report(results, 2.0, ()))
        self.assertNotIn(fs.FLAKY_PREFIX, fs.report(results, 2.0)[1])
        self.assertEqual(fs.report(results, 2.0, ("n",))[0], 1)
        code, body = fs.report([result("tests.test_a")], 2.0, ["n1", "n2"])
        self.assertEqual(code, 0)
        self.assertIn(fs.FLAKY_PREFIX + "n1, n2", body)

    def test_recheck_without_failures_starts_no_process(self):
        results = [result("tests.test_a"), result("tests.test_b")]
        with mock.patch.object(fs, "run") as run:
            self.assertEqual(fs.recheck(results, ROOT, None), (results, ()))
        run.assert_not_called()

    def test_recheck_runs_only_the_failed_modules_once_and_alone_and_keeps_the_order(self):
        results = [result("tests.test_a"), result("tests.test_b", 1, PARALLEL_FAIL), result("tests.test_c", 1, "FAIL: c (tests.test_c.C)")]
        again = [result("tests.test_b", 0, "ok-b"), result("tests.test_c", 1, "still-c")]
        with mock.patch.object(fs, "run", return_value=again) as run:
            new, flaky = fs.recheck(results, ROOT, 9)
        self.assertEqual(run.call_count, 1)
        self.assertEqual((list(run.call_args.args[0]), run.call_args.args[1:]), (["tests.test_b", "tests.test_c"], (1, ROOT, 9)))
        self.assertEqual([r["module"] for r in new], ["tests.test_a", "tests.test_b", "tests.test_c"])
        self.assertEqual([r["output"] for r in new], ["", "ok-b", "still-c"])
        self.assertEqual(flaky, (NAME,))
        self.assertEqual(results[1]["output"], PARALLEL_FAIL, "元のリストは変えない")

    def test_a_flake_without_a_test_name_is_recorded_by_the_module_name(self):
        for output in ("tests.test_agy_pinned は 5 秒で時間切れになり、強制終了しました", "ImportError: no module"):
            with mock.patch.object(fs, "run", return_value=[result("tests.test_agy_pinned", 0)]):
                _, flaky = fs.recheck([result("tests.test_agy_pinned", 124, output)], ROOT, None)
            self.assertEqual(flaky, ("tests.test_agy_pinned",))


class Main(unittest.TestCase):
    def test_a_failure_in_parallel_that_passes_alone_is_exit_zero_with_the_names_in_the_body(self):
        first = [result("tests.test_a"), result("tests.test_agy_pinned", 1, PARALLEL_FAIL)]
        code, body, run = run_main(first, [result("tests.test_agy_pinned", 0, "")])
        self.assertEqual(code, 0, body)
        self.assertIn(fs.FLAKY_PREFIX + NAME, body)
        self.assertNotIn("===== 失敗", body)
        self.assertEqual(run.call_count, 2)
        self.assertEqual((list(run.call_args_list[1].args[0]), run.call_args_list[1].args[1]), (["tests.test_agy_pinned"], 1))

    def test_a_failure_alone_too_is_exit_one_and_the_body_keeps_the_alone_output(self):
        first = [result("tests.test_a"), result("tests.test_agy_pinned", 1, PARALLEL_FAIL)]
        code, body, run = run_main(first, [result("tests.test_agy_pinned", 1, ALONE_FAIL)])
        self.assertEqual(code, 1)
        for text in ("===== 失敗: tests.test_agy_pinned", NAME, TRACE, "alone-run", "落ちたモジュール: tests.test_agy_pinned"):
            self.assertIn(text, body)
        self.assertNotIn(fs.FLAKY_PREFIX, body)
        self.assertEqual(run.call_count, 2, "走らせ直しは 1 回だけ")

    def test_all_green_runs_once(self):
        code, body, run = run_main([result("tests.test_a"), result("tests.test_agy_pinned")], [])
        self.assertEqual((code, run.call_count), (0, 1))
        self.assertNotIn(fs.FLAKY_PREFIX, body)

    def test_a_real_child_that_fails_first_and_passes_second_is_a_flake(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "seen"
            code_text = (f"import pathlib, sys\np = pathlib.Path({str(marker)!r})\nif p.exists():\n    print('Ran 1 test in 0.0s'); sys.exit(0)\n"
                         f"p.write_text('x')\nprint('FAIL: test_once (tests.test_z.Z)'); sys.exit(1)")
            with mock.patch.object(fs, "module_command", return_value=[sys.executable, "-c", code_text]), \
                    mock.patch.object(Path, "cwd", return_value=ROOT), mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                code = fs.main(["tests.test_z"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn(fs.FLAKY_PREFIX + "test_once (tests.test_z.Z)", out.getvalue())


class Boundary(unittest.TestCase):
    def test_every_child_command_is_the_python_unittest_runner(self):
        codes = iter([1, 0, 0])
        fake = lambda *a, **k: mock.Mock(returncode=next(codes))   # noqa: E731
        with mock.patch.object(fs.subprocess, "run", side_effect=fake) as sub, mock.patch.object(Path, "cwd", return_value=ROOT), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = fs.main(["tests.test_x", "tests.test_y", "-j", "1"])
        self.assertEqual(code, 0, out.getvalue())
        commands = [c.args[0] for c in sub.call_args_list]
        self.assertEqual(commands, [[sys.executable, "-m", "unittest", m] for m in ("tests.test_x", "tests.test_y", "tests.test_x")])
        for cmd in commands:
            self.assertFalse({"git", "gh", "claude", "agy"} & set(cmd))


class Gate(unittest.TestCase):
    def gate(self, code, body):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(hline.proc, "run", return_value=(code, body, "")) as run:
            ok, out = hline.gate(CFG, Path(tmp), ["docs/x.md"])
        self.assertEqual([c.args[0] for c in run.call_args_list], [CANARY], "テストの呼び出しは 1 回（影響テストが無いのでカナリア）")
        return ok, out

    def bodies(self, second_code):
        first = [result("tests.test_a"), result("tests.test_agy_pinned", 1, PARALLEL_FAIL)]
        alone = ALONE_FAIL if second_code else ""
        code, body, _ = run_main(first, [result("tests.test_agy_pinned", second_code, alone)])
        return code, body

    def run_task(self, ok, feedback):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(hline, "new_worktree", return_value=(Path(tmp), "br")), \
                mock.patch.object(hline, "changed_paths", return_value=["x"]), \
                mock.patch.object(hline, "implement", return_value=(0, ["m"], None, None, 3)), \
                mock.patch.object(hline, "gate", return_value=(ok, feedback)):
            return hline.run_task(CFG, "t1", {}, Path(tmp))

    def test_a_flake_does_not_fail_gate_one_and_run_task_records_the_names(self):
        code, body = self.bodies(0)
        ok, feedback = self.gate(code, body)
        self.assertTrue(ok)
        wt, branch, tries = self.run_task(ok, feedback)
        self.assertEqual(branch, "br")
        self.assertEqual(tries[0]["flaky"], [NAME])
        for key in ("run", "attempt", "cli_exit", "models", "gate", "usage", "cutoff", "turns"):
            self.assertIn(key, tries[0])

    def test_a_failure_alone_too_is_returned_to_the_implementer_and_flaky_is_empty(self):
        code, body = self.bodies(1)
        ok, feedback = self.gate(code, body)
        self.assertFalse(ok)
        for text in ("tests.test_agy_pinned", NAME, TRACE):
            self.assertIn(text, feedback)
        wt, branch, tries = self.run_task(ok, feedback)
        self.assertEqual((wt, branch), (None, None))
        self.assertEqual([t["flaky"] for t in tries], [[]] * CFG["max_attempts"])


class Report(unittest.TestCase):
    def item(self, **extra):
        tries = [{"run": 0, "attempt": 1, "cli_exit": 0, "models": ["m"], "gate": False, "flaky": ["a (m.X)"]},
                 {"run": 0, "attempt": 2, "cli_exit": 0, "models": ["m"], "gate": True, "flaky": ["a (m.X)", "b (m.Y)"]}]
        return {"status": "done", "title": "題", "milestone": "B7.1", "task": None, "deps": [], "tid": "t1", "tries": tries, **extra}

    def state(self, items):
        return {"items": items, "awaiting_pr": None, "skipped": []}

    def test_flaky_tests_flattens_in_order_without_duplicates_and_tolerates_old_records(self):
        self.assertEqual(hr.flaky_tests(self.item()), ["a (m.X)", "b (m.Y)"])
        old = {"tries": [{"run": 0}, {"run": 0, "flaky": None}, {"flaky": []}]}
        self.assertEqual((hr.flaky_tests(old), hr.flaky_tests({}), hr.flaky_tests({"tries": []})), ([], [], []))

    def test_report_md_section_names_the_item_only_when_something_flaked(self):
        quiet = self.item(tries=[{"run": 0, "attempt": 1, "cli_exit": 0, "models": ["m"], "gate": True}])
        plain = hr.h_section(CFG, self.state({"010-a": quiet}))
        self.assertNotIn("揺れた", plain)
        text = hr.h_section(CFG, self.state({"010-a": quiet, "020-b": self.item()}))
        self.assertIn("揺れたテスト", text)
        self.assertIn("- 020-b: a (m.X), b (m.Y)", text)
        self.assertNotIn("010-a: a", text)

    def test_the_pr_body_item_section_gets_one_line_with_the_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = dict(CFG, out=tmp)
            with_flaky = hr.item_section(cfg, "020-b", self.item(), True)
            quiet = hr.item_section(cfg, "010-a", self.item(tries=[{"run": 0, "attempt": 1, "cli_exit": 0, "models": ["m"], "gate": True}]), True)
        lines = [x for x in with_flaky.splitlines() if "揺れたテスト" in x]
        self.assertEqual(lines, ["- 揺れたテスト（並列で落ち、単独で通った）: a (m.X), b (m.Y)"])
        self.assertNotIn("揺れ", quiet)


if __name__ == "__main__":
    unittest.main()
