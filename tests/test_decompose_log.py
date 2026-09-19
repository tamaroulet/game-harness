"""分解役の生出力が、TTL 超過でもディスクへ残ること（harness/decompose.py）。

    python -m unittest discover -s tests -v

**なぜ要るか**: 2026-09-19 の Issue #14 は、分解役（claude）が 600 秒の TTL にかかって
rc=124 で落ちた。当時は打ち切り時点の出力を捨てていた（`TimeoutExpired` を握りつぶして
空文字を返していた）ため、分解役が 12 分間なにをしていたのかが、ハーネスの記録からは
一切たどれなかった。実際には使い捨てのプロジェクトを立てて出現順を算出していたのだが、
それが分かったのは CLI 自身のセッション記録を別に掘ったからで、こちらには何も残って
いなかった。

`TimeoutExpired` は kill のあとに communicate() した結果を持っている。捨てる理由は無い。
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import decompose  # noqa: E402


class DecomposeLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.run_dir = self.tmp / "run"
        self.prompt_dir = self.tmp / "prompt"

        # モジュールのグローバルを退避して差し替える（1 プロセスで他のテストと同居するため）
        for name in ("CFG", "ROOT", "LOG_DIR"):
            self.addCleanup(setattr, decompose, name, getattr(decompose, name))
        decompose.ROOT = self.tmp / "repo"
        decompose.LOG_DIR = self.run_dir
        decompose.CFG = {
            "cli": "claude",
            "headless_flag": "-p",
            "prompt_arg_template": "{prompt_file} を読んでください",
            "extra_flags": ["--dangerously-skip-permissions"],
            "output_format_args": [],
            "prompt_file": str(self.prompt_dir / "prompt.md"),
            "ttl_seconds": {"claude": 600, "gh": 60},
        }

    def log(self, d=None):
        return (d or self.run_dir) / "decompose_response.log"

    # ---- 打ち切り時点の出力を捨てない（退行そのもの）

    def test_partial_output_survives_a_timeout(self):
        """TimeoutExpired は kill 後の出力を持っている。ここを捨てると原因が追えない。"""
        boom = subprocess.TimeoutExpired("claude", 600, output="途中まで書いた JSON",
                                         stderr="ここで打ち切られた")
        with mock.patch.object(decompose.subprocess, "run", side_effect=boom):
            rc, out, err = decompose.run(["claude"], 600, "claude")
        self.assertEqual(rc, 124)
        self.assertEqual(out, "途中まで書いた JSON")
        self.assertIn("TTL超過 (600s): claude", err)
        self.assertIn("ここで打ち切られた", err)

    def test_a_timeout_with_no_output_is_still_handled(self):
        """出力が無いまま打ち切られることもある。None を文字列として扱う。"""
        boom = subprocess.TimeoutExpired("claude", 600, output=None, stderr=None)
        with mock.patch.object(decompose.subprocess, "run", side_effect=boom):
            rc, out, err = decompose.run(["claude"], 600, "claude")
        self.assertEqual((rc, out), (124, ""))
        self.assertIn("TTL超過", err)

    # ---- ログの書き出し

    def test_the_log_is_written_when_the_cli_times_out(self):
        """これが退行そのもの。TTL 超過こそ記録が要る場面。"""
        with mock.patch.object(decompose, "resolve_cli", return_value="claude.exe"), \
             mock.patch.object(decompose, "run",
                               return_value=(124, "途中まで書いた JSON",
                                             "TTL超過 (600s): claude")), \
             mock.patch("builtins.print"), \
             self.assertRaises(SystemExit):
            decompose.call_claude("指示の本文")
        body = self.log().read_text(encoding="utf-8")
        self.assertIn("途中まで書いた JSON", body)
        self.assertIn("TTL超過 (600s): claude", body)
        self.assertIn("- rc: 124", body)
        self.assertIn("打ち切り時点", body, "打ち切りであることが読み手に分かる")

    def test_the_log_is_written_on_success(self):
        with mock.patch.object(decompose, "resolve_cli", return_value="claude.exe"), \
             mock.patch.object(decompose, "run", return_value=(0, '{"ok": true}', "")), \
             mock.patch("builtins.print"):
            out = decompose.call_claude("指示の本文")
        self.assertEqual(out, '{"ok": true}')
        self.assertIn('{"ok": true}', self.log().read_text(encoding="utf-8"))

    def test_the_prompt_is_kept(self):
        """指示ファイルはプロジェクトごとに使い回して上書きされる。何を渡した出力かが要る。"""
        with mock.patch.object(decompose, "resolve_cli", return_value="claude.exe"), \
             mock.patch.object(decompose, "run", return_value=(0, "x", "")), \
             mock.patch("builtins.print"):
            decompose.call_claude("この単位のプロンプト本文")
        self.assertIn("この単位のプロンプト本文", self.log().read_text(encoding="utf-8"))

    def test_it_falls_back_next_to_the_prompt_file(self):
        """--telemetry を渡さない手動実行でも残す。"""
        decompose.LOG_DIR = None
        with mock.patch("builtins.print"):
            decompose.write_decompose_log("指示", 0, "応答", "")
        self.assertIn("応答", self.log(self.prompt_dir).read_text(encoding="utf-8"))

    def test_a_failed_write_does_not_stop_the_run(self):
        """観測のための機能。判定には使わないので、書けなくても実行は止めない。"""
        with mock.patch.object(decompose.Path, "write_text", side_effect=OSError("掴まれています")), \
             mock.patch("builtins.print"):
            self.assertIsNone(decompose.write_decompose_log("指示", 0, "応答", ""))
        self.assertFalse(self.log().exists())


if __name__ == "__main__":
    unittest.main()
