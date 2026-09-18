"""実装役の生出力が試行ごとにディスクへ残ること（harness/pipeline.py の write_implementer_log）。

    python -m unittest discover -s tests -v

**なぜ要るか**: 実装役が「なぜ実装を書かなかったか」を書くのは応答本文だけで、終了コードにも
差分にも出ない。2026-09-18 の Issue #12 は 3 試行とも rc=0・差分ゼロで不合格になったが、
rc == 0 の応答を捨てていたため、実行記録からは理由をまったく追えなかった
（実装役の常駐規約がファイル編集を禁じていて、実装をチャット本文に貼るだけで終わっていた）。

観測のための機能なので、ログが書けないときに実行を止めてはいけない。それも確かめる。
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import pipeline  # noqa: E402

# 実測で採れた拒否の応答（Issue #12 の試行 1）。rc は 0 で返ってきていた。
REFUSAL = ("### 現在の状況\n"
           "現在、作業対象リポジトリに対する変更やツール実行は一切行っていません。\n"
           "副操縦士プロトコルに基づき、エージェント側での自律的なファイル書き込みを行いません。")


class ImplementerLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.out = self.tmp / "out"
        self.run_dir = self.tmp / "run"
        self.run_dir.mkdir(parents=True)

        self.c = SimpleNamespace(
            cfg={"implementer": {"cli": "agy", "headless_flag": "-p",
                                 "auto_approve_flag": "--dangerously-skip-permissions",
                                 "model_flag": "--model", "model_name": "gemini-3.8-flash-high"}},
            unit={"prompt": "GamePhase.cs を作る", "whitelist": ["Game/Assets/Core/GamePhase.cs"]},
            sandbox=self.tmp / "sandbox",
            ttl={"implementer": 300},
            out=self.out,
            tel_path=self.run_dir / "pipeline.telemetry.json",
            cur=None,
            metrics={"attempt": 1},
        )
        self.c.sb = lambda rel: self.c.sandbox / rel.replace("/", "\\")

    def call(self, rc=0, out="", err="", feedback=""):
        """実装役 CLI だけ差し替えて call_implementer を回す。"""
        with mock.patch("builtins.print"), \
             mock.patch.object(pipeline, "resolve_cli", return_value=["agy.exe"]), \
             mock.patch.object(pipeline, "run", return_value=(rc, out, err)):
            return pipeline.call_implementer(self.c, feedback)

    def log(self, n=1):
        return (self.run_dir / f"implementer_attempt_{n}.log").read_text(encoding="utf-8")

    def test_response_is_kept_even_when_rc_is_zero(self):
        """これが退行そのもの。rc == 0 の応答を捨てると原因が追えなくなる。"""
        ok, _ = self.call(rc=0, out=REFUSAL)
        self.assertTrue(ok)
        self.assertIn("副操縦士プロトコル", self.log())
        self.assertIn("- rc: 0", self.log())

    def test_stderr_is_kept_when_the_cli_fails(self):
        ok, _ = self.call(rc=124, err="TTL超過 (300s): 実装AI")
        self.assertFalse(ok)
        self.assertIn("TTL超過", self.log())
        self.assertIn("- rc: 124", self.log())

    def test_the_prompt_and_the_feedback_are_kept(self):
        """何を渡したかが残らないと、応答だけ見ても切り分けられない。"""
        self.call(rc=0, out="ok", feedback="GamePhase.cs が存在しません")
        body = self.log()
        self.assertIn(str(self.c.sandbox), body)          # 作業場所の前置き
        self.assertIn("GamePhase.cs を作る", body)         # 単位のプロンプト
        self.assertIn("GamePhase.cs が存在しません", body)  # 前回の失敗

    def test_each_attempt_gets_its_own_file(self):
        """上書きすると、3 試行のうち最後しか残らない。"""
        self.call(rc=0, out="1 回目")
        self.c.metrics["attempt"] = 2
        self.call(rc=0, out="2 回目")
        self.assertIn("1 回目", self.log(1))
        self.assertIn("2 回目", self.log(2))

    def test_the_log_sits_next_to_the_telemetry(self):
        """1 回の実行の記録をばらさない。調べる人は pipeline.log と同じ所を見る。"""
        self.call(rc=0, out="ok")
        self.assertTrue((self.c.tel_path.parent / "implementer_attempt_1.log").exists())

    def test_it_falls_back_to_the_out_dir_without_telemetry(self):
        """--telemetry を渡さない手動実行でも残す。"""
        self.c.tel_path = None
        self.call(rc=0, out="ok")
        self.assertIn("ok", (self.out / "implementer_attempt_1.log").read_text(encoding="utf-8"))

    def test_a_failed_write_does_not_change_the_verdict(self):
        """観測のための機能。判定には使わないので、書けなくても実行は止めない。"""
        with mock.patch.object(pipeline.Path, "write_text", side_effect=OSError("掴まれています")):
            ok, msg = self.call(rc=0, out=REFUSAL)
        self.assertTrue(ok)
        self.assertEqual(msg, "")
        self.assertFalse((self.run_dir / "implementer_attempt_1.log").exists())


if __name__ == "__main__":
    unittest.main()
