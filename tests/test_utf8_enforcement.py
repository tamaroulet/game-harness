"""proc.run でのサブプロセス UTF-8 強制（ADR-004、手順 2 止血）。

Windows / PowerShell / Python 環境での CP932 混入・文字化けを防ぐため、
proc.run が常に PYTHONUTF8=1 と PYTHONIOENCODING=utf-8 を注入することを検証する。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import proc  # noqa: E402


class Utf8EnforcementTests(unittest.TestCase):
    def test_proc_run_injects_pythonutf8_into_subprocess(self):
        code, out, err = proc.run(
            [sys.executable, "-c", "import os; print(os.environ.get('PYTHONUTF8'), os.environ.get('PYTHONIOENCODING'))"],
            cwd=ROOT,
            ttl=10,
            label="test_utf8_env",
        )
        self.assertEqual(code, 0)
        self.assertIn("1 utf-8", out)

    def test_proc_run_preserves_custom_env_while_injecting_utf8(self):
        custom_env = {"MY_CUSTOM_VAR": "custom_val"}
        code, out, err = proc.run(
            [sys.executable, "-c", "import os; print(os.environ.get('MY_CUSTOM_VAR'), os.environ.get('PYTHONUTF8'))"],
            cwd=ROOT,
            ttl=10,
            label="test_custom_env",
            env=custom_env,
        )
        self.assertEqual(code, 0)
        self.assertIn("custom_val 1", out)

    def test_proc_run_handles_japanese_utf8_io(self):
        japanese_text = "日本語テスト：文字コード共通強制\n"
        code, out, err = proc.run(
            [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
            cwd=ROOT,
            ttl=10,
            label="test_japanese_io",
            input=japanese_text,
        )
        self.assertEqual(code, 0)
        self.assertEqual(out, japanese_text)


if __name__ == "__main__":
    unittest.main()
