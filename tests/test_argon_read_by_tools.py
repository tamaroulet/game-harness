"""tests/test_argon_read_by_tools.py

harness/argon_eval.py に追加される read_mode='tools'・focus・CLI オプションの受入試験。
"""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import argon_eval


class TestArgonReadByTools(unittest.TestCase):
    """argon_eval の tools 読み取り方式および追加機能の受入試験。"""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.td.name).resolve()

        # main の写し（mirror）の作成
        self.mirror_dir = self.tmp_path / "mirror"
        self.mirror_dir.mkdir()
        (self.mirror_dir / ".mirror.json").write_text(
            json.dumps({"sha": "main-sha-001"}), encoding="utf-8"
        )
        (self.mirror_dir / "app.py").write_text(
            "print('main code: version 1.0')\n", encoding="utf-8"
        )
        (self.mirror_dir / "common.py").write_text(
            "def util():\n    return 'common'\n", encoding="utf-8"
        )

        # 枝の写し（branches/12）の作成
        self.branches_dir = self.tmp_path / "branches"
        self.branches_dir.mkdir()
        self.b12_dir = self.branches_dir / "12"
        self.b12_dir.mkdir()
        (self.b12_dir / ".branch.json").write_text(
            json.dumps({"sha": "b12-sha-001"}), encoding="utf-8"
        )
        # app.py は変更、common.py は同一、feature.py は追加
        (self.b12_dir / "app.py").write_text(
            "print('branch 12 code: version 2.0')\n", encoding="utf-8"
        )
        (self.b12_dir / "common.py").write_text(
            "def util():\n    return 'common'\n", encoding="utf-8"
        )
        (self.b12_dir / "feature.py").write_text(
            "def new_feature():\n    return True\n", encoding="utf-8"
        )

        # argon 評価ディレクトリの設定
        self.argon_dir = self.tmp_path / "argon"
        self.argon_dir.mkdir()

        self.cfg = {
            "mirror": str(self.mirror_dir),
            "branches": str(self.branches_dir),
            "argon": str(self.argon_dir),
            "evaluator": {
                "cli": "agy",
                "model_name": "gemini-test",
                "output_format_args": ["--format", "stream-json"],
            },
        }

    def tearDown(self):
        self.td.cleanup()

    def test_tools_mode_does_not_inline_content_and_summarizes_copies(self):
        """tools モードのとき、写しのファイル本文を含めず、名前・根の場所・ファイル数・文字数合計を含めること。"""
        captured_prompts = []

        def fake_model(prompt):
            captured_prompts.append(prompt)
            return "判定: LOW\n判断点: 特になし", {"model": "test-argon", "prompt_tokens": 10}

        rec = argon_eval.evaluate(
            "pre",
            "main",
            cfg=self.cfg,
            model_fn=fake_model,
            root=ROOT,
            read_mode="tools",
        )

        self.assertEqual(len(captured_prompts), 1, "model_fn が 1 回呼び出されるべきです")
        prompt = captured_prompts[0]

        # 目印となるファイル中身が含まれていないこと
        self.assertNotIn(
            "print('main code: version 1.0')",
            prompt,
            "tools モードのプロンプトに mirror のファイル本文が含まれてしまっています",
        )
        self.assertNotIn(
            "print('branch 12 code: version 2.0')",
            prompt,
            "tools モードのプロンプトに branches のファイル本文が含まれてしまっています",
        )

        # 写しの名前・根の絶対パスが含まれていること
        self.assertIn("main", prompt, "プロンプトに写しの名前 'main' が含まれていません")
        self.assertIn(
            "branches/12",
            prompt,
            "プロンプトに写しの名前 'branches/12' が含まれていません",
        )
        self.assertIn(
            str(self.mirror_dir),
            prompt,
            "プロンプトに main の根の絶対パスが含まれていません",
        )
        self.assertIn(
            str(self.b12_dir),
            prompt,
            "プロンプトに branches/12 の根の絶対パスが含まれていません",
        )

        # ファイル数（main は 2、branches/12 は 3）が含まれていること
        self.assertIn("2", prompt, "プロンプトに main のファイル数 2 が含まれていません")
        self.assertIn("3", prompt, "プロンプトに branches/12 のファイル数 3 が含まれていません")

        # 記録および戻り値のメタデータ検証
        self.assertEqual(
            rec.get("read_mode"),
            "tools",
            "evaluate の戻り値の read_mode が 'tools' になっていません",
        )
        self.assertEqual(
            rec.get("focus_chars"),
            0,
            "focus 未指定時の focus_chars は 0 である必要があります",
        )

    def test_inline_mode_default_includes_content_and_records_mode(self):
        """既定（read_mode='inline'）のとき、従来どおりファイル本文が含まれ、記録に read_mode と focus_chars が足されること。"""
        captured_prompts = []

        def fake_model(prompt):
            captured_prompts.append(prompt)
            return "判定: LOW\n判断点: 特になし", {"model": "test-argon", "prompt_tokens": 10}

        # read_mode を指定せず既定値で呼ぶ
        rec = argon_eval.evaluate(
            "pre",
            "main",
            cfg=self.cfg,
            model_fn=fake_model,
            root=ROOT,
        )

        self.assertEqual(len(captured_prompts), 1, "model_fn が 1 回呼び出されるべきです")
        prompt = captured_prompts[0]

        # inline ではファイル本文がプロンプトに含まれること
        self.assertIn(
            "print('main code: version 1.0')",
            prompt,
            "inline モードのプロンプトにファイル本文が含まれていません",
        )

        # 戻り値と evals.jsonl の記録に read_mode と focus_chars が存在すること
        self.assertEqual(
            rec.get("read_mode"),
            "inline",
            "既定呼び出し時の戻り値 read_mode が 'inline' ではありません",
        )
        self.assertEqual(
            rec.get("focus_chars"),
            0,
            "focus 未指定時の戻り値 focus_chars が 0 ではありません",
        )

        evals_file = self.argon_dir / "evals.jsonl"
        self.assertTrue(evals_file.exists(), "evals.jsonl が生成されていません")
        lines = evals_file.read_text(encoding="utf-8").strip().splitlines()
        last_rec = json.loads(lines[-1])
        self.assertEqual(
            last_rec.get("read_mode"),
            "inline",
            "evals.jsonl に記録された read_mode が 'inline' ではありません",
        )
        self.assertEqual(
            last_rec.get("focus_chars"),
            0,
            "evals.jsonl に記録された focus_chars が 0 ではありません",
        )

    def test_tools_mode_post_pr_diff_included_and_supports_hash_prefix(self):
        """tools モードで kind='post' かつ target が PR 番号（'12' および '#12'）のとき、差分情報が含まれること。"""
        for pr_target in ["12", "#12"]:
            captured_prompts = []

            def fake_model(prompt):
                captured_prompts.append(prompt)
                return "判定: LOW\n判断点: 特になし", {"model": "test-argon", "prompt_tokens": 10}

            argon_eval.evaluate(
                "post",
                pr_target,
                cfg=self.cfg,
                model_fn=fake_model,
                root=ROOT,
                read_mode="tools",
            )

            self.assertEqual(
                len(captured_prompts),
                1,
                f"target={pr_target} で model_fn が呼ばれていません",
            )
            prompt = captured_prompts[0]

            # 変更ファイル（app.py）と追加ファイル（feature.py）の相対パスが含まれること
            self.assertIn(
                "app.py",
                prompt,
                f"target={pr_target} のプロンプトに変更ファイルの相対パス app.py が含まれていません",
            )
            self.assertIn(
                "feature.py",
                prompt,
                f"target={pr_target} のプロンプトに追加ファイルの相対パス feature.py が含まれていません",
            )

            # 変更行（unified diff 形式の + や -）が含まれること
            self.assertTrue(
                "-" in prompt and "+" in prompt,
                f"target={pr_target} のプロンプトに unified diff の変更行が含まれていません",
            )

    def test_tools_mode_diff_truncated_over_80000_chars(self):
        """diff が合計 80,000 字を超えた場合、切り詰めて『（ここで切った）』が付与されること。"""
        # 80,000字を超える差分を生成
        huge_file = self.b12_dir / "huge_diff.py"
        huge_content = "# line\n" + ("x = 1\n" * 15000)  # 約 90,000 文字
        huge_file.write_text(huge_content, encoding="utf-8")

        captured_prompts = []

        def fake_model(prompt):
            captured_prompts.append(prompt)
            return "判定: LOW\n判断点: 特になし", {"model": "test-argon", "prompt_tokens": 10}

        argon_eval.evaluate(
            "post",
            "12",
            cfg=self.cfg,
            model_fn=fake_model,
            root=ROOT,
            read_mode="tools",
        )

        self.assertEqual(len(captured_prompts), 1, "model_fn が呼ばれていません")
        prompt = captured_prompts[0]

        self.assertIn(
            "（ここで切った）",
            prompt,
            "80,000字を超えた diff に対して『（ここで切った）』の注記が含まれていません",
        )
        self.assertNotIn(
            huge_content,
            prompt,
            "80,000字を超える巨大な diff 全文が切り詰められずにプロンプトに含まれています",
        )

    def test_focus_included_in_prompt_and_recorded_properly(self):
        """focus が渡されたとき、見出し『## 照らしてほしい仮説と予想』の下に入り、字数が記録されること。"""
        focus_text = "仮説: tools モードへの移行によりプロンプトサイズが劇的に削減されること"
        captured_prompts = []

        def fake_model(prompt):
            captured_prompts.append(prompt)
            return "判定: LOW\n判断点: 特になし", {"model": "test-argon", "prompt_tokens": 10}

        rec = argon_eval.evaluate(
            "pre",
            "main",
            cfg=self.cfg,
            model_fn=fake_model,
            root=ROOT,
            read_mode="tools",
            focus=focus_text,
        )

        prompt = captured_prompts[0]
        self.assertIn(
            "## 照らしてほしい仮説と予想",
            prompt,
            "プロンプトに見出し『## 照らしてほしい仮説と予想』が含まれていません",
        )
        self.assertIn(
            focus_text,
            prompt,
            "見出しの下に focus の文がそのまま含まれていません",
        )

        # 戻り値と evals.jsonl の focus_chars を検証
        expected_len = len(focus_text)
        self.assertEqual(
            rec.get("focus_chars"),
            expected_len,
            "戻り値の focus_chars が focus 文字数と一致しません",
        )
        self.assertEqual(
            rec.get("read_mode"),
            "tools",
            "戻り値の read_mode が 'tools' ではありません",
        )

        evals_file = self.argon_dir / "evals.jsonl"
        lines = evals_file.read_text(encoding="utf-8").strip().splitlines()
        last_rec = json.loads(lines[-1])
        self.assertEqual(
            last_rec.get("focus_chars"),
            expected_len,
            "evals.jsonl の focus_chars が一致しません",
        )
        self.assertEqual(
            last_rec.get("read_mode"),
            "tools",
            "evals.jsonl の read_mode が一致しません",
        )

    def test_call_argon_model_passes_cwd_and_add_dirs_to_run_fn(self):
        """_call_argon_model が引数 cwd と add_dirs を run_fn に正しく渡すこと。"""
        fake_run_fn = MagicMock(return_value=(0, "dummy", ""))
        custom_cwd = str(self.mirror_dir)
        custom_add_dirs = [str(self.branches_dir), str(self.argon_dir)]

        with patch.object(argon_eval, "agy_pinned", create=True) as mock_agy_pinned, \
             patch.object(argon_eval, "model_pin", create=True) as mock_model_pin, \
             patch.object(argon_eval, "agy_stream", create=True) as mock_agy_stream, \
             patch.object(argon_eval.proc, "resolve_cli", return_value=["agy"]):

            mock_agy_pinned.require.return_value = None
            mock_model_pin.require_implementer.return_value = None
            mock_model_pin.check_reported.return_value = None
            mock_agy_stream.args.return_value = ["agy", "exec"]
            mock_agy_stream.parse.return_value = {
                "response": "判定: LOW\n判断点: 特になし",
                "model": "gemini-test",
            }

            # 1. cwd と add_dirs を指定して呼び出し
            argon_eval._call_argon_model(
                "テスト用プロンプト",
                self.cfg,
                run_fn=fake_run_fn,
                cwd=custom_cwd,
                add_dirs=custom_add_dirs,
            )

            self.assertTrue(fake_run_fn.called, "run_fn が呼び出されていません")
            cmd = fake_run_fn.call_args[0][0]
            kwargs = fake_run_fn.call_args[1]

            self.assertEqual(
                kwargs.get("cwd"),
                custom_cwd,
                "run_fn に渡された cwd が指定値と一致しません",
            )
            self.assertIn("--add-dir", cmd, "cmd に '--add-dir' が含まれていません")
            for d in custom_add_dirs:
                self.assertIn(d, cmd, f"cmd に add_dirs のパス '{d}' が含まれていません")

            # 2. cwd 省略時は ROOT になること
            fake_run_fn.reset_mock()
            argon_eval._call_argon_model(
                "テスト用プロンプト",
                self.cfg,
                run_fn=fake_run_fn,
            )
            kwargs_default = fake_run_fn.call_args[1]
            self.assertEqual(
                kwargs_default.get("cwd"),
                ROOT,
                "cwd 省略時に ROOT が run_fn に渡されていません",
            )

    def test_evaluate_tools_mode_passes_cwd_and_branches_to_call_model(self):
        """evaluate が read_mode='tools' のとき、cwd に main 写しの場所、add_dirs に cfg['branches'] を渡すこと。"""
        with patch.object(
            argon_eval,
            "_call_argon_model",
            return_value=("判定: LOW\n判断点: 特になし", {"model": "test"}),
        ) as mock_call:
            argon_eval.evaluate(
                "pre",
                "main",
                cfg=self.cfg,
                root=ROOT,
                read_mode="tools",
            )

            self.assertTrue(mock_call.called, "_call_argon_model が呼ばれていません")
            call_kwargs = mock_call.call_args[1]

            # cwd に main の写しの場所が渡されていること
            self.assertEqual(
                str(Path(call_kwargs.get("cwd")).resolve()),
                str(self.mirror_dir.resolve()),
                "tools モードで _call_argon_model に渡された cwd が main の写しの場所と一致しません",
            )

            # add_dirs に cfg['branches'] が含まれていること
            passed_add_dirs = [
                str(Path(p).resolve()) for p in call_kwargs.get("add_dirs", ())
            ]
            self.assertIn(
                str(self.branches_dir.resolve()),
                passed_add_dirs,
                "tools モードで _call_argon_model の add_dirs に cfg['branches'] が渡されていません",
            )

    def test_main_cli_accepts_tools_and_with_flags(self):
        """main(argv) が --tools および --with を正しく受け取って evaluate に渡すこと。"""
        focus_file = self.tmp_path / "focus_input.txt"
        focus_content = "仮説: 差分評価が正確に行われること"
        focus_file.write_text(focus_content, encoding="utf-8")

        with patch.object(
            argon_eval,
            "evaluate",
            return_value={"decision": "LOW", "read_mode": "tools", "focus_chars": len(focus_content)},
        ) as mock_eval:
            argv = [
                "post",
                "12",
                "--tools",
                "--with",
                str(focus_file),
            ]
            exit_code = argon_eval.main(argv, cfg=self.cfg)

            self.assertEqual(
                exit_code,
                0,
                f"main が正常引数に対して終了コード 0 以外（{exit_code}）を返しました",
            )
            self.assertTrue(mock_eval.called, "evaluate が呼ばれていません")
            eval_kwargs = mock_eval.call_args[1]
            self.assertEqual(
                eval_kwargs.get("read_mode"),
                "tools",
                "--tools フラグから read_mode='tools' が渡されていません",
            )
            self.assertEqual(
                eval_kwargs.get("focus"),
                focus_content,
                "--with ファイルの内容が focus として渡されていません",
            )

    def test_main_cli_invalid_arguments_returns_1_and_no_record(self):
        """main(argv) が不正引数（不足、未知フラグ、--with の欠落）のとき終了コード 1 を返し記録を書かないこと。"""
        evals_file = self.argon_dir / "evals.jsonl"
        invalid_argvs = [
            ["pre"],  # 引数が 2 つ未満
            ["pre", "main", "--unknown-flag"],  # 知らない旗
            ["pre", "main", "--with"],  # --with の後にファイルが無い
        ]

        for argv in invalid_argvs:
            exit_code = argon_eval.main(argv, cfg=self.cfg)
            self.assertEqual(
                exit_code,
                1,
                f"不正な引数 {argv} に対して終了コード 1 以外（{exit_code}）が返されました",
            )
            if evals_file.exists():
                lines = evals_file.read_text(encoding="utf-8").strip().splitlines()
                self.assertEqual(
                    len(lines),
                    0,
                    f"不正な引数 {argv} で evals.jsonl に記録が書き込まれてしまっています",
                )


if __name__ == "__main__":
    unittest.main()
