"""bench のジョブ（harness/bench_job.py）の検査：タスクの定義・バッチ・起こす前の断り・状態の読み方・登録の手順。

    python -m unittest tests.test_bench_job -v

タスク スケジューラは呼ばない。proc.run と subprocess.run を差し替え、外部の呼び出しは記録するか落とす。
"""
import json
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
import bench_job  # noqa: E402

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "bench" / "cycle"
        self.home.mkdir(parents=True)
        self.patch(bench_job, "home", return_value=self.home)
        self.calls = []
        self.task = "Ready 0"
        self.run_code = 0
        self.patch(bench_job.proc, "run", side_effect=self.fake)

    def patch(self, *args, **kw):
        p = mock.patch.object(*args, **kw)
        self.addCleanup(p.stop)
        return p.start()

    def fake(self, args, cwd, ttl, label, env=None, input=None):
        self.calls.append(list(args))
        if args[0] == "powershell":
            return 0, self.task + "\n", ""
        if args[0] == "whoami":
            return 0, '"PC\\me","S-1-5-21-1-2-3-1001"\n', ""
        return self.run_code, "SUCCESS\n", ""

    def ran(self, name):
        return [c for c in self.calls if c[0] == name]


class TaskXml(Base):
    def setUp(self):
        super().setUp()
        self.doc = ET.fromstring(bench_job.task_xml().split("\n", 1)[1])

    def text(self, path):
        return self.doc.find(path, NS).text

    def test_it_runs_the_one_batch_as_bench_with_a_stored_password(self):
        self.assertEqual(self.text("t:Principals/t:Principal/t:UserId"), "bench")
        self.assertEqual(self.text("t:Principals/t:Principal/t:LogonType"), "Password")
        self.assertEqual(self.text("t:Principals/t:Principal/t:RunLevel"), "LeastPrivilege")
        self.assertEqual(self.text("t:Actions/t:Exec/t:Command"), "cmd.exe")
        self.assertEqual(self.text("t:Actions/t:Exec/t:Arguments"), f'/c "{self.home / "job.bat"}"')

    def test_it_has_no_trigger_and_never_runs_on_its_own(self):
        self.assertEqual(list(self.doc.find("t:Triggers", NS)), [])
        self.assertEqual(self.text("t:Settings/t:StartWhenAvailable"), "false")
        self.assertEqual(self.text("t:Settings/t:WakeToRun"), "false")

    def test_the_settings_match_the_runas_runs(self):
        self.assertEqual(self.text("t:Settings/t:Priority"), "5")   # タスクの既定の 7 は Below Normal
        self.assertEqual(self.text("t:Settings/t:MultipleInstancesPolicy"), "IgnoreNew")
        self.assertEqual(self.text("t:Settings/t:ExecutionTimeLimit"), "PT24H")
        self.assertEqual(self.text("t:Settings/t:DisallowStartIfOnBatteries"), "false")
        self.assertEqual(self.text("t:Settings/t:StopIfGoingOnBatteries"), "false")
        self.assertEqual(self.text("t:Settings/t:AllowStartOnDemand"), "true")


class Batch(Base):
    def text(self):
        return bench_job.batch_text("j1", "C:/w", ["python -m a", "python -m b --x y"])

    def test_it_is_ascii_with_crlf_and_never_waits_for_a_person(self):
        data = self.text().encode("ascii")
        self.assertEqual(data.count(b"\n"), data.count(b"\r\n"))
        self.assertNotIn("pause", self.text())

    def test_it_uses_the_bench_environment_before_anything_else(self):
        lines = self.text().splitlines()
        self.assertEqual(lines[2], f"call {self.home.parent / 'bench_env.bat'}")
        self.assertEqual(lines[4], "cd /d C:/w || goto end")

    def test_it_stops_at_the_first_failure_and_always_writes_the_exit_code(self):
        d = self.home / "jobs" / "j1"
        lines = self.text().splitlines()
        self.assertEqual(lines[5:], [f"python -m a >> {d / 'job.log'} 2>&1 || goto end",
                                     f"python -m b --x y >> {d / 'job.log'} 2>&1 || goto end",
                                     ":end", f"echo exit=%ERRORLEVEL% > {d / 'done'}"])

    def test_errorlevel_left_by_the_environment_does_not_stop_the_job(self):
        self.assertNotIn("if errorlevel", self.text())   # set X= で残る ERRORLEVEL 1 に引っかからない・負の終了コードも止める

    def test_the_batch_really_behaves_that_way_in_cmd(self):
        if sys.platform != "win32":   # cmd が無い
            return
        env = self.home.parent / "bench_env.bat"
        env.write_bytes(b"@echo off\r\nset L7_NOT_DEFINED_ANYWHERE=\r\n")   # ERRORLEVEL を 1 にして返す
        work = self.tmp / "w"
        work.mkdir()
        (self.home / "jobs" / "j1").mkdir(parents=True)
        py = sys.executable
        marker = work / "next.txt"
        touch = f'"{py}" -c "open(r\'{marker}\', \'w\').close()"'
        for cmd, want, reaches_next in ((f'"{py}" -c "print(1)"', 0, True), (f'"{py}" -c "import sys; sys.exit(7)"', 7, False)):
            marker.unlink(missing_ok=True)
            bat = self.home / "t.bat"
            bat.write_bytes(bench_job.batch_text("j1", work, [cmd, touch]).encode("ascii"))
            subprocess.run(["cmd.exe", "/c", str(bat)], timeout=60, capture_output=True)
            self.assertEqual(bench_job.exit_code(self.home / "jobs" / "j1"), want)
            self.assertEqual(marker.exists(), reaches_next)

    def test_the_shape_of_a_job_is_checked(self):
        self.assertEqual(bench_job.problems("a.b-c_1", "C:/w", ["x"]), [])
        for bad in (("a b", "C:/w", ["x"]), ("../a", "C:/w", ["x"]), ("a", "C:/w", []),
                    ("a", "C:/作業", ["x"]), ("a", "C:/w", ["x\ny"]), ("a", "C:/w", ["%PATH%"]), ("a", "C:/w", ["日本語"])):
            self.assertTrue(bench_job.problems(*bad), bad)


class Submit(Base):
    def test_it_replaces_the_batch_and_starts_the_task(self):
        d = bench_job.submit("j1", "C:/w", ["python -m a"])
        self.assertEqual(self.ran("schtasks"), [["schtasks", "/Run", "/TN", "game-harness-bench"]])
        self.assertEqual((self.home / "job.bat").read_bytes().decode("ascii"), bench_job.batch_text("j1", "C:/w", ["python -m a"]))
        self.assertEqual(json.loads((d / "job.json").read_text(encoding="utf-8"))["cmds"], ["python -m a"])

    def test_it_refuses_while_a_job_runs_and_leaves_the_batch_alone(self):
        self.task = "Running 267009"
        with self.assertRaisesRegex(bench_job.JobError, "走っているジョブ"):
            bench_job.submit("j1", "C:/w", ["python -m a"])
        self.assertFalse((self.home / "job.bat").exists())
        self.assertEqual(self.ran("schtasks"), [])

    def test_it_refuses_the_same_id_and_a_bad_shape_without_touching_the_task(self):
        (self.home / "jobs" / "j1").mkdir(parents=True)
        with self.assertRaisesRegex(bench_job.JobError, "同じ id"):
            bench_job.submit("j1", "C:/w", ["python -m a"])
        with self.assertRaisesRegex(bench_job.JobError, "ASCII"):
            bench_job.submit("j2", "C:/w", ["日本語"])
        self.assertEqual(self.calls, [])

    def test_a_refused_start_is_an_error(self):
        self.run_code = 1
        with self.assertRaisesRegex(bench_job.JobError, "起こせません"):
            bench_job.submit("j1", "C:/w", ["python -m a"])


class Status(Base):
    def job(self, epoch=1000.0, **files):
        d = self.home / "jobs" / "j1"
        d.mkdir(parents=True)
        (d / "job.json").write_text(json.dumps({"epoch": epoch}), encoding="utf-8")
        for name, text in files.items():
            (d / name).write_text(text, encoding="ascii")
        return d

    def status(self, at=2000.0):
        return bench_job.status("j1", clock=lambda: at)

    def test_an_unknown_job(self):
        self.assertEqual(self.status()["state"], "none")

    def test_done_comes_from_the_file_and_does_not_ask_the_task(self):
        self.job(done="exit=3 \r\n")
        self.assertEqual(self.status(), {"id": "j1", "state": "done", "exit": 3})
        self.assertEqual(self.calls, [])

    def test_a_running_task_is_running(self):
        self.job(started="started \r\n")
        self.task = "Running 267009"
        self.assertEqual(self.status(), {"id": "j1", "state": "running", "started": True})

    def test_just_after_the_start_a_ready_task_is_not_lost_yet(self):
        self.job(epoch=1950.0)
        self.assertEqual(self.status()["state"], "starting")

    def test_a_task_that_ended_without_done_is_lost_with_its_last_result(self):
        self.job(started="started \r\n")
        self.task = "Ready 2147943785"
        got = self.status()
        self.assertEqual((got["state"], got["last_result"], got["started"]), ("lost", "2147943785", True))

    def test_a_broken_done_file_is_not_an_exit_code(self):
        self.job(done="garbage")
        self.assertEqual(self.status()["state"], "lost")

    def test_an_unreadable_task_state_is_an_error(self):
        self.job()
        self.task = "only-one-word"
        with self.assertRaisesRegex(bench_job.JobError, "状態を読めません"):
            self.status()


class Register(Base):
    def setUp(self):
        super().setUp()
        self.sub = self.patch(bench_job.subprocess, "run", return_value=mock.Mock(returncode=0))
        self.patch(bench_job, "print", create=True)

    def test_it_refuses_without_admin_and_asks_nothing(self):
        self.patch(bench_job, "is_admin", return_value=False)
        self.assertEqual(bench_job.register(), 2)
        self.sub.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_schtasks_asks_for_the_password_and_the_account_is_granted(self):
        self.patch(bench_job, "is_admin", return_value=True)
        self.assertEqual(bench_job.register(), 0)
        args = self.sub.call_args.args[0]
        self.assertEqual(args[args.index("/RU") + 1], "bench")
        self.assertEqual(args[args.index("/RP") + 1], "*")   # 密語はコードを通らない
        self.assertNotIn("stdin", self.sub.call_args.kwargs)
        self.assertIn("timeout", self.sub.call_args.kwargs)
        self.assertIn("S-1-5-21-1-2-3-1001", self.ran("powershell")[0][-1])
        self.assertTrue((self.home / "job.bat").exists())

    def test_it_keeps_a_batch_that_is_already_there(self):
        self.patch(bench_job, "is_admin", return_value=True)
        (self.home / "job.bat").write_bytes(b"keep")
        bench_job.register()
        self.assertEqual((self.home / "job.bat").read_bytes(), b"keep")

    def test_a_failed_registration_grants_nothing(self):
        self.patch(bench_job, "is_admin", return_value=True)
        self.sub.return_value = mock.Mock(returncode=1)
        self.assertEqual(bench_job.register(), 1)
        self.assertEqual(self.calls, [])

    def test_the_grant_keeps_the_acl_and_adds_one_entry(self):
        self.assertEqual(bench_job.grant(), 0)
        script = self.ran("powershell")[0][-1]
        self.assertIn("GetSecurityDescriptor(4)", script)
        self.assertIn("(A;;FA;;;S-1-5-21-1-2-3-1001)", script)
        self.assertIn("-notmatch 'S-1-5-21-1-2-3-1001'", script)

    def test_stop_and_unregister_name_the_task(self):
        with mock.patch("builtins.print"):
            self.assertEqual(bench_job.stop(), 0)
            self.assertEqual(bench_job.unregister(), 0)
        self.assertEqual(self.ran("schtasks"), [["schtasks", "/End", "/TN", "game-harness-bench"],
                                                ["schtasks", "/Delete", "/TN", "game-harness-bench", "/F"]])


class Main(Base):
    def test_a_job_error_ends_with_2_and_the_shape_is_checked_first(self):
        with mock.patch("sys.stderr"):
            self.assertEqual(bench_job.main(["run", "--id", "a b", "--cwd", "C:/w", "--cmd", "x"]), 2)
        self.assertEqual(self.calls, [])

    def test_xml_only_prints(self):
        with mock.patch("builtins.print") as p:
            self.assertEqual(bench_job.main(["xml"]), 0)
        self.assertIn("game-harness", p.call_args.args[0])
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
