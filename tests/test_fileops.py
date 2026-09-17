"""ファイルロック（WinError 32）への再試行（Step 5）。

    python -m unittest discover -s tests -v

- ロック系のエラーだけを再試行し、それ以外（ファイルが無い等）はそのまま上げること
- 再試行し尽くしたら FileLockError（ABORT の理由として表示できる）
- 実物の共有違反: 開いたまま（共有削除なし）のファイルの置き換えが、離されたあとに成功すること
- sandbox_reset が、git reset / clean の後に作業ツリーが空であることを確かめ、空でなければやり直す／止まること
"""
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import fileops  # noqa: E402
import pipeline  # noqa: E402


def sharing_violation():
    e = PermissionError(13, "The process cannot access the file because it is being used by another process")
    e.winerror = 32
    return e


class RetryTests(unittest.TestCase):
    def test_lock_errors_are_retried_until_success(self):
        calls, sleeps = [], []

        def fn():
            calls.append(1)
            if len(calls) <= 2:
                raise sharing_violation()
            return "ok"
        self.assertEqual(fileops.retry_os(fn, "test", sleep=sleeps.append), "ok")
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleeps, list(fileops.DELAYS[:2]))

    def test_gives_up_with_file_lock_error(self):
        sleeps = []

        def fn():
            raise sharing_violation()
        with self.assertRaises(fileops.FileLockError) as ctx:
            fileops.retry_os(fn, "削除 X", sleep=sleeps.append)
        self.assertEqual(len(sleeps), len(fileops.DELAYS))
        self.assertIn("削除 X", str(ctx.exception))

    def test_other_errors_are_not_retried(self):
        for err in (FileNotFoundError(2, "no"), IsADirectoryError(21, "dir"), OSError(22, "invalid")):
            sleeps = []
            with self.subTest(type(err).__name__), self.assertRaises(type(err)):
                fileops.retry_os(lambda err=err: (_ for _ in ()).throw(err), "x", sleep=sleeps.append)
            self.assertEqual(sleeps, [])

    def test_unlink_missing_is_fine(self):
        with tempfile.TemporaryDirectory() as d:
            fileops.unlink(Path(d) / "none.txt")

    @unittest.skipUnless(sys.platform == "win32", "Windows の共有違反を実物で確かめる")
    def test_real_sharing_violation_on_replace(self):
        """ほかのプロセスが（削除共有なしで）開いている間は置き換えられない。離されたら成功する。"""
        with tempfile.TemporaryDirectory() as d:
            dst, src = Path(d) / "heartbeat.json", Path(d) / "new.json"
            dst.write_text("old", encoding="utf-8")
            src.write_text("new", encoding="utf-8")
            holder = subprocess.Popen(
                [sys.executable, "-c",
                 "import sys,time; f=open(sys.argv[1]); print('held', flush=True); time.sleep(1.5)", str(dst)],
                stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(holder.stdout.readline().strip(), "held")
                with self.assertRaises(PermissionError):
                    os.replace(src, dst)
                fileops.replace(src, dst)   # 待って再試行するうちに離される
            finally:
                holder.wait(timeout=30)
            self.assertEqual(dst.read_text(encoding="utf-8"), "new")


class SandboxResetTests(unittest.TestCase):
    """git は偽物。status の結果だけを制御して、sandbox_reset の再試行と中止を確かめる。"""

    def ctx(self, d):
        (Path(d) / ".git").mkdir()
        return SimpleNamespace(sandbox=Path(d), repo=Path(d), ttl={"git": 1}, test_driven=True)

    def fake_run(self, statuses):
        calls = []

        def run(args, cwd, ttl, label, env=None):
            calls.append(args[1])
            if args[1] == "rev-parse":
                return 0, "abc\n", ""
            if args[1] == "status":
                return 0, statuses.pop(0) if len(statuses) > 1 else statuses[0], ""
            return 0, "", ""
        return run, calls

    def test_retries_until_the_tree_is_clean(self):
        run, calls = self.fake_run([" M Game/Assets/Core/Locked.dll\n", ""])
        with tempfile.TemporaryDirectory() as d, mock.patch.object(pipeline, "run", side_effect=run), \
                mock.patch.object(pipeline, "purge_holdout"), mock.patch.object(pipeline.time, "sleep") as sleep:
            pipeline.sandbox_reset(self.ctx(d))
        self.assertEqual(calls.count("reset"), 2)
        self.assertEqual(calls.count("clean"), 2)
        self.assertEqual(sleep.call_count, 1)

    def test_aborts_when_files_stay_locked(self):
        run, calls = self.fake_run(["?? tests/Core.Tests/bin/Locked.dll\n"])
        with tempfile.TemporaryDirectory() as d, mock.patch.object(pipeline, "run", side_effect=run), \
                mock.patch.object(pipeline, "purge_holdout"), mock.patch.object(pipeline.time, "sleep"):
            with self.assertRaises(SystemExit) as ctx:
                pipeline.sandbox_reset(self.ctx(d))
        self.assertIn("ABORT", str(ctx.exception.code))
        self.assertIn("Locked.dll", str(ctx.exception.code))
        self.assertEqual(calls.count("reset"), len(fileops.DELAYS) + 1)


if __name__ == "__main__":
    unittest.main()
