"""実装役への入力に規模の制約の節を出さない（hline.build_prompt・implement）の検査。規模の検査は Gate 1 の警告として残る。
一時ディレクトリと mock だけで済ませ、CLI・git・ネットワーク・モデルは呼ばない。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import hline  # noqa: E402
import size_limits as sl  # noqa: E402

CFG, LIM = hline.load_config(), sl.limits()
WHAT = "# T-010-a\n\n## What\n本文"   # 実装役に渡すのは What の本文（C5）
GONE = ("規模の制約", "循環的複雑度", "丸ごと消してよいファイル", "すでに上限を超えているファイル")


def lines(n):
    return "x = 1\n" * n


class Base(unittest.TestCase):
    def tree(self, files):
        d = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name, text in files.items():
            (d / name).parent.mkdir(parents=True, exist_ok=True)
            (d / name).write_bytes(text.encode("utf-8"))
        return d


class Removed(unittest.TestCase):
    def test_the_section_builders_are_gone(self):
        self.assertFalse(hasattr(sl, "prompt_text"))
        self.assertFalse(hasattr(sl, "oversized_files"))


class BuildPrompt(unittest.TestCase):
    def test_the_prompt_has_no_size_section_and_keeps_the_what_body(self):
        for prompt in (hline.build_prompt(WHAT), hline.build_prompt(WHAT, "FB", "# 目次X")):
            for word in GONE:
                self.assertNotIn(word, prompt)
            self.assertIn(WHAT.strip(), prompt)
        self.assertIn("# 目次X", hline.build_prompt(WHAT, None, "# 目次X"))


class Implement(Base):
    def run_implement(self, wt, feedback):
        inputs = []

        def fake_run(args, cwd, ttl, label, env=None, input=None):
            inputs.append(input)
            return 0, json.dumps({"result": "x", "modelUsage": {"claude-sonnet-5-5": {}}, "num_turns": 1}), ""
        with mock.patch.object(hline.proc, "run", side_effect=fake_run), mock.patch.object(hline.proc, "resolve_cli", return_value=["claude"]):
            hline.implement(CFG, wt, WHAT, feedback, Path(wt) / "log")
        return inputs[0]

    def test_neither_the_first_attempt_nor_a_retry_carries_the_size_section(self):
        wt = self.tree({"harness/big.py": lines(LIM["new_module_max_lines"] + 3)})
        for feedback in (None, "前回の出力"):
            sent = self.run_implement(wt, feedback)
            for word in GONE:
                self.assertNotIn(word, sent)
            self.assertNotIn(f"（{LIM['new_module_max_lines'] + 3} 行）", sent)   # 目次には載るが、超過ファイルの一覧は出ない
            self.assertEqual("前回の出力" in sent, bool(feedback))


class GateSize(Base):
    def gate(self, files, before=None):
        wt = self.tree(files)
        ok, warn = (0, "", ""), []
        with mock.patch.object(sl, "head_source", return_value=lambda path: before), \
                mock.patch.object(hline, "diff_counts", return_value={"added": 1, "deleted": 0, "deleted_files": ()}), \
                mock.patch.object(hline.proc, "run", return_value=ok):
            return (*hline.gate(CFG, wt, list(files), warnings=warn), warn)

    def test_oversized_modules_and_complexity_still_fail_and_small_ones_pass(self):
        """規模の制約は Gate 1 を不合格にせず、警告として記録される（C2）。"""
        ok, why, warn = self.gate({"harness/x.py": lines(LIM["new_module_max_lines"])})
        self.assertTrue(ok, why)
        self.assertEqual(warn, [])
        ok, why, warn = self.gate({"harness/x.py": lines(LIM["new_module_max_lines"] + 1)})
        self.assertTrue(ok)
        self.assertIn(str(LIM["new_module_max_lines"] + 1), "\n".join(warn))
        branchy = "def f(x):\n" + "    if x:\n        x += 1\n" * LIM["max_complexity"] + "    return x\n"
        ok, why, warn = self.gate({"harness/x.py": branchy})
        self.assertTrue(ok)
        self.assertIn("循環的複雑度", "\n".join(warn))


if __name__ == "__main__":
    unittest.main()
