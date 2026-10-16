"""H ラインの定期起動（harness/hline_cron.py）の検査：タスクの定義・作業ディレクトリの固定・poll.log への追記・画面を出さない起動。

    python -m unittest tests.test_hline_cron -v

タスク スケジューラへの登録（register）は呼ばない。proc.run を差し替え、外部の呼び出しが起きたら落とす。
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "harness")]
import hline_cron  # noqa: E402
import proc  # noqa: E402

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
ROOT = HERE.parent


class TaskXml(unittest.TestCase):
    def setUp(self):
        self.doc = ET.fromstring(hline_cron.task_xml(r"C:\Py\python.exe").split("\n", 1)[1])

    def text(self, path):
        return self.doc.find(path, NS).text

    def test_it_runs_every_10_minutes_with_pythonw_in_this_repository(self):
        self.assertEqual(self.text("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval"), "PT10M")
        self.assertEqual(self.text("t:Actions/t:Exec/t:Command"), r"C:\Py\pythonw.exe")
        self.assertEqual(self.text("t:Actions/t:Exec/t:Arguments"), f'"{ROOT / "harness" / "hline_cron.py"}" run')
        self.assertEqual(Path(self.text("t:Actions/t:Exec/t:WorkingDirectory")), ROOT)

    def test_the_three_settings(self):
        self.assertEqual(self.text("t:Settings/t:StartWhenAvailable"), "true")   # スリープからの復帰後に逃した回を実行する
        self.assertEqual(self.text("t:Settings/t:MultipleInstancesPolicy"), "IgnoreNew")   # 前の回が走っていれば起動しない
        self.assertIsNone(self.doc.find("t:Settings/t:ExecutionTimeLimit", NS))   # 走行時間の上限は既定のまま

    def test_pythonw_is_the_one_next_to_python(self):
        self.assertEqual(hline_cron.pythonw(r"C:\a\python.exe"), Path(r"C:\a\pythonw.exe"))
        self.assertEqual(hline_cron.pythonw(r"C:\a\pythonw.exe"), Path(r"C:\a\pythonw.exe"))


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        cwd, exe = os.getcwd(), sys.executable
        self.addCleanup(os.chdir, cwd)
        self.addCleanup(setattr, sys, "executable", exe)
        p = mock.patch.object(proc, "run", side_effect=AssertionError("外部の呼び出しが起きました"))
        p.start()
        self.addCleanup(p.stop)
        self.cfg = {"out": str(self.tmp / "out")}
        self.log = self.tmp / "out" / "poll.log"

    def run_from(self, cwd, poll, hour=9):
        os.chdir(cwd)
        import datetime
        return hline_cron.run(self.cfg, poll, lambda: datetime.datetime(2026, 10, 9, hour, 0, 0))

    def test_the_working_directory_is_this_repository_wherever_it_starts(self):
        seen = []
        self.run_from(self.tmp, lambda: seen.append(Path(os.getcwd())) or 0)
        self.assertEqual(seen, [ROOT])

    def test_each_run_appends_a_time_line_and_the_output_to_poll_log(self):
        self.assertEqual(self.run_from(self.tmp, lambda: print("一回目") or 0), 0)
        self.assertEqual(self.run_from(self.tmp, lambda: print("二回目") or 1, hour=10), 1)
        lines = self.log.read_text(encoding="utf-8").splitlines()
        self.assertTrue(lines[0].startswith("=== 2026-10-09 09:00:00 poll"), lines[0])
        self.assertEqual(lines[1:3], ["一回目", "--- 終了コード 0"])
        self.assertTrue(lines[3].startswith("=== 2026-10-09 10:00:00 poll"), lines[3])
        self.assertEqual(lines[4:], ["二回目", "--- 終了コード 1"])

    def test_a_crash_is_written_to_poll_log_and_ends_with_2(self):
        def boom():
            raise RuntimeError("壊れた")
        self.assertEqual(self.run_from(self.tmp, boom), 2)
        text = self.log.read_text(encoding="utf-8")
        self.assertIn("RuntimeError: 壊れた", text)
        self.assertIn("--- 終了コード 2", text)

    def test_children_are_started_with_the_console_python_under_pythonw(self):
        sys.executable = r"C:\Py\pythonw.exe"
        self.run_from(self.tmp, lambda: 0)
        self.assertEqual(sys.executable, r"C:\Py\python.exe")


class NoWindow(unittest.TestCase):
    def test_proc_run_starts_children_without_a_window(self):
        with mock.patch.object(proc.job_object, "Child", side_effect=RuntimeError("止める")) as child, self.assertRaises(RuntimeError):
            proc.run(["x"], ROOT, 1, "t")
        kw = child.call_args.kwargs
        self.assertEqual(kw["creationflags"] & getattr(subprocess, "CREATE_NO_WINDOW", 0), getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if sys.platform == "win32":
            self.assertEqual(kw["startupinfo"].wShowWindow, 0)


if __name__ == "__main__":
    unittest.main()
