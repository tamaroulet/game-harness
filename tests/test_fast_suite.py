"""全件テストの並列実行（harness/fastsuite.py）の検査。本物のプロセスは短い組み込みのコードだけ。push・PR・モデルの呼び出しは起きない。"""
import io
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import base_whitelist as bw  # noqa: E402
import fastsuite as fs  # noqa: E402
import hline  # noqa: E402

CFG = hline.load_config()
NAMES = [f"tests.test_{c}" for c in "abcd"]
TRACE = "Traceback (most recent call last):\n  File \"x.py\", line 1, in test_b\nAssertionError: boom"


def result(module, code=0, ran=1, output="", seconds=0.1):
    return {"module": module, "code": code, "output": output, "ran": ran, "seconds": seconds}


def python_command(code):
    return mock.patch.object(fs, "module_command", return_value=[sys.executable, "-c", code])


class Parallel(unittest.TestCase):
    def fake(self, delays=None):
        state, lock = {"now": 0, "peak": 0, "workers": []}, threading.Lock()

        def run_module(module, root, worker, timeout):
            with lock:
                state["now"] += 1
                state["peak"] = max(state["peak"], state["now"])
                state["workers"].append(worker)
            time.sleep((delays or {}).get(module, 0.2))
            with lock:
                state["now"] -= 1
            return result(module)

        return state, mock.patch.object(fs, "run_module", side_effect=run_module)

    def test_modules_run_at_the_same_time_but_never_more_than_jobs(self):
        state, patch = self.fake()
        with patch:
            started = time.monotonic()
            parallel = fs.run(NAMES, 4, ".", None)
            self.assertLess(time.monotonic() - started, 0.6, "逐次なら 0.8 秒")
            self.assertTrue(2 <= state["peak"] <= 4 and set(state["workers"]) <= {0, 1, 2, 3})
            state["peak"] = 0
            self.assertEqual(fs.run(NAMES, 1, ".", None), parallel, "jobs=1 でも結果は同じ")
        self.assertEqual(state["peak"], 1)

    def test_the_result_order_is_the_module_order_whatever_the_finishing_order(self):
        state, patch = self.fake({"tests.test_a": 0.3, "tests.test_b": 0.2, "tests.test_c": 0.1, "tests.test_d": 0.0})
        with patch:
            self.assertEqual([r["module"] for r in fs.run(NAMES, 4, ".", None)], NAMES)
        results = [result(n, 1 if n.endswith(("b", "d")) else 0, output=f"FAIL: {n}") for n in NAMES]
        self.assertEqual(fs.report(results, 1.0), fs.report([dict(r) for r in results], 1.0))
        body = fs.report(results, 1.0)[1]
        self.assertLess(body.index("失敗: tests.test_b"), body.index("失敗: tests.test_d"))

    def test_the_biggest_test_file_starts_first_but_the_results_keep_the_module_order(self):
        names, started = ["tests.test_textnorm", "tests.test_scheduler", "tests.test_missing"], []
        with mock.patch.object(fs, "run_module", side_effect=lambda m, *a: started.append(m) or result(m)):
            results = fs.run(names, 1, ROOT, None)
        self.assertEqual((started[0], [r["module"] for r in results]), ("tests.test_scheduler", names))

    def test_the_gate_runs_fastsuite_through_proc_run(self):
        self.assertEqual(CFG["gate_command"], ["python", "-m", "harness.fastsuite", "discover", "-s", "tests"])
        with mock.patch.object(hline.proc, "run", return_value=(0, "", "")) as run:
            ok, _ = hline.gate(CFG, Path("."), ["harness/x.py"])
        self.assertTrue(ok)
        self.assertEqual(run.call_args.args[0], CFG["gate_command"])


class Report(unittest.TestCase):
    def test_a_failure_puts_the_names_and_traceback_in_the_body_and_the_exit_code_is_nonzero(self):
        results = [result("tests.test_a", ran=5), result("tests.test_b", 1, 2, "FAIL: test_x (tests.test_b.B)\n" + TRACE)]
        code, body = fs.report(results, 3.0)
        self.assertEqual(code, 1)
        for text in ("FAIL: test_x (tests.test_b.B)", TRACE, "落ちたモジュール: tests.test_b", "走ったテスト: 7 件", "壁時計: 3.0 秒"):
            self.assertIn(text, body)
        self.assertEqual(fs.report([result("tests.test_a")], 1.0)[0], 0)

    def test_a_timeout_is_a_failure_that_names_the_module(self):
        with python_command("import time; print('partial', flush=True); time.sleep(30)"):
            r = fs.run_module("tests.test_slow", ROOT, 0, 1)
        self.assertNotEqual(r["code"], 0)
        self.assertTrue("tests.test_slow" in r["output"] and "partial" in r["output"])
        self.assertEqual(fs.report([r], 1.0)[0], 1)

    def test_ran_count_sums_every_block_and_is_zero_when_absent(self):
        self.assertEqual(fs.ran_count("....\nRan 12 tests in 0.5s\n\nOK"), 12)
        self.assertEqual(fs.ran_count("Ran 1 test in 0.1s\nRan 2 tests in 0.1s"), 3)
        self.assertEqual(fs.ran_count("ImportError"), 0)

    def test_discover_matches_the_test_files_in_name_order_without_importing_them(self):
        found = fs.discover(ROOT / "tests")
        self.assertEqual(found, tuple(sorted({f"tests.{p.stem}" for p in (ROOT / "tests").glob("test_*.py")})))
        self.assertRaises(FileNotFoundError, fs.discover, ROOT / "no_such_dir")


class Isolation(unittest.TestCase):
    PRINT = ("import os;print('|'.join([os.environ[k] for k in ('TMP','TEMP','TMPDIR','HARNESS_TEST_WORKER','PYTHONUTF8')]"
             "+[str(os.path.isdir(os.environ['TMP']))]));print('Ran 2 tests in 0.0s')")

    def test_each_module_gets_its_own_temp_directory_and_worker_number_and_it_is_removed(self):
        seen = []
        with python_command(self.PRINT):
            for worker in (3, 4):
                r = fs.run_module("tests.test_x", ROOT, worker, 30)
                seen.append(r["output"].splitlines()[0].split("|"))
                self.assertEqual((r["code"], r["ran"]), (0, 2))
        for tmp, temp, tmpdir, _, utf8, existed in seen:
            self.assertEqual((tmp, temp, utf8, existed), (temp, tmpdir, "1", "True"))
            self.assertFalse(Path(tmp).exists())
        self.assertNotEqual(seen[0][0], seen[1][0])
        self.assertEqual([s[3] for s in seen], ["3", "4"])


class Boundary(unittest.TestCase):
    def test_the_child_command_is_only_the_python_unittest_runner(self):
        cmd = fs.module_command("tests.test_x")
        self.assertEqual(cmd, [sys.executable, "-m", "unittest", "tests.test_x"])
        self.assertFalse({"git", "gh", "claude", "agy"} & set(cmd))

    def test_unittest_command_keeps_the_gate_runner_and_returns_none_for_no_modules(self):
        self.assertEqual(bw.unittest_command(CFG["gate_command"], ["tests.test_a"]), ["python", "-m", "harness.fastsuite", "tests.test_a"])
        self.assertEqual(bw.unittest_command(["python", "-m", "unittest", "discover", "-s", "tests"], ["tests.test_a"]),
                         ["python", "-m", "unittest", "tests.test_a"])
        self.assertIsNone(bw.unittest_command(CFG["gate_command"], ()))

    @mock.patch("sys.stderr", new_callable=mock.MagicMock)
    def test_main_accepts_only_discover_or_module_names_and_returns_the_report_code(self, _stderr):
        for argv in ([], ["discover"], ["discover", "-s"], ["tests.test_a", "-j", "0"], ["tests.test_a", "-j"], ["bad name"],
                     ["discover", "-s", str(ROOT / "no_such_dir")]):
            self.assertEqual(fs.main(argv), 2, argv)
        for codes, want in (((0, 0), 0), ((0, 1), 1)):
            with mock.patch.object(fs, "run", return_value=[result(n, c) for n, c in zip(NAMES, codes)]) as run, mock.patch("builtins.print"):
                self.assertEqual(fs.main(["tests.test_a", "tests.test_b", "-j", "2"]), want)
            self.assertEqual((list(run.call_args.args[0]), run.call_args.args[1]), (["tests.test_a", "tests.test_b"], 2))

    def test_main_runs_a_real_module_in_a_child_process_and_prints_the_body(self):
        with mock.patch.object(Path, "cwd", return_value=ROOT), mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = fs.main(["tests.test_textnorm"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertRegex(out.getvalue(), r"走ったテスト: [1-9]\d* 件")


if __name__ == "__main__":
    unittest.main()
