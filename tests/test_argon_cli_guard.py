"""評価役の道具（argon_eval）の打ち違いの守り：対象が - で始まる・-h と --help の位置・組んだ頼みの字の上限。
モデルは呼ばない（model_fn か _call_argon_model を差し替える）。"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import argon_eval  # noqa: E402


def run_main(argv, cfg):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = argon_eval.main(argv, cfg=cfg)
    return code, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        mirror, branches = self.root / "main", self.root / "branches"
        (branches / "12").mkdir(parents=True)
        mirror.mkdir()
        (mirror / ".mirror.json").write_text(json.dumps({"sha": "m1"}), encoding="utf-8")
        (mirror / "a.py").write_text("x = 1\n", encoding="utf-8")
        (branches / "12" / ".branch.json").write_text(json.dumps({"sha": "b1"}), encoding="utf-8")
        (branches / "12" / "a.py").write_text("x = 2\n", encoding="utf-8")
        self.cfg = {"mirror": str(mirror), "branches": str(branches), "argon": str(self.root / "argon")}
        self.prompts = []

    def model(self, prompt):
        self.prompts.append(prompt)
        return "判定: LOW\n判断点: よい", {"model": "m", "prompt_tokens": 1, "completion_tokens": 1}

    def records(self):
        f = self.root / "argon" / "evals.jsonl"
        return f.read_text(encoding="utf-8").splitlines() if f.exists() else []


class Usage(Base):
    def test_target_starting_with_dash_shows_usage_and_returns_2(self):
        """対象（2 つ目の引数）が - で始まれば、評価を呼ばずに使い方を出して 2。"""
        for argv in (["post", "-x"], ["post", "--tools", "12"], ["pre", "--with", "f.txt"], ["post", "-"]):
            with self.subTest(argv=argv), mock.patch.object(argon_eval, "evaluate") as ev:
                code, _, err = run_main(argv, self.cfg)
                self.assertEqual(code, 2)
                self.assertIn(argon_eval.USAGE, err)
                ev.assert_not_called()

    def test_help_anywhere_shows_usage_and_returns_0(self):
        """-h・--help がどこにあっても、評価を呼ばずに使い方を出して 0（10 月の打ち違い post --help を含む）。"""
        argvs = (["--help"], ["-h"], ["post", "--help"], ["post", "12", "-h"], ["pre", "W-1", "--tools", "--help"],
                 ["post", "12", "--with", "-h"])
        for argv in argvs:
            with self.subTest(argv=argv), mock.patch.object(argon_eval, "evaluate") as ev:
                code, out, _ = run_main(argv, self.cfg)
                self.assertEqual(code, 0)
                self.assertIn(argon_eval.USAGE, out)
                ev.assert_not_called()
        self.assertEqual(self.records(), [])

    def test_well_formed_arguments_still_evaluate(self):
        """形の合う引数は今どおり評価を呼ぶ。"""
        with mock.patch.object(argon_eval, "evaluate", return_value={"decision": "LOW", "usage": {}}) as ev:
            self.assertEqual(run_main(["post", "12", "--tools"], self.cfg)[0], 0)
        ev.assert_called_once()


class PromptLimit(Base):
    def length(self, **kw):
        """上限を外した走りで組まれる頼みの字数。"""
        with mock.patch.object(argon_eval, "INLINE_MAX_CHARS", 10 ** 12):
            argon_eval.evaluate("post", "12", cfg=self.cfg, model_fn=self.model, root=self.root, **kw)
        return len(self.prompts.pop())

    def test_limit_is_one_million_chars(self):
        self.assertEqual(argon_eval.INLINE_MAX_CHARS, 1_000_000)

    def test_exactly_at_limit_passes_and_one_over_stops(self):
        """頼みの字数ちょうどの上限は通り、1 字超えはモデルを呼ばずに止まり、記録も書かない。inline・tools・--with のどれでも同じ。"""
        for kw in ({}, {"read_mode": "tools"}, {"focus": "仮説: 違いは 1 行だけ"}, {"read_mode": "tools", "focus": "予想"}):
            with self.subTest(**kw):
                n = self.length(**kw)
                before = len(self.records())
                with mock.patch.object(argon_eval, "INLINE_MAX_CHARS", n):
                    rec = argon_eval.evaluate("post", "12", cfg=self.cfg, model_fn=self.model, root=self.root, **kw)
                self.assertEqual(rec["prompt_chars"], n)
                self.assertEqual(len(self.records()), before + 1)
                with mock.patch.object(argon_eval, "INLINE_MAX_CHARS", n - 1):
                    with self.assertRaises(argon_eval.ArgonError) as ctx:
                        argon_eval.evaluate("post", "12", cfg=self.cfg, model_fn=self.model, root=self.root, **kw)
                self.assertIn(str(n), str(ctx.exception))
                self.assertEqual(len(self.prompts), 1)   # 上限ちょうどの 1 回だけ
                self.prompts.clear()
                self.assertEqual(len(self.records()), before + 1)

    def test_main_prints_one_line_and_returns_2_without_calling_the_model(self):
        """main は字数と、--with で絞るか tools の読み方にする旨を 1 行出して 2。本物のモデルの呼び出しに届かない。"""
        with mock.patch.object(argon_eval, "INLINE_MAX_CHARS", 10), \
                mock.patch.object(argon_eval, "_call_argon_model") as call:
            code, _, err = run_main(["post", "12"], self.cfg)
        self.assertEqual(code, 2)
        call.assert_not_called()
        line = err.strip()
        self.assertEqual(len(line.splitlines()), 1)
        for word in ("字", "--with", "--tools"):
            self.assertIn(word, line)
        self.assertEqual(self.records(), [])


if __name__ == "__main__":
    unittest.main()
