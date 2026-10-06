"""テストの出力を黙らせる仕組み（tests/quiet.py）。合格のときは静かで、落ちたときだけ失敗が届き、件数は減らない。

    python -m unittest discover -s tests -v
"""
import io
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent
sys.path.insert(0, str(TESTS_DIR))

import quiet  # noqa: E402

# 変更の前に `python -m unittest discover -s tests` で実測した件数（これより減らさない）
BASELINE_TEST_COUNT = 991
CHILD_TIMEOUT = 900


class _Synth:
    """合成のテスト。モジュール直下に TestCase を置くと探索に拾われるので、入れ物の中に置く。"""

    class Passes(quiet.Quiet, unittest.TestCase):
        def setUp(self):
            print("setUp の出力")
            self.addCleanup(print, "cleanup の出力")

        def test_it(self):
            print("標準出力に書く")
            sys.stderr.write("標準エラーに書く\n")
            self.assertEqual(self.written(), "setUp の出力\n標準出力に書く\n標準エラーに書く\n")

    class Fails(quiet.Quiet, unittest.TestCase):
        def test_it(self):
            print("落ちる前の出力")
            self.fail("わざと落とす")

    class Inner(quiet.Quiet, unittest.TestCase):
        def test_it(self):
            with mock.patch("sys.stdout", new_callable=io.StringIO) as inner:
                print("内側へ")
                self.assertEqual(inner.getvalue(), "内側へ\n")
            with mock.patch.object(sys, "stdout", None):
                self.assertIsNone(sys.stdout)
            print("外側へ")
            self.assertEqual(self.written(), "外側へ\n")


def run_case(case_class):
    out, err = io.StringIO(), io.StringIO()
    result = unittest.TestResult()
    with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
        case_class("test_it").run(result)
        after = (sys.stdout, sys.stderr)
    assert after == (out, err), "Quiet が標準出力・標準エラーを元に戻していない"
    return result, out.getvalue(), err.getvalue()


def run_child(args, cwd, timeout=CHILD_TIMEOUT):
    env = dict(os.environ, PYTHONUTF8="1")
    return subprocess.run([sys.executable, "-m", "unittest", *args], cwd=str(cwd), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env=env, timeout=timeout)


class Mechanism(unittest.TestCase):
    def test_passing_test_writes_nothing_and_keeps_buffer_for_assertions(self):
        result, out, err = run_case(_Synth.Passes)
        self.assertTrue(result.wasSuccessful(), result.failures + result.errors)
        self.assertEqual((out, err), ("", ""))

    def test_failing_test_flushes_buffer_to_the_real_stderr(self):
        result, out, err = run_case(_Synth.Fails)
        self.assertEqual((len(result.failures), out, err), (1, "", "落ちる前の出力\n"))


class WithInnerPatches(unittest.TestCase):
    def test_mock_patch_of_stdout_and_none_stdout_coexist_with_quiet(self):
        result, out, err = run_case(_Synth.Inner)
        self.assertTrue(result.wasSuccessful(), result.failures + result.errors)
        self.assertEqual((out, err), ("", ""))


class Captured(unittest.TestCase):
    def test_returns_value_and_both_streams(self):
        def fn(a, b=0):
            print("標準出力")
            sys.stderr.write("標準エラー")
            return a + b
        value, text = quiet.captured(fn, 1, b=2)
        self.assertEqual(value, 3)
        self.assertEqual(text, "標準出力\n標準エラー")

    def test_exception_propagates_and_streams_are_restored(self):
        before = (sys.stdout, sys.stderr)

        def boom():
            print("途中")
            raise ValueError("そのまま伝わる")
        with self.assertRaises(ValueError):
            quiet.captured(boom)
        self.assertEqual((sys.stdout, sys.stderr), before)


class RealSilence(unittest.TestCase):
    REPORT_LINE = re.compile(r"^([.sxFE]*|-+|Ran \d+ tests? .*|OK.*)$")

    def test_fixed_modules_are_silent_when_they_pass(self):
        r = run_child(["tests.test_canary", "tests.test_gdd_check", "tests.test_exitcode_coverage",
                       "tests.test_scheduler"], ROOT)
        self.assertEqual(r.returncode, 0, r.stderr[-4000:])
        self.assertEqual(r.stdout, "")
        stray = [line for line in r.stderr.splitlines() if not self.REPORT_LINE.match(line)]
        self.assertEqual(stray, [])


class FailuresStillArrive(unittest.TestCase):
    SOURCE = textwrap.dedent('''\
        import sys
        import unittest
        sys.path.insert(0, {tests_dir!r})
        import quiet


        class Synthetic(quiet.Quiet, unittest.TestCase):
            def test_loud_but_passing(self):
                for i in range(2000):
                    print("騒がしい出力", i)

            def test_broken_on_purpose(self):
                print("落ちる直前の出力")
                self.assertEqual(1, 2, "わざと落とした印")
        ''')

    def test_failed_test_name_traceback_and_message_reach_the_tail(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "test_synthetic.py").write_text(self.SOURCE.format(tests_dir=str(TESTS_DIR)),
                                                       encoding="utf-8")
            r = run_child(["discover", "-s", d], d, timeout=120)
        self.assertNotEqual(r.returncode, 0)
        combined = r.stderr + r.stdout
        tail = combined[-4000:]
        for needle in ("test_broken_on_purpose", "Traceback", "わざと落とした印", "落ちる直前の出力"):
            self.assertIn(needle, tail)
        self.assertNotIn("騒がしい出力", combined)


class TestCountGuard(unittest.TestCase):
    def test_discovered_count_does_not_shrink(self):
        suite = unittest.defaultTestLoader.discover(str(TESTS_DIR), top_level_dir=str(TESTS_DIR))
        self.assertGreaterEqual(suite.countTestCases(), BASELINE_TEST_COUNT)


if __name__ == "__main__":
    unittest.main()
