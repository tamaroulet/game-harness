"""改善ループの見回りの定期起動（harness/cycle_cron.py）の検査：タスクの定義・作業ディレクトリの固定・ログへの追記・machine.json の渡し方。

    python -m unittest tests.test_cycle_cron -v

タスク スケジューラは呼ばない。proc.run を差し替える。
"""
import datetime
import os
import shutil
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(ROOT / "harness")]
import cycle_base as base  # noqa: E402
import cycle_cron  # noqa: E402

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
PY, PYW = Path("Py") / "python.exe", Path("Py") / "pythonw.exe"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for target, name, value in ((base, "home", self.tmp / "cycle"), (base, "runner", self.tmp / "cycle" / "runner")):
            p = mock.patch.object(target, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)


class TaskXml(Base):
    def setUp(self):
        super().setUp()
        self.doc = ET.fromstring(cycle_cron.task_xml(str(PY), "M.json").split("\n", 1)[1])

    def text(self, path):
        return self.doc.find(path, NS).text

    def test_it_runs_every_10_minutes_with_pythonw_in_the_dedicated_clone(self):
        run = base.runner()
        self.assertEqual(self.text("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval"), "PT10M")
        self.assertEqual(self.text("t:Actions/t:Exec/t:Command"), str(PYW))
        self.assertEqual(self.text("t:Actions/t:Exec/t:Arguments"), f'"{run / "harness" / "cycle_cron.py"}" run --machine "M.json"')
        self.assertEqual(Path(self.text("t:Actions/t:Exec/t:WorkingDirectory")), run)

    def test_the_settings(self):
        self.assertEqual(self.text("t:Principals/t:Principal/t:LogonType"), "InteractiveToken")
        self.assertEqual(self.text("t:Principals/t:Principal/t:RunLevel"), "LeastPrivilege")
        self.assertEqual(self.text("t:Settings/t:MultipleInstancesPolicy"), "IgnoreNew")
        self.assertEqual(self.text("t:Settings/t:StartWhenAvailable"), "true")
        self.assertEqual(self.text("t:Settings/t:DisallowStartIfOnBatteries"), "false")
        self.assertEqual(self.text("t:Settings/t:StopIfGoingOnBatteries"), "false")
        self.assertEqual(self.text("t:Settings/t:ExecutionTimeLimit"), "PT4H")


class Run(Base):
    def setUp(self):
        super().setUp()
        cwd, exe = os.getcwd(), sys.executable
        self.addCleanup(os.chdir, cwd)
        self.addCleanup(setattr, sys, "executable", exe)
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        self.log = self.tmp / "cycle" / "tick.log"

    def run_it(self, tick, machine_file=None):
        return cycle_cron.run(machine_file, tick, lambda: datetime.datetime(2026, 11, 7, 9, 0, 0))

    def test_the_output_and_the_exit_code_go_to_the_log(self):
        self.assertEqual(self.run_it(lambda: print("見回り") or 0), 0)
        lines = self.log.read_text(encoding="utf-8").splitlines()
        self.assertTrue(lines[0].startswith("=== 2026-11-07 09:00:00 見回り"), lines[0])
        self.assertEqual(lines[1:], ["見回り", "--- 終了コード 0"])

    def test_a_crash_is_written_and_ends_with_2(self):
        def boom():
            raise RuntimeError("壊れた")
        self.assertEqual(self.run_it(boom), 2)
        self.assertIn("RuntimeError: 壊れた", self.log.read_text(encoding="utf-8"))

    def test_the_machine_file_is_passed_on(self):
        """専用のクローンの親には .local が無いので、machine.json の場所を渡す。"""
        seen = []
        self.run_it(lambda: seen.append(os.environ.get("HARNESS_MACHINE")) or 0, "M.json")
        self.assertEqual(seen, ["M.json"])

    def test_children_use_the_console_python_under_pythonw(self):
        sys.executable = str(PYW)
        self.run_it(lambda: 0)
        self.assertEqual(sys.executable, str(PY))


class Setup(Base):
    def test_an_existing_clone_is_left_alone(self):
        base.runner().mkdir(parents=True)
        with mock.patch.object(cycle_cron.proc, "run", side_effect=AssertionError("呼ばない")), mock.patch("builtins.print"):
            self.assertEqual(cycle_cron.setup(), 0)

    def test_it_clones_from_origin(self):
        calls = []

        def fake(args, cwd, ttl, label, env=None, input=None):
            calls.append(args)
            return 0, "https://example.com/r.git\n", ""
        with mock.patch.object(cycle_cron.proc, "run", side_effect=fake), mock.patch("builtins.print"):
            self.assertEqual(cycle_cron.setup(), 0)
        self.assertEqual(calls[1], ["git", "clone", "-q", "https://example.com/r.git", str(base.runner())])

    def test_register_makes_the_clone_then_the_task(self):
        calls = []

        def fake(args, cwd, ttl, label, env=None, input=None):
            calls.append(args)
            return 0, "ok\n", ""
        with mock.patch.object(cycle_cron.proc, "run", side_effect=fake), mock.patch("builtins.print"):
            self.assertEqual(cycle_cron.register(), 0)
        self.assertEqual(calls[-1][:3], ["schtasks", "/Create", "/TN"])
        self.assertEqual(calls[-1][3], "game-harness-cycle")

    def test_unregister_names_the_task(self):
        calls = []
        with mock.patch.object(cycle_cron.proc, "run", side_effect=lambda a, *r, **k: calls.append(a) or (0, "ok", "")), mock.patch("builtins.print"):
            self.assertEqual(cycle_cron.unregister(), 0)
        self.assertEqual(calls, [["schtasks", "/Delete", "/TN", "game-harness-cycle", "/F"]])


if __name__ == "__main__":
    unittest.main()
